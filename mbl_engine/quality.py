# -*- coding: utf-8 -*-
"""Calidad de una topología de quads: lo que se ve, no solo el error de forma.

El error de forma, los polos y el % de quads no dicen si una malla sirve para trabajar UVs: un retopo
puede ajustarse al original y aun así tener caras cizalladas, tamaños que saltan de una cara a la
vecina, triángulos o agujeros en los huecos estrechos y anillos que no se cierran. Aquí se miden esas
cosas, con umbrales, para elegir entre resultados y descartar los malos.

- shape: quads deformados (ángulos muy agudos o abiertos, lados desiguales).
- uniformity: tamaño de cada cara frente a sus vecinas.
- defects: caras dadas la vuelta o dobladas respecto al original, agujeros nuevos, triángulos.
- flow: loops cerrados y su longitud (los anillos alrededor de brazos, dedos...).
- symmetry: reflejo del resultado y bucle central.
- grade(): nota (0 bien, 1 revisar, 2 malo) con las razones.

Todo es numpy puro (se prueba sin Maya).
"""

import numpy as np

from . import metrics, symmetry
from .mesh import Mesh

# quad deformado: ángulo interior fuera de este rango o lado mayor / lado menor por encima del tope
BAD_ANGLE = (40.0, 140.0)
BAD_ASPECT = 3.0
# par de caras vecinas con tamaños (área) que difieren más que este factor
SIZE_JUMP = 3.0
# cara doblada: sus normal y la de una vecina forman un ángulo mayor que esto (grados)
FOLD_ANGLE = 120.0
FOLD_COS = float(np.cos(np.radians(FOLD_ANGLE)))
# cara dada la vuelta: su normal frente a la del original en el punto más cercano
FLIP_COS = 0.0


def _face_normals(points, faces):
    """Normal unitaria de cada cara (diagonales para quads, Newell para el resto)."""
    normals = np.zeros((len(faces), 3))
    for k in {len(f) for f in faces}:
        index = np.array([i for i, f in enumerate(faces) if len(f) == k])
        p = points[np.array([faces[i] for i in index])]
        n = np.cross(p, np.roll(p, -1, axis=1)).sum(axis=1)
        normals[index] = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
    return normals


def quad_shape(points, quads):
    """(deformado, ángulos F x 4, proporción de lados) de cada quad de `quads` (F x 4 índices)."""
    p = points[quads]                                                      # F x 4 x 3
    prev, nxt = np.roll(p, 1, axis=1), np.roll(p, -1, axis=1)
    u, w = prev - p, nxt - p
    cos = np.einsum("fkj,fkj->fk", u, w) / np.maximum(np.linalg.norm(u, axis=2) * np.linalg.norm(w, axis=2), 1e-30)
    angles = np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))
    edges = np.linalg.norm(nxt - p, axis=2)
    aspect = edges.max(axis=1) / np.maximum(edges.min(axis=1), 1e-30)
    bad = (angles.min(axis=1) < BAD_ANGLE[0]) | (angles.max(axis=1) > BAD_ANGLE[1]) | (aspect > BAD_ASPECT)
    return bad, angles, aspect


def deformed_quads(points, quads):
    """Máscara de los quads deformados (ángulos muy agudos o abiertos, lados desiguales)."""
    return quad_shape(points, quads)[0]


def shape_stats(mesh):
    """% de quads deformados y medias: ángulo mínimo, ángulo máximo y proporción de lados."""
    faces = [f for f in mesh.faces() if len(f) == 4]
    if not faces:
        return {"bad_percent": 0.0, "angle_min": 90.0, "angle_max": 90.0, "aspect_mean": 1.0}
    bad, angles, aspect = quad_shape(mesh.points, np.array(faces))
    return {"bad_percent": 100.0 * float(bad.mean()), "angle_min": float(angles.min(axis=1).mean()),
            "angle_max": float(angles.max(axis=1).mean()), "aspect_mean": float(aspect.mean())}


def _neighbor_pairs(mesh):
    """Pares de caras que comparten una arista (P x 2)."""
    owners = {}
    for i, face in enumerate(mesh.faces()):
        n = len(face)
        for k in range(n):
            a, b = face[k], face[(k + 1) % n]
            owners.setdefault((a, b) if a < b else (b, a), []).append(i)
    return np.array([o for o in owners.values() if len(o) == 2], dtype=np.int64).reshape(-1, 2)


def uniformity_stats(mesh, pairs=None):
    """% de pares de caras vecinas cuyo tamaño (área) salta más de SIZE_JUMP veces, y variación local."""
    pairs = _neighbor_pairs(mesh) if pairs is None else pairs
    if not len(pairs):
        return {"jump_percent": 0.0, "log_ratio_mean": 0.0}
    areas = np.maximum(mesh.face_areas, 1e-30)
    ratio = np.maximum(areas[pairs[:, 0]], areas[pairs[:, 1]]) / np.minimum(areas[pairs[:, 0]], areas[pairs[:, 1]])
    return {"jump_percent": 100.0 * float((ratio > SIZE_JUMP).mean()), "log_ratio_mean": float(np.log(ratio).mean())}


