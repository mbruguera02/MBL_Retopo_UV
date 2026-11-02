# -*- coding: utf-8 -*-
"""Grosor del original para el Motor MBL: qué piezas son más finas que la arista del retopo.

Una arista del retopo más grande que la pieza donde cae (un dedo, una pluma, el borde de un ala) la
aplasta o la pierde. Aquí se mide el grosor de cada vértice (thickness_field) y se engordan las piezas
finas, solo para el motor, hasta que quepa una arista (inflate); luego los vértices se pegan al original.

Todo es numpy puro (se prueba sin Maya).
"""

import numpy as np

from . import orient
from .closest import make_grid
# pasos de la búsqueda de grosor, como múltiplos de la arista normal
THICKNESS_STEPS = (0.1, 0.18, 0.3, 0.5, 0.8, 1.2)
# la bola del paso s ya toca otra parte de la superficie si su distancia es menor que esto * s
THICKNESS_TOUCH = 0.6
# al engordar, las piezas con menos de INFLATE_RATIO aristas de grosor se llevan a ese grosor, sin
# pasar de INFLATE_GAP_FRACTION del hueco libre que tienen delante; INFLATE_SMOOTH vueltas de suavizado
INFLATE_RATIO = 1.5
INFLATE_GAP_FRACTION = 0.4
INFLATE_SMOOTH = 2
# el grosor se mide en una versión reducida de la malla con esta cantidad de triángulos por cara pedida
# (con 6, la arista de la reducida es ~0,6 de la del retopo y ve todo lo que importa)
PROBE_PER_TARGET_FACE = 6
# al pasar el grosor de la malla reducida a la completa, "no hay nada cerca" vale THICKNESS_FAR aristas
THICKNESS_FAR = 8.0


