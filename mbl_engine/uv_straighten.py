# -*- coding: utf-8 -*-
"""Enderezado de islas de UV sin Maya: solo arrays (numpy).

Un brazo, una pierna, un dedo, un cable o un cinturón son tubos de quads que se cortan a lo largo. Al desplegarlos, las dos
orillas del corte no salen paralelas (el tubo se abre en abanico o se curva como un plátano) y una textura con vetas o
rayones se parte en la costura: el dibujo gira al cruzarla. Aquí cada isla que tiene un corte contra sí misma se deforma lo
mínimo para que las dos copias de cada arista cortada sean la misma arista en UV (misma dirección y misma longitud: la
costura pasa a ser una traslación), con las filas de quads rectas a los dos lados.

Cómo (por isla):
- Los cortes de la isla contra sí misma son los pares de aristas 3D con dos caras en la isla y UV distintas. Solo se
  fuerzan los pares que giran poco entre sí (menos de cada límite de MAX_PAIR_ANGLES: un abanico, no un pliegue) y que no
  acaban en un vértice de la isla (la punta de un corte).
- Se resuelve un ARAP (rígido lo más posible, Sorkine-Alexa) sobre las UV actuales: cada cara se parece a la que tenía,
  girada y con un tirón hacia el área que le toca por su tamaño 3D (AREA_PULL), y las dos copias de cada arista cortada
  pesan `mu` por igualar su vector. Con `mu` grande las orillas casan del todo pero la isla se deforma más: para cada
  límite se prueba una escalera de `mu` de mayor a menor.
- Se acepta una solución solo si la isla no estira más que antes (estiramiento medio y caras > 1,5x, con su propia
  densidad de referencia), no tiene caras en espejo ni solapes nuevos y casa más longitud de corte; entre las que sirven,
  la que más casa. Si ninguna sirve (o la isla no tiene cortes contra sí misma, tiene n-gons o es muy grande) se devuelve
  None y la isla se deja como estaba.

Por qué no una rejilla recta de filas y columnas: en los retopos de prueba casi ninguna isla es una rejilla de quads pura
(humano: 0 de 28; criatura y midnight: 9 y 12 con 22 y 32 caras en total; prop: 4, y ya casaban), porque llevan polos,
manos y dedos. El ARAP con costura de traslación hace lo mismo (filas rectas y orillas paralelas) sin pedir esa estructura.

Núcleo puro: no importa nada de Maya. `straighten_island` trabaja una isla; `straighten` las recorre todas.
"""

import contextlib
import glob
import os

import numpy as np

from . import uv_quality

# giro máximo entre las dos copias de una arista cortada (grados) para que se fuerce a casar: más allá es un pliegue
MAX_PAIR_ANGLES = (25.0, 40.0, 60.0, 90.0)
# "casa" = giro menor que esto (misma definición que uv_quality / el banco)
MATCH_ANGLE = uv_quality.SEAM_ANGLE
# escalera de pesos de la costura (ARAP: cada arista de cara pesa 1), de más a menos casado; se queda la primera que sirve
MU_LADDER = (30.0, 10.0, 3.0, 1.0, 0.3)
ARAP_ITERATIONS = 6
# el sistema es denso (n x n): por encima de estos vértices UV la isla no se toca
MAX_VERTICES = 1600
# una isla se toca solo si gana al menos esta longitud de costura casada (fracción de la longitud de sus cortes)
MIN_GAIN = 0.02
# el estiramiento medio de la isla no puede subir más que esto: ARAP no recompone las formas exactas, y un anillo ya
# desplegado sin distorsión (estiramiento 1,000) queda en 1,004 al ponerlo recto aunque sus quads sean rectángulos
STRETCH_TOLERANCE = 5e-3
# cuánto tira cada cara hacia el área que le toca por su tamaño 3D (0 = solo conservar su forma, 1 = igualar todas)
AREA_PULL = 0.3
# regularización que ancla la traslación
ANCHOR = 1e-6


# ------------------------------------------------------------------ hilos de BLAS

