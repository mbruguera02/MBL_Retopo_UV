# -*- coding: utf-8 -*-
"""Semáforo de estado: tras cada paso dice si el resultado está listo (verde), se puede usar pero
conviene mirarlo (amarillo) o hay que arreglarlo (rojo), y qué tocar en cada caso.

Los umbrales salen de las pruebas con el humano (CC) y el águila (Meshy, 528k triángulos), con los
ajustes por defecto:
- retopo: 93.5-97.1% de vértices regulares, error medio 0.04-0.12%, error máximo 0.7-1.6%.
  Un motor descartado con agujeros en el águila: error máximo 5.2% (lo que hay que avisar).
- UVs: 62-70% de textura aprovechada, 4-6% de caras estiradas más de 1.5x. Antes de las mejoras
  de las UVs: 29-42% y 8-13%.

El paso Retopo también pasa por el juez del motor (mbl_engine.quality: quads deformados, saltos de
tamaño, caras dadas la vuelta o dobladas, triángulos y agujeros nuevos), con sus mismos umbrales: el
semáforo nunca sale más verde que la nota del juez (0 verde, 1 amarillo, 2 rojo). El paso UVs hace lo mismo con
el juez de UVs (mbl_engine.uv_quality: solapes, espejos, estiramiento, aprovechado, densidad de texel entre islas,
margen y continuidad de los cortes), con sus mismos umbrales.
"""

import math

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from . import cleanup, diagnostics, retopo, uv
from .common import dag_path, mesh_shape, short_name
from mbl_engine import quality, uv_quality
from mbl_engine.analyze import thousands
from mbl_engine.mesh import Mesh

GREEN, YELLOW, RED = 0, 1, 2
LEVEL_NAMES = {GREEN: "listo", YELLOW: "revisar", RED: "arreglar"}
LEVEL_COLORS = {GREEN: "#4caf50", YELLOW: "#e0b050", RED: "#ff6b6b"}
STEP_LABELS = {"cleanup": "Limpieza", "retopo": "Retopo", "uv": "UVs"}

# (amarillo desde, rojo desde)
REGULAR_PERCENT = (92.0, 85.0)          # menos es peor
ERROR_MEAN_PERCENT = (0.25, 0.6)        # más es peor
ERROR_MAX_PERCENT = (2.5, 5.0)
# % de vértices con 6+ aristas que se tolera sin avisar (humano: 3 de 2.900 = 0.1%, sin importancia;
# un motor descartado con agujeros en el águila: 0.5%)
POLES_6_PERCENT = 0.25
MIN_VERTICES_FOR_POLES = 200
QUAD_PERCENT = 98.0
# amarillo si el retopo tiene menos de esta fracción de las caras mínimas recomendadas
MIN_FACES_TOLERANCE = 0.85
UV_COVERAGE_PERCENT = (50.0, 35.0)      # menos es peor
# con "Costuras escondidas" se cambia resolución por costuras ocultas (águila 42%, humano 46%)
UV_COVERAGE_HIDDEN_PERCENT = (35.0, 25.0)
UV_STRETCH_PERCENT = (10.0, 20.0)       # % de caras estiradas más de 1.5x
# por encima de estos triángulos el original no se compara con el juez (convertirlo y construir su rejilla
# gasta memoria y tiempo: el águila de pruebas, con 528.000, se hace en ~1.5 s)
JUDGE_MAX_SOURCE_TRIANGLES = 1000000


def _check(level, text, advice=""):
    return {"level": level, "text": text, "advice": advice}


def _grade(value, limits, higher_is_worse=True):
    yellow, red = limits
    if higher_is_worse:
        return RED if value > red else YELLOW if value > yellow else GREEN
    return RED if value < red else YELLOW if value < yellow else GREEN


def _result(step, checks):
    return {"step": step, "level": max(c["level"] for c in checks) if checks else GREEN, "checks": checks}


# ------------------------------------------------------------------ limpieza

