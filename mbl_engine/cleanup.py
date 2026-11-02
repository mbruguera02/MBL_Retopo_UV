# -*- coding: utf-8 -*-
"""Limpieza de mallas sin Maya: detectar problemas, fusionar vértices cercanos y separar lo
no-manifold. Sustituye a polyInfo, polyMergeVertex, polySplitEdge y polySplitVertex.

Todo vectorizado con numpy: tiene que aguantar mallas de IA o escaneo de cientos de miles de
caras (el águila de prueba tiene 528.000 triángulos).
"""

import numpy as np

from .mesh import Mesh
from .repair import _split_bowties

PROBLEM_LABELS = {
    "nonmanifold_edges": "aristas no-manifold",
    "nonmanifold_vertices": "vértices no-manifold",
    "lamina_faces": "caras láminas (duplicadas sobre sí mismas)",
    "zero_area_faces": "caras de área cero",
    "isolated_vertices": "vértices sueltos (sin caras)",
    "winged_vertices": "vértices con solo 2 aristas (sobran en mitad de una arista)",
}


def nonmanifold_vertices(mesh):
    """Vértices donde la malla no es una superficie: tocan una arista de más de 2 caras, o varios
    abanicos de caras se juntan solo en ellos (pajaritas: más de 2 aristas de borde)."""
    edges, count = mesh.edges, mesh.edge_face_count
    bad = np.zeros(mesh.vertex_count, dtype=bool)
    bad[edges[count > 2].ravel()] = True
    border_per_vertex = np.bincount(edges[count == 1].ravel(), minlength=mesh.vertex_count)
    bad |= border_per_vertex > 2
    return np.nonzero(bad)[0]


def analyze(mesh, relative_area_tolerance=1e-6):
    """Busca problemas sin modificar nada. Devuelve {problema: array de índices}."""
    report = {}
    report["nonmanifold_edges"] = np.nonzero(mesh.edge_face_count > 2)[0]
    report["nonmanifold_vertices"] = nonmanifold_vertices(mesh)
    # láminas: caras con el mismo conjunto de vértices que otra
    keys = {}
    lamina = []
    for f, face in enumerate(mesh.faces()):
        key = tuple(sorted(face))
        if key in keys:
            lamina += [keys[key], f]
        else:
            keys[key] = f
    report["lamina_faces"] = np.unique(np.array(lamina, dtype=np.int64))
    areas = mesh.face_areas
    threshold = max(float(areas.mean()) * relative_area_tolerance, 1e-12) if len(areas) else 0.0
    report["zero_area_faces"] = np.nonzero(areas <= threshold)[0]
    report["isolated_vertices"] = np.nonzero(~mesh.used_vertices)[0]
    report["winged_vertices"] = np.nonzero(mesh.used_vertices & ~mesh.boundary_vertices & (mesh.valence == 2))[0]
    return report


def count_problems(report):
    return {key: len(value) for key, value in report.items()}


def merge_vertices(points, faces, distance):
    """Fusiona los vértices a menos de `distance` (en cadena, como polyMergeVertex) en su punto
    medio. Quita las caras que quedan con menos de 3 vértices distintos. Devuelve
    (puntos, caras, nº de vértices fusionados). Los vértices sobrantes se conservan pero sin usar."""
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    n = len(points)
    if n < 2 or distance <= 0:
        return points, [list(f) for f in faces], 0
    cell = np.floor(points / distance).astype(np.int64)
    cell -= cell.min(axis=0)
    dims = cell.max(axis=0) + 2
    key = (cell[:, 0] * dims[1] + cell[:, 1]) * dims[2] + cell[:, 2]
    order = np.argsort(key, kind="stable")
    sorted_keys = key[order]
    pairs_a, pairs_b = [], []
    r = np.arange(-1, 2)
    for offset in np.stack(np.meshgrid(r, r, r, indexing="ij"), axis=-1).reshape(-1, 3):
        other_key = ((cell[:, 0] + offset[0]) * dims[1] + cell[:, 1] + offset[1]) * dims[2] + cell[:, 2] + offset[2]
        lo = np.searchsorted(sorted_keys, other_key, side="left")
        hi = np.searchsorted(sorted_keys, other_key, side="right")
        count = hi - lo
        if not count.sum():
            continue
        a = np.repeat(np.arange(n), count)
        b = order[np.repeat(lo, count) + (np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count))]
        keep = (a < b)
        a, b = a[keep], b[keep]
        close = np.einsum("ij,ij->i", points[a] - points[b], points[a] - points[b]) <= distance * distance
        pairs_a.append(a[close])
        pairs_b.append(b[close])
    label = np.arange(n)
    if pairs_a:
        a, b = np.concatenate(pairs_a), np.concatenate(pairs_b)
        # componentes conexas: propagar la etiqueta mínima hasta que no cambie
        while len(a):
            low = np.minimum(label[a], label[b])
            changed = (label[a] != low) | (label[b] != low)
            if not changed.any():
                break
            np.minimum.at(label, a, low)
            np.minimum.at(label, b, low)
            label = label[label]
    merged = int(n - len(np.unique(label)))
    if merged:
        sums = np.zeros_like(points)
        np.add.at(sums, label, points)
        counts = np.bincount(label, minlength=n).astype(float)
        points = points.copy()
        roots = counts > 0
        points[roots] = sums[roots] / counts[roots, None]
    out = []
    for face in faces:
        clean = []
        for v in (int(label[v]) for v in face):
            if not clean or clean[-1] != v:
                clean.append(v)
        if len(clean) > 1 and clean[0] == clean[-1]:
            clean.pop()
        if len(set(clean)) >= 3:
            out.append(clean)
    return points, out, merged


def fix_nonmanifold(points, faces):
    """Separa lo no-manifold, como Mesh > Cleanup de Maya. Pura.

    1) Aristas con más de 2 caras: las caras de más se despegan por esa arista (vértices nuevos).
    2) Pajaritas: cada abanico de caras de un vértice se queda con su propia copia del vértice.
    Devuelve (puntos, caras, nº de componentes separados).
    """
    points = [tuple(p) for p in np.asarray(points, dtype=float).reshape(-1, 3)]
    faces = [list(f) for f in faces]
    owners = {}
    for i, face in enumerate(faces):
        for a, b in zip(face, face[1:] + face[:1]):
            owners.setdefault((min(a, b), max(a, b)), []).append(i)
    split = 0
    for (a, b), fs in owners.items():
        for f in fs[2:]:
            for v in (a, b):
                points.append(points[v])
                faces[f] = [len(points) - 1 if x == v else x for x in faces[f]]
            split += 1
    count = len(points)
    points, faces = _split_bowties(points, faces)
    split += len(points) - count
    return np.array(points, dtype=float).reshape(-1, 3), faces, split


def clean(points, faces, merge_distance=0.0):
    """Fusionar (si merge_distance > 0) y separar lo no-manifold. Devuelve (Mesh, informe)."""
    report = {"merged": 0, "nonmanifold_split": 0}
    if merge_distance > 0:
        points, faces, report["merged"] = merge_vertices(points, faces, merge_distance)
    mesh = Mesh(points, faces)
    if len(analyze_nonmanifold(mesh)):
        points, faces, report["nonmanifold_split"] = fix_nonmanifold(mesh.points, mesh.faces())
        mesh = Mesh(points, faces)
    return mesh, report


def analyze_nonmanifold(mesh):
    """Aristas y vértices no-manifold juntos (rápido: antes de las UVs)."""
    return np.concatenate([np.nonzero(mesh.edge_face_count > 2)[0], nonmanifold_vertices(mesh)])
