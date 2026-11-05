# -*- coding: utf-8 -*-
"""Calidad de unas UVs sin Maya: solo arrays.

Hasta ahora las UVs solo se medían dentro de Maya (`mbl_retopo_uv/uv.py`: estiramiento, textura aprovechada, solapes y
caras en espejo) y en el banco (`tests/bench.py`: continuidad en los cortes). Aquí están esas mismas medidas sobre arrays,
para juzgar UVs fuera de Maya y reutilizarlas desde otros programas:

- `points` (N x 3) y `faces` (lista de listas de índices de punto),
- `uv_points` (M x 2) y `uv_faces`: por cada cara, los índices de UV de sus esquinas, en el mismo orden (vacía si la
  cara no tiene UVs). Es lo que dan `getUVs` y `getAssignedUVs` de Maya, o los `vt` y las caras de un OBJ.

- stretch: distorsión de área de cada cara frente a 3D (media ponderada por área y % de caras con más de 1,5x).
- used: textura aprovechada (suma de las áreas UV frente al cuadrado 0-1).
- overlaps / flipped: caras que se pisan en UV y caras en espejo (área UV con signo negativo).
- islands: nº de islas, variación de la densidad de texel entre ellas y margen mínimo entre sus orillas.
- seams: cuánto casan las dos orillas de cada corte (giro y escala relativos).
- grade(): nota (0 bien, 1 revisar, 2 malo) con las razones.

Todo es numpy puro (se prueba sin Maya).
"""

import numpy as np

# dos caras se solapan si el interior de una entra más que esto (unidades UV) en el de la otra; tocarse no cuenta
OVERLAP_TOL = 1e-6
# el margen entre islas se mide solo hasta esta distancia (unidades UV): más lejos es "de sobra" y se da este valor
MARGIN_CAP = 0.02
# estiramiento: una cara cuenta como mala por encima de este factor, y en la media ninguna pesa más que el tope
STRETCH_BAD = 1.5
STRETCH_CAP = 4.0
# continuidad en los cortes (misma definición que tests/bench.py): giro y escala relativos entre las dos orillas
SEAM_ANGLE = 15.0
SEAM_SCALE = float(np.log2(1.1))
# pares de cajas que se comprueban a la vez, y entradas máximas de la rejilla (memoria acotada)
PAIR_CHUNK = 400000
MAX_ENTRIES = 20000000


# ------------------------------------------------------------------ tablas de esquinas

def _flatten(faces):
    """(nº de esquinas de cada cara, índices de todas las esquinas seguidos)."""
    counts = np.fromiter((len(f) for f in faces), dtype=np.int64, count=len(faces))
    ids = np.fromiter((i for f in faces for i in f), dtype=np.int64, count=int(counts.sum()))
    return counts, ids


def _corners(counts):
    """(cara de cada esquina, esquina siguiente dentro de su cara), con las esquinas en fila."""
    face = np.repeat(np.arange(len(counts)), counts)
    first = np.repeat(np.cumsum(counts) - counts, counts)
    following = first + (np.arange(len(face)) - first + 1) % np.repeat(counts, counts)
    return face, following


def face_areas(points, faces):
    """Área 3D de cada cara (vector de Newell: vale para quads alabeados y n-gons)."""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    counts, ids = _flatten(faces)
    if not len(ids):
        return np.zeros(len(counts))
    face, following = _corners(counts)
    p = points[ids] - points.mean(axis=0)
    cross = np.cross(p, p[following])
    total = np.stack([np.bincount(face, weights=cross[:, k], minlength=len(counts)) for k in range(3)], axis=1)
    return 0.5 * np.linalg.norm(total, axis=1)