@contextlib.contextmanager
def _single_thread():
    """BLAS con un solo hilo mientras dura el bloque (si se puede). Las matrices de una isla son pequeñas (cientos de
    vértices) y con los hilos por defecto, en un equipo ocupado, una inversión de 200 x 200 tardaba 0,16 s en vez de 1 ms.
    Mejor esfuerzo: si numpy no trae el OpenBLAS conocido no se toca nada."""
    set_threads = get_threads = None
    try:
        import ctypes
        folder = os.path.join(os.path.dirname(os.path.abspath(np.__file__)), os.pardir, "numpy.libs")
        for path in glob.glob(os.path.join(folder, "*openblas*.dll")):
            lib = ctypes.CDLL(path)
            for suffix in ("64_", "_", ""):
                if hasattr(lib, "openblas_set_num_threads" + suffix):
                    set_threads = getattr(lib, "openblas_set_num_threads" + suffix)
                    get_threads = getattr(lib, "openblas_get_num_threads" + suffix)
                    break
            if set_threads:
                break
        previous = get_threads() if set_threads else 0
        if set_threads and previous > 1:
            set_threads(1)
        else:
            set_threads = None
    except Exception:
        set_threads = None
    try:
        yield
    finally:
        if set_threads:
            set_threads(previous)


# ------------------------------------------------------------------ tablas de la isla

def _seam_pairs(faces, uv_faces):
    """Pares de aristas cortadas dentro del grupo de caras: (índices UV a1, b1, a2, b2, longitud) con a -> b en el mismo
    sentido 3D en las dos copias. `faces` son índices de punto y `uv_faces` índices de UV (mismo orden de esquinas)."""
    edges = {}
    for f, g in zip(faces, uv_faces):
        n = len(f)
        for k in range(n):
            a, b = f[k], f[(k + 1) % n]
            ua, ub = g[k], g[(k + 1) % n]
            if a > b:
                a, b, ua, ub = b, a, ub, ua
            edges.setdefault((a, b), []).append((ua, ub))
    out = [(s[0][0], s[0][1], s[1][0], s[1][1], a, b) for (a, b), s in edges.items() if len(s) == 2 and s[0] != s[1]]
    return out


def _angles(uv, pairs):
    """Giro (grados, 0 a 180) entre las dos copias de cada par; 180 si alguna copia no mide nada."""
    d1 = uv[pairs[:, 1]] - uv[pairs[:, 0]]
    d2 = uv[pairs[:, 3]] - uv[pairs[:, 2]]
    live = (np.linalg.norm(d1, axis=1) > 1e-12) & (np.linalg.norm(d2, axis=1) > 1e-12)
    angle = np.abs(np.degrees(np.arctan2(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0], (d1 * d2).sum(axis=1))))
    return np.where(live, angle, 180.0)


def _matched(uv, pairs, length):
    """Longitud 3D de costura que casa (giro menor que MATCH_ANGLE) entre los pares."""
    return float(length[_angles(uv, pairs) < MATCH_ANGLE].sum())


def _stretch(world, flat):
    """(media, nº de caras > 1,5x) del estiramiento de área de la isla, con su propia densidad como referencia: el
    empaquetado final deja a todas las islas con la misma densidad de texel."""
    ok = world > 0
    if flat.sum() <= 0 or not ok.any():
        return 0.0, 0
    ratio = (flat[ok] / world[ok]) / (flat[ok].sum() / world[ok].sum())
    distortion = np.where(ratio > 0, np.maximum(ratio, 1.0 / np.maximum(ratio, 1e-300)), 10.0)
    mean = float((np.minimum(distortion, uv_quality.STRETCH_CAP) * world[ok]).sum() / world[ok].sum())
    return mean, int((distortion > uv_quality.STRETCH_BAD).sum())


# ------------------------------------------------------------------ ARAP con costura de traslación

