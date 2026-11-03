# -*- coding: utf-8 -*-
"""Limpieza de polos y relajación de la retopo (fase 1, punto 6). Python + numpy, sin Maya.

Los motores automáticos dejan parejas de polos (vértices de 3 y 5 aristas) en zonas suaves, donde
el campo de direcciones duda. Aquí se quitan con las mismas operaciones que haría un artista a mano
(Tarini et al., "Practical quad mesh simplification", 2010):
  - colapso diagonal: se cierra un quad por su diagonal y sus dos esquinas opuestas se funden
  - giro de arista: la arista que comparten dos quads se gira dentro del hexágono que forman
Una operación solo se acepta si baja la energía de polos, suma de (aristas - 4)^2, y no deja quads
doblados. Los vértices de borde, los que tocan caras que no son quads y los no-manifold no se tocan.

Después, relax() iguala el tamaño de los quads deslizando los vértices por la superficie.
"""

import numpy as np

from . import quality


def _energy(valence):
    return (valence - 4) ** 2


# polígono más grande que se junta o se vuelve a partir al mover un triángulo
MAX_POLYGON = 8
# caras que puede recorrer un triángulo hasta encontrar su pentágono
WALK_STEPS = 8


def _cycle(face, start):
    i = face.index(start)
    return face[i:] + face[:i]


def _normal(points, face):
    """Normal de Newell (sirve también para quads no planos). Con floats de Python: con 3 números por
    vértice, numpy tarda más en preparar cada operación que en hacerla."""
    p = points[face].tolist()
    n = len(p)
    x = y = z = 0.0
    for i in range(n):
        a, b = p[i], p[(i + 1) % n]
        x += a[1] * b[2] - a[2] * b[1]
        y += a[2] * b[0] - a[0] * b[2]
        z += a[0] * b[1] - a[1] * b[0]
    return np.array([x, y, z])


def _quad_ok(points, quad, reference, min_cos=0.1):
    """La cara (quad, triángulo o n-gon) es convexa y mira hacia `reference` (ni doblada ni en forma
    de flecha)."""
    p = points[quad].tolist()
    n = len(quad)
    rx, ry, rz = (float(c) for c in reference)
    length = (rx * rx + ry * ry + rz * rz) ** 0.5
    if length == 0:
        return False
    rx, ry, rz = rx / length, ry / length, rz / length
    for i in range(n):
        a, b, c = p[i], p[(i + 1) % n], p[i - 1]
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        size = (cx * cx + cy * cy + cz * cz) ** 0.5
        if size == 0 or cx * rx + cy * ry + cz * rz < min_cos * size:
            return False
    return True


def _topology(faces, alive, freeze_nonquad=True):
    vertex_faces, edge_faces, neighbors = {}, {}, {}
    for fi, face in enumerate(faces):
        if not alive[fi]:
            continue
        for k, v in enumerate(face):
            w = face[(k + 1) % len(face)]
            vertex_faces.setdefault(v, []).append(fi)
            edge_faces.setdefault((min(v, w), max(v, w)), []).append(fi)
            neighbors.setdefault(v, set()).add(w)
            neighbors.setdefault(w, set()).add(v)
    frozen = set()
    for (a, b), owners in edge_faces.items():
        if len(owners) != 2:
            frozen.update((a, b))
    if freeze_nonquad:
        for fi, face in enumerate(faces):
            if alive[fi] and len(face) != 4:
                frozen.update(face)
    # vértice interior con más de un abanico de caras ("pajarita")
    for v, owners in vertex_faces.items():
        if len(neighbors[v]) != len(owners):
            frozen.add(v)
    return vertex_faces, edge_faces, neighbors, frozen


def pole_count(faces):
    """Vértices interiores que no tienen 4 aristas."""
    vertex_faces, edge_faces, _, _ = _topology(faces, [True] * len(faces))
    border = set()
    for (a, b), owners in edge_faces.items():
        if len(owners) == 1:
            border.update((a, b))
    return sum(1 for v, owners in vertex_faces.items() if v not in border and len(owners) != 4)