def cleanup_status(transform, report=None):
    report = report if report is not None else cleanup.analyze(transform)
    counts = cleanup.count_problems(report)
    checks = []
    nonmanifold = counts["nonmanifold_edges"] + counts["nonmanifold_vertices"]
    if nonmanifold:
        checks.append(_check(RED, "{} componentes no-manifold sin reparar".format(nonmanifold),
                             "El retopo y las UVs pueden fallar. Pulsa 'Analizar y seleccionar problemas' y "
                             "borra o separa esas caras a mano."))
    minor = [(counts[key], label) for key, label in cleanup.PROBLEM_LABELS.items()
             if key not in ("nonmanifold_edges", "nonmanifold_vertices") and counts[key]]
    for count, label in minor:
        checks.append(_check(YELLOW, "{} {}".format(count, label),
                             "No suele molestar al retopo. Para quitarlos, pulsa 'Limpiar' con 'Fusionar vértices' "
                             "marcado; 'Analizar' los selecciona."))
    if not checks:
        checks.append(_check(GREEN, "Malla limpia: sin no-manifold, caras rotas ni vértices sueltos"))
    return _result("cleanup", checks)


# ------------------------------------------------------------------ retopo

def judge_report(transform, source=None):
    """(informe de quality.report del retopo frente a su original, si se comparó con él).

    Sin original (o si es enorme) solo se miden las cifras que no lo necesitan: quads deformados, saltos de
    tamaño, caras dobladas y triángulos. Lanza lo que sea si la malla no se puede convertir."""
    mesh = Mesh(*retopo.mesh_arrays(transform))
    original = None
    if source and cmds.objExists(source) and cmds.polyEvaluate(source, triangle=True) <= JUDGE_MAX_SOURCE_TRIANGLES:
        original = Mesh(*retopo.mesh_arrays(source))
    report = quality.report(mesh, original)
    if original is None:
        report["defects"]["new_holes"] = 0      # sin original no se sabe qué agujeros son nuevos
    return report, original is not None


def _judge_values(report):
    """% que mira quality.grade en cada cifra, con los nombres de quality.LIMITS."""
    faces = max(report["faces"], 1)
    d = report["defects"]
    return {"bad_shape_percent": report["shape"]["bad_percent"],
            "size_jump_percent": report["uniformity"]["jump_percent"],
            "flipped_percent": 100.0 * d["flipped"] / faces,
            "folded_percent": 100.0 * d["folded"] / faces,
            "tri_percent": 100.0 * (d["tris"] + d["ngons"]) / faces}


def _judge_level(key, value):
    review, bad = quality.LIMITS[key]
    return RED if value >= bad else YELLOW if value >= review else GREEN


def _judge_checks(report, levels, compared):
    """Avisos del juez en texto claro. Los triángulos y los agujeros nuevos los cuentan sus propios avisos de
    retopo_status; si todo está bien sale una sola línea en verde."""
    d = report["defects"]
    values = _judge_values(report)
    checks = []
    if levels["bad_shape_percent"]:
        checks.append(_check(levels["bad_shape_percent"],
                             "{:.1f}% de quads deformados (ángulos muy agudos o abiertos, lados muy desiguales)".format(
                                 values["bad_shape_percent"]),
                             "La textura se estiraría en esas caras. Con Maya, QuadWild o Instant Meshes sube 'Relajar "
                             "quads' (en 'Más opciones'); o prueba otro motor: 'Automático' elige el mejor."))
    if levels["size_jump_percent"]:
        checks.append(_check(levels["size_jump_percent"],
                             "{:.1f}% de saltos de tamaño entre caras vecinas (una cara más de {:g} veces mayor que la "
                             "de al lado)".format(values["size_jump_percent"], quality.SIZE_JUMP),
                             "Con Maya, QuadWild o Instant Meshes sube 'Relajar quads' (en 'Más opciones') para igualar "
                             "los tamaños, o prueba otro motor. Si pintaste detalle, el salto en el borde de lo pintado "
                             "es normal: baja 'Densidad en lo pintado'."))
    if levels["flipped_percent"]:
        checks.append(_check(levels["flipped_percent"],
                             "Caras dadas la vuelta respecto al original: {} ({:.2f}%)".format(
                                 d["flipped"], values["flipped_percent"]),
                             "Se sombrean al revés y fallan al hornear mapas o pintar. Pasa en piezas finas o que se "
                             "tocan (dedos, pliegues, ropa pegada): sube 'Caras objetivo' ('Recomendar caras' dice "
                             "cuántas), prueba otro motor o usa 'Pintar detalle' en esa zona y repite el retopo."))
    if levels["folded_percent"]:
        checks.append(_check(levels["folded_percent"],
                             "Caras dobladas sobre su vecina: {} ({:.2f}%)".format(d["folded"], values["folded_percent"]),
                             "La superficie se pliega sobre sí misma (suele pasar entre dedos o en piezas muy finas). "
                             "Sube 'Caras objetivo', prueba otro motor ('Maya' o 'Automático') o usa 'Pintar detalle' "
                             "en esa zona y repite el retopo."))
    if not checks and not d["new_holes"]:
        checks.append(_check(GREEN, "{}; {:.1f}% de quads deformados".format(
            "Sin caras dadas la vuelta ni dobladas" if compared else "Sin caras dobladas", values["bad_shape_percent"])))
    return checks


