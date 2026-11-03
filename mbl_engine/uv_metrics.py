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


# ------------------------------------------------------------------ corrección de una isla

# una corrección se acepta si la isla gana al menos esta fracción de sus caras > 1,5x (o tantas como pide `gain`) sin que su
# estiramiento medio suba más que MEAN_TOLERANCE
MEAN_TOLERANCE = 0.01


def own_stretch(world, flat):
    """(estiramiento medio, nº de caras > 1,5x) de un grupo de caras con SU densidad como referencia (el empaquetado final
    deja a todas las islas con la misma densidad, así que no importa a qué escala estén ahora)."""
    ok = world > 0
    if not ok.any() or flat[ok].sum() <= 0:
        return 0.0, 0
    ratio = (flat[ok] / world[ok]) / (flat[ok].sum() / world[ok].sum())
    distortion = np.where(ratio > 0, np.maximum(ratio, 1.0 / np.maximum(ratio, 1e-300)), 10.0)
    mean = float((np.minimum(distortion, uv_quality.STRETCH_CAP) * world[ok]).sum() / world[ok].sum())
    return mean, int((distortion > uv_quality.STRETCH_BAD).sum())


def improved(before, after, gain=0.0, tolerance=MEAN_TOLERANCE):
    """¿Mejora la isla? `before` y `after` son (media, caras > 1,5x) de own_stretch. Tiene que perder caras estiradas (al
    menos una, o la fracción `gain` de las que tenía) sin subir la media más que `tolerance`."""
    if after[0] > before[0] + tolerance:
        return False
    return before[1] - after[1] >= max(1, int(np.ceil(gain * before[1])))


def cut_path(points, faces, face_ids, start_face):
    """Aristas (pares de vértices) del camino más corto, por las aristas de las caras `face_ids`, desde un vértice de
    `start_face` hasta la orilla de esa zona (una arista de una sola cara dentro del grupo). [] si no hay camino que valga
    (la cara ya toca la orilla, o la zona no tiene orilla: una isla cerrada)."""
    import heapq
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    count = {}
    for f in face_ids:
        face = faces[f]
        for k in range(len(face)):
            a, b = face[k], face[(k + 1) % len(face)]
            key = (a, b) if a < b else (b, a)
            count[key] = count.get(key, 0) + 1
    border = set()
    graph = {}
    for (a, b), n in count.items():
        if n == 1:
            border.update((a, b))
        graph.setdefault(a, []).append(b)
        graph.setdefault(b, []).append(a)
    starts = [v for v in faces[start_face] if v not in border]
    if not starts or not border:
        return []
    # el vértice de la cara más lejos de la orilla da el corte más largo y más útil: se prueba el más cercano a su centro
    centre = pts[list(faces[start_face])].mean(axis=0)
    start = min(starts, key=lambda v: float(np.linalg.norm(pts[v] - centre)))
    dist, parent, queue = {start: 0.0}, {}, [(0.0, start)]
    while queue:
        d, v = heapq.heappop(queue)
        if d > dist.get(v, 1e300):
            continue
        if v in border:
            path = []
            while v != start:
                path.append((parent[v], v))
                v = parent[v]
            return path
        for w in graph[v]:
            nd = d + float(np.linalg.norm(pts[v] - pts[w]))
            if nd < dist.get(w, 1e300):
                dist[w], parent[w] = nd, v
                heapq.heappush(queue, (nd, w))
    return []