class _Mesh:
    """Malla editable con deshacer, para buscar secuencias de operaciones."""

    def __init__(self, points, faces, mixed=False):
        """mixed=True: admite triángulos y n-gons (no congela sus vértices) y los trata como parte
        de la energía: una cara de n lados cuesta (n - 4)^2, igual que un vértice de n aristas."""
        self.points = np.array(points, dtype=float)
        self.faces = [list(face) for face in faces]
        self.mixed = bool(mixed)
        self.vf = {}
        for fi, face in enumerate(self.faces):
            for v in face:
                self.vf.setdefault(v, set()).add(fi)
        # vecinos de cada vértice ya calculados (neighbors() es lo que más se pide); set_face() borra los que cambian
        self._near = {}
        _, _, _, self.frozen = _topology(self.faces, [True] * len(self.faces), freeze_nonquad=not self.mixed)

    def valence(self, v):
        return len(self.vf[v])

    def irregular(self, v):
        """Vértice que cuenta como polo: no tiene 4 aristas o toca una cara que no es un quad (modo mixto)."""
        if len(self.vf[v]) != 4:
            return True
        return self.mixed and any(len(self.faces[f]) != 4 for f in self.vf[v])

    def neighbors(self, v):
        """Vértices unidos a v por una arista. El set es el de la caché: quien lo recibe no lo modifica."""
        result = self._near.get(v)
        if result is None:
            result = set()
            for fi in self.vf[v]:
                face = self.faces[fi]
                k = face.index(v)
                result.add(face[k - 1])
                result.add(face[(k + 1) % len(face)])
            self._near[v] = result
        return result

    def edge_faces(self, u, w):
        result = []
        for fi in self.vf[u] & self.vf[w]:
            face = self.faces[fi]
            k = face.index(u)
            if w in (face[k - 1], face[(k + 1) % len(face)]):
                result.append(fi)
        return result

    def set_face(self, fi, face):
        old = self.faces[fi]
        near = self._near
        if old is not None:
            for v in old:
                self.vf[v].discard(fi)
                near.pop(v, None)
        self.faces[fi] = face
        if face is not None:
            for v in face:
                self.vf.setdefault(v, set()).add(fi)
                near.pop(v, None)
        return old

    # --- operaciones: cada una devuelve (delta de energía, vértices cambiados, deshacer) o None

    def collapse(self, fi, a, c, check_only=False):
        quad = self.faces[fi]
        k = quad.index(a)
        if quad[(k + 2) % 4] != c:
            return None
        b, d = quad[(k + 1) % 4], quad[(k + 3) % 4]
        if self.frozen.intersection((a, b, c, d)):
            return None
        va, vb, vc, vd = (self.valence(x) for x in (a, b, c, d))
        if vb - 1 < 3 or vd - 1 < 3 or va + vc - 2 > 6:
            return None
        if self.neighbors(a) & self.neighbors(c) != {b, d} or self.vf[a] & self.vf[c] != {fi}:
            return None
        delta = (_energy(va + vc - 2) + _energy(vb - 1) + _energy(vd - 1)
                 - _energy(va) - _energy(vb) - _energy(vc) - _energy(vd))
        if check_only:
            return delta
        affected = [f for f in (self.vf[a] | self.vf[c]) if f != fi]
        references = {f: _normal(self.points, self.faces[f]) for f in affected}
        old_point = self.points[a].copy()
        self.points[a] = (self.points[a] + self.points[c]) * 0.5
        updated = {f: [a if v == c else v for v in self.faces[f]] for f in affected}
        if not all(_quad_ok(self.points, updated[f], references[f]) for f in affected):
            self.points[a] = old_point
            return None
        old_faces = {fi: self.set_face(fi, None)}
        for f, face in updated.items():
            old_faces[f] = self.set_face(f, face)

        def undo():
            for f in old_faces:
                self.set_face(f, None)
            for f, face in old_faces.items():
                self.set_face(f, face)
            self.points[a] = old_point
        return delta, (a, b, d), undo

    def rotations(self, u, w):
        """Giros posibles de la arista u-w: lista de (delta, f1, f2, cara1, cara2, extremos nuevos)."""
        owners = self.edge_faces(u, w)
        if len(owners) != 2:
            return []
        f1, f2 = owners
        if len(self.faces[f1]) != 4 or len(self.faces[f2]) != 4:
            return []
        if _cycle(self.faces[f1], u)[1] != w:
            f1, f2 = f2, f1
        q1, q2 = _cycle(self.faces[f1], u), _cycle(self.faces[f2], w)
        if q1[1] != w or q2[1] != u:
            return []
        x, y, p, q = q1[2], q1[3], q2[2], q2[3]
        hexagon = [w, x, y, u, p, q]
        if len(set(hexagon)) != 6 or self.frozen.intersection(hexagon):
            return []
        vu, vw = self.valence(u), self.valence(w)
        if vu - 1 < 3 or vw - 1 < 3:
            return []
        result = []
        for e1, e2, new1, new2 in ((x, p, [x, y, u, p], [p, q, w, x]),
                                   (y, q, [y, u, p, q], [q, w, x, y])):
            v1, v2 = self.valence(e1), self.valence(e2)
            if v1 + 1 > 6 or v2 + 1 > 6 or e2 in self.neighbors(e1):
                continue
            delta = (_energy(vu - 1) + _energy(vw - 1) + _energy(v1 + 1) + _energy(v2 + 1)
                     - _energy(vu) - _energy(vw) - _energy(v1) - _energy(v2))
            result.append((delta, f1, f2, new1, new2, (u, w, e1, e2)))
        return result

    def _polygon(self, u, w):
        """(f1, f2, polígono) de las dos caras que comparten la arista u-w, o None."""
        owners = self.edge_faces(u, w)
        if len(owners) != 2:
            return None
        f1, f2 = owners
        if _cycle(self.faces[f1], u)[1] != w:
            f1, f2 = f2, f1
        q1, q2 = _cycle(self.faces[f1], u), _cycle(self.faces[f2], w)
        if q1[1] != w or q2[1] != u:
            return None  # orientación incoherente entre las dos caras
        poly = [w] + q1[2:] + [u] + q2[2:]
        if len(set(poly)) != len(poly) or self.frozen.intersection(poly) or len(poly) > MAX_POLYGON:
            return None
        return f1, f2, poly

    def _valence_delta(self, changes):
        """Energía de vértices que cambia si cada vértice de `changes` varía su valencia en ese número."""
        total = 0
        for v, d in changes.items():
            if d == 0:
                continue
            new = self.valence(v) + d
            if new < 3 or new > 6:
                return None
            total += _energy(new) - _energy(self.valence(v))
        return total

    def flips(self, u, w):
        """Giros generales de la arista u-w: se unen las dos caras y se vuelven a partir por otra
        diagonal (triángulo+quad -> triángulo+quad, triángulo+pentágono -> dos quads...).
        Lista de (delta, f1, f2, cara1, cara2, vértices cambiados)."""
        found = self._polygon(u, w)
        if found is None:
            return []
        f1, f2, poly = found
        n, n1, n2 = len(poly), len(self.faces[f1]), len(self.faces[f2])
        result = []
        for i in range(n):
            for j in range(i + 2, n):
                if i == 0 and j == n - 1:
                    continue
                d1, d2 = poly[i], poly[j]
                if {d1, d2} == {u, w} or d2 in self.neighbors(d1):
                    continue
                new1, new2 = poly[i:j + 1], poly[j:] + poly[:i + 1]
                changes = {u: -1, w: -1}
                changes[d1] = changes.get(d1, 0) + 1
                changes[d2] = changes.get(d2, 0) + 1
                delta = self._valence_delta(changes)
                if delta is None:
                    continue
                delta += _energy(len(new1)) + _energy(len(new2)) - _energy(n1) - _energy(n2)
                result.append((delta, f1, f2, new1, new2, (u, w, d1, d2)))
        return result

    def dissolves(self, u, w):
        """Quitar la arista u-w y juntar sus dos caras en una (dos triángulos -> un quad)."""
        found = self._polygon(u, w)
        if found is None:
            return []
        f1, f2, poly = found
        if len(poly) < 4:
            return []
        delta = self._valence_delta({u: -1, w: -1})
        if delta is None:
            return []
        delta += _energy(len(poly)) - _energy(len(self.faces[f1])) - _energy(len(self.faces[f2]))
        return [(delta, f1, f2, poly, None, (u, w))]

    def reshape(self, option):
        """Aplica un giro (dos caras nuevas) o una unión (una cara) si la geometría queda bien."""
        delta, f1, f2, new1, new2, changed = option
        reference = _normal(self.points, self.faces[f1]) + _normal(self.points, self.faces[f2])
        if not _quad_ok(self.points, new1, reference):
            return None
        if new2 is not None and not _quad_ok(self.points, new2, reference):
            return None
        old1 = self.set_face(f1, new1)
        old2 = self.set_face(f2, new2)

        def undo():
            self.set_face(f1, old1)
            self.set_face(f2, old2)
        return delta, changed, undo

    def rotate(self, option):
        delta, f1, f2, new1, new2, changed = option
        reference = _normal(self.points, self.faces[f1]) + _normal(self.points, self.faces[f2])
        if not (_quad_ok(self.points, new1, reference) and _quad_ok(self.points, new2, reference)):
            return None
        old1, old2 = self.set_face(f1, new1), self.set_face(f2, new2)

        def undo():
            self.set_face(f1, old1)
            self.set_face(f2, old2)
        return delta, changed, undo

    def moves(self, v, allow_neutral):
        """Operaciones alrededor del vértice v con delta <= 0 (o < 0), mejores primero."""
        if v in self.frozen or v not in self.vf or not self.irregular(v):
            return []
        if self.mixed:
            return self.mixed_moves(v, allow_neutral)
        found = []
        for fi in list(self.vf[v]):
            quad = self.faces[fi]
            if len(quad) != 4:
                continue
            for k in (0, 1):
                a, c = quad[k], quad[k + 2]
                delta = self.collapse(fi, a, c, check_only=True)
                if delta is not None and (delta < 0 or (allow_neutral and delta == 0)):
                    found.append((delta, "collapse", (fi, a, c)))
        ring = set(self.neighbors(v))
        for w in ring:
            for option in self.rotations(v, w):
                if option[0] < 0 or (allow_neutral and option[0] == 0):
                    found.append((option[0], "rotate", option))
        # también las aristas del anillo: mueven el polo sin tocarlo
        for w in ring:
            for z in self.neighbors(w):
                if z != v and z not in ring:
                    for option in self.rotations(w, z):
                        if option[0] < 0 or (allow_neutral and option[0] == 0):
                            found.append((option[0], "rotate", option))
        found.sort(key=lambda item: item[0])
        return found

    def mixed_moves(self, v, allow_neutral):
        """Colapsos de quads, giros generales y uniones de caras alrededor del vértice v."""
        found = []
        for fi in list(self.vf[v]):
            quad = self.faces[fi]
            if len(quad) != 4:
                continue
            for k in (0, 1):
                a, c = quad[k], quad[k + 2]
                delta = self.collapse(fi, a, c, check_only=True)
                if delta is not None and (delta < 0 or (allow_neutral and delta == 0)):
                    found.append((delta, "collapse", (fi, a, c)))
        ring = set(self.neighbors(v))
        edges = [(v, w) for w in ring]
        for w in ring:
            edges += [(w, z) for z in self.neighbors(w) if z != v and z not in ring]
        for u, w in edges:
            for option in self.flips(u, w) + self.dissolves(u, w):
                if option[0] < 0 or (allow_neutral and option[0] == 0):
                    found.append((option[0], "reshape", option))
        found.sort(key=lambda item: item[0])
        return found

    # --- anular triángulos con pentágonos

    def _dual_path(self, start, max_steps):
        """Camino de caras (quads) desde el triángulo `start` hasta el pentágono más cercano, o None."""
        previous = {start: None}
        frontier = [start]
        for _ in range(max_steps):
            following = []
            for fi in frontier:
                face = self.faces[fi]
                for k, a in enumerate(face):
                    b = face[(k + 1) % len(face)]
                    for other in self.edge_faces(a, b):
                        if other == fi or other in previous:
                            continue
                        if self.frozen.intersection(self.faces[other]):
                            continue
                        previous[other] = fi
                        if len(self.faces[other]) == 5:
                            path = [other]
                            while previous[path[-1]] is not None:
                                path.append(previous[path[-1]])
                            return path[::-1]
                        if len(self.faces[other]) == 4:
                            following.append(other)
            frontier = following
            if not frontier:
                break
        return None

    def _shared_edge(self, f1, f2):
        face = self.faces[f1]
        for k, a in enumerate(face):
            b = face[(k + 1) % len(face)]
            if f2 in self.edge_faces(a, b):
                return a, b
        return None

    def walk_triangle(self, triangle, max_steps):
        """Corre el triángulo hasta el pentágono más cercano con giros tri+quad y los convierte en
        dos quads. Se queda si la energía total no sube (un triángulo y un pentágono pasan a dos quads a cambio de, como mucho, dos polos); si no, lo deshace. Devuelve 1 o 0."""
        path = self._dual_path(triangle, max_steps)
        if path is None or len(path) < 2:
            return 0
        total, undos, current = 0, [], triangle
        for index in range(1, len(path)):
            target = path[index]
            edge = self._shared_edge(current, target)
            if edge is None:
                break
            last = index == len(path) - 1
            options = []
            for option in self.flips(*edge):
                delta, f1, f2, new1, new2, _ = option
                if last:
                    ok = len(new1) == 4 and len(new2) == 4
                else:
                    ok = {len(new1), len(new2)} == {3, 4}
                if ok:
                    options.append(option)
            options.sort(key=lambda option: option[0])
            applied = None
            for option in options:
                delta, f1, f2, new1, new2, _ = option
                if not last:
                    # el triángulo nuevo tiene que quedar pegado a la siguiente cara del camino
                    tri_id, tri = (f1, new1) if len(new1) == 3 else (f2, new2)
                    nxt = path[index + 1]
                    if not any(nxt in self.edge_faces(tri[k], tri[(k + 1) % 3]) or
                               len(set(tri) & set(self.faces[nxt])) >= 2 for k in range(3)):
                        continue
                result = self.reshape(option)
                if result is None:
                    continue
                applied = (result, f1 if len(new1) == 3 else f2)
                break
            if applied is None:
                break
            result, tri_id = applied
            total += result[0]
            undos.append(result[2])
            current = tri_id
            if last:
                if total <= 0:
                    return 1
                break
        for undo in reversed(undos):
            undo()
        return 0

    def annihilate(self, max_steps):
        """Intenta anular cada triángulo con el pentágono más cercano. Devuelve cuántos se anularon."""
        done = 0
        for fi in [f for f, face in enumerate(self.faces) if face is not None and len(face) == 3]:
            if self.faces[fi] is not None and len(self.faces[fi]) == 3:
                done += self.walk_triangle(fi, max_steps)
        return done

    def apply(self, move):
        _, kind, data = move
        if kind == "collapse":
            return self.collapse(*data)
        if kind == "reshape":
            return self.reshape(data)
        return self.rotate(data)

    def search(self, v, depth):
        """Busca una secuencia de hasta `depth` operaciones desde el polo v que baje la energía.

        Los pasos intermedios pueden ser neutros (desplazan el polo); solo se confirma la
        secuencia si al final la energía baja. Devuelve el nº de operaciones aplicadas.
        """
        for move in self.moves(v, allow_neutral=depth > 1):
            result = self.apply(move)
            if result is None:
                continue
            delta, changed, undo = result
            if delta < 0:
                return 1
            for moved in changed:
                if moved in self.vf and self.irregular(moved):
                    steps = self.search(moved, depth - 1)
                    if steps:
                        return steps + 1
            undo()
        return 0

    def compact(self):
        faces = [face for face in self.faces if face is not None]
        used = sorted({v for face in faces for v in face})
        remap = {v: i for i, v in enumerate(used)}
        return self.points[used], [[remap[v] for v in face] for face in faces]