def retopo_status(transform, source=None, stats=None):
    source = source or diagnostics.find_source(transform)
    stats = stats or diagnostics.cached_stats(transform) or diagnostics.topology_stats(transform)
    has_source = bool(source and cmds.objExists(source))
    checks = []

    # el juez del motor (la misma nota que el banco de pruebas): lo que ve y el semáforo no veía
    try:
        report, compared = judge_report(transform, source)
        levels = {key: _judge_level(key, value) for key, value in _judge_values(report).items()}
    except Exception:
        import traceback
        traceback.print_exc()
        report, compared, levels = None, False, {}
        checks.append(_check(YELLOW, "No he podido calcular la calidad de la malla (caras dadas la vuelta, dobladas...)",
                             "Pulsa 'Comprobar estado' otra vez; si se repite, pasa 'Limpiar' al retopo."))

    edges, vertices = cleanup.nonmanifold_counts(transform)
    if edges or vertices:
        checks.append(_check(RED, "{} componentes no-manifold".format(edges + vertices),
                             "Las UVs pueden fallar. Pulsa 'Limpiar' con el retopo seleccionado."))

    new_holes = judge_holes = 0
    if has_source:
        new_holes = len(retopo.border_loops(transform)) - len(retopo.border_loops(source))
    if report and compared:
        judge_holes = report["defects"]["new_holes"]
    if new_holes > 0 or judge_holes:
        # el juez da por mala una malla con agujeros nuevos (rojo); el conteo de Maya solo avisa (amarillo)
        checks.append(_check(RED if judge_holes else YELLOW, "{} agujeros que el original no tiene".format(
            max(new_holes, judge_holes)),
            "Sube 'Rellenar agujeros de hasta' y repite el retopo, o ciérralos con Mesh > Fill Hole."))

    tri_level = levels.get("tri_percent", GREEN)
    if stats["ngons"] or stats["quad_percent"] < QUAD_PERCENT or tri_level:
        checks.append(_check(max(YELLOW, tri_level), "{:.1f}% quads ({} triángulos, {} n-gons)".format(
            stats["quad_percent"], stats["tris"], stats["ngons"]),
            "Para texturizar no es grave. Si vas a subdividir, usa el motor 'Automático' o 'Maya', que "
            "dan 100% quads." if tri_level != RED else
            "Demasiados triángulos para subdividir o deformar la malla. Usa el motor 'Automático' o 'Maya', que "
            "dan 100% quads."))
    else:
        checks.append(_check(GREEN, "{:.1f}% quads".format(stats["quad_percent"])))

    level = _grade(stats["regular_percent"], REGULAR_PERCENT, higher_is_worse=False)
    if stats["vertices"] < MIN_VERTICES_FOR_POLES:
        # en piezas pequeñas los polos de las esquinas son inevitables (un cubo tiene 8 y 0% regulares)
        level = GREEN
    checks.append(_check(level, "{:.1f}% de vértices regulares ({} polos)".format(
        stats["regular_percent"], stats["pole_count"]),
        "" if level == GREEN else
        "Usa el motor 'Automático' y deja marcado 'Limpiar polos'. 'Diagnóstico' pinta los polos "
        "(azul 3 aristas, rojo 5) para moverlos a zonas que no se deformen."))

    poles6 = len(stats["poles"][6])
    if poles6 and stats["vertices"] >= MIN_VERTICES_FOR_POLES and 100.0 * poles6 / max(stats["vertices"], 1) > POLES_6_PERCENT:
        checks.append(_check(YELLOW, "{} polos de 6 o más aristas".format(poles6),
                             "Pueden marcar pellizcos al subdividir. 'Diagnóstico' los pinta en magenta."))

    if report:
        checks.extend(_judge_checks(report, levels, compared))
        if has_source and not compared:
            checks.append(_check(YELLOW, "El original tiene {} triángulos: no se comprueba si hay caras dadas la vuelta".format(
                thousands(cmds.polyEvaluate(source, triangle=True))),
                "Es un límite del cálculo, no un fallo del retopo. Revisa a ojo las zonas finas (dedos, pliegues)."))

    if has_source:
        error = stats.get("error") or diagnostics.surface_error(source, transform)
        level = shape_level = _grade(error["mean_percent"], ERROR_MEAN_PERCENT)
        checks.append(_check(level, "Error de forma medio {:.3f}% del tamaño".format(error["mean_percent"]),
                             "" if level == GREEN else
                             "Sube 'Caras objetivo' (más caras = más detalle) y deja marcado "
                             "'Reproyectar sobre el original'."))
        level = _grade(error["max_percent"], ERROR_MAX_PERCENT)
        shape_level = max(shape_level, level)
        checks.append(_check(level, "Error de forma máximo {:.2f}% del tamaño".format(error["max_percent"]),
                             "" if level == GREEN else
                             "Alguna zona fina (dedos, orejas, picos) se ha perdido o deformado. Sube "
                             "'Caras objetivo' o prueba otro motor."))
        recommended = retopo.recommended_faces(source)
        # con pocas caras pero la forma bien medida (una esfera, una pieza sencilla) no hay nada que avisar
        if recommended and stats["faces"] < MIN_FACES_TOLERANCE * recommended["minimum"] and shape_level != GREEN:
            checks.append(_check(YELLOW, "{} caras; se recomiendan al menos {}".format(
                thousands(stats["faces"]), thousands(recommended["minimum"])),
                "Sube 'Caras objetivo' a {} o más: con menos se pierden puntas y piezas finas (dedos, plumas, "
                "orejas). 'Recomendar caras' lo calcula.".format(thousands(recommended["minimum"]))))
        elif recommended:
            checks.append(_check(GREEN, "{} caras (mínimo recomendado {})".format(
                thousands(stats["faces"]), thousands(recommended["minimum"]))))
    else:
        checks.append(_check(YELLOW, "No encuentro la malla original: no se mide el error de forma",
                             "Si la borraste o renombraste no pasa nada; el resto de comprobaciones vale."))

    if report:
        # red de seguridad: nunca más verde que la nota del juez, aunque quality.grade mire algo que aquí no se traduce
        grade, reasons = quality.grade(report)
        if grade > max(c["level"] for c in checks):
            checks.append(_check(grade, "Calidad de la topología: {}".format("; ".join(reasons)),
                                 "Prueba otro motor ('Automático' elige el mejor) o sube 'Caras objetivo'."))
    return _result("retopo", checks)


