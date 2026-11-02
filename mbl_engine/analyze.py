# -*- coding: utf-8 -*-
"""Analizador de caras mínimas: cuántas caras hacen falta para no perder la forma del original.

Antes del retopo, mide dos cosas de la malla y las traduce a un número de caras:

* **Curvatura** → mínimo recomendado. Una arista de longitud L sobre una superficie de radio R se aparta de ella
  L²/(8R) (error de cuerda), así que el error de forma medio de un retopo de N caras iguales es proporcional a 1/N.
  Con la curvatura de cada zona se calcula el N que deja el error por debajo del objetivo. Es la cifra que el plugin
  compara con las caras pedidas. Calibrada con el error real del Motor MBL en un humano, un águila y una criatura.
* **Grosor** → máximo detalle. Una pieza más fina que unas pocas aristas (un dedo, una pluma, una oreja) no se
  puede representar con su grosor real. Se mide con `sizefield.thickness` y se calcula el N con el que solo una
  pequeña parte del área queda más fina que `MIN_WIDTH` aristas. Es informativo: el Motor MBL engorda las piezas
  finas y las salva con muchas menos caras (en el águila y la criatura, 12.000 caras dan un error medio de 0,06 % aunque
  esta cifra pida más de 100.000), así que no manda en el mínimo.

Las láminas (más finas que `SHEET_EDGES` aristas de referencia) no cuentan en el grosor. Todo es numpy puro (se prueba
sin Maya).
"""

import numpy as np

from . import sizefield

# error de forma medio que se persigue, como fracción de la diagonal del modelo. El semáforo del plugin pone el
# retopo en amarillo a partir de 0,25 %; se apunta por debajo para dejar margen
TARGET_ERROR = 0.0012
# el error medio real del motor es este múltiplo del error de cuerda calculado (humano, águila y criatura a
# 1.000-12.000 caras: entre 0,6 y 1,7 veces la previsión con este valor)
CURVATURE_GAIN = 2.0
# las zonas con peor error (el 1 % del área más lejos del original) bajan más despacio que el error medio: con la
# cifra de curvatura justa salían 2 % en las puntas de dedos y plumas. Este margen las deja en el 1 % o menos
MINIMUM_MARGIN = 1.3
# aristas que como mínimo deben caber a lo ancho de una pieza para no considerarla fina
MIN_WIDTH = 3.0
# parte del área de volúmenes que se acepta más fina que MIN_WIDTH aristas
THIN_ALLOWED = 0.10
# lo más fino que esto (en aristas de un retopo de REFERENCE_FACES caras) es una lámina, no un volumen
SHEET_EDGES = 0.6
# el grosor se mide con una arista de referencia de este nº de caras, en pasos geométricos hasta STEP_COUNT
REFERENCE_FACES = 30000
STEP_FIRST, STEP_RATIO, STEP_COUNT = 0.15, 1.5, 14
# más allá de esto (en diagonales) una pieza se considera gruesa
THICK_LIMIT_DIAGONALS = 0.5
# la curvatura y el grosor se miden en una malla de como mucho estos triángulos
PROBE_TRIANGLES = 30000
# nunca se recomienda menos que esto
FLOOR_FACES = 200
# se avisa si se piden menos de esta fracción del mínimo recomendado
WARN_BELOW = 0.85
# con el mínimo recomendado, el aviso habla de piezas finas si al menos este % del área de volúmenes lo es
THIN_WARN_PERCENT = 5.0
# aunque se llegue al mínimo, se avisa si con las caras pedidas al menos este % del área de volúmenes queda más fino que
# MIN_WIDTH aristas (a 30.000 caras: humano 6 %, águila 18 %; modelo de Meshy 36 %, criatura 50 %). Ahí el motor
# engorda o funde dedos, ropa pegada y pliegues. Es un aviso: no se ha medido cuántos salen mal, solo cuánta superficie
THIN_NOTE_PERCENT = 20.0


def thousands(number):
    """12345 -> "12.345" (como se escribe en castellano)."""
    return "{:,}".format(int(number)).replace(",", ".")