def clean_poles(points, faces, depth=3, max_rounds=20, locked=None, mixed=False):
    """Quita parejas de polos. Devuelve (puntos, caras, nº de operaciones).

    Para cada polo se buscan secuencias de hasta `depth` operaciones (giros de arista y colapsos
    diagonales) que bajen la energía; los pasos intermedios pueden solo desplazar el polo para que
    se encuentre con su pareja. Las caras conservan su orientación. `locked`: vértices que no se
    tocan (los de las guías de flujo). mixed=True: la malla trae triángulos y n-gons (salida del modo
    quad-dominante de Instant Meshes): se mueven y se juntan con las mismas búsquedas.
    """
    mesh = _Mesh(points, faces, mixed=mixed)
    if locked is not None:
        mesh.frozen.update(int(v) for v in locked)
    operations = 0
    for sweep in range(2 if mixed else 1):
        for _ in range(max_rounds):
            poles = [v for v in mesh.vf if v not in mesh.frozen and mesh.vf[v] and mesh.irregular(v)]
            applied = 0
            for v in poles:
                if v in mesh.vf and mesh.vf[v] and mesh.irregular(v):
                    applied += mesh.search(v, depth)
            operations += applied
            if not applied:
                break
        if mixed and sweep == 0:
            # los triángulos que quedan se corren hasta un pentágono y se anulan; los giros dejan
            # polos sueltos que la segunda vuelta limpia
            operations += mesh.annihilate(WALK_STEPS)
    points, faces = mesh.compact()
    return points, faces, operations