# ------------------------------------------------------------------ UVs

def _unmapped_faces(transform):
    fn = om.MFnMesh(dag_path(mesh_shape(transform)))
    counts, _ = fn.getAssignedUVs()
    return sum(1 for c in counts if c == 0)


def uv_arrays(transform):
    """(puntos, caras, puntos UV Mx2, caras de UV) de la malla, como los pide uv_quality.report (el mismo formato
    que usa test_uv_quality_matches_maya)."""
    points, faces = retopo.mesh_arrays(transform)
    fn = om.MFnMesh(dag_path(mesh_shape(transform)))
    us, vs = fn.getUVs()
    counts, ids = fn.getAssignedUVs()
    ids = list(ids)
    uv_faces, start = [], 0
    for count in counts:
        uv_faces.append(ids[start:start + count])
        start += count
    return points, faces, np.column_stack([us, vs]), uv_faces


def uv_judge_report(transform, udim=False):
    """Informe de uv_quality.report de las UVs de la malla. En UDIM `used_percent` suma áreas (cada tile es un cuadrado 0-1
    y pasaría de 100), así que se reparte entre los tiles que ocupan las islas."""
    report = uv_quality.report(*uv_arrays(transform))
    if udim:
        tiles = {(math.floor((b[0] + b[1]) * 0.5), math.floor((b[2] + b[3]) * 0.5)) for b in uv.shell_bounds(transform)}
        report["used_percent"] /= max(len(tiles), 1)
    return report


