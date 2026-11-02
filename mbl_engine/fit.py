# -*- coding: utf-8 -*-
"""Paso de ajuste del Motor MBL: acerca el retopo a las zonas del original que ninguna cara cubre.

Pegar los vértices al original (proyectar) deja el error "retopo -> original" casi en cero, pero no
dice nada de lo contrario: puntas de dedos, bordes de plumas o el remate de una cabeza que quedan
fuera de todas las caras. Ese es el error máximo de verdad. Aquí se miran los puntos del original
más lejos del retopo (error "original -> retopo"), se tira de los vértices de la cara más cercana
hasta ellos y se vuelve a pegar al original. Solo se aceptan los movimientos que no dan la vuelta a
ninguna cara ni dejan un quad casi plano, y la ronda se descarta si el error medio sube.

Todo es numpy puro (se prueba sin Maya).
"""

import numpy as np

from . import quadopt
from .closest import make_grid
from .mesh import Mesh
from .metrics import sample_points

# solo se atiende lo que queda más lejos que esta fracción de la diagonal (un cuarto de por ciento)
GAP_FRACTION = 0.0025
# fracción de la distancia que se recorre en cada ronda, y rondas como máximo
STRENGTH = 0.5
ROUNDS = 8
# un vértice no se mueve más que esta fracción de la arista media de sus caras en una ronda
MAX_STEP_EDGES = 0.4
# la normal de una cara no puede girar más que esto (coseno mínimo con la de antes) al mover un vértice
MIN_NORMAL_COS = 0.7
# el área de una cara no puede cambiar más de este factor en una ronda
MAX_AREA_CHANGE = 1.6
# el error medio no puede subir más que esto (fracción) en una ronda
MEAN_TOLERANCE = 0.05


def _quad_normals(points, faces_array):
    """Normal sin normalizar de cada quad (diagonales) y de cada triángulo."""
    p = points[faces_array]
    return np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 1]) if faces_array.shape[1] == 4 else \
        np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])


def _quad_quality(points, array):
    """(¿deformado?, área) de cada quad de `array` (F x 4): ángulos fuera de 40-140 grados o lados 3 veces desiguales."""
    p = points[array]
    prev, nxt = np.roll(p, 1, axis=1), np.roll(p, -1, axis=1)
    u, w = prev - p, nxt - p
    cos = np.einsum("fkj,fkj->fk", u, w) / np.maximum(np.linalg.norm(u, axis=2) * np.linalg.norm(w, axis=2), 1e-30)
    angles = np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))
    edges = np.linalg.norm(nxt - p, axis=2)
    aspect = edges.max(axis=1) / np.maximum(edges.min(axis=1), 1e-30)
    bad = (angles.min(axis=1) < 40.0) | (angles.max(axis=1) > 140.0) | (aspect > 3.0)
    area = 0.5 * np.linalg.norm(np.cross(p, nxt).sum(axis=1), axis=1)
    return bad, area


def _bad_vertices(old, new, faces):
    """Vértices que tocan una cara cuya normal gira demasiado, se queda casi sin área, queda deformada
    (si antes no lo estaba) o cambia de tamaño más del doble."""
    bad = np.zeros(len(new), dtype=bool)
    by_size = {}
    for i, face in enumerate(faces):
        by_size.setdefault(len(face), []).append(i)
    for size, indices in by_size.items():
        if size not in (3, 4):
            continue
        array = np.array([faces[i] for i in indices], dtype=np.int64)
        n_old, n_new = _quad_normals(old, array), _quad_normals(new, array)
        len_old, len_new = np.linalg.norm(n_old, axis=1), np.linalg.norm(n_new, axis=1)
        cos = np.einsum("ij,ij->i", n_old, n_new) / np.maximum(len_old * len_new, 1e-30)
        wrong = (cos < MIN_NORMAL_COS) | (len_new < 0.15 * len_old)
        if size == 4:
            bad_old, area_old = _quad_quality(old, array)
            bad_new, area_new = _quad_quality(new, array)
            ratio = area_new / np.maximum(area_old, 1e-30)
            wrong |= (bad_new & ~bad_old) | (ratio > MAX_AREA_CHANGE) | (ratio < 1.0 / MAX_AREA_CHANGE)
            # tampoco se admite una pareja de quads vecinos que pase a quedar doblada (lámina fina plegada)
            pairs = quadopt.quad_pairs(array)
            made = pairs[quadopt.fold_pairs(n_new, pairs) & ~quadopt.fold_pairs(n_old, pairs)]
            wrong[made.ravel()] = True
        bad[array[wrong].ravel()] = True
    return bad


def _mean_edge(points, faces):
    total, count = np.zeros(len(points)), np.zeros(len(points))
    for face in faces:
        n = len(face)
        for i in range(n):
            a, b = face[i], face[(i + 1) % n]
            length = np.linalg.norm(points[a] - points[b])
            total[a] += length
            total[b] += length
            count[a] += 1
            count[b] += 1
    return total / np.maximum(count, 1.0)


