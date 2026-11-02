# -*- coding: utf-8 -*-
"""Reparación de topología sin Maya: caras degeneradas, aristas compartidas de más, vértices de 2
aristas, orientación, pajaritas, restos sueltos y agujeros. Más lectura y escritura de OBJ.

Funciones puras sobre (puntos, caras): las usan los motores de retopo (Instant Meshes, QuadWild,
Maya) para dejar su resultado en una malla limpia que Unfold3D acepte.
"""

import numpy as np


def read_obj(path):
    """(posiciones, caras) de un OBJ; las caras usan índices desde 0."""
    points, faces, invalid = [], [], set()
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("v "):
                try:
                    point = tuple(float(x) for x in line.split()[1:4])
                except ValueError:
                    # Instant Meshes escribe "-nan(ind)" cuando algo sale mal en una zona
                    point = (0.0, 0.0, 0.0)
                    invalid.add(len(points))
                if any(c != c for c in point):
                    invalid.add(len(points))
                points.append(point)
            elif line.startswith("f "):
                faces.append([int(token.split("/")[0]) - 1 for token in line.split()[1:]])
    if invalid:
        faces = [f for f in faces if not invalid.intersection(f)]
    return points, faces


def write_obj(path, points, faces, keep_order=False):
    """Escribe solo los vértices usados (así no quedan vértices sueltos al importar).

    keep_order=True escribe todos los vértices en su orden: hace falta cuando hay un valor por
    vértice en otro archivo (densidad), que el motor lee en ese mismo orden."""
    uniform = _uniform_faces(faces)
    if uniform is not None:
        _write_obj_uniform(path, points, uniform, keep_order)
        return
    remap = {}
    if keep_order:
        remap = {v: v for v in range(len(points))}
    for face in faces:
        for v in face:
            if v not in remap:
                remap[v] = len(remap)
    with open(path, "w", encoding="utf-8") as handle:
        for v in sorted(remap, key=remap.get):
            handle.write("v {:.6f} {:.6f} {:.6f}\n".format(*points[v]))
        for face in faces:
            handle.write("f " + " ".join(str(remap[v] + 1) for v in face) + "\n")


def _uniform_faces(faces):
    """Las caras como un array F x k si todas tienen los mismos lados (triángulos o quads), o None."""
    if isinstance(faces, np.ndarray):
        return faces.astype(np.int64) if faces.ndim == 2 and faces.shape[1] >= 3 else None
    if len(faces) < 1000:
        return None
    sizes = {len(f) for f in faces}
    if len(sizes) != 1 or min(sizes) < 3:
        return None
    return np.array(faces, dtype=np.int64)


def _write_obj_uniform(path, points, faces, keep_order):
    """write_obj para caras de igual nº de lados: mismo archivo (vértices en orden de primera aparición),
    pero con numpy en vez de un diccionario cara a cara (528.000 triángulos: de 2 s a medio segundo)."""
    points = np.asarray(points, dtype=float)
    flat = faces.ravel()
    if keep_order:
        order = np.arange(len(points))
        index = faces
    else:
        _, first = np.unique(flat, return_index=True)
        order = flat[np.sort(first)]
        rank = np.empty(int(flat.max()) + 1, dtype=np.int64)
        rank[order] = np.arange(len(order))
        index = rank[faces]
    face_format = "f " + " ".join(["%d"] * faces.shape[1])
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join("v %.6f %.6f %.6f" % tuple(p) for p in points[order].tolist()))
        handle.write("\n")
        handle.write("\n".join(face_format % tuple(f) for f in (index + 1).tolist()))
        handle.write("\n")


def vertex_areas(points, faces):
    """Área de cada vértice (un tercio de la de sus triángulos), para pesar valores por vértice (pura)."""
    points = np.asarray(points, dtype=float)
    tris = np.array([f[:3] for f in faces], dtype=int)
    areas = np.zeros(len(points))
    if not len(tris):
        return areas
    a, b, c = points[tris[:, 0]], points[tris[:, 1]], points[tris[:, 2]]
    tri_area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1) / 3.0
    for k in range(3):
        np.add.at(areas, tris[:, k], tri_area)
    return areas