def uv_face_areas(uv_points, uv_faces):
    """(área UV con signo de cada cara, tiene UVs): negativa = cara en espejo. Con menos de 3 esquinas no hay UVs."""
    uv = np.asarray(uv_points, dtype=np.float64).reshape(-1, 2)
    counts, ids = _flatten(uv_faces)
    has = counts >= 3
    if not len(ids):
        return np.zeros(len(counts)), has
    face, following = _corners(counts)
    cross = uv[ids, 0] * uv[ids[following], 1] - uv[ids[following], 0] * uv[ids, 1]
    area = 0.5 * np.bincount(face, weights=cross, minlength=len(counts))
    return np.where(has, area, 0.0), has


# ------------------------------------------------------------------ islas

def islands(uv_faces):
    """(isla de cada cara, nº de islas). Caras que comparten una UV son de la misma isla, como en Maya; -1 sin UVs."""
    counts, ids = _flatten(uv_faces)
    has = counts >= 3
    result = np.full(len(counts), -1, dtype=np.int64)
    if not has.any():
        return result, 0
    face, following = _corners(counts)
    use = has[face]
    a, b = ids[use], ids[following][use]
    parent = np.arange(int(ids.max()) + 1)
    while True:
        ra, rb = parent[a], parent[b]
        differ = ra != rb
        if not differ.any():
            break
        # la raíz mayor cuelga de la menor; después se acorta todo el árbol (salto de punteros)
        np.minimum.at(parent, np.maximum(ra[differ], rb[differ]), np.minimum(ra[differ], rb[differ]))
        while True:
            jumped = parent[parent]
            if (jumped == parent).all():
                break
            parent = jumped
    first = (np.cumsum(counts) - counts)[has]
    _, labels = np.unique(parent[ids[first]], return_inverse=True)
    result[has] = labels
    return result, int(labels.max()) + 1


def texel_variation(world, flat, island, count):
    """Variación (%) de la densidad de texel entre islas: desviación típica / media, ponderada por área 3D.

    La densidad de una isla es raíz(área UV / área 3D); si todas valen lo mismo, 0 %.
    """
    if count < 2:
        return 0.0
    ok = island >= 0
    world_sum = np.bincount(island[ok], weights=world[ok], minlength=count)
    flat_sum = np.bincount(island[ok], weights=flat[ok], minlength=count)
    use = (world_sum > 0) & (flat_sum > 0)
    if use.sum() < 2:
        return 0.0
    density = np.sqrt(flat_sum[use] / world_sum[use])
    weight = world_sum[use] / world_sum[use].sum()
    mean = float((weight * density).sum())
    return 100.0 * float(np.sqrt((weight * (density - mean) ** 2).sum())) / mean


# ------------------------------------------------------------------ estiramiento y aprovechado

def stretch_stats(world, flat):
    """Estiramiento de área, con la misma definición que `uv.stretch` de Maya.

    `world` y `flat`: área 3D y área UV (sin signo) de cada cara. 1.0 = perfecto. Devuelve la media ponderada por
    área 3D (ninguna cara pesa más que STRETCH_CAP) y el % de caras con más de STRETCH_BAD.
    """
    world_total, flat_total = float(world.sum()), float(flat.sum())
    if world_total <= 0 or flat_total <= 0:
        return {"stretch_mean": 0.0, "over_1_5_percent": 0.0}
    ok = world > 0
    ratio = (flat[ok] / world[ok]) / (flat_total / world_total)
    distortion = np.where(ratio > 0, np.maximum(ratio, 1.0 / np.maximum(ratio, 1e-300)), 10.0)
    return {"stretch_mean": float((np.minimum(distortion, STRETCH_CAP) * world[ok]).sum() / world_total),
            "over_1_5_percent": 100.0 * float((distortion > STRETCH_BAD).sum()) / len(world)}


# ------------------------------------------------------------------ pares de cajas (rejilla)