def _corner_arrays(faces):
    """(nº de lados, vértice, cara y vértice siguiente de cada esquina) de una lista de caras."""
    counts = np.array([len(face) for face in faces], dtype=np.int64)
    flat = np.array([v for face in faces for v in face], dtype=np.int64)
    face_id = np.repeat(np.arange(len(faces)), counts)
    ends = np.cumsum(counts) - 1
    following = np.arange(len(flat)) + 1
    following[ends] = ends - counts + 1
    return counts, flat, face_id, flat[following]


def _held_vertices(points, faces, corners, feature_angle, fixed):
    """Vértices que el relajado no mueve: los de borde, los de aristas marcadas (las dos caras forman un
    ángulo mayor que feature_angle) y los de `fixed`."""
    counts, flat, face_id, nxt = corners
    edges = np.sort(np.stack([flat, nxt], axis=1), axis=1)
    unique, inverse, edge_count = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()
    held = np.zeros(len(points), dtype=bool)
    held[unique[edge_count == 1].ravel()] = True

    face_normals = np.zeros((len(faces), 3))
    np.add.at(face_normals, face_id, np.cross(points[flat], points[nxt]))
    lengths = np.linalg.norm(face_normals, axis=1)
    face_normals[lengths > 0] /= lengths[lengths > 0, None]
    order = np.argsort(inverse, kind="stable")
    sorted_edges = inverse[order]
    pair = (sorted_edges[:-1] == sorted_edges[1:])
    first, second = order[:-1][pair], order[1:][pair]
    cosines = np.einsum("ij,ij->i", face_normals[face_id[first]], face_normals[face_id[second]])
    sharp = unique[sorted_edges[:-1][pair][cosines < np.cos(np.radians(feature_angle))]]
    held[sharp.ravel()] = True
    if fixed is not None and len(fixed):
        held[np.asarray(list(fixed), dtype=np.int64)] = True
    return held