def write_ply(path, points, faces):
    """PLY de triángulos que conserva el orden de los vértices (Instant Meshes lo lee tal cual)."""
    with open(path, "w", encoding="ascii") as handle:
        handle.write("ply\nformat ascii 1.0\nelement vertex {}\nproperty float x\nproperty float y\n"
                     "property float z\nelement face {}\nproperty list uchar int vertex_indices\n"
                     "end_header\n".format(len(points), len(faces)))
        for p in points:
            handle.write("{:.6f} {:.6f} {:.6f}\n".format(p[0], p[1], p[2]))
        for f in faces:
            handle.write("3 {} {} {}\n".format(f[0], f[1], f[2]))


def _edges(face):
    return zip(face, face[1:] + face[:1])


def _edge_faces(faces):
    result = {}
    for index, face in enumerate(faces):
        for a, b in _edges(face):
            result.setdefault((min(a, b), max(a, b)), []).append(index)
    return result


def _remove_degenerate(faces):
    out, seen = [], set()
    for face in faces:
        clean = []
        for v in face:
            if not clean or clean[-1] != v:
                clean.append(v)
        if len(clean) > 1 and clean[0] == clean[-1]:
            clean.pop()
        key = tuple(sorted(clean))
        if len(set(clean)) == len(clean) >= 3 and key not in seen:
            seen.add(key)
            out.append(clean)
    return out


def _remove_overshared_edges(faces):
    """Aristas con más de 2 caras: se quitan las caras sobrantes (las de después)."""
    drop = set()
    for owners in _edge_faces(faces).values():
        if len(owners) > 2:
            drop.update(owners[2:])
    return [f for i, f in enumerate(faces) if i not in drop]


def _dissolve_doublets(faces):
    """Quita los vértices interiores de solo 2 aristas (dos caras que comparten dos aristas)."""
    for _ in range(10):
        vertex_faces = {}
        for index, face in enumerate(faces):
            for v in face:
                vertex_faces.setdefault(v, []).append(index)
        edge_faces = _edge_faces(faces)
        changed = False
        removed = set()
        for v, owners in vertex_faces.items():
            if len(owners) != 2 or owners[0] in removed or owners[1] in removed:
                continue
            neighbours = {u for i in owners for a, b in _edges(faces[i]) for u in (a, b) if v in (a, b) and u != v}
            interior = all(len(edge_faces.get((min(v, u), max(v, u)), [])) == 2 for u in neighbours)
            if len(neighbours) == 2 and interior:
                first, second = (faces[i][faces[i].index(v):] + faces[i][:faces[i].index(v)] for i in owners)
                if first[1] == second[-1] and first[-1] == second[1] and len(first) + len(second) == 8:
                    # dos quads (o más lados) que comparten las dos aristas del vértice: se unen en una sola
                    # cara sin ese vértice (quitarlo de las dos las dejaba como dos triángulos pegados)
                    faces[owners[0]] = first[1:] + second[2:-1]
                    faces[owners[1]] = []
                else:
                    for i in owners:
                        faces[i] = [x for x in faces[i] if x != v]
                removed.update(owners)
                changed = True
        faces = _remove_degenerate(faces)
        if not changed:
            break
    return faces


def _orient_consistently(faces):
    """Recorre cada pieza y da la vuelta a las caras cuyo orden choca con el de sus vecinas."""
    edge_faces = _edge_faces(faces)
    visited = [False] * len(faces)
    for start in range(len(faces)):
        if visited[start]:
            continue
        visited[start] = True
        stack = [start]
        while stack:
            index = stack.pop()
            directed = set(_edges(faces[index]))
            for a, b in list(directed):
                for other in edge_faces[(min(a, b), max(a, b))]:
                    if other == index or visited[other]:
                        continue
                    if (a, b) in set(_edges(faces[other])):
                        faces[other] = faces[other][::-1]
                    visited[other] = True
                    stack.append(other)
    return faces