def _vertex_areas(points, tris):
    """Un tercio del área de cada triángulo a cada uno de sus vértices."""
    a, b, c = points[tris[:, 0]], points[tris[:, 1]], points[tris[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1) / 3.0
    return np.bincount(tris.ravel(), weights=np.repeat(area, 3), minlength=len(points))


def vertex_curvature(points, tris, normals):
    """Curvatura aproximada (1/R) de cada vértice: lo que giran las normales por unidad de longitud en la arista
    que más gira de las que le llegan, promediado con sus vecinos para quitar ruido."""
    edges = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    edges = np.unique(np.sort(edges, axis=1), axis=0)
    a, b = edges[:, 0], edges[:, 1]
    length = np.linalg.norm(points[a] - points[b], axis=1)
    turn = np.arccos(np.clip(np.einsum("ij,ij->i", normals[a], normals[b]), -1.0, 1.0))
    kappa_edge = turn / np.maximum(length, 1e-12)
    kappa = np.zeros(len(points))
    np.maximum.at(kappa, a, kappa_edge)
    np.maximum.at(kappa, b, kappa_edge)
    acc, count = kappa.copy(), np.ones(len(points))
    np.add.at(acc, a, kappa[b])
    np.add.at(acc, b, kappa[a])
    np.add.at(count, a, 1.0)
    np.add.at(count, b, 1.0)
    return acc / count


def vertex_thickness(points, tris, normals, grid=None):
    """Grosor local de cada vértice (distancia a la otra parte de la superficie por delante o por detrás de la
    normal), en unidades del modelo; inf si no hay nada cerca."""
    edge = sizefield.base_edge(points, tris, REFERENCE_FACES)
    steps = tuple(STEP_FIRST * STEP_RATIO ** k for k in range(STEP_COUNT))
    return sizefield.thickness(points, tris, normals, edge, steps=steps, grid=grid)


def weighted_quantile(values, weights, q):
    """Valor por debajo del cual queda una fracción q del peso total."""
    order = np.argsort(values)
    cum = np.cumsum(weights[order])
    return float(values[order][min(np.searchsorted(cum, q * cum[-1]), len(values) - 1)])


def analyze(points, tris, target_error=TARGET_ERROR, min_width=MIN_WIDTH, thin_allowed=THIN_ALLOWED, probe=None,
            grid=None):
    """Mide el original y devuelve las cifras de caras.

    probe: (puntos, triángulos) de una versión reducida de la malla (unos 30.000 triángulos) donde medir;
    si no se da, se mide en la malla tal cual (más lento si es muy grande).

    Devuelve un diccionario con:
      "minimum"      caras recomendadas como mínimo (con caras de tamaño igual)
      "detail"       caras con las que las piezas finas no bajarían de `min_width` aristas (informativo)
      "by_curvature", "by_thickness"   los dos criterios sin margen
      "sheet_percent"  % del área que son láminas (más finas que SHEET_EDGES aristas): el motor las engorda
      "thin_percent"   % del área de volúmenes más fino que `min_width` aristas con el mínimo recomendado
      "area", "diagonal"
      "_vertices"    campos por vértice para predict()
    """
    if probe is not None:
        points, tris = probe
    points = np.asarray(points, dtype=float)
    tris = np.asarray(tris, dtype=np.int64)
    normals = sizefield.outward_normals(points, tris)
    diagonal = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0))) or 1.0
    area = _vertex_areas(points, tris)
    total = float(area.sum())

    kappa = vertex_curvature(points, tris, normals)
    thick = vertex_thickness(points, tris, normals, grid)
    limit = THICK_LIMIT_DIAGONALS * diagonal
    thick = np.where(np.isfinite(thick), np.minimum(thick, limit), limit)
    sheet = thick < SHEET_EDGES * sizefield.base_edge(points, tris, REFERENCE_FACES)
    solid = ~sheet

    # curvatura: el error medio es proporcional a 1 / caras, así que el número de caras sale de una división
    mean_kappa = float((area * kappa).sum() / total)
    by_curvature = CURVATURE_GAIN * total * mean_kappa / (8.0 * target_error * diagonal)
    # grosor: la arista con la que solo `thin_allowed` del área de volúmenes queda más fina que `min_width` aristas
    by_thickness = 0.0
    if solid.any():
        by_thickness = total / weighted_quantile(thick[solid] / min_width, area[solid], thin_allowed) ** 2
    minimum = max(by_curvature * MINIMUM_MARGIN, FLOOR_FACES)
    report = {
        "minimum": int(round(minimum)),
        "detail": int(round(max(by_thickness, minimum))),
        "by_curvature": int(round(by_curvature)),
        "by_thickness": int(round(by_thickness)),
        "sheet_percent": float(100.0 * area[sheet].sum() / total),
        "area": total,
        "diagonal": diagonal,
        "_vertices": {"area": area, "kappa": kappa, "thick": thick, "sheet": sheet, "diagonal": diagonal,
                      "total": total, "min_width": min_width},
    }
    report["thin_percent"] = thin_percent(report, report["minimum"])
    return report


