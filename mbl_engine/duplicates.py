# -*- coding: utf-8 -*-
"""Piezas repetidas sin Maya: agrupar mallas idénticas salvo una transformación rígida (B4).

Veinte tornillos copiados no necesitan veinte retopos: se hace uno y el resultado se copia a los demás, cada copia
en su sitio. Dos mallas son "la misma pieza" si

- tienen la misma topología, vértice a vértice (mismo nº de vértices y las mismas caras con los mismos índices:
  es lo que da duplicar o copiar un objeto; una malla rehecha con otro orden de vértices no se reconoce), y
- existe un giro y un desplazamiento que lleva los puntos de una a los de la otra con un error por debajo de la
  tolerancia (Kabsch: ajuste por mínimos cuadrados, comprobado después punto a punto).

Escala y espejo: NO se agrupan. Una copia a otra escala (aunque sea uniforme) o reflejada es otra pieza: su tamaño
cambia lo que mide el retopo y las UVs en unidades de escena (densidad de texel, caras objetivo por área) y un
espejo invierte el sentido de las caras. Solo cuentan como iguales las que tienen el mismo tamaño. Los puntos se
comparan en el mundo, así que da igual si la copia está congelada o si su forma viene de la transformación del objeto.
"""

import hashlib

import numpy as np

# error máximo permitido (fracción de la diagonal de la pieza) entre los puntos de la copia y los de la pieza girada
# y movida. Una copia con un vértice desplazado un 0,1 % de su tamaño ya es otra pieza; los errores de coma flotante
# de Maya (float32 en las posiciones) quedan unas mil veces por debajo
TOLERANCE = 1e-4
# holgura extra por precisión de float32 respecto a la mayor coordenada (una pieza pequeña lejos del origen)
FLOAT32_SLACK = 8 * 2.0 ** -23


def topology_key(point_count, faces):
    """Clave comparable de la topología: nº de vértices, nº de caras y huella de los índices de cada cara."""
    counts = np.fromiter((len(f) for f in faces), dtype=np.int64, count=len(faces))
    flat = np.fromiter((i for f in faces for i in f), dtype=np.int64, count=int(counts.sum()))
    digest = hashlib.blake2b(counts.tobytes() + flat.tobytes(), digest_size=16).hexdigest()
    return (int(point_count), len(faces), digest)


def rigid_fit(reference, points):
    """Giro R (3x3) y desplazamiento t con points ~ reference @ R.T + t, por mínimos cuadrados (Kabsch).
    Siempre un giro propio (determinante +1): un espejo no es una transformación rígida y sale con error alto."""
    reference = np.asarray(reference, dtype=float).reshape(-1, 3)
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    centre_a, centre_b = reference.mean(axis=0), points.mean(axis=0)
    covariance = (reference - centre_a).T @ (points - centre_b)
    u, _, vt = np.linalg.svd(covariance)
    sign = 1.0 if np.linalg.det(vt.T @ u.T) >= 0 else -1.0
    rotation = vt.T @ np.diag([1.0, 1.0, sign]) @ u.T
    return rotation, centre_b - rotation @ centre_a


def rigid_matrix(rotation, translation):
    """Matriz 4x4 (vectores columna: q = M @ [p, 1]) de un giro y un desplazamiento."""
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = translation
    return matrix


def match(reference, points, tolerance=TOLERANCE):
    """Matriz 4x4 que lleva `reference` (Nx3) a `points` (Nx3, mismo orden de vértices) si lo hace dentro de la
    tolerancia; None si no hay giro y desplazamiento que lo consigan (otra forma, otra escala o un espejo)."""
    reference = np.asarray(reference, dtype=float).reshape(-1, 3)
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    if reference.shape != points.shape or not len(reference):
        return None
    diagonal = float(np.linalg.norm(np.ptp(reference, axis=0)))
    limit = tolerance * diagonal + FLOAT32_SLACK * float(max(np.abs(reference).max(), np.abs(points).max()))
    rotation, translation = rigid_fit(reference, points)
    error = np.linalg.norm(reference @ rotation.T + translation - points, axis=1).max()
    return rigid_matrix(rotation, translation) if error <= limit else None


def group_identical(meshes, tolerance=TOLERANCE):
    """Agrupa las mallas que son la misma pieza. meshes: [(puntos Nx3 en el mundo, caras)].

    Devuelve [grupo, ...] en el orden de la primera malla de cada uno; cada grupo es [(índice, matriz), ...] con la
    primera (el representante, la de menor índice) con matriz None y las demás con la matriz 4x4 (vectores columna)
    que lleva los puntos del representante a los suyos. Una malla sin copias es un grupo de una sola."""
    groups, buckets = [], {}
    for index, (points, faces) in enumerate(meshes):
        points = np.asarray(points, dtype=float).reshape(-1, 3)
        key = topology_key(len(points), faces)
        for group in buckets.setdefault(key, []):
            matrix = match(meshes[group[0][0]][0], points, tolerance)
            if matrix is not None:
                group.append((index, matrix))
                break
        else:
            group = [(index, None)]
            buckets[key].append(group)
            groups.append(group)
    return groups
