# -*- coding: utf-8 -*-
"""Punto de partida para desplegar: cada isla aplanada sin ninguna cara dada la vuelta (embebido de Tutte, 1963).

El borde de la isla va a un círculo y cada punto interior a la media ponderada de sus vecinos. En un disco con borde
convexo y pesos positivos el resultado no tiene caras invertidas ni solapes, así que Unfold3D parte de algo limpio en vez
de una proyección arrugada que luego no sabe deshacer. Los agujeros propios de la isla (ojos, boca) quedan libres.

Numpy puro: se prueba sin Maya.
"""

import numpy as np

# gradiente conjugado: hasta este nº de vueltas o este residuo relativo
MAX_ROUNDS = 4000
TOLERANCE = 1e-6


def _uv_edges(uv_faces):
    """(a, b) de todas las aristas de las caras, en el sentido de cada cara."""
    counts = np.fromiter((len(f) for f in uv_faces), dtype=np.int64, count=len(uv_faces))
    ids = np.fromiter((i for f in uv_faces for i in f), dtype=np.int64, count=int(counts.sum()))
    first = np.repeat(np.cumsum(counts) - counts, counts)
    following = first + (np.arange(len(ids)) - first + 1) % np.repeat(counts, counts)
    return ids, ids[following]


def _components(count, a, b):
    label = np.arange(count)
    while True:
        low = np.minimum(label[a], label[b])
        new = label.copy()
        np.minimum.at(new, label[a], low)
        np.minimum.at(new, label[b], low)
        while True:
            jump = new[new]
            if np.array_equal(jump, new):
                break
            new = jump
        if np.array_equal(new, label):
            return label
        label = new


def _boundary_loops(a, b):
    """Bordes (lista de listas de UVs en orden) a partir de las aristas que solo aparecen en un sentido."""
    key = np.minimum(a, b) * (max(int(a.max()), int(b.max())) + 1) + np.maximum(a, b)
    _, inverse, repeats = np.unique(key, return_inverse=True, return_counts=True)
    single = repeats[inverse] == 1
    following = {}
    for u, v in zip(a[single].tolist(), b[single].tolist()):
        following.setdefault(u, []).append(v)
    loops, seen = [], set()
    for start in following:
        if start in seen:
            continue
        loop, u = [], start
        while u not in seen and u in following:
            seen.add(u)
            loop.append(u)
            u = following[u][0]
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def tutte(positions, uv_faces):
    """UVs (N x 2) de cada punto UV. positions: posición 3D de cada punto UV (N x 3). Cada isla sale centrada en el
    origen con un radio proporcional a su tamaño; una isla sin borde (cerrada) se deja como la trae su proyección a
    su plano medio (no se puede aplanar: quien llama la corta antes)."""
    positions = np.asarray(positions, dtype=np.float64)
    count = len(positions)
    uv = np.zeros((count, 2))
    if not len(uv_faces):
        return uv
    a, b = _uv_edges(uv_faces)
    label = _components(count, a, b)
    fixed = np.zeros(count, dtype=bool)
    length = np.linalg.norm(positions[a] - positions[b], axis=1)
    loops_by_island = {}
    for loop in _boundary_loops(a, b):
        loops_by_island.setdefault(int(label[loop[0]]), []).append(loop)
    for island, loops in loops_by_island.items():
        outer = np.array(max(loops, key=lambda loop: np.linalg.norm(
            positions[loop] - positions[np.roll(loop, -1)], axis=1).sum()), dtype=np.int64)
        steps = np.linalg.norm(positions[outer] - positions[np.roll(outer, -1)], axis=1)
        perimeter = max(float(steps.sum()), 1e-12)
        angle = 2.0 * np.pi * (np.concatenate([[0.0], np.cumsum(steps)[:-1]]) / perimeter)
        radius = perimeter / (2.0 * np.pi)
        uv[outer] = radius * np.column_stack([np.cos(angle), np.sin(angle)])
        fixed[outer] = True
    # sistema (D - W) x = 0 en los puntos libres, con los del borde fijos: pesos 1 / longitud (positivos)
    weight = 1.0 / np.maximum(length, 1e-9 * max(float(length.max()), 1e-30))
    i = np.concatenate([a, b])
    j = np.concatenate([b, a])
    w = np.concatenate([weight, weight])
    diagonal = np.maximum(np.bincount(i, weights=w, minlength=count), 1e-30)
    free = ~fixed

    def laplacian(x):
        """(D - W) x, tratando los fijos como cero."""
        values = np.bincount(i, weights=w * x[j], minlength=count)
        return (diagonal * x - values) * free

    for axis in range(2):
        right = np.bincount(i, weights=w * (uv[:, axis] * fixed)[j], minlength=count) * free
        x = np.zeros(count)
        residual = right.copy()
        z = residual / diagonal
        direction = z.copy()
        rho = float(residual @ z)
        goal = (TOLERANCE ** 2) * max(rho, 1e-30)
        for _ in range(MAX_ROUNDS):
            if rho <= goal:
                break
            q = laplacian(direction)
            step = rho / max(float(direction @ q), 1e-30)
            x += step * direction
            residual -= step * q
            z = residual / diagonal
            new_rho = float(residual @ z)
            direction = z + (new_rho / max(rho, 1e-30)) * direction
            rho = new_rho
        uv[free, axis] = x[free]
    return uv