def relax(points, faces, iterations=10, project=None, strength=0.5, feature_angle=35.0, fixed=None):
    """Iguala los quads: cada vértice se acerca al centro de sus caras vecinas, solo en tangente.

    `project(puntos) -> puntos` vuelve a pegar los vértices a la superficie original en cada pasada.
    No se mueven los vértices de borde ni los de aristas marcadas (ángulo entre caras mayor que
    feature_angle): si no, las esquinas de las piezas duras se redondeaban (cubo: error del 29%).
    Tampoco los de `fixed` (los de las guías de flujo: si no, el loop se apartaba de la guía).
    """
    points = np.array(points, dtype=float)
    if iterations <= 0 or not faces:
        return points
    corners = _corner_arrays(faces)
    counts, flat, face_id, nxt = corners
    border = _held_vertices(points, faces, corners, feature_angle, fixed)

    valence = np.bincount(flat, minlength=len(points)).astype(float)
    movable = (valence > 0) & ~border

    for _ in range(iterations):
        centroids = np.zeros((len(faces), 3))
        np.add.at(centroids, face_id, points[flat])
        centroids /= counts[:, None]
        target = np.zeros_like(points)
        np.add.at(target, flat, centroids[face_id])
        target[movable] /= valence[movable, None]

        face_normals = np.zeros((len(faces), 3))
        np.add.at(face_normals, face_id, np.cross(points[flat], points[nxt]))
        normals = np.zeros_like(points)
        np.add.at(normals, flat, face_normals[face_id])
        lengths = np.linalg.norm(normals, axis=1)
        normals[lengths > 0] /= lengths[lengths > 0, None]

        move = target - points
        move -= normals * np.einsum("ij,ij->i", move, normals)[:, None]
        points[movable] += strength * move[movable]
        if project is not None:
            points[movable] = project(points[movable])
    return points