def defect_stats(mesh, source=None, grid=None, source_loops=None, pairs=None):
    """Defectos visibles: caras dobladas, dadas la vuelta (frente al original), agujeros nuevos y triángulos."""
    pairs = _neighbor_pairs(mesh) if pairs is None else pairs
    faces = mesh.faces()
    normals = _face_normals(mesh.points, faces)
    folded = 0
    if len(pairs):
        dots = np.einsum("ij,ij->i", normals[pairs[:, 0]], normals[pairs[:, 1]])
        folded = int((dots < FOLD_COS).sum())
    flipped = 0
    if source is not None:
        grid = grid or metrics.make_grid(source.points, source.triangles()[0])
        _, _, owner = grid.closest(mesh.face_centers())
        stris = source.triangles()[0]
        sp = source.points
        sn = np.cross(sp[stris[:, 1]] - sp[stris[:, 0]], sp[stris[:, 2]] - sp[stris[:, 0]])
        sn /= np.maximum(np.linalg.norm(sn, axis=1, keepdims=True), 1e-30)
        flipped = int((np.einsum("ij,ij->i", normals, sn[owner]) < FLIP_COS).sum())
        if source_loops is None:
            source_loops = len(source.boundary_loops())
    holes = max(len(mesh.boundary_loops()) - (source_loops or 0), 0) if source_loops is not None else len(mesh.boundary_loops())
    sizes = mesh.face_sizes
    return {"folded": folded, "flipped": flipped, "new_holes": holes, "tris": int((sizes == 3).sum()),
            "ngons": int((sizes > 4).sum()), "faces": mesh.face_count}


def symmetry_stats(mesh):
    """Error de reflejo (% de la diagonal) y si hay bucle central: fracción de aristas sobre el plano de simetría."""
    used = mesh.points[mesh.used_vertices]
    if not len(used):
        return {"mirror_percent": 0.0, "center_edges": 0.0}
    center = symmetry.plane_x(used)
    error = symmetry.mirror_error(mesh)
    edge = float(np.median(np.linalg.norm(mesh.points[mesh.edges[:, 0]] - mesh.points[mesh.edges[:, 1]], axis=1)))
    on_plane = np.abs(mesh.points[:, 0] - center) < 0.1 * edge
    both = on_plane[mesh.edges[:, 0]] & on_plane[mesh.edges[:, 1]]
    return {"mirror_percent": float(error[0]) if isinstance(error, tuple) else float(error),
            "center_edges": float(both.sum()) / max(float(len(mesh.edges)) ** 0.5, 1.0)}


def report(mesh, source=None, grid=None):
    """Todas las cifras de calidad de `mesh` (frente a `source` si se da)."""
    pairs = _neighbor_pairs(mesh)
    topo = metrics.topology_stats(mesh)
    loops = metrics.loop_stats(mesh)
    return {"shape": shape_stats(mesh), "uniformity": uniformity_stats(mesh, pairs),
            "defects": defect_stats(mesh, source, grid, pairs=pairs),
            "flow": {"loop_mean": loops["loop_mean"], "closed_percent": loops["loop_closed_percent"]},
            "poles": topo["pole_count"], "quad_percent": topo["quad_percent"], "faces": topo["faces"],
            "symmetry": symmetry_stats(mesh)}


# umbral (revisar, malo) de cada cifra; provisionales hasta calibrarlos con modelos de buena topología
LIMITS = {
    "bad_shape_percent": (3.0, 10.0),      # quads deformados
    "size_jump_percent": (5.0, 15.0),      # saltos de tamaño entre vecinas
    "flipped_percent": (0.05, 0.5),        # caras dadas la vuelta
    "folded_percent": (0.05, 0.5),         # caras dobladas
    "tri_percent": (1.0, 5.0),             # triángulos
}


def defect_score(shape, uniformity, defects, faces):
    """Un número que sube con lo feo (0 = limpio): quads deformados, saltos de tamaño y caras dadas la vuelta o
    dobladas, cada uno en su escala (5 %, 3 % y 1 % de las caras valen 1 punto)."""
    faces = max(faces, 1)
    return (shape["bad_percent"] / 5.0 + uniformity["jump_percent"] / 3.0
            + 100.0 * (defects["flipped"] + defects["folded"]) / faces / 1.0
            + 5.0 * defects["new_holes"])


def grade(rep):
    """(nota, razones): 0 bien, 1 revisar, 2 malo. Un solo defecto malo basta para la nota 2."""
    faces = max(rep["faces"], 1)
    d = rep["defects"]
    values = {
        "bad_shape_percent": rep["shape"]["bad_percent"],
        "size_jump_percent": rep["uniformity"]["jump_percent"],
        "flipped_percent": 100.0 * d["flipped"] / faces,
        "folded_percent": 100.0 * d["folded"] / faces,
        "tri_percent": 100.0 * (d["tris"] + d["ngons"]) / faces,
    }
    names = {"bad_shape_percent": "quads deformados", "size_jump_percent": "saltos de tamaño entre caras vecinas",
             "flipped_percent": "caras dadas la vuelta", "folded_percent": "caras dobladas",
             "tri_percent": "triángulos o n-gons"}
    level, reasons = 0, []
    for key, value in values.items():
        review, bad = LIMITS[key]
        if value >= bad:
            level = 2
            reasons.append("{}: {:.2f}%".format(names[key], value))
        elif value >= review:
            level = max(level, 1)
            reasons.append("{}: {:.2f}%".format(names[key], value))
    if d["new_holes"]:
        level = 2
        reasons.append("{} agujeros nuevos".format(d["new_holes"]))
    return level, reasons
