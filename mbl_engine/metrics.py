# -*- coding: utf-8 -*-
"""Métricas de un retopo sin Maya: topología, polos, edge loops y error de forma.

Mismos criterios que mbl_retopo_uv/diagnostics.py (la versión con la API de Maya), para que las
cifras de antes y de ahora se puedan comparar.
"""

import numpy as np

from .closest import make_grid
from .mesh import Mesh


def topology_stats(mesh):
    """Caras por tipo, polos y % de vértices regulares.

    Regular = vértice interior con 4 aristas o de borde con 3. Polo = vértice interior con otro
    número de aristas (los de borde no cuentan como polo). Solo cuentan los vértices usados.
    """
    sizes = mesh.face_sizes
    tris = int(np.sum(sizes == 3))
    quads = int(np.sum(sizes == 4))
    ngons = int(np.sum(sizes > 4))
    valence = mesh.valence
    used = mesh.used_vertices
    border = mesh.boundary_vertices
    interior = used & ~border
    regular = int(np.sum(used & border & (valence == 3)) + np.sum(interior & (valence == 4)))
    poles = {
        3: np.nonzero(interior & (valence <= 3))[0].tolist(),
        5: np.nonzero(interior & (valence == 5))[0].tolist(),
        6: np.nonzero(interior & (valence >= 6))[0].tolist(),
    }
    faces = mesh.face_count
    total = int(used.sum())
    return {
        "faces": faces,
        "tris": tris,
        "quads": quads,
        "ngons": ngons,
        "quad_percent": 100.0 * quads / faces if faces else 0.0,
        "vertices": total,
        "regular_percent": 100.0 * regular / total if total else 0.0,
        "poles": poles,
        "pole_count": sum(len(v) for v in poles.values()),
    }


def loop_stats(faces):
    """Edge loops: cadenas de aristas que siguen recto por vértices de 4 aristas.

    Un loop se corta al llegar a un polo o a un triángulo; por un borde abierto sigue por la otra
    arista de borde. Cuanto más largos, mejor sigue la malla la forma: un buen retopo tiene loops
    largos y cerrados (anillos alrededor de brazos, ojos...). `faces`: Mesh o lista de caras.
      loop_mean: longitud media ponderada por aristas (sum L² / sum L), en aristas.
      loop_closed_percent: % de aristas que están en un loop cerrado.
      loops: nº de cadenas.
    """
    if isinstance(faces, Mesh):
        faces = faces.faces()
    neighbors, adjacent, edge_faces = {}, {}, {}
    for face in faces:
        n = len(face)
        for i, v in enumerate(face):
            a, b = face[i - 1], face[(i + 1) % n]
            neighbors.setdefault(v, set()).update((a, b))
            pairs = adjacent.setdefault(v, {})
            pairs.setdefault(a, set()).add(b)
            pairs.setdefault(b, set()).add(a)
            if n != 4:
                edge_faces.setdefault(frozenset((v, b)), []).append(None)  # marca: arista de un no-quad
            edge_faces.setdefault(frozenset((v, b)), []).append(n)

    def straight(v, came_from):
        """Siguiente vértice recto al cruzar v viniendo de came_from, o None."""
        near = neighbors[v]
        if len(near) == 3 and len(edge_faces[frozenset((v, came_from))]) == 1:
            # borde abierto: el loop sigue por la otra arista de borde (vértice regular de borde)
            ahead = [w for w in near if w != came_from and len(edge_faces[frozenset((v, w))]) == 1]
            return ahead[0] if len(ahead) == 1 else None
        if len(near) != 4:
            return None
        if any(len(edge_faces.get(frozenset((v, w)), ())) != 2 or None in edge_faces[frozenset((v, w))]
               or 4 not in edge_faces[frozenset((v, w))] for w in near):
            return None
        opposite = near - adjacent[v][came_from] - {came_from}
        return next(iter(opposite)) if len(opposite) == 1 else None

    seen = set()
    lengths, closed_edges = [], 0
    for edge in edge_faces:
        if edge in seen:
            continue
        a, b = tuple(edge)
        seen.add(edge)
        length, closed = 1, False
        for start, nxt in ((a, b), (b, a)):
            prev, cur = start, nxt
            while True:
                step = straight(cur, prev)
                if step is None:
                    break
                e = frozenset((cur, step))
                if e in seen:
                    closed = e == edge or closed
                    break
                seen.add(e)
                length += 1
                prev, cur = cur, step
            if closed:
                break
        lengths.append(length)
        if closed:
            closed_edges += length
    total = sum(lengths)
    return {
        "loops": len(lengths),
        "loop_mean": sum(l * l for l in lengths) / float(total) if total else 0.0,
        "loop_closed_percent": 100.0 * closed_edges / total if total else 0.0,
    }


def sample_points(mesh, max_points):
    """Vértices usados y centros de cara, submuestreados de forma uniforme (como
    diagnostics._sample_points; en Maya no hay vértices sin cara, aquí puede haberlos)."""
    points = np.concatenate([mesh.points[mesh.used_vertices], mesh.face_centers()])
    if len(points) > max_points:
        step = len(points) / float(max_points)
        points = points[(np.arange(max_points) * step).astype(np.int64)]
    return points


class SurfaceReference(object):
    """Error de forma frente a un original, con la rejilla del original construida una sola vez
    (varias variantes de retopo se miden contra el mismo original)."""

    def __init__(self, source, max_points=30000, grid=None):
        self.source = source
        self.max_points = max_points
        self.grid = grid or make_grid(source.points, source.triangles()[0])
        self.samples = sample_points(source, max_points)
        self.diagonal = source.diagonal()

    def distances(self, target):
        """(there, back): distancia de cada punto del retopo al original y de cada punto del original
        al retopo. `back` va con los puntos de self.samples, `there` con sample_points(target)."""
        there = self.grid.closest(sample_points(target, self.max_points))[1]
        back = make_grid(target.points, target.triangles()[0]).closest(self.samples)[1]
        return there, back

    def error(self, target):
        """Distancia entre superficies en los dos sentidos (retopo -> original y original -> retopo)."""
        there, back = self.distances(target)
        both = np.concatenate([there, back])
        max_error = float(both.max()) if len(both) else 0.0
        mean_error = float(both.mean()) if len(both) else 0.0
        return {
            "max": max_error,
            "mean": mean_error,
            "max_percent": 100.0 * max_error / self.diagonal,
            "mean_percent": 100.0 * mean_error / self.diagonal,
            "diagonal": self.diagonal,
        }


def surface_error(source, target, max_points=30000):
    """Distancia entre superficies en los dos sentidos (retopo -> original y original -> retopo).

    Es la medida estándar de fidelidad de forma (distancia de Hausdorff). Porcentajes respecto a la
    diagonal de la caja del original. source y target: Mesh.
    """
    return SurfaceReference(source, max_points).error(target)
