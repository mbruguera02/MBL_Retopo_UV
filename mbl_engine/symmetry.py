# -*- coding: utf-8 -*-
"""Simetría en X sin Maya: detectar, cortar la mitad +X, reflejarla y soldar la costura central.

Así los dos lados de un retopo tienen exactamente la misma topología (polos en el mismo sitio,
loops que se corresponden), que es lo que se espera de un personaje y lo que permite pintar en
simetría.

Detección (muestras del modelo reflejadas en el plano X central y distancia a la superficie, en %
de la diagonal): humano CC 0.005% de media, águila 0.057%; en los ejes Y y Z, del 2% al 14%.
"""

import numpy as np

from .closest import make_grid

# media y percentil 95 de la distancia reflejada (% de la diagonal) por debajo de los cuales el
# modelo se considera simétrico en X
SYMMETRIC_MEAN_PERCENT = 0.3
SYMMETRIC_P95_PERCENT = 1.0
SAMPLES = 4000
# vértices de borde a menos de esta fracción de la arista media del plano se pegan a él y se sueldan
# Humano: los vértices del corte quedan a < 0.1 del plano (arista ~2.5) y los de agujeros que rozan el
# centro (boca, espalda) a > 1. Con 0.6 se soldaban también esos agujeros: pliegues en V que Unfold3D
# rechaza ("Mesh has non-manifold UVs")
WELD_EDGE_FRACTION = 0.2
CAP_TOLERANCE_FRACTION = 0.1
LOOSE_OFFSET_FRACTION = 0.2


def plane_x(points):
    """X del plano de simetría: el centro de la caja del modelo."""
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    return 0.5 * float(points[:, 0].min() + points[:, 0].max()) if len(points) else 0.0


def mirror_error(mesh, center=None, samples=SAMPLES):
    """(media, percentil 95) de la distancia entre el modelo reflejado en X y el original, en % de
    la diagonal. mesh: Mesh."""
    points = mesh.points[mesh.used_vertices]
    if not len(points) or not mesh.face_count:
        return 100.0, 100.0
    center = plane_x(points) if center is None else center
    diagonal = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0))) or 1.0
    picked = points[np.linspace(0, len(points) - 1, min(samples, len(points))).astype(int)].copy()
    picked[:, 0] = 2.0 * center - picked[:, 0]
    distances = make_grid(mesh.points, mesh.triangles()[0]).closest(picked)[1]
    distances = 100.0 * distances / diagonal
    return float(distances.mean()), float(np.percentile(distances, 95))


def is_symmetric(mesh):
    mean, p95 = mirror_error(mesh)
    return mean <= SYMMETRIC_MEAN_PERCENT and p95 <= SYMMETRIC_P95_PERCENT, mean, p95


def cut_half(points, faces, center):
    """Solo la mitad +X, cortada justo en el plano x = center (sustituye a polyCut). Pura.

    Las caras que cruzan el plano se recortan; el punto nuevo de cada arista cortada se comparte
    entre las dos caras que la usan, así no se abren grietas. Devuelve (puntos, caras).
    """
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    span = float(np.ptp(points[:, 0])) if len(points) else 1.0
    eps = 1e-9 * (span or 1.0)
    side = points[:, 0] - center
    side[np.abs(side) <= eps] = 0.0          # sobre el plano: se queda, sin crear puntos nuevos
    new_points = []
    on_edge = {}

    def cut(a, b):
        key = (a, b) if a < b else (b, a)
        if key not in on_edge:
            t = side[a] / (side[a] - side[b])
            p = points[a] + (points[b] - points[a]) * t
            p[0] = center
            on_edge[key] = len(points) + len(new_points)
            new_points.append(p)
        return on_edge[key]

    out = []
    for face in faces:
        s = side[face]
        if (s >= 0).all():
            out.append(list(face))
            continue
        if (s <= 0).all():
            continue
        clipped = []
        n = len(face)
        for i in range(n):
            a, b = face[i], face[(i + 1) % n]
            if side[a] >= 0:
                clipped.append(a)
            if (side[a] > 0 and side[b] < 0) or (side[a] < 0 and side[b] > 0):
                clipped.append(cut(a, b))
        # quitar repetidos seguidos (dos vértices sobre el plano)
        clean = [v for i, v in enumerate(clipped) if v != clipped[i - 1]] if len(clipped) > 1 else clipped
        if len(set(clean)) >= 3:
            out.append(clean)
    if new_points:
        points = np.vstack([points, np.array(new_points)])
    return points, out