# ------------------------------------------------------------------ quads cuadrados

# cuánto se acerca el tamaño de cada quad al de sus vecinos en cada pasada (0 = conserva el suyo)
SQUARE_UNIFORM = 0.5
# un vértice que gira la normal de un quad más que esto o lo deja casi sin área se queda a medio paso
SQUARE_MIN_NORMAL_COS = 0.7
SQUARE_MIN_AREA = 0.15


def _diagonal_cross(points, quads):
    """Producto vectorial de las diagonales de cada quad: su normal con longitud 2 x área."""
    p = points[quads]
    return np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 1])


def square_targets(points, quads, sizes=None):
    """El cuadrado que mejor encaja con cada quad (F x 4 x 3, mismo orden de vértices): mismo centro, en su
    plano, lados iguales y ángulos de 90 grados. `sizes`: lado que se quiere (None = el que ya tiene).

    Un cuadrado de centro c y normal n son los puntos c + d, c + n x d, c - d, c - n x d; el mejor d por
    mínimos cuadrados es la media de los cuatro vértices girados hacia el primero.
    """
    p = points[quads]
    center = p.mean(axis=1)
    normal = np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 1])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-30)
    r = p - center[:, None, :]
    r -= normal[:, None, :] * np.einsum("fkj,fj->fk", r, normal)[:, :, None]
    d = 0.25 * (r[:, 0] - np.cross(normal, r[:, 1]) - r[:, 2] + np.cross(normal, r[:, 3]))
    if sizes is not None:
        d *= (np.asarray(sizes) / np.sqrt(2.0) / np.maximum(np.linalg.norm(d, axis=1), 1e-30))[:, None]
    turned = np.cross(normal, d)
    return center[:, None, :] + np.stack([d, turned, -d, -turned], axis=1)


def quad_pairs(quads):
    """Parejas de quads que comparten una arista (P x 2)."""
    edges = np.sort(np.stack([quads, np.roll(quads, -1, axis=1)], axis=2).reshape(-1, 2), axis=1)
    owner = np.repeat(np.arange(len(quads)), 4)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges, owner = edges[order], owner[order]
    same = (edges[:-1] == edges[1:]).all(axis=1)
    return np.stack([owner[:-1][same], owner[1:][same]], axis=1)


def fold_pairs(cross, pairs):
    """Qué parejas de quads vecinos quedan dobladas (sus normales forman más de 120 grados)."""
    normals = cross / np.maximum(np.linalg.norm(cross, axis=1, keepdims=True), 1e-30)
    return np.einsum("ij,ij->i", normals[pairs[:, 0]], normals[pairs[:, 1]]) < quality.FOLD_COS