def _box_pairs(lo, hi):
    """Pares (a, b) de cajas (lo, hi: n x 2) que se tocan, por trozos. Una rejilla reparte las cajas en celdas y solo
    se miran las que comparten celda; cada par sale una vez (en la celda de su esquina común más baja)."""
    n = len(lo)
    if n < 2:
        return
    origin = lo.min(axis=0)
    span = np.maximum(hi.max(axis=0) - origin, 1e-12)
    g = int(np.clip(np.sqrt(n / 3.0), 1, 2048))
    while True:
        size = span / g
        c0 = np.minimum(((lo - origin) / size).astype(np.int64), g - 1)
        c1 = np.minimum(((hi - origin) / size).astype(np.int64), g - 1)
        width = c1 - c0 + 1
        counts = width[:, 0] * width[:, 1]
        if int(counts.sum()) <= MAX_ENTRIES or g == 1:
            break
        g = max(g // 2, 1)
    box = np.repeat(np.arange(n), counts)
    local = np.arange(len(box)) - np.repeat(np.cumsum(counts) - counts, counts)
    cell = (c0[box, 1] + local // width[box, 0]) * g + c0[box, 0] + local % width[box, 0]
    order = np.argsort(cell, kind="stable")
    box, cell = box[order], cell[order]
    total = len(box)
    first = np.concatenate([[True], cell[1:] != cell[:-1]])
    ends = np.concatenate([np.flatnonzero(first)[1:], [total]])
    group = np.cumsum(first) - 1
    partners = ends[group] - np.arange(total) - 1                      # entradas que quedan detrás en su celda
    cumulative = np.cumsum(partners)
    begin = 0
    while begin < total:
        done = int(cumulative[begin - 1]) if begin else 0
        stop = max(int(np.searchsorted(cumulative, done + PAIR_CHUNK, side="right")), begin + 1)
        many = partners[begin:stop]
        count = int(many.sum())
        if count:
            e = np.repeat(np.arange(begin, stop), many)
            f = e + 1 + np.arange(count) - np.repeat(np.cumsum(many) - many, many)
            a, b = box[e], box[f]
            corner = np.minimum(((np.maximum(lo[a], lo[b]) - origin) / size).astype(np.int64), g - 1)
            keep = (cell[e] == corner[:, 1] * g + corner[:, 0]) & (a != b)
            yield a[keep], b[keep]
        begin = stop


# ------------------------------------------------------------------ solapes

def _uv_triangles(uv, counts, ids):
    """(triángulos T x 3 x 2, cara de cada uno) de las caras con UVs. Los quads se cortan por la diagonal que queda
    dentro de la cara (importa si el quad es cóncavo); el resto, en abanico."""
    pieces = np.maximum(counts - 2, 0)
    face = np.repeat(np.arange(len(counts)), pieces)
    k = np.arange(len(face)) - np.repeat(np.cumsum(pieces) - pieces, pieces)
    c0 = (np.cumsum(counts) - counts)[face]
    corner = np.stack([c0, c0 + k + 1, c0 + k + 2], axis=1)
    tris = uv[ids[corner]]
    area = 0.5 * ((tris[:, 1, 0] - tris[:, 0, 0]) * (tris[:, 2, 1] - tris[:, 0, 1])
                  - (tris[:, 2, 0] - tris[:, 0, 0]) * (tris[:, 1, 1] - tris[:, 0, 1]))
    quad = counts[face] == 4
    if quad.any():
        total = np.bincount(face, weights=area, minlength=len(counts))
        wrong = np.unique(face[quad & (area * np.sign(total[face]) < 0)])
        swap = np.isin(face, wrong)
        if swap.any():
            other = np.where((k[swap] == 0)[:, None], np.stack([c0[swap] + 1, c0[swap] + 2, c0[swap] + 3], axis=1),
                             np.stack([c0[swap] + 1, c0[swap] + 3, c0[swap]], axis=1))
            corner[swap] = other
            tris = uv[ids[corner]]
    return tris, face


def _triangles_overlap(a, b, tol):
    """a, b: m x 3 x 2. True donde los dos triángulos se pisan en más de `tol` en todos los ejes (eje separador)."""
    edges = np.concatenate([np.roll(a, -1, axis=1) - a, np.roll(b, -1, axis=1) - b], axis=1)         # m x 6 x 2
    axes = np.stack([-edges[..., 1], edges[..., 0]], axis=-1)
    length = np.linalg.norm(axes, axis=-1)
    axes = axes / np.maximum(length, 1e-30)[..., None]
    pa = np.einsum("mkc,mjc->mkj", axes, a)
    pb = np.einsum("mkc,mjc->mkj", axes, b)
    depth = np.minimum(pa.max(axis=2), pb.max(axis=2)) - np.maximum(pa.min(axis=2), pb.min(axis=2))
    depth = np.where(length > 1e-12, depth, np.inf)
    return (depth > tol).all(axis=1)


def overlapping_faces(uv_points, uv_faces, tol=OVERLAP_TOL):
    """Máscara de las caras cuyo interior en UV entra más de `tol` en el de otra cara (caras vecinas que solo comparten
    arista o esquina no cuentan)."""
    uv = np.asarray(uv_points, dtype=np.float64).reshape(-1, 2)
    counts, ids = _flatten(uv_faces)
    hit = np.zeros(len(counts), dtype=bool)
    if not (counts >= 3).any():
        return hit
    tris, face = _uv_triangles(uv, counts, ids)
    area = np.abs(0.5 * ((tris[:, 1, 0] - tris[:, 0, 0]) * (tris[:, 2, 1] - tris[:, 0, 1])
                         - (tris[:, 2, 0] - tris[:, 0, 0]) * (tris[:, 1, 1] - tris[:, 0, 1])))
    solid = area > 1e-14
    tris, face = tris[solid], face[solid]
    lo, hi = tris.min(axis=1), tris.max(axis=1)
    for a, b in _box_pairs(lo, hi):
        near = (face[a] != face[b]) & ((np.minimum(hi[a], hi[b]) - np.maximum(lo[a], lo[b])) > tol).all(axis=1)
        a, b = a[near], b[near]
        if len(a):
            over = _triangles_overlap(tris[a], tris[b], tol)
            hit[face[a[over]]] = True
            hit[face[b[over]]] = True
    return hit


# ------------------------------------------------------------------ margen entre islas

def _point_segment(p, a, b):
    """Distancia de los puntos p a los segmentos a-b (todos m x 2)."""
    ab = b - a
    t = np.clip(((p - a) * ab).sum(axis=1) / np.maximum((ab * ab).sum(axis=1), 1e-30), 0.0, 1.0)
    return np.linalg.norm(p - (a + t[:, None] * ab), axis=1)


def _segment_distance(p1, p2, q1, q2):
    """Distancia entre los segmentos p1-p2 y q1-q2 (m x 2 cada extremo); 0 si se cruzan o se tocan."""
    def side(a, b, c):
        return (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    crossing = (side(p1, p2, q1) * side(p1, p2, q2) < 0) & (side(q1, q2, p1) * side(q1, q2, p2) < 0)
    nearest = np.minimum(np.minimum(_point_segment(p1, q1, q2), _point_segment(p2, q1, q2)),
                         np.minimum(_point_segment(q1, p1, p2), _point_segment(q2, p1, p2)))
    return np.where(crossing, 0.0, nearest)


def island_margin(uv_points, uv_faces, island=None, count=None, cap=MARGIN_CAP):
    """Distancia mínima en UV entre las orillas de dos islas distintas. None con una sola isla; `cap` si todas están
    más lejos que eso (no se busca más allá)."""
    uv = np.asarray(uv_points, dtype=np.float64).reshape(-1, 2)
    if island is None:
        island, count = islands(uv_faces)
    if count is None or count < 2:
        return None
    counts, ids = _flatten(uv_faces)
    face, following = _corners(counts)
    use = counts[face] >= 3
    a, b, owner = ids[use], ids[following][use], island[face[use]]
    # orillas: aristas UV que solo tiene una cara
    key = np.minimum(a, b) * (int(ids.max()) + 1) + np.maximum(a, b)
    _, inverse, times = np.unique(key, return_inverse=True, return_counts=True)
    edge = times[inverse] == 1
    a, b, owner = a[edge], b[edge], owner[edge]
    p, q = uv[a], uv[b]
    pad = 0.5 * cap
    lo, hi = np.minimum(p, q) - pad, np.maximum(p, q) + pad
    best = cap
    for i, j in _box_pairs(lo, hi):
        other = owner[i] != owner[j]
        i, j = i[other], j[other]
        if len(i):
            best = min(best, float(_segment_distance(p[i], q[i], p[j], q[j]).min()))
    return best


# ------------------------------------------------------------------ continuidad en los cortes

def seam_continuity(points, faces, uv_points, uv_faces):
    """Cómo casan las dos orillas de cada corte: giro y escala relativos, ponderado por longitud 3D.

    Misma definición que `seam_continuity` de tests/bench.py. Un corte es una arista de dos caras cuyas UV no son las
    mismas. Devuelve el % de longitud que son cortes y el % de ellos con giro < 15 grados, con escala dentro del 10 %
    y con las dos cosas (casan). Sin cortes, todo 100.
    """
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    uv = np.asarray(uv_points, dtype=np.float64).reshape(-1, 2)
    counts, ids = _flatten(faces)
    uv_counts, uv_ids = _flatten(uv_faces)
    whole = (counts >= 3) & (counts == uv_counts)
    ids, uv_ids, counts = ids[np.repeat(whole, counts)], uv_ids[np.repeat(whole, uv_counts)], counts[whole]
    empty = {"seam_percent": 0.0, "racord_rot_lt_15": 100.0, "racord_scale_lt_10": 100.0, "racord_percent": 100.0}
    if not len(ids):
        return empty
    _, following = _corners(counts)
    a, b, ua, ub = ids, ids[following], uv_ids, uv_ids[following]
    swap = a > b
    a, b, ua, ub = np.where(swap, b, a), np.where(swap, a, b), np.where(swap, ub, ua), np.where(swap, ua, ub)
    _, inverse, times = np.unique(a * (len(points) + 1) + b, return_inverse=True, return_counts=True)
    length = np.linalg.norm(points[b] - points[a], axis=1)
    edge_length = np.zeros(len(times))
    edge_length[inverse] = length
    total = float(edge_length.sum())
    # las aristas con exactamente dos caras: sus dos esquinas, una detrás de otra
    order = np.argsort(inverse, kind="stable")
    first = np.cumsum(times) - times
    pair = np.flatnonzero(times == 2)
    c1, c2 = order[first[pair]], order[first[pair] + 1]
    cut = (ua[c1] != ua[c2]) | (ub[c1] != ub[c2])
    c1, c2, size = c1[cut], c2[cut], edge_length[pair[cut]]
    if not len(c1) or total <= 0:
        return dict(empty, seam_percent=0.0)
    s1, s2 = uv[ub[c1]] - uv[ua[c1]], uv[ub[c2]] - uv[ua[c2]]
    n1, n2 = np.linalg.norm(s1, axis=1), np.linalg.norm(s2, axis=1)
    live = (n1 >= 1e-9) & (n2 >= 1e-9)
    result = dict(empty, seam_percent=100.0 * float(size.sum()) / total)
    if not live.any():
        return result
    s1, s2, n1, n2, size = s1[live], s2[live], n1[live], n2[live], size[live]
    angle = np.abs(np.degrees(np.arctan2(s1[:, 0] * s2[:, 1] - s1[:, 1] * s2[:, 0], (s1 * s2).sum(axis=1))))
    scale = np.abs(np.log2(n1 / n2))
    weight = size / size.sum()
    turn, same = angle < SEAM_ANGLE, scale < SEAM_SCALE
    result.update(racord_rot_lt_15=100.0 * float(weight[turn].sum()), racord_scale_lt_10=100.0 * float(weight[same].sum()),
                  racord_percent=100.0 * float(weight[turn & same].sum()))
    return result


# ------------------------------------------------------------------ informe y nota

def shape_stats(points, faces, uv_points, uv_faces):
    """Si los cuadrados siguen siendo cuadrados (la prueba del tablero): el estiramiento mide área, y un rectángulo con la
    misma área pasaría por bueno. Por triángulo, razón entre el mayor y el menor estiramiento de la transformación de 3D a
    UV. Devuelve (media ponderada por área, % del área con más de 1,5x)."""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    uv = np.asarray(uv_points, dtype=np.float64).reshape(-1, 2)
    a3, b3, c3, a2, b2, c2 = [], [], [], [], [], []
    for verts, corners in zip(faces, uv_faces):
        if len(corners) != len(verts):
            continue
        for k in range(1, len(verts) - 1):
            a3.append(verts[0]); b3.append(verts[k]); c3.append(verts[k + 1])
            a2.append(corners[0]); b2.append(corners[k]); c2.append(corners[k + 1])
    if not a3:
        return 1.0, 0.0
    e1, e2 = points[b3] - points[a3], points[c3] - points[a3]
    f1, f2 = uv[b2] - uv[a2], uv[c2] - uv[a2]
    len1 = np.maximum(np.linalg.norm(e1, axis=1), 1e-30)
    x = e1 / len1[:, None]
    ex2 = (e2 * x).sum(axis=1)
    ey2 = np.linalg.norm(e2 - ex2[:, None] * x, axis=1)
    area = 0.5 * len1 * ey2
    ok = area > 1e-30
    # J = [f1 f2] [[len1, ex2], [0, ey2]]^-1
    j1 = f1[ok] / len1[ok][:, None]
    j2 = (f2[ok] - j1 * ex2[ok][:, None]) / ey2[ok][:, None]
    e, f, g = (j1 * j1).sum(axis=1), (j1 * j2).sum(axis=1), (j2 * j2).sum(axis=1)
    root = np.sqrt(np.maximum((e - g) ** 2 + 4.0 * f * f, 0.0))
    big, small = np.sqrt((e + g + root) / 2.0), np.sqrt(np.maximum((e + g - root) / 2.0, 0.0))
    ratio = np.minimum(big / np.maximum(small, 1e-12), STRETCH_CAP)
    weight = area[ok] / max(float(area[ok].sum()), 1e-30)
    return float((ratio * weight).sum()), float(100.0 * weight[ratio > STRETCH_BAD].sum())


def report(points, faces, uv_points, uv_faces):
    """Todas las cifras de calidad de unas UVs.

    - faces / mapped_faces: caras y caras con UVs.
    - overlapping_faces / flipped_faces: caras que se pisan en UV y caras en espejo.
    - stretch_mean / over_1_5_percent: estiramiento (media ponderada y % de caras con más de 1,5x).
    - used_percent: textura aprovechada (suma de áreas UV; con solapes se pasa de lo que cubre de verdad).
    - islands / texel_variation_percent / margin_min: islas, variación de densidad entre ellas y margen mínimo
      entre sus orillas en UV (None con una sola isla; no pasa de MARGIN_CAP).
    - seam_percent y racord_*: longitud de cortes y cuánto casan sus dos orillas (ver seam_continuity).
    """
    faces_count = len(faces)
    world = face_areas(points, faces)
    signed, has = uv_face_areas(uv_points, uv_faces)
    flat = np.abs(signed)
    island, count = islands(uv_faces)
    rep = {"faces": faces_count, "mapped_faces": int(has.sum()),
           "overlapping_faces": int(overlapping_faces(uv_points, uv_faces).sum()), "flipped_faces": int((signed < 0).sum()),
           "used_percent": 100.0 * float(flat.sum()), "islands": count,
           "texel_variation_percent": texel_variation(world, flat, island, count),
           "margin_min": island_margin(uv_points, uv_faces, island, count)}
    rep.update(stretch_stats(world, flat))
    rep["shape_mean"], rep["shape_bad_percent"] = shape_stats(points, faces, uv_points, uv_faces)
    rep.update(seam_continuity(points, faces, uv_points, uv_faces))
    return rep


# umbral (revisar, malo) de cada cifra, PROPIOS y provisionales (la nota usa >= en los que suben con lo feo y <= en
# LOWER_IS_WORSE). None = esa cifra no llega nunca a "malo" por sí sola. Se eligieron dejando por encima de "revisar" el
# peor resultado de la línea base del banco (tests/baseline.json, 10 resultados) con holgura, y a las que el banco no
# medía se les puso margen sobre lo que da el plugin (ver docs/notas-tecnicas.md).
LIMITS = {
    "overlapping_percent": (1e-9, 0.5),    # el plugin no deja ninguna: cualquier cara solapada avisa; desde 0,5 %, mal
    "flipped_percent": (1e-9, 0.5),        # caras en espejo: igual (darían normal maps invertidos)
    "unmapped_percent": (1e-9, 1.0),       # caras sin UVs: un hueco en la textura
    "stretch_mean": (1.30, 1.60),          # línea base: 1,07 a 1,19
    "over_1_5_percent": (12.0, 30.0),      # línea base: 1,2 a 9,7 %
    "texel_variation_percent": (25.0, 60.0),  # el plugin da 0 a 0,14 %
    "used_percent": (40.0, 20.0),          # línea base: 49 a 65 %
    "racord_percent": (0.1, None),         # giro y escala a la vez: 0,24 a 17 % medido; en piezas duras casi no hay
    "margin_min": (0.001, None),           # unidades UV; el plugin deja 0,0039 a 0,0061 (relleno de 16 px a 4096)
}
LOWER_IS_WORSE = ("used_percent", "racord_percent", "margin_min")


def grade(rep):
    """(nota, razones): 0 bien, 1 revisar, 2 malo. Un solo defecto malo basta para la nota 2."""
    if not rep["mapped_faces"]:
        return 2, ["la malla no tiene UVs"]
    faces = max(rep["faces"], 1)
    values = {
        "overlapping_percent": 100.0 * rep["overlapping_faces"] / faces,
        "flipped_percent": 100.0 * rep["flipped_faces"] / faces,
        "unmapped_percent": 100.0 * (rep["faces"] - rep["mapped_faces"]) / faces,
        "stretch_mean": rep["stretch_mean"],
        "over_1_5_percent": rep["over_1_5_percent"],
        "texel_variation_percent": rep["texel_variation_percent"],
        "used_percent": rep["used_percent"],
        "racord_percent": rep["racord_percent"] if rep["seam_percent"] > 0 else None,
        "margin_min": rep["margin_min"],
    }
    names = {"overlapping_percent": "caras solapadas en UV ({:.2f}%)", "flipped_percent": "caras en espejo ({:.2f}%)",
             "unmapped_percent": "caras sin UVs ({:.2f}%)", "stretch_mean": "estiramiento medio {:.2f}x",
             "over_1_5_percent": "caras con más de 1,5x de estiramiento ({:.1f}%)",
             "texel_variation_percent": "densidad de texel distinta entre islas ({:.0f}%)",
             "used_percent": "textura aprovechada solo {:.0f}%",
             "racord_percent": "cortes que no casan: solo {:.1f}% de la costura continúa",
             "margin_min": "islas casi pegadas (margen {:.4f} en UV)"}
    level, reasons = 0, []
    for key, value in values.items():
        if value is None:
            continue
        review, bad = LIMITS[key]
        worse = (lambda v, limit: v <= limit) if key in LOWER_IS_WORSE else (lambda v, limit: v >= limit)
        if bad is not None and worse(value, bad):
            level = 2
        elif worse(value, review):
            level = max(level, 1)
        else:
            continue
        reasons.append(names[key].format(value))
    return level, reasons