class _Solver(object):
    """ARAP local-global sobre las UV de una isla con las parejas de aristas cortadas tiradas a ser iguales.

    La matriz del paso global es Laplaciano + mu * S'S (S: una fila por par, con su restricción (b1 - a1) - (b2 - a2)).
    Se invierte una sola vez el Laplaciano y cada `mu` se resuelve con Woodbury, que solo invierte una matriz k x k (k =
    pares): probar una escalera de pesos cuesta casi lo mismo que probar uno."""

    def __init__(self, uv, faces, scale=None):
        self.uv = uv
        n = len(uv)
        self.n = n
        e_i, e_j, e_f = [], [], []
        for k, f in enumerate(faces):
            m = len(f)
            for a in range(m):
                e_i.append(f[a])
                e_j.append(f[(a + 1) % m])
                e_f.append(k)
            if m == 4:
                # las diagonales impiden que un quad se cizalle sin coste
                for a in (0, 1):
                    e_i.append(f[a])
                    e_j.append(f[a + 2])
                    e_f.append(k)
        self.e_i, self.e_j, self.e_f = np.array(e_i), np.array(e_j), np.array(e_f)
        self.faces = len(faces)
        self.old = uv[self.e_i] - uv[self.e_j]
        if scale is not None:
            # cada cara apunta a su tamaño ideal (área 3D por la densidad de la isla), no solo a su forma
            self.old = self.old * scale[self.e_f][:, None]
        size = n * n
        i, j = self.e_i, self.e_j
        laplacian = (np.bincount(i * n + i, minlength=size) + np.bincount(j * n + j, minlength=size)
                     - np.bincount(i * n + j, minlength=size) - np.bincount(j * n + i, minlength=size))
        system = laplacian.reshape(n, n).astype(np.float64) + ANCHOR * np.eye(n)
        self.inverse = np.linalg.inv(system)

    def link(self, pairs):
        """Fija las parejas de aristas cortadas que se pueden forzar a ser iguales (una fila de S por pareja)."""
        n, k = self.n, len(pairs)
        seam = np.zeros((k, n))
        rows = np.repeat(np.arange(k), 4)
        cols = np.stack([pairs[:, 1], pairs[:, 0], pairs[:, 3], pairs[:, 2]], axis=1).ravel()
        np.add.at(seam, (rows, cols), np.tile([1.0, -1.0, -1.0, 1.0], k))
        self.seam = seam
        self.reach = self.inverse @ seam.T
        self.gram = seam @ self.reach

    def solve(self, mu, active):
        """UV nuevas con las parejas `active` (máscara) pesando `mu`. Woodbury: (A + S'MS)^-1 = A^-1 - W (I + M G)^-1 M W',
        con W = A^-1 S' y G = S W, todo restringido a las parejas activas."""
        uv, n = self.uv, self.n
        idx = np.flatnonzero(active)
        reach, seam = self.reach[:, idx], self.seam[idx]
        link = mu * np.linalg.inv(np.eye(len(idx)) + mu * self.gram[np.ix_(idx, idx)])
        p = uv.copy()
        for _ in range(ARAP_ITERATIONS):
            new = p[self.e_i] - p[self.e_j]
            old = self.old
            # giro óptimo de cada cara (2D): ángulo de la covarianza de sus aristas
            sin = np.bincount(self.e_f, weights=new[:, 1] * old[:, 0] - new[:, 0] * old[:, 1], minlength=self.faces)
            cos = np.bincount(self.e_f, weights=new[:, 0] * old[:, 0] + new[:, 1] * old[:, 1], minlength=self.faces)
            length = np.maximum(np.hypot(sin, cos), 1e-300)
            c, s = (cos / length)[self.e_f], (sin / length)[self.e_f]
            target = np.stack([c * old[:, 0] - s * old[:, 1], s * old[:, 0] + c * old[:, 1]], axis=1)
            rhs = np.zeros((n, 2))
            for k in range(2):
                rhs[:, k] = (np.bincount(self.e_i, weights=target[:, k], minlength=n)
                             - np.bincount(self.e_j, weights=target[:, k], minlength=n))
            free = self.inverse @ (rhs + ANCHOR * uv)
            q = free - reach @ (link @ (seam @ free))
            moved = float(np.abs(q - p).max())
            p = q
            if moved < 1e-7 * max(float(np.ptp(uv)), 1e-12):
                break
        return p