def guarded_project(points, faces, project, locked=None):
    """project(points) sin dejar que un vértice salte de capa: en un hueco estrecho (entre dedos, un
    pliegue) el punto más cercano del original puede estar al otro lado y la cara se dobla. Los vértices
    cuyas caras se deforman, doblan o cambian mucho de tamaño se quedan a medio camino o donde estaban."""
    points = np.asarray(points, dtype=float)
    moved = project(points)
    if locked is not None:
        moved[locked] = points[locked]
    faces = [list(f) for f in faces]
    for guard in range(3):
        bad = _bad_vertices(points, moved, faces)
        if not bad.any():
            break
        moved[bad] = 0.5 * (moved[bad] + points[bad]) if guard < 2 else points[bad]
    return moved


def fit_to_source(points, faces, reference, project, rounds=ROUNDS, gap_fraction=GAP_FRACTION,
                  strength=STRENGTH, locked=None, log=None):
    """Devuelve (puntos ajustados, informe). `reference`: metrics.SurfaceReference del original;
    `project`: función que pega puntos al original (retopo.projector); `locked`: máscara de vértices
    que no se mueven (los de borde, que tienen que seguir en su sitio)."""
    log = log or (lambda message: None)
    points = np.array(points, dtype=float)
    faces = [list(f) for f in faces]
    diagonal = reference.diagonal
    samples = reference.samples
    report = {"rounds": 0, "before": None, "after": None}
    edge = _mean_edge(points, faces)

    def measure(p):
        """(triángulos, punto más cercano, distancia y triángulo de cada muestra del original, y el
        error en los dos sentidos: (media, máximo) como fracción de la diagonal)."""
        mesh = Mesh(p, faces)
        tris, _ = mesh.triangles()
        closest, dist, owner = make_grid(p, tris).closest(samples)
        there = reference.grid.closest(sample_points(mesh, reference.max_points))[1]
        both = np.concatenate([there, dist])
        return tris, closest, dist, owner, (float(both.mean() / diagonal * 100), float(both.max() / diagonal * 100))

    tris, closest, dist, owner, error = measure(points)
    report["before"] = error
    for _ in range(int(rounds)):
        far = np.nonzero(dist > gap_fraction * diagonal)[0]
        if not len(far):
            break
        # el triángulo más cercano de cada punto lejano y sus coordenadas baricéntricas
        corners = tris[owner[far]]
        a, b, c = (points[corners[:, k]] for k in range(3))
        ab, ac, ap = b - a, c - a, closest[far] - a
        d00, d01, d11 = np.einsum("ij,ij->i", ab, ab), np.einsum("ij,ij->i", ab, ac), np.einsum("ij,ij->i", ac, ac)
        d20, d21 = np.einsum("ij,ij->i", ap, ab), np.einsum("ij,ij->i", ap, ac)
        denom = np.maximum(d00 * d11 - d01 * d01, 1e-30)
        v = (d11 * d20 - d01 * d21) / denom
        w = (d00 * d21 - d01 * d20) / denom
        weights = np.clip(np.stack([1.0 - v - w, v, w], axis=1), 0.0, 1.0)
        weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
        gap = samples[far] - closest[far]
        norm = np.maximum((weights ** 2).sum(axis=1), 1e-6)
        push = np.zeros_like(points)
        mass = np.zeros(len(points))
        for k in range(3):
            np.add.at(push, corners[:, k], (weights[:, k] / norm)[:, None] * gap)
            np.add.at(mass, corners[:, k], weights[:, k] ** 2 / norm)
        step = strength * push / np.maximum(mass, 1e-12)[:, None]
        # un vértice no avanza más que una arista por ronda
        length = np.linalg.norm(step, axis=1)
        limit = MAX_STEP_EDGES * edge
        step *= np.minimum(1.0, limit / np.maximum(length, 1e-30))[:, None]
        step[mass == 0] = 0.0
        if locked is not None:
            step[locked] = 0.0
        moved = project(points + step)
        if locked is not None:
            moved[locked] = points[locked]
        for _guard in range(3):
            bad = _bad_vertices(points, moved, faces)
            if not bad.any():
                break
            moved[bad] = 0.5 * (moved[bad] + points[bad]) if _guard < 2 else points[bad]
        new_tris, new_closest, new_dist, new_owner, new_error = measure(moved)
        if new_error[0] > (1.0 + MEAN_TOLERANCE) * error[0] or new_error[1] >= error[1] * 0.999:
            # esta ronda no mejora el máximo (en los dos sentidos) o empeora la media: se deja como estaba
            break
        points, tris, closest, dist, owner, error = moved, new_tris, new_closest, new_dist, new_owner, new_error
        report["rounds"] += 1
    report["after"] = error
    log("ajuste: {} rondas, error máximo {:.2f}% -> {:.2f}%".format(
        report["rounds"], report["before"][1], report["after"][1]))
    return points, report
