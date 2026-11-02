# -*- coding: utf-8 -*-
"""Direcciones guía para el campo del Motor MBL (Instant Meshes --orient).

Los edge loops de un buen retopo siguen las direcciones principales de curvatura: anillos
alrededor de brazos, dedos y plumas, y líneas a lo largo de ellos. Aquí se calcula, por vértice,
una de esas direcciones (la otra es perpendicular y el campo de 4 direcciones la incluye) y un
peso: alto donde la superficie se curva claramente más en un sentido que en otro (un tubo), cero
donde es plana o igual en todas direcciones (una esfera), que es donde el campo debe ir libre.

Todo es numpy puro (se prueba sin Maya).
"""

import numpy as np

# vueltas de suavizado del tensor de curvatura sobre los vecinos: quitan el ruido de mallas de
# escaneo o de IA sin borrar la forma (cada vuelta mezcla con el anillo de vecinos)
SMOOTH_ITERATIONS = 6
# por debajo de esta anisotropía (|k1 - k2| / (|k1| + |k2|)) no se impone dirección
MIN_ANISOTROPY = 0.25
# curvatura relativa al tamaño de cara (|k| · arista) a partir de la cual la dirección pesa del todo.
# Por debajo la superficie es casi plana a esa escala y la dirección es ruido (en el humano, guiar
# toda la piel subía los polos de 131 a 210)
CURVATURE_FULL = 0.5


def _edges(faces):
    tris = np.asarray([f[:3] for f in faces], dtype=np.int64)
    edges = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    edges = np.sort(edges, axis=1)
    return tris, np.unique(edges, axis=0)


def vertex_normals(points, tris):
    """Normales por vértice (media de las de sus triángulos, ponderada por área)."""
    a, b, c = points[tris[:, 0]], points[tris[:, 1]], points[tris[:, 2]]
    face_n = np.cross(b - a, c - a)
    normals = np.zeros_like(points)
    for k in range(3):
        np.add.at(normals, tris[:, k], face_n)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return normals / np.maximum(length, 1e-30)


def principal_curvatures(points, faces, smooth=SMOOTH_ITERATIONS):
    """(k1, k2, dirección de la curvatura mayor) de cada vértice, con el tensor suavizado `smooth` vueltas.

    k1 es la curvatura de mayor valor absoluto. Un tubo de radio r da |k1| = 1/r y k2 = 0; una
    esfera, k1 = k2 = 1/r (con signo según la orientación de las normales).
    """
    points = np.asarray(points, dtype=float)
    n = len(points)
    tris, edges = _edges(faces)
    normals = vertex_normals(points, tris)

    # base tangente (u, v) de cada vértice
    helper = np.where(np.abs(normals[:, :1]) < 0.9, [[1.0, 0.0, 0.0]], [[0.0, 1.0, 0.0]])
    u = np.cross(normals, helper)
    u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-30)
    v = np.cross(normals, u)

    gram = np.zeros((n, 2, 2))   # sum a aᵀ
    cross = np.zeros((n, 2, 2))  # sum b aᵀ
    for i, j in ((0, 1), (1, 0)):
        p, q = edges[:, i], edges[:, j]
        d = points[q] - points[p]
        dn = normals[q] - normals[p]
        a = np.column_stack([np.einsum("ij,ij->i", d, u[p]), np.einsum("ij,ij->i", d, v[p])])
        b = np.column_stack([np.einsum("ij,ij->i", dn, u[p]), np.einsum("ij,ij->i", dn, v[p])])
        # peso 1/|d|²: cada arista cuenta igual sea corta o larga
        w = 1.0 / np.maximum(np.einsum("ij,ij->i", a, a), 1e-30)
        np.add.at(gram, p, w[:, None, None] * a[:, :, None] * a[:, None, :])
        np.add.at(cross, p, w[:, None, None] * b[:, :, None] * a[:, None, :])
    gram += np.eye(2)[None] * 1e-9 * np.maximum(np.trace(gram, axis1=1, axis2=2), 1e-30)[:, None, None]
    shape = cross @ np.linalg.inv(gram)
    shape = 0.5 * (shape + np.transpose(shape, (0, 2, 1)))
    frame = np.stack([u, v], axis=2)  # n x 3 x 2
    tensor = frame @ shape @ np.transpose(frame, (0, 2, 1))

    # suavizado: media con los vecinos (el tensor es simétrico, así que da igual el signo de t)
    a, b = edges[:, 0], edges[:, 1]
    degree = np.bincount(np.concatenate([a, b]), minlength=n).astype(float)
    for _ in range(int(smooth)):
        acc = tensor.copy()
        np.add.at(acc, a, tensor[b])
        np.add.at(acc, b, tensor[a])
        tensor = acc / (degree + 1.0)[:, None, None]

    # quitar la componente normal y diagonalizar en el plano tangente
    proj = np.eye(3)[None] - normals[:, :, None] * normals[:, None, :]
    tensor = proj @ tensor @ proj
    values, vectors = np.linalg.eigh(tensor)
    # el autovector de la normal tiene autovalor ~0: los dos tangentes son los otros dos
    along_normal = np.abs(np.einsum("nij,ni->nj", vectors, normals))
    normal_slot = np.argmax(along_normal, axis=1)
    slots = np.array([[1, 2], [0, 2], [0, 1]])[normal_slot]
    rows = np.arange(n)
    k1, k2 = values[rows, slots[:, 0]], values[rows, slots[:, 1]]
    first = np.abs(k1) >= np.abs(k2)
    directions = np.where(first[:, None], vectors[rows, :, slots[:, 0]], vectors[rows, :, slots[:, 1]])

    return k1, k2, directions


def curvature_directions(points, faces, edge_length=None, smooth=SMOOTH_ITERATIONS,
                         min_anisotropy=MIN_ANISOTROPY, curvature_full=CURVATURE_FULL):
    """(direcciones Nx3, pesos N en 0..1) de la curvatura principal de cada vértice.

    edge_length: arista del retopo final. Si se da, el peso baja donde la curvatura es pequeña a
    esa escala (solo mandan tubos y pliegues que el retopo puede ver).

    Operador de forma por mínimos cuadrados: en cada arista p -> q el cambio de normal es
    S·(q - p) en el plano tangente; con todas las aristas del vértice se ajusta S (2x2) y se pasa a
    3D. No depende de que las aristas estén repartidas por igual en todas direcciones (la malla de
    una esfera de Maya tiene aristas en cruz y daba curvaturas distintas donde no las hay).
    """
    k1, k2, directions = principal_curvatures(points, faces, smooth)
    anisotropy = np.abs(k1 - k2) / np.maximum(np.abs(k1) + np.abs(k2), 1e-30)
    weights = np.clip((anisotropy - min_anisotropy) / max(1.0 - min_anisotropy, 1e-6), 0.0, 1.0)
    if edge_length:
        bend = np.maximum(np.abs(k1), np.abs(k2)) * float(edge_length)
        weights *= np.clip(bend / curvature_full, 0.0, 1.0)
    return directions, weights


def write_orientation(path, directions, weights, strength=1.0):
    """Archivo de --orient: "dx dy dz peso" por vértice."""
    data = np.column_stack([directions, np.clip(weights * float(strength), 0.0, 1.0)])
    np.savetxt(path, data, fmt="%.5f")