# ------------------------------------------------------------------ una isla

def straighten_island(points, faces, uv_points, uv_faces, face_ids, world=None, ladder=None, angle_steps=None):
    """Endereza una isla. `face_ids`: las caras de la isla.

    Devuelve None si la isla se deja como está (sin cortes contra sí misma, con n-gons, muy grande, o ninguna solución
    gana costura casada sin estirar más ni solapar). Si no, un diccionario con:
      uv_ids       índices de UV de la isla
      uv           sus posiciones nuevas (k x 2)
      mu           peso de la solución elegida
      matched      (antes, después): longitud 3D de costura propia que casa, en %
      stretch      (antes, después): estiramiento medio de la isla
    """
    ladder = MU_LADDER if ladder is None else ladder
    angle_steps = MAX_PAIR_ANGLES if angle_steps is None else angle_steps
    face_ids = list(face_ids)
    if not face_ids or any(len(faces[f]) not in (3, 4) or len(uv_faces[f]) != len(faces[f]) for f in face_ids):
        return None
    uv_points = np.asarray(uv_points, dtype=np.float64).reshape(-1, 2)
    local = np.unique(np.fromiter((u for f in face_ids for u in uv_faces[f]), dtype=np.int64))
    if len(local) > MAX_VERTICES:
        return None
    index = {int(u): i for i, u in enumerate(local)}
    lfaces = [[index[u] for u in uv_faces[f]] for f in face_ids]
    pairs = _seam_pairs([faces[f] for f in face_ids], lfaces)
    if not pairs:
        return None
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    length = np.array([np.linalg.norm(pts[a] - pts[b]) for *_, a, b in pairs])
    pairs = np.array([p[:4] for p in pairs], dtype=np.int64)
    uv0 = uv_points[local]
    total = float(length.sum())
    if total <= 0:
        return None
    # solo se fuerzan los pares que casarían con una deformación suave: ni pliegues ni puntas de corte
    angle = _angles(uv0, pairs)
    tip = (pairs[:, 0] == pairs[:, 2]) | (pairs[:, 1] == pairs[:, 3]) | (pairs[:, 0] == pairs[:, 3]) | (pairs[:, 1] == pairs[:, 2])
    if not ((angle < max(angle_steps)) & ~tip).any():
        return None
    before = _matched(uv0, pairs, length)
    if world is None:
        world = uv_quality.face_areas(points, faces)
    world = np.asarray(world)[face_ids]
    flat0 = np.abs(uv_quality.uv_face_areas(uv0, lfaces)[0])
    stretch0 = _stretch(world, flat0)
    scale = None
    if AREA_PULL > 0 and flat0.sum() > 0:
        ideal = world * (flat0.sum() / max(world.sum(), 1e-300))
        scale = (np.maximum(ideal, 1e-300) / np.maximum(flat0, 1e-300)) ** (0.5 * AREA_PULL)
    solver = _Solver(uv0, lfaces, scale)
    candidates = (angle < max(angle_steps)) & ~tip
    solver.link(pairs[candidates])
    angle = angle[candidates]
    options = []
    overlaps0 = None
    seen = 0
    for limit in angle_steps:
        active = angle < limit
        if active.sum() == seen:
            continue
        seen = int(active.sum())
        # de más a menos casado: la primera solución que sirve de cada límite (con menos mu casa menos)
        for mu in ladder:
            new = solver.solve(mu, active)
            signed = uv_quality.uv_face_areas(new, lfaces)[0]
            if (signed <= 0).any():
                continue
            after = _matched(new, pairs, length)
            if after - before < MIN_GAIN * total:
                continue
            stretch1 = _stretch(world, np.abs(signed))
            if stretch1[1] > stretch0[1] or stretch1[0] > stretch0[0] + STRETCH_TOLERANCE:
                continue
            if overlaps0 is None:
                overlaps0 = int(uv_quality.overlapping_faces(uv0, lfaces).sum())
            if int(uv_quality.overlapping_faces(new, lfaces).sum()) > overlaps0:
                continue
            options.append((after, mu, new, stretch1[0]))
            break
    if not options:
        return None
    after, mu, new, stretch1 = max(options, key=lambda o: o[0])
    return {"uv_ids": local, "uv": new, "mu": mu, "matched": (100.0 * before / total, 100.0 * after / total),
            "stretch": (stretch0[0], stretch1), "length": total}