def _uv_judge_values(report):
    """Cifras que mira uv_quality.grade, con los nombres de uv_quality.LIMITS."""
    faces = max(report["faces"], 1)
    return {"overlapping_percent": 100.0 * report["overlapping_faces"] / faces,
            "flipped_percent": 100.0 * report["flipped_faces"] / faces,
            "unmapped_percent": 100.0 * (report["faces"] - report["mapped_faces"]) / faces,
            "stretch_mean": report["stretch_mean"], "over_1_5_percent": report["over_1_5_percent"],
            "texel_variation_percent": report["texel_variation_percent"], "used_percent": report["used_percent"],
            "racord_percent": report["racord_percent"] if report["seam_percent"] > 0 else None,
            "margin_min": report["margin_min"]}


def _uv_judge_level(key, value):
    if value is None:
        return GREEN
    review, bad = uv_quality.LIMITS[key]
    worse = (lambda v, limit: v <= limit) if key in uv_quality.LOWER_IS_WORSE else (lambda v, limit: v >= limit)
    return RED if bad is not None and worse(value, bad) else YELLOW if worse(value, review) else GREEN


def _uv_judge_checks(report, values, levels, covered, map_size=None, udim=False):
    """Avisos del juez de UVs con su «→ qué tocar». `covered` = nivel que ya da uv_status a esa cifra con sus propias
    comprobaciones: solo sale un aviso nuevo si el juez es más duro (así no se repite lo que ya se avisa). Sin avisos,
    una línea en verde."""
    margin = values["margin_min"]
    margin_text = "{:.1f} px en la textura de {}".format(margin * map_size, map_size) if margin is not None and map_size else \
        "{}".format(margin)
    texts = {
        "overlapping_percent": ("{} caras con UVs solapadas ({:.2f}%)".format(report["overlapping_faces"], values["overlapping_percent"]),
                                "Se pintarían a la vez en Substance. Repite 'UVs'; si sigue, usa Costuras 'Máxima resolución' "
                                "o sube 'Dividir islas'."),
        "flipped_percent": ("{} caras con UVs en espejo ({:.2f}%)".format(report["flipped_faces"], values["flipped_percent"]),
                            "Los normal maps saldrían invertidos. Repite 'UVs'."),
        "unmapped_percent": ("{} caras sin UVs ({:.2f}%)".format(report["faces"] - report["mapped_faces"], values["unmapped_percent"]),
                             "Repite 'UVs'."),
        "stretch_mean": ("Estiramiento medio de {:.2f}x (1x = sin deformar)".format(values["stretch_mean"]),
                         "La textura se vería deformada en general. Sube 'Optimize' o 'Dividir islas', o usa Costuras "
                         "'Máxima resolución', y repite 'UVs'."),
        "over_1_5_percent": ("{:.1f}% de caras estiradas más de 1.5x".format(values["over_1_5_percent"]),
                             "La textura se vería deformada ahí. Sube 'Optimize' o 'Dividir islas', o usa Costuras "
                             "'Máxima resolución', y repite 'UVs'."),
        "texel_variation_percent": ("Densidad de texel distinta entre islas ({:.0f}% de variación)".format(
                                        values["texel_variation_percent"]),
                                    "Unas partes saldrían más nítidas que otras al texturizar. Repite 'UVs': el plugin da la "
                                    "misma densidad a todas las islas (si escalaste islas a mano, se pierde)."),
        "used_percent": ("Solo el {:.0f}% de {} aprovechado".format(values["used_percent"], "cada tile UDIM" if udim else "la textura"),
                         "Sube 'Intentos de empaquetado' o 'Calidad de layout'"
                         + (" o baja 'Nº de UDIMs' para que las islas llenen menos tiles." if udim else
                            ", baja 'Padding' o marca 'UDIM' para tener más resolución.")),
        "racord_percent": ("Costuras que no casan: solo el {:.1f}% de su longitud continúa de un lado al otro sin giro ni "
                           "cambio de escala".format(values["racord_percent"] or 0.0),
                           "El dibujo de la textura (vetas, rayones) se cortaría en cada costura. Para tener menos cortes baja "
                           "'Dividir islas' o elige 'Costuras escondidas' (en Costuras); deja marcado 'Islas rectas y de pie' "
                           "y desmarcado 'Permitir rotar islas'."),
        "margin_min": ("Islas casi pegadas: margen mínimo de {}".format(margin_text),
                       "El color de una isla manchará a la vecina al hornear o con mipmaps. Sube 'Padding' y repite 'UVs'."),
    }
    checks = [_check(level, *texts[key]) for key, level in levels.items() if level > covered.get(key, GREEN)]
    if not checks:
        parts = ["densidad de texel igual entre islas ({:.1f}% de variación)".format(report["texel_variation_percent"])]
        if margin is not None:
            parts.append("margen entre islas de {}".format(margin_text))
        text = "; ".join(parts)
        checks.append(_check(GREEN, text[0].upper() + text[1:]))
    return checks