def _split_bowties(points, faces):
    """Separa los vértices en los que varios abanicos de caras solo se tocan en un punto."""
    vertex_faces = {}
    for index, face in enumerate(faces):
        for v in face:
            vertex_faces.setdefault(v, []).append(index)
    points = list(points)
    for v, owners in vertex_faces.items():
        if len(owners) < 2:
            continue
        # abanicos: caras alrededor de v conectadas por una arista que contiene v
        parent = {i: i for i in owners}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        by_edge = {}
        for i in owners:
            for a, b in _edges(faces[i]):
                if v in (a, b):
                    by_edge.setdefault((min(a, b), max(a, b)), []).append(i)
        for linked in by_edge.values():
            for other in linked[1:]:
                parent[find(other)] = find(linked[0])
        fans = {}
        for i in owners:
            fans.setdefault(find(i), []).append(i)
        for fan in list(fans.values())[1:]:
            points.append(points[v])
            new = len(points) - 1
            for i in fan:
                faces[i] = [new if x == v else x for x in faces[i]]
    return points, faces


def _collapse_short_edges(points, faces):
    """Une los vértices de las aristas de longitud (casi) cero."""
    lengths = []
    for face in faces:
        for a, b in _edges(face):
            lengths.append(sum((points[a][k] - points[b][k]) ** 2 for k in range(3)) ** 0.5)
    if not lengths:
        return faces
    tolerance = sum(lengths) / len(lengths) * 1e-4
    target = {}

    def find(v):
        while v in target:
            v = target[v]
        return v

    for face in faces:
        for a, b in _edges(face):
            a, b = find(a), find(b)
            if a != b and sum((points[a][k] - points[b][k]) ** 2 for k in range(3)) ** 0.5 <= tolerance:
                target[max(a, b)] = min(a, b)
    if not target:
        return faces
    return _remove_degenerate([[find(v) for v in face] for face in faces])


def _remove_small_pieces(faces, min_ratio):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for face in faces:
        for v in face[1:]:
            ra, rb = find(face[0]), find(v)
            if ra != rb:
                parent[rb] = ra
    pieces = {}
    for index, face in enumerate(faces):
        pieces.setdefault(find(face[0]), []).append(index)
    biggest = max(len(p) for p in pieces.values()) if pieces else 0
    keep = set()
    for piece in pieces.values():
        if len(piece) == biggest or len(piece) >= len(faces) * min_ratio:
            keep.update(piece)
    return [f for i, f in enumerate(faces) if i in keep], len(pieces) - len({find(faces[i][0]) for i in keep})


# los agujeros pares de hasta tantas aristas se cierran con holefill; los mayores, con una estrella
PATTERN_MAX_EDGES = 24


def _fill_holes(points, faces, max_edges, accept=None, quads=False):
    """Rellena los agujeros pequeños con un abanico hacia su centro.

    Solo los de borde limpio (cada vértice del borde aparece una vez): rellenar un borde que se
    toca a sí mismo crea polígonos que comparten varias aristas con la misma vecina y Unfold3D
    los rechaza. Las caras deben estar ya orientadas de forma coherente.
    accept(lista de vértices) decide qué agujeros se rellenan (None = todos los pequeños).
    quads: abanico de quads (centro + 3 vértices seguidos del borde; un triángulo si es impar).
    """
    directed = {(a, b) for face in faces for a, b in _edges(face)}
    outgoing = {}
    for a, b in directed:
        if (b, a) not in directed:
            outgoing.setdefault(a, []).append(b)
    points = list(points)
    used, filled = set(), 0
    for start in list(outgoing):
        if start in used or len(outgoing[start]) != 1:
            continue
        loop, current, clean = [start], outgoing[start][0], True
        while current != start and len(loop) <= max_edges:
            if current in used or len(outgoing.get(current, [])) != 1 or current in loop:
                clean = False
                break
            loop.append(current)
            current = outgoing[current][0]
        used.update(loop)
        if not clean or current != start or len(loop) < 3 or len(loop) > max_edges:
            continue
        if accept and not accept(loop):
            continue
        n = len(loop)
        if quads and n >= 4 and n % 2 == 0 and n <= PATTERN_MAX_EDGES:
            # patrón de menos polos (estrella, rayas, rejilla o cuerdas) según la valencia de cada vértice del borde
            from . import holefill
            neighbors = {}
            for u, w in directed:
                neighbors.setdefault(u, set()).add(w)
                neighbors.setdefault(w, set()).add(u)
            pattern = holefill.best_pattern([len(neighbors.get(v, ())) for v in loop])
            base = len(points)
            ring = np.array([points[v] for v in loop], dtype=float)
            points.extend(tuple(p) for p in holefill.interior_points(pattern, ring))
            ids = loop + list(range(base, base + len(pattern.interior)))
            for face in pattern.faces:
                faces.append([ids[v] for v in reversed(face)])
            filled += 1
            continue
        center = tuple(sum(points[v][k] for v in loop) / len(loop) for k in range(3))
        points.append(center)
        c = len(points) - 1
        if quads and n >= 4:
            for i in range(0, n - 1, 2):
                a, b, d = loop[i], loop[i + 1], loop[(i + 2) % n]
                faces.append([d, b, a, c])
            if n % 2:
                faces.append([loop[0], loop[-1], c])
        else:
            for i, a in enumerate(loop):
                b = loop[(i + 1) % n]
                faces.append([b, a, c])
        filled += 1
    return points, faces, filled