def _border_edges(faces):
    count = {}
    for face in faces:
        for a, b in zip(face, face[1:] + face[:1]):
            key = (a, b) if a < b else (b, a)
            count[key] = count.get(key, 0) + 1
    return [edge for edge, n in count.items() if n == 1]


def _border_vertices(faces):
    return {v for edge in _border_edges(faces) for v in edge}


def mirror_mesh(points, faces, center, tolerance):
    """Refleja la mitad +X y suelda la costura (pura).

    Las caras enteras sobre el plano se quitan. Los vértices de borde a menos de `tolerance` del plano
    se pegan a él y se comparten con su reflejo; los demás no pueden pasar al otro lado. Los vértices
    que quedan sin cara los descarta create_mesh. Devuelve (puntos, caras, nº de vértices soldados).
    """
    points = np.array(points, dtype=float)
    count = len(points)
    # caras enteras sobre el plano: la tapa que pone el relleno de agujeros al cerrar el corte
    # (con pocas caras el borde del corte es corto y se rellena como un agujero más)
    # Tolerancia mucho más fina que la de soldar: la tapa queda prácticamente sobre el plano, y con la
    # misma tolerancia se borraban quads estrechos junto a la costura (humano: 4 -> 22 polos de 6+)
    near = np.abs(points[:, 0] - center) <= CAP_TOLERANCE_FRACTION * tolerance
    faces = [f for f in faces if not all(near[v] for v in f)]
    near_plane = np.abs(points[:, 0] - center) <= tolerance
    # Se sueldan solo los vértices de la costura: los que tienen alguna arista de borde con los dos
    # extremos junto al plano. Un vértice de borde suelto junto al plano (un agujero que roza el
    # centro) soldado con su reflejo dejaba los dos lados tocándose en un punto: un vértice "pajarita"
    # que, una vez separado, Unfold3D rechaza ("Mesh has non-manifold UVs")
    on_plane = np.zeros(count, dtype=bool)
    for a, b in _border_edges(faces):
        if near_plane[a] and near_plane[b]:
            on_plane[a] = on_plane[b] = True
    points[on_plane, 0] = center
    points[:, 0] = np.maximum(points[:, 0], center)
    # los sueltos se apartan un poco del plano para no coincidir con su reflejo
    loose = near_plane & ~on_plane & np.isin(np.arange(count), list(_border_vertices(faces)))
    points[loose, 0] = np.maximum(points[loose, 0], center + LOOSE_OFFSET_FRACTION * tolerance)

    remap = np.empty(count, dtype=int)
    others = np.flatnonzero(~on_plane)
    remap[on_plane] = np.flatnonzero(on_plane)
    remap[others] = count + np.arange(len(others))
    mirrored = points[others].copy()
    mirrored[:, 0] = 2.0 * center - mirrored[:, 0]

    out_faces = [list(f) for f in faces]
    for face in faces:
        # orden invertido: el reflejo cambia la orientación
        out_faces.append([int(remap[v]) for v in reversed(face)])
    return np.vstack([points, mirrored]), out_faces, int(on_plane.sum())


def average_edge(points, faces):
    points = np.asarray(points, dtype=float)
    total, n = 0.0, 0
    for face in faces:
        for a, b in zip(face, face[1:] + face[:1]):
            total += float(np.linalg.norm(points[a] - points[b]))
            n += 1
    return total / n if n else 0.0


def plane_edge(points, faces, center):
    """Arista media de los bordes cercanos al plano (la costura): la referencia para soldar."""
    points = np.asarray(points, dtype=float)
    border = _border_vertices(faces)
    general = average_edge(points, faces)
    near = [v for v in border if abs(points[v, 0] - center) <= general]
    if not near:
        return general
    near = set(near)
    lengths = [float(np.linalg.norm(points[a] - points[b])) for face in faces
               for a, b in zip(face, face[1:] + face[:1]) if a in near and b in near]
    return float(np.mean(lengths)) if lengths else general


def describe(mean, p95):
    return "error de espejo {:.3f}% de media, {:.2f}% en el 95% de puntos".format(mean, p95)