def uv_status(transform, report=None, map_size=None, hidden_seams=False):
    fn = om.MFnMesh(dag_path(mesh_shape(transform)))
    if fn.numUVs() == 0:
        return _result("uv", [_check(RED, "La malla no tiene UVs", "Pulsa 'UVs'.")])
    (umin, umax), (vmin, vmax) = cmds.polyEvaluate(transform, boundingBox2d=True)
    udim = umax > 1 + 1e-5 or vmax > 1 + 1e-5
    if report is None:
        report = uv.check(transform, udim=udim)
    stretch = report.get("stretch") or uv.stretch(transform)
    checks = []
    # nivel que ya da a cada cifra del juez de UVs lo que se comprueba aquí abajo
    covered = {}

    # el juez de UVs (la misma nota que el banco de pruebas): además de lo de abajo mira la densidad entre islas, el
    # margen, el estiramiento medio y la continuidad de los cortes
    try:
        judge = uv_judge_report(transform, udim)
        judge_values = _uv_judge_values(judge)
        judge_levels = {key: _uv_judge_level(key, value) for key, value in judge_values.items()}
    except Exception:
        import traceback
        traceback.print_exc()
        judge = judge_values = judge_levels = None
        checks.append(_check(YELLOW, "No he podido calcular la calidad de las UVs (densidad entre islas, margen, costuras)",
                             "Pulsa 'Comprobar estado' otra vez; si se repite, repite 'UVs'."))

    unmapped = _unmapped_faces(transform)
    if unmapped:
        checks.append(_check(RED, "{} caras sin UVs".format(unmapped), "Repite 'UVs'."))
        covered["unmapped_percent"] = RED
    if report["overlapping_faces"]:
        checks.append(_check(RED, "{} caras con UVs solapadas".format(report["overlapping_faces"]),
                             "Se pintarían a la vez en Substance. Repite 'UVs'; si sigue, usa Costuras "
                             "'Máxima resolución' o sube 'Dividir islas'."))
        covered["overlapping_percent"] = RED
    if report["flipped_faces"]:
        checks.append(_check(RED, "{} caras con UVs en espejo".format(report["flipped_faces"]),
                             "Los normal maps saldrían invertidos. Repite 'UVs'."))
        covered["flipped_percent"] = RED
    if udim:
        if umin < -1e-5 or vmin < -1e-5:
            checks.append(_check(RED, "UVs en coordenadas negativas", "Repite 'UVs' con 'UDIM' marcado."))
        crossing = report.get("crossing_tiles") or uv.shells_crossing_tiles(uv.shell_bounds(transform))
        if crossing:
            checks.append(_check(RED, "{} islas cruzan el borde de un UDIM".format(len(crossing)),
                                 "Se cortarían entre dos texturas. Repite 'UVs' con 'UDIM' marcado."))
    elif umin < -1e-5 or vmin < -1e-5:
        checks.append(_check(RED, "UVs fuera del cuadro 0-1", "Repite 'UVs' (con 'UDIM' desmarcado)."))
    else:
        coverage = 100.0 * uv.uv_area(transform)
        limits = UV_COVERAGE_HIDDEN_PERCENT if hidden_seams else UV_COVERAGE_PERCENT
        level = _grade(coverage, limits, higher_is_worse=False)
        if judge_levels:
            # con Costuras escondidas los umbrales de aquí son más tolerantes que los del juez: manda el más duro
            level = max(level, judge_levels["used_percent"])
        covered["used_percent"] = level
        advice = ("Sube 'Intentos de empaquetado' o 'Calidad de layout', baja 'Padding' o marca 'UDIM' "
                  "para tener más resolución.")
        if hidden_seams:
            advice += " Con Costuras 'Máxima resolución' se aprovecha más (pero se ven más costuras)."
        else:
            advice += " Si hay pocas islas muy grandes, sube 'Dividir islas'."
        checks.append(_check(level, "{:.0f}% de la textura aprovechada ({} islas)".format(coverage, report["shells"]),
                             "" if level == GREEN else advice))

    level = _grade(stretch["over_1_5_percent"], UV_STRETCH_PERCENT)
    covered["over_1_5_percent"] = level
    checks.append(_check(level, "{:.1f}% de caras estiradas más de 1.5x".format(stretch["over_1_5_percent"]),
                         "" if level == GREEN else
                         "La textura se vería deformada ahí. Sube 'Optimize' o 'Dividir islas', o usa "
                         "Costuras 'Máxima resolución', y repite 'UVs'."))
    if map_size and not udim:
        checks.append(_check(GREEN, "{:.1f} px por unidad con textura de {}".format(
            uv.texel_density(transform, map_size), map_size)))
    if judge:
        checks.extend(_uv_judge_checks(judge, judge_values, judge_levels, covered, map_size, udim))
        # red de seguridad: nunca más verde que la nota del juez, aunque uv_quality.grade mire algo que aquí no se traduce
        grade, reasons = uv_quality.grade(judge)
        if grade > max(c["level"] for c in checks):
            checks.append(_check(grade, "Calidad de las UVs: {}".format("; ".join(reasons)),
                                 "Repite 'UVs' o prueba otras Costuras."))
    return _result("uv", checks)