# ------------------------------------------------------------------ toda la malla

def straighten(points, faces, uv_points, uv_faces, **kwargs):
    """Endereza todas las islas que se dejan. Devuelve (UV nuevas como array M x 2, lista con el informe de cada isla
    enderezada). Las UV de las islas que se dejan no cambian."""
    uv = np.array(uv_points, dtype=np.float64).reshape(-1, 2)
    island, count = uv_quality.islands(uv_faces)
    world = uv_quality.face_areas(points, faces)
    done = []
    with _single_thread():
        for k in range(count):
            face_ids = np.flatnonzero(island == k)
            result = straighten_island(points, faces, uv_points, uv_faces, face_ids, world, **kwargs)
            if result is not None:
                uv[result["uv_ids"]] = result["uv"]
                result["island"] = k
                result["faces"] = len(face_ids)
                done.append(result)
    return uv, done


# ------------------------------------------------------------------ islas que son una rejilla

# una rejilla se endereza si no gana caras con más de 1,5x de distorsión de área y su distorsión media casi no sube
GRID_EXTRA_MEAN = 0.005


def _grid_coordinates(island_faces, uv_faces):
    """(fila, columna) entera de cada punto UV si las caras forman una rejilla sin polos; None si no."""
    by_edge = {}
    for f in island_faces:
        corners = uv_faces[f]
        if len(corners) != 4:
            return None
        for k in range(4):
            a, b = corners[k], corners[(k + 1) % 4]
            by_edge.setdefault((min(a, b), max(a, b)), []).append(f)
    first = uv_faces[island_faces[0]]
    grid = {first[0]: (0, 0), first[1]: (1, 0), first[2]: (1, 1), first[3]: (0, 1)}
    seen, stack = {island_faces[0]}, [island_faces[0]]
    while stack:
        f = stack.pop()
        corners = uv_faces[f]
        for k in range(4):
            a, b, c, d = corners[k], corners[(k + 1) % 4], corners[(k + 2) % 4], corners[(k + 3) % 4]
            for other in by_edge[(min(a, b), max(a, b))]:
                if other == f:
                    continue
                ring = uv_faces[other]
                i = ring.index(a)
                # el vecino recorre la arista compartida en sentido contrario
                if ring[(i + 3) % 4] != b:
                    return None
                next_b, next_a = ring[(i + 2) % 4], ring[(i + 1) % 4]
                step = (grid[a][0] - grid[d][0], grid[a][1] - grid[d][1])
                wanted = {next_a: (grid[a][0] + step[0], grid[a][1] + step[1]),
                          next_b: (grid[b][0] + step[0], grid[b][1] + step[1])}
                for uv_id, cell in wanted.items():
                    if grid.setdefault(uv_id, cell) != cell:
                        return None
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
    # dos puntos en la misma casilla: la isla da la vuelta sobre sí misma
    return grid if len(set(grid.values())) == len(grid) else None