def predict(report, faces):
    """Qué se espera de un retopo de `faces` caras iguales: error de forma medio (% de la diagonal) y % del área de
    volúmenes más fina que `min_width` aristas."""
    v = report["_vertices"]
    edge = np.sqrt(v["total"] / max(int(faces), 1))
    sag = CURVATURE_GAIN * v["kappa"] * edge ** 2 / 8.0
    error = float((v["area"] * np.minimum(sag, 0.25 * edge)).sum() / v["total"]) / v["diagonal"] * 100.0
    return {"error_percent": error, "thin_percent": thin_percent(report, faces)}


def thin_percent(report, faces):
    """% del área de volúmenes cuya pieza es más fina que `min_width` aristas de un retopo de `faces` caras iguales."""
    v = report["_vertices"]
    edge = np.sqrt(v["total"] / max(int(faces), 1))
    thin = (v["thick"] < v["min_width"] * edge) & ~v["sheet"]
    return float(100.0 * v["area"][thin].sum() / v["total"])


def recommend(points, tris, requested, decimator=None, **kwargs):
    """Analiza la malla (reducida si es muy grande) y compara con las caras pedidas.

    Devuelve (informe, aviso): aviso es None si lo pedido llega al mínimo recomendado y no hay demasiada superficie en
    piezas más finas que MIN_WIDTH aristas."""
    from . import decimate
    tris = np.asarray(tris, dtype=np.int64)
    probe = None
    if decimator and len(tris) > PROBE_TRIANGLES:
        probe = decimate.qem_decimate(np.asarray(points, dtype=float), tris, PROBE_TRIANGLES, decimator)
    report = analyze(points, tris, probe=probe, **kwargs)
    warning = None
    if requested < WARN_BELOW * report["minimum"]:
        expected = predict(report, requested)
        warning = ("Pides {} caras y para esta malla se recomiendan al menos {}. Con {} se espera un error de "
                   "forma medio de {:.2f} % (con el mínimo, {:.2f} %){}.").format(
                       thousands(requested), thousands(report["minimum"]), thousands(requested),
                       expected["error_percent"], predict(report, report["minimum"])["error_percent"],
                       " y se pueden perder puntas y piezas finas (dedos, plumas, orejas)"
                       if expected["thin_percent"] >= THIN_WARN_PERCENT else "")
    elif requested > 0:
        thin = thin_percent(report, requested)
        if thin >= THIN_NOTE_PERCENT:
            warning = ("Con {} caras el {:.0f} % de la superficie son piezas de menos de {:.0f} aristas de grosor (dedos "
                       "que se tocan, ropa pegada al cuerpo, pliegues): el retopo puede fundirlas o aplastarlas. Con el "
                       "doble de caras ({}) bajaría al {:.0f} %; para no subir tanto, usa Pintar detalle en esas zonas."
                       ).format(thousands(requested), thin, report["_vertices"]["min_width"], thousands(2 * requested),
                                thin_percent(report, 2 * requested))
    return report, warning
