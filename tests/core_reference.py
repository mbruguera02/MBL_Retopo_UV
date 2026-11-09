# -*- coding: utf-8 -*-
"""Cifras de una malla de referencia (la retopología de un artista), para saber a qué tiene que llegar el motor nativo.

    python tests/core_reference.py malla.obj [salida.json]

- Juez del motor actual (`mbl_engine.quality.report`): forma, uniformidad, defectos, loops, polos.
- Polos por valencia.
- Parches: desde cada polo se siguen sus aristas en línea recta (por vértices de 4 aristas) hasta otro polo, un borde o una
  línea ya trazada. Esas líneas parten la malla en parches de cuatro lados: es la estructura que el motor por parches tiene
  que reproducir.

Sin Maya: solo numpy.
"""

import collections
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mbl_engine import quality  # noqa: E402
from mbl_engine.mesh import Mesh  # noqa: E402


def read_obj(path):
    points, faces = [], []
    with open(path, errors="replace") as handle:
        for line in handle:
            if line.startswith("v "):
                points.append([float(x) for x in line.split()[1:4]])
            elif line.startswith("f "):
                faces.append([int(w.split("/")[0]) - 1 for w in line.split()[1:]])
    return np.array(points), faces


def topology(faces):
    edges = collections.defaultdict(list)
    for f, verts in enumerate(faces):
        for k in range(len(verts)):
            a, b = verts[k], verts[(k + 1) % len(verts)]
            edges[(min(a, b), max(a, b))].append(f)
    vertex_edges = collections.defaultdict(list)
    for edge in edges:
        vertex_edges[edge[0]].append(edge)
        vertex_edges[edge[1]].append(edge)
    border = {v for edge, owners in edges.items() if len(owners) == 1 for v in edge}
    return edges, vertex_edges, border


def straight_on(edges, vertex_edges, edge, v):
    """Arista que sigue en línea recta a `edge` al pasar por `v` (el vértice debe tener 4 aristas y ser interior)."""
    if len(vertex_edges[v]) != 4:
        return None
    around = set(edges[edge])
    for other in vertex_edges[v]:
        if other != edge and not around.intersection(edges[other]):
            return other
    return None


def patches(faces):
    """(nº de parches, nº de líneas trazadas, aristas trazadas)."""
    edges, vertex_edges, border = topology(faces)
    poles = [v for v in vertex_edges if v not in border and len(vertex_edges[v]) != 4]
    traced = set(e for e, owners in edges.items() if len(owners) == 1)
    lines = 0
    for pole in poles:
        for start in vertex_edges[pole]:
            if start in traced:
                continue
            lines += 1
            edge, v = start, pole
            while edge is not None and edge not in traced:
                traced.add(edge)
                v = edge[1] if edge[0] == v else edge[0]
                if v in border or len(vertex_edges[v]) != 4:
                    break
                edge = straight_on(edges, vertex_edges, edge, v)
    parent = list(range(len(faces)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for edge, owners in edges.items():
        if edge not in traced and len(owners) == 2:
            parent[find(owners[0])] = find(owners[1])
    sizes = collections.Counter(find(f) for f in range(len(faces)))
    return len(sizes), lines, sizes


def main():
    points, faces = read_obj(sys.argv[1])
    mesh = Mesh(points, faces)
    rep = quality.report(mesh)
    edges, vertex_edges, border = topology(faces)
    valence = collections.Counter(len(vertex_edges[v]) for v in vertex_edges if v not in border)
    count, lines, sizes = patches(faces)
    big = sorted(sizes.values(), reverse=True)
    result = {
        "faces": len(faces), "sides": dict(collections.Counter(len(f) for f in faces)),
        "poles_by_valence": {str(k): n for k, n in sorted(valence.items()) if k != 4},
        "patches": count, "traced_lines": lines,
        "faces_per_patch_median": float(np.median(big)), "single_face_patches": sum(1 for s in big if s == 1),
        "quality": {k: rep[k] for k in ("shape", "uniformity", "defects", "flow", "poles", "quad_percent")},
    }
    text = json.dumps(result, indent=1, default=float)
    print(text)
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w") as handle:
            handle.write(text)


if __name__ == "__main__":
    main()
