# -*- coding: utf-8 -*-
"""Estiramiento y densidad de texel POR ISLA, sin Maya: solo arrays (numpy).

Con las mismas definiciones que `uv_quality.report` (distorsión de área de cada cara respecto a la densidad común de toda
la malla, tope de 4 en la media y umbral de 1,5x). Sirve para saber qué islas estiran y dónde, no corrige nada.
"""

import numpy as np

from . import uv_quality

# una isla es "mala" si más de esta fracción de sus caras pasa de 1,5x, o su estiramiento medio pasa de ISLAND_MEAN
ISLAND_BAD_FRACTION = 0.10
ISLAND_MEAN = 1.30


def face_stretch(points, faces, uv_points, uv_faces):
    """(distorsión de área de cada cara, isla de cada cara). 1 = perfecto; -1 de isla = cara sin UVs."""
    world = uv_quality.face_areas(points, faces)
    flat = np.abs(uv_quality.uv_face_areas(uv_points, uv_faces)[0])
    island, _ = uv_quality.islands(uv_faces)
    ok = (world > 0) & (island >= 0)
    reference = flat[ok].sum() / max(world[ok].sum(), 1e-300)
    ratio = np.ones(len(world))
    ratio[ok] = flat[ok] / world[ok] / max(reference, 1e-300)
    distortion = np.where(ratio > 0, np.maximum(ratio, 1.0 / np.maximum(ratio, 1e-300)), 10.0)
    return distortion, island


def island_stats(points, faces, uv_points, uv_faces):
    """Una fila por isla: {"island", "faces", "mean" (estiramiento medio, ponderado por área 3D), "over_percent"
    (% de caras > 1,5x), "texel" (densidad relativa a la media de la malla), "bad" (pasa de los umbrales)}."""
    distortion, island = face_stretch(points, faces, uv_points, uv_faces)
    world = uv_quality.face_areas(points, faces)
    flat = np.abs(uv_quality.uv_face_areas(uv_points, uv_faces)[0])
    ok = island >= 0
    reference = np.sqrt(flat[ok].sum() / max(world[ok].sum(), 1e-300))
    rows = []
    for k in range(int(island.max()) + 1 if ok.any() else 0):
        members = island == k
        w = world[members]
        mean = float((np.minimum(distortion[members], uv_quality.STRETCH_CAP) * w).sum() / max(w.sum(), 1e-300))
        over = float((distortion[members] > uv_quality.STRETCH_BAD).mean())
        texel = float(np.sqrt(flat[members].sum() / max(w.sum(), 1e-300)) / max(reference, 1e-300))
        rows.append({"island": k, "faces": int(members.sum()), "mean": mean, "over_percent": 100.0 * over,
                     "texel": texel, "bad": over > ISLAND_BAD_FRACTION or mean > ISLAND_MEAN})
    return rows