def regularize(points, faces, iterations=20, project=None, strength=0.7, feature_angle=35.0, fixed=None,
               uniform=SQUARE_UNIFORM):
    """Deja los quads lo más cuadrados y parejos posible: cada quad propone el cuadrado que mejor encaja con
    él (y de tamaño más cercano al de sus vecinos), y cada vértice va a la media de lo que proponen sus
    quads (alternancia local / global). Con polos las propuestas chocan y sale un término medio.

    `project(puntos) -> puntos` pega los vértices al original en cada pasada. No se mueven los vértices de
    borde, de aristas marcadas (`feature_angle`) ni de `fixed`; los triángulos y n-gons no proponen nada.
    Un vértice que daría la vuelta a un quad o lo aplastaría se queda a medio paso.
    """
    points = np.array(points, dtype=float)
    index = np.array([i for i, face in enumerate(faces) if len(face) == 4], dtype=np.int64)
    if iterations <= 0 or not len(index):
        return points
    quads = np.array([faces[i] for i in index], dtype=np.int64)
    held = _held_vertices(points, faces, _corner_arrays(faces), feature_angle, fixed)
    count = np.bincount(quads.ravel(), minlength=len(points)).astype(float)
    movable = (count > 0) & ~held
    pairs = quad_pairs(quads)
    degree = np.ones(len(quads))
    np.add.at(degree, pairs[:, 0], 1.0)
    np.add.at(degree, pairs[:, 1], 1.0)

    for _ in range(iterations):
        cross = _diagonal_cross(points, quads)
        area = 0.5 * np.linalg.norm(cross, axis=1)
        size = np.log(np.maximum(np.sqrt(area), 1e-30))
        around = size.copy()
        np.add.at(around, pairs[:, 0], size[pairs[:, 1]])
        np.add.at(around, pairs[:, 1], size[pairs[:, 0]])
        wanted = np.exp((1.0 - uniform) * size + uniform * around / degree)
        proposal = square_targets(points, quads, wanted)
        target = np.zeros_like(points)
        np.add.at(target, quads.ravel(), proposal.reshape(-1, 3))
        step = np.zeros_like(points)
        step[movable] = target[movable] / count[movable, None] - points[movable]
        moved = points + strength * step
        if project is not None:
            moved[movable] = project(moved[movable])
        before = fold_pairs(cross, pairs)
        for _guard in range(3):
            now = _diagonal_cross(moved, quads)
            cos = np.einsum("ij,ij->i", cross, now) / np.maximum(np.linalg.norm(cross, axis=1) * np.linalg.norm(now, axis=1), 1e-30)
            wrong = (cos < SQUARE_MIN_NORMAL_COS) | (np.linalg.norm(now, axis=1) < SQUARE_MIN_AREA * np.linalg.norm(cross, axis=1))
            made = pairs[fold_pairs(now, pairs) & ~before]
            wrong[made.ravel()] = True
            if not wrong.any():
                break
            bad = np.unique(quads[wrong])
            moved[bad] = 0.5 * (moved[bad] + points[bad]) if _guard < 2 else points[bad]
        points = moved
    return points


def fix_deformed(points, faces, project=None, rounds=6, spread=2):
    """Relaja a cuadrados los vértices de los quads deformados y los de `spread` filas de quads alrededor,
    aunque estén en bordes del modelo o en aristas marcadas: la mayoría de los deformados (9 de cada 10 en
    el humano) tocan un vértice que el relajado normal deja quieto. Los demás no se mueven.
    Devuelve (puntos, antes, después)."""
    points = np.array(points, dtype=float)
    quads = np.array([face for face in faces if len(face) == 4], dtype=np.int64)
    if not len(quads):
        return points, 0, 0
    bad = quality.deformed_quads(points, quads)
    before = int(bad.sum())
    for _ in range(rounds):
        if not bad.any():
            break
        held = np.ones(len(points), dtype=bool)
        near = bad
        for _row in range(spread):
            touched = np.zeros(len(points), dtype=bool)
            touched[quads[near]] = True
            near = touched[quads].any(axis=1)
        held[np.unique(quads[near])] = False
        points = regularize(points, faces, iterations=5, project=project, feature_angle=180.0, fixed=np.nonzero(held)[0])
        bad = quality.deformed_quads(points, quads)
    return points, before, int(bad.sum())


# ------------------------------------------------------------------ chequeo de sanidad antes de UVs

# caras degeneradas: un ángulo interior menor que este (grados) o área menor que esta fracción de la
# mediana. En el águila, 6 de las 8 caras con UVs solapadas tenían un ángulo < 20° y las otras 2 un
# área de 0,001 veces la mediana (en toda la malla solo hay 34 y 20 de cada)
DEGENERATE_MIN_ANGLE = 20.0
DEGENERATE_MIN_AREA = 0.05


def degenerate_faces(points, faces, min_angle=DEGENERATE_MIN_ANGLE, min_area=DEGENERATE_MIN_AREA):
    """Índices de los quads (o n-gons) con un ángulo interior muy agudo o un área casi nula.

    Los triángulos no cuentan: con 3 lados un ángulo de 20° es normal y relajarlos no lo arregla.
    Caras del mismo número de lados a la vez (numpy).
    """
    points = np.asarray(points, dtype=float)
    areas = np.zeros(len(faces))
    angles = np.full(len(faces), 180.0)
    by_size = {}
    for i, face in enumerate(faces):
        by_size.setdefault(len(face), []).append(i)
    for size, indices in by_size.items():
        corners = points[np.array([faces[i] for i in indices], dtype=np.int64)]       # F x k x 3
        nxt, prev = np.roll(corners, -1, axis=1), np.roll(corners, 1, axis=1)
        areas[indices] = 0.5 * np.linalg.norm(np.cross(corners, nxt).sum(axis=1), axis=1)
        u, w = prev - corners, nxt - corners
        lengths = np.linalg.norm(u, axis=2) * np.linalg.norm(w, axis=2)
        cos = np.einsum("fkj,fkj->fk", u, w) / np.maximum(lengths, 1e-30)
        angles[indices] = np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))).min(axis=1)
    median = float(np.median(areas)) if len(areas) else 0.0
    return [i for i in range(len(faces))
            if len(faces[i]) >= 4 and (angles[i] < min_angle or areas[i] < min_area * median)]