def repair_topology(points, faces, min_piece_ratio=0.01, max_hole_edges=60):
    """Devuelve (posiciones, caras, informe) con la topología reparada.

    Se repite hasta que la malla deja de cambiar: separar pajaritas puede crear nuevos vértices
    de 2 aristas y viceversa.
    """
    report = {"faces_in": len(faces), "doublets": 0, "bowties": 0, "pieces": 0, "holes": 0}
    faces = [list(f) for f in faces]
    for _ in range(6):
        signature = (len(points), len(faces), sum(len(f) for f in faces))
        faces = _remove_degenerate(faces)
        faces = _collapse_short_edges(points, faces)
        faces = _remove_overshared_edges(faces)
        before = sum(len(f) for f in faces)
        faces = _dissolve_doublets(faces)
        report["doublets"] += (before - sum(len(f) for f in faces)) // 2
        faces = _orient_consistently(faces)
        count = len(points)
        points, faces = _split_bowties(points, faces)
        report["bowties"] += len(points) - count
        if (len(points), len(faces), sum(len(f) for f in faces)) == signature:
            break
    faces, report["pieces"] = _remove_small_pieces(faces, min_piece_ratio)
    if max_hole_edges > 0:
        points, faces, report["holes"] = _fill_holes(points, faces, max_hole_edges)
    report["faces_out"] = len(faces)
    return points, faces, report


# un borde del retopo es una abertura del modelo (y se deja) si la mitad de sus vértices tienen
# un borde del original a menos de esta fracción de su arista media; si no, es un agujero nuevo
OPENING_MATCH_EDGES = 1.5


def fill_new_holes(points, faces, source_border, max_edges):
    """Rellena con quads los agujeros que abrió el motor y que no estaban en el original (pura).

    polyRetopo abre agujeros en zonas finas (las plumas del águila: 5 agujeros nuevos con todos
    los ajustes), y Unfold3D los ve como UVs no-manifold. Devuelve (puntos, caras, rellenados).
    """
    pts = np.asarray(points, dtype=float)
    source_border = np.asarray(source_border, dtype=float).reshape(-1, 3)

    def is_new(loop):
        ring = pts[loop]
        edge = float(np.mean(np.linalg.norm(ring - np.roll(ring, 1, axis=0), axis=1))) or 1.0
        if not len(source_border):
            return True
        near = 0
        for p in ring:
            if np.min(np.sum((source_border - p) ** 2, axis=1)) <= (OPENING_MATCH_EDGES * edge) ** 2:
                near += 1
        return near < 0.5 * len(loop)

    faces = [list(f) for f in faces]
    points, faces, filled = _fill_holes(list(map(tuple, pts)), faces, max_edges, accept=is_new, quads=True)
    return points, faces, filled