def rectify_grids(points, faces, uv_points, uv_faces):
    """Deja como una cuadrícula perfecta las islas que son una rejilla sin polos (tiras, correas, piezas duras): cada fila y
    columna, recta y con el ancho medio que tiene en 3D. Solo se tocan si no estiran más que antes.
    Devuelve (UVs nuevas, nº de islas enderezadas)."""
    points = np.asarray(points, dtype=np.float64)
    uv = np.array(uv_points, dtype=np.float64)
    position = {}
    for corners, verts in zip(uv_faces, faces):
        for uv_id, v in zip(corners, verts):
            position[uv_id] = v
    owners = {}
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for corners in uv_faces:
        for k in range(1, len(corners)):
            parent[find(corners[0])] = find(corners[k])
    for f, corners in enumerate(uv_faces):
        if corners:
            owners.setdefault(find(corners[0]), []).append(f)
    done = 0

    def distortion(island_faces, candidate):
        """(fracción de caras con más de 1,5x, media) de la distorsión de área dentro de la isla."""
        world = np.array([_polygon_area(points[faces[f]]) for f in island_faces])
        flat = np.array([_polygon_area(candidate[uv_faces[f]]) for f in island_faces])
        ok = (world > 0) & (flat > 0)
        if not ok.any():
            return 1.0, 4.0
        ratio = (flat[ok] / world[ok]) / (flat[ok].sum() / world[ok].sum())
        ratio = np.maximum(ratio, 1.0 / ratio)
        return float(np.mean(ratio > 1.5)), float(np.mean(ratio))

    for island_faces in owners.values():
        grid = _grid_coordinates(island_faces, uv_faces)
        if grid is None:
            continue
        widths, heights = {}, {}
        along = [np.zeros(2), np.zeros(2)]
        for f in island_faces:
            corners = uv_faces[f]
            for k in range(4):
                a, b = corners[k], corners[(k + 1) % 4]
                di, dj = grid[b][0] - grid[a][0], grid[b][1] - grid[a][1]
                length = float(np.linalg.norm(points[position[a]] - points[position[b]]))
                if abs(di) == 1 and dj == 0:
                    widths.setdefault(min(grid[a][0], grid[b][0]), []).append(length)
                    along[0] += (uv[b] - uv[a]) * di
                elif abs(dj) == 1 and di == 0:
                    heights.setdefault(min(grid[a][1], grid[b][1]), []).append(length)
                    along[1] += (uv[b] - uv[a]) * dj

        def positions(spans):
            result, value = {}, 0.0
            low, high = min(spans), max(spans)
            result[low] = 0.0
            for index in range(low, high + 1):
                value += float(np.mean(spans[index])) if index in spans else 0.0
                result[index + 1] = value
            return result
        if not widths or not heights:
            continue
        us, vs = positions(widths), positions(heights)
        # cada eje de la rejilla, al eje de la textura al que ya apuntaba (mismo giro: no se voltea nada)
        a, b = along
        if abs(a[0]) >= abs(a[1]):
            axis_i, axis_j = np.array([np.sign(a[0]) or 1.0, 0.0]), np.array([0.0, np.sign(b[1]) or 1.0])
        else:
            axis_i, axis_j = np.array([0.0, np.sign(a[1]) or 1.0]), np.array([np.sign(b[0]) or 1.0, 0.0])
        ids = list(grid)
        new = np.array([us[grid[i][0]] * axis_i + vs[grid[i][1]] * axis_j for i in ids])
        old = uv[ids]
        old_area = sum(_polygon_area(uv[uv_faces[f]]) for f in island_faces)
        candidate = uv.copy()
        candidate[ids] = new
        new_area = sum(_polygon_area(candidate[uv_faces[f]]) for f in island_faces)
        if new_area <= 0 or old_area <= 0:
            continue
        new = (new - new.mean(axis=0)) * (old_area / new_area) ** 0.5 + old.mean(axis=0)
        candidate[ids] = new
        new_bad, new_mean = distortion(island_faces, candidate)
        old_bad, old_mean = distortion(island_faces, uv)
        if new_bad > old_bad or new_mean > old_mean + GRID_EXTRA_MEAN:
            continue
        uv = candidate
        done += 1
    return uv, done


def _polygon_area(p):
    """Área de un polígono en 2D o en 3D."""
    p = np.asarray(p, dtype=np.float64)
    if p.shape[1] == 2:
        p = np.column_stack([p, np.zeros(len(p))])
    return 0.5 * float(np.linalg.norm(np.cross(p, np.roll(p, -1, axis=0)).sum(axis=0)))