def fix_degenerate(points, faces, project=None, rounds=4):
    """Relaja solo los vértices de las caras degeneradas, aunque estén en pliegues (la relajación
    normal los deja quietos para no redondear piezas duras). Devuelve (puntos, antes, después)."""
    points = np.array(points, dtype=float)
    bad = degenerate_faces(points, faces)
    before = len(bad)
    for _ in range(rounds):
        if not bad:
            break
        free = {v for f in bad for v in faces[f]}
        fixed = [v for v in range(len(points)) if v not in free]
        points = relax(points, faces, iterations=5, project=project, feature_angle=180.0, fixed=fixed)
        bad = degenerate_faces(points, faces)
    return points, before, len(bad)


# ------------------------------------------------------------------ triángulos -> quads

# un quad nacido de dos triángulos solo vale si sus ángulos interiores quedan entre estos grados
# (más agudo o más abierto sale un quad deformado) y si los dos triángulos no forman un pliegue
MERGE_MIN_ANGLE = 35.0
MERGE_MAX_ANGLE = 145.0
MERGE_MIN_NORMAL_COS = 0.5


def merge_triangles(points, faces, min_angle=MERGE_MIN_ANGLE, max_angle=MERGE_MAX_ANGLE,
                    min_normal_cos=MERGE_MIN_NORMAL_COS):
    """Junta parejas de triángulos vecinos en quads, los más cuadrados primero. Devuelve (caras, nº de uniones).

    Instant Meshes en modo quad-dominante extrae a la resolución final (con la mitad del error de
    forma que el modo solo-quads) pero deja ~10 % de triángulos, cada uno con vértices de 5 aristas.
    Unir dos triángulos quita la arista que comparten, así que sus dos extremos bajan una valencia.
    Los triángulos que no encuentran pareja buena se quedan.
    """
    points = np.asarray(points, dtype=float)
    faces = [list(f) for f in faces]
    owners = {}
    for i, face in enumerate(faces):
        if len(face) == 3:
            for k in range(3):
                a, b = face[k], face[(k + 1) % 3]
                owners.setdefault((a, b) if a < b else (b, a), []).append(i)

    def normal(face):
        p = points[face]
        return np.cross(p[1] - p[0], p[2] - p[0])

    candidates = []
    for (a, b), pair in owners.items():
        if len(pair) != 2:
            continue
        t1, t2 = faces[pair[0]], faces[pair[1]]
        # cada triángulo recorre la arista compartida en sentido contrario: si no, están al revés
        i = t1.index(a)
        if t1[(i + 1) % 3] != b:
            a, b = b, a
            i = t1.index(a)
        c = t1[(i + 2) % 3]
        j = t2.index(b)
        if t2[(j + 1) % 3] != a:
            continue
        d = t2[(j + 2) % 3]
        quad = [a, d, b, c]
        n1, n2 = normal(t1), normal(t2)
        l1, l2 = np.linalg.norm(n1), np.linalg.norm(n2)
        if l1 < 1e-30 or l2 < 1e-30 or float(n1 @ n2) / (l1 * l2) < min_normal_cos:
            continue
        p = points[quad]
        u = np.roll(p, 1, axis=0) - p
        w = np.roll(p, -1, axis=0) - p
        cos = np.einsum("ij,ij->i", u, w) / np.maximum(np.linalg.norm(u, axis=1) * np.linalg.norm(w, axis=1), 1e-30)
        angles = np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))
        if angles.min() < min_angle or angles.max() > max_angle:
            continue
        # convexo: los cuatro giros van en el mismo sentido que la normal
        turn = np.cross(p - np.roll(p, 1, axis=0), np.roll(p, -1, axis=0) - p) @ (n1 + n2)
        if (turn <= 0).any():
            continue
        candidates.append((float(np.abs(angles - 90.0).max()), pair[0], pair[1], quad))
    candidates.sort(key=lambda item: item[0])
    used, merged, out = set(), 0, {}
    for _, i, j, quad in candidates:
        if i in used or j in used:
            continue
        used.update((i, j))
        out[i] = quad
        out[j] = None
        merged += 1
    result = []
    for index, face in enumerate(faces):
        if index in out:
            if out[index] is not None:
                result.append(out[index])
        else:
            result.append(face)
    return result, merged