def base_edge(points, tris, target_faces):
    """Arista normal: la de un retopo de `target_faces` quads de tamaño uniforme."""
    points = np.asarray(points, dtype=float)
    tris = np.asarray(tris, dtype=np.int64)
    a, b, c = points[tris[:, 0]], points[tris[:, 1]], points[tris[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum()
    return float(np.sqrt(area / max(int(target_faces), 1)))


def thickness_sides(points, tris, normals, edge, steps=THICKNESS_STEPS, touch=THICKNESS_TOUCH, grid=None):
    """(fuera, dentro): distancia de cada vértice a la otra parte de la superficie por delante de la
    normal (el hueco hasta lo que tiene delante) y por detrás (el grosor de la pieza); inf si es
    mayor que steps[-1] * edge. Con las normales hacia fuera, `dentro` es el grosor.

    Se avanza `s` por la normal y se mira la distancia d del punto a la superficie: mientras el punto
    sigue más cerca de su propio vértice que de nada, d = s; cuando otra parte lo supera (d < touch * s)
    la separación entre las dos es de s + d."""
    points = np.asarray(points, dtype=float)
    grid = grid or make_grid(points, tris)
    sides = []
    for sign in (1.0, -1.0):
        result = np.full(len(points), np.inf)
        open_ = np.ones(len(points), dtype=bool)
        for k in steps:
            s = k * edge
            idx = np.nonzero(open_)[0]
            if not len(idx):
                break
            d = grid.closest(points[idx] + sign * s * normals[idx])[1]
            hit = d < touch * s
            found = idx[hit]
            result[found] = s + d[hit]
            open_[found] = False
        sides.append(result)
    return sides[0], sides[1]


def thickness(points, tris, normals, edge, steps=THICKNESS_STEPS, touch=THICKNESS_TOUCH, grid=None):
    """Distancia a la otra parte de la superficie por cualquiera de los dos lados (inf si no hay)."""
    outside, inside = thickness_sides(points, tris, normals, edge, steps, touch, grid)
    return np.minimum(outside, inside)


def outward_normals(points, tris):
    """Normales por vértice hacia fuera: si el volumen con signo de la malla es negativo, se dan la vuelta."""
    tris = np.asarray(tris, dtype=np.int64)
    normals = orient.vertex_normals(points, tris)
    a, b, c = points[tris[:, 0]], points[tris[:, 1]], points[tris[:, 2]]
    volume = np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0
    return -normals if volume < 0 else normals


def thickness_field(points, tris, edge, probe=None):
    """Lo que hace falta para engordar, que no depende de los ajustes: (normales hacia fuera, hueco por
    delante, grosor por detrás, aristas) de la malla. Se mide una vez y sirve para todas las variantes.

    probe: (puntos, triángulos) de una versión reducida de la malla donde medir (en cada vértice de una
    de 100.000 triángulos tarda un minuto; en una de 30.000, unos segundos); se pasa a la malla completa."""
    points = np.asarray(points, dtype=float)
    tris = np.asarray(tris, dtype=np.int64)
    normals = outward_normals(points, tris)
    if probe is None:
        outside, inside = thickness_sides(points, tris, normals, edge)
    else:
        probe_points, probe_tris = np.asarray(probe[0], dtype=float), np.asarray(probe[1], dtype=np.int64)
        probe_normals = outward_normals(probe_points, probe_tris)
        far = THICKNESS_FAR * edge       # inf no se puede interpolar
        measured = [np.minimum(side, far) for side in thickness_sides(probe_points, probe_tris, probe_normals, edge)]
        outside, inside = (transfer(probe_points, probe_tris, side, points) for side in measured)
    _, edges = orient._edges(tris.tolist())
    return {"normals": normals, "outside": outside, "inside": inside, "edges": edges, "edge": edge}


def inflation_shift(field, thickness_ratio=INFLATE_RATIO, gap_fraction=INFLATE_GAP_FRACTION,
                    smooth=INFLATE_SMOOTH):
    """Cuánto hay que engordar cada vértice (>= 0, en unidades del modelo) para que la pieza donde cae
    tenga al menos thickness_ratio * edge de grosor: (grosor_pedido - grosor) / 2, sin pasar de
    gap_fraction del hueco libre que tiene delante, y suavizado `smooth` vueltas."""
    wanted = thickness_ratio * field["edge"]
    shift = np.where(field["inside"] < wanted, 0.5 * (wanted - field["inside"]), 0.0)
    shift = np.minimum(shift, gap_fraction * field["outside"])
    a, b = field["edges"][:, 0], field["edges"][:, 1]
    degree = np.bincount(np.concatenate([a, b]), minlength=len(shift)).astype(float)
    for _ in range(int(smooth)):
        acc = shift.copy()
        np.add.at(acc, a, shift[b])
        np.add.at(acc, b, shift[a])
        shift = acc / (degree + 1.0)
    return shift


def transfer(source_points, source_tris, values, queries):
    """Valor en cada punto de `queries`: el de su triángulo más cercano de la malla de origen
    (ponderado por cercanía a sus tres vértices). Pasa un campo de una malla reducida a la completa."""
    source_points = np.asarray(source_points, dtype=float)
    source_tris = np.asarray(source_tris, dtype=np.int64)
    closest, _, owner = make_grid(source_points, source_tris).closest(queries)
    corners = source_tris[owner]
    weights = 1.0 / (np.linalg.norm(source_points[corners] - closest[:, None, :], axis=2) + 1e-12)
    weights /= weights.sum(axis=1, keepdims=True)
    return (np.asarray(values)[corners] * weights).sum(axis=1)


def inflate(points, tris, edge, thickness_ratio=INFLATE_RATIO, gap_fraction=INFLATE_GAP_FRACTION,
            smooth=INFLATE_SMOOTH, probe=None, field=None):
    """Copia de `points` con las piezas más finas que thickness_ratio * edge engordadas por la normal.

    Los motores pierden o aplastan lo que es más fino que su arista (dedos, plumas). El motor recibe
    esta versión engordada, solo para decidir la topología; los vértices que saca se pegan después a
    la superficie original, así que la forma no cambia.

    field: el campo de thickness_field() si ya se midió (varias variantes); si no, se mide aquí (con `probe`).
    Devuelve (puntos, informe)."""
    points = np.asarray(points, dtype=float)
    field = field or thickness_field(points, tris, edge, probe)
    shift = inflation_shift(field, thickness_ratio, gap_fraction, smooth)
    moved = shift > 1e-3 * edge
    return points + field["normals"] * shift[:, None], {"inflated_percent": float(100.0 * moved.mean()),
                                                         "inflate_max": float(shift.max() / edge)}