# ------------------------------------------------------------------ todo junto

def evaluate(transform, steps=None, reports=None, settings=None):
    """Semáforo de cada paso. Sin `steps` se deduce qué tiene la malla (retopo si tiene original
    enlazado, UVs si tiene UVs). Devuelve [{"step", "level", "checks"}]."""
    reports = reports or {}
    settings = settings or {}
    map_size = settings.get("uv_map_size")
    hidden_seams = settings.get("uv_seam_style") == "hidden"
    if steps is None:
        steps = ["cleanup"]
        if diagnostics.find_source(transform):
            steps.append("retopo")
        if om.MFnMesh(dag_path(mesh_shape(transform))).numUVs():
            steps.append("uv")
    results = []
    for step in steps:
        if step == "cleanup":
            # tras el retopo el informe de limpieza es del original: se mira la malla actual
            results.append(cleanup_status(transform, None if "retopo" in steps else reports.get("cleanup")))
        elif step == "retopo":
            results.append(retopo_status(transform))
        elif step == "uv":
            results.append(uv_status(transform, reports.get("uv"), map_size, hidden_seams))
    return results


def overall(results):
    return max((r["level"] for r in results), default=GREEN)


def log_status(transform, results, log):
    """Resumen en el log: una línea por paso y los consejos de lo que no está en verde."""
    name = short_name(transform)
    line = "  ".join("{}: {}".format(STEP_LABELS[r["step"]], LEVEL_NAMES[r["level"]]) for r in results)
    levels = {GREEN: "info", YELLOW: "warning", RED: "error"}
    log("{}: estado -> {}".format(name, line), levels[overall(results)])
    for r in results:
        for c in r["checks"]:
            if c["level"] != GREEN:
                log("  {} {}: {}. {}".format(STEP_LABELS[r["step"]], LEVEL_NAMES[c["level"]], c["text"],
                                             c["advice"]).rstrip(), levels[c["level"]])
