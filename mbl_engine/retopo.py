# -*- coding: utf-8 -*-
"""Retopología completa sin Maya: motor externo + reparación + limpieza de polos + relajado +
reproyección sobre el original, con simetría en X opcional.

Motores (ejecutables, ver CREDITS.md):
- Instant Meshes (nuestra versión, BSD): campo de orientación y posición.
- QuadriFlow (BSD): como Instant Meshes, más un flujo de coste mínimo que quita los polos de 6+.
"""

import os
import shutil
import tempfile

import numpy as np

from . import decimate, fit, metrics, orient, quality, quadopt, sizefield, symmetry
from .closest import find_native, make_grid
from .mesh import Mesh
from .process import EngineError, _run_once, run_instant_meshes
from .repair import (_dissolve_doublets, _remove_small_pieces, fill_new_holes, read_obj, repair_topology,
                     write_obj, write_ply, vertex_areas)


# Instant Meshes en modo solo quads subdivide al final: da ~3,15 veces las caras pedidas
IM_PURE_QUAD_FACTOR = 3.15
# QuadriFlow da ~0,75 veces las caras pedidas (humano: 7.000 -> 5.612; águila: 7.000 -> 5.778)
QUADRIFLOW_FACTOR = 1.3
# el total de caras se acepta si queda a menos de esta fracción del objetivo (hasta QUADRIFLOW_ATTEMPTS pasadas)
QUADRIFLOW_TOLERANCE = 0.12
QUADRIFLOW_ATTEMPTS = 3
# puntos del original con los que se mide lo que el retopo no cubre (también es el tamaño de SurfaceReference)
FIT_SAMPLES = 40000

# Piezas finas (dedos de manos y pies): con caras repartidas por igual quedan más finas que una
# arista y el motor las aplasta. En el humano a 5.000 caras, manos y pies daban el 68 % del error de
# forma (el resto del cuerpo, 0,05 %, como Maya). Donde el radio de la pieza (1 / curvatura) es menor
# que THIN_EDGES_PER_RADIUS aristas, la densidad sube hasta que la arista quepa; como mucho
# THIN_MAX_DENSITY veces. El total de caras no cambia: lo demás cede algunas.
# Medido en el humano (opción thin_features): con máximo 4x el error medio baja del 0,204 % al 0,132 %
# (manos del 0,61 % al 0,35 %), pero los polos suben de 231 a 369; con 16x Instant Meshes rompe los
# dedos en trozos sueltos (14) y el error sube. Por eso va desactivada por defecto.
THIN_EDGES_PER_RADIUS = 1.0
THIN_MAX_DENSITY = 4.0
THIN_SMOOTH = 2

# vértices a esta distancia del plano de simetría (en aristas del retopo) se consideran sobre él
CUT_PLANE_EDGES = 0.1

# Pasadas del relajado a quads cuadrados (quadopt.regularize) y filas de quads que se sueltan alrededor de cada
# quad deformado (quadopt.fix_deformed); options["regular"] = 0 vuelve al relajado por centroides de antes.
# Medido (QuadriFlow, 5.000 caras): quads deformados del 4,4 % al 2,8 % en el humano y del 8,0 % al 3,3 % en el
# águila, con saltos de tamaño menores y el error máximo del águila de 3,51 a 2,19. Con 40 pasadas en vez de 20
# no cambia nada; el relajado normal solo no baja los deformados porque 9 de cada 10 tocan un vértice de arista
# marcada o de borde que no mueve.
REGULAR_ITERATIONS = 20
REGULAR_SPREAD = 2


def thin_feature_density(points, faces, target_faces, edges_per_radius=THIN_EDGES_PER_RADIUS,
                         max_density=THIN_MAX_DENSITY):
    """Densidad por vértice (1 = normal) para que las piezas finas no se pierdan. faces: triángulos."""
    mesh = Mesh(points, faces)
    edge = float(np.sqrt(mesh.face_areas.sum() / max(int(target_faces), 1)))
    k1, k2, _ = orient.principal_curvatures(points, faces, THIN_SMOOTH)
    bend = np.maximum(np.abs(k1), np.abs(k2)) * edge / edges_per_radius
    return np.clip(bend * bend, 1.0, max_density)


def projector(source):
    """Función que pega puntos (N x 3) a la superficie de `source` (Mesh)."""
    grid = make_grid(source.points, source.triangles()[0])
    return lambda points: grid.closest(points)[0]


def _instant_meshes(executable, points, faces, target, work, timeout, tick, options):
    destination = os.path.join(work, "output.obj")
    density = options.get("density")
    if density is not None:
        # PLY: el valor de densidad de cada vértice tiene que ir en el mismo orden
        source = os.path.join(work, "input.ply")
        write_ply(source, points, faces)
        density_file = os.path.join(work, "density.txt")
        np.savetxt(density_file, density, fmt="%.4f")
    else:
        source = os.path.join(work, "input.obj")
        write_obj(source, points, faces)
    dominant = bool(options.get("dominant"))
    # solo quads: Instant Meshes extrae con ~1/3 de las caras y subdivide al final; en modo
    # quad-dominante (-D) extrae ya con las caras pedidas (y deja algún triángulo)
    asked = target if dominant else max(int(target / IM_PURE_QUAD_FACTOR), 1)
    result = None
    for attempt in range(2):
        command = [executable, source, "-o", destination, "-f", str(asked), "-r", "4", "-p", "4",
                   "-S", str(int(options.get("smooth", 2)))]
        if options.get("deterministic", True):
            command.append("-d")
        if dominant:
            command.append("-D")
        if options.get("align_boundaries"):
            command.append("-b")
        if density is not None:
            command += ["--density", density_file]
        elif options.get("adaptive"):
            # densidad por curvatura (nuestra versión de Instant Meshes)
            command += ["--adaptive", "{:g}".format(float(options["adaptive"]))]
        run_instant_meshes(command, destination, timeout, os.path.join(work, "im.log"), tick)
        result = read_obj(destination)
        got = len(result[1])
        # el nº de caras real depende de la malla: si se desvía mucho se corrige una vez
        if attempt or not got or abs(got - target) <= 0.2 * target:
            break
        asked = max(int(asked * float(target) / got), 1)
    return result


def _quadriflow(executable, points, faces, target, work, timeout, tick, options):
    source = os.path.join(work, "input.obj")
    destination = os.path.join(work, "output.obj")
    density = options.get("density")
    asked = target * QUADRIFLOW_FACTOR
    density_file = None
    if density is not None:
        # nuestra versión de QuadriFlow (-density): el valor de cada vértice va en el mismo orden
        write_obj(source, points, faces, keep_order=True)
        density_file = os.path.join(work, "density.txt")
        np.savetxt(density_file, density, fmt="%.4f")
        # QuadriFlow no mantiene el total: da las caras pedidas con tamaño uniforme y luego encoge las
        # aristas donde hay densidad, así que el total sube por la densidad media (por área). Se piden
        # menos caras de base para que el total siga siendo el objetivo.
        areas = vertex_areas(points, faces)
        asked /= float(np.sum(areas * density) / max(np.sum(areas), 1e-30))
    else:
        write_obj(source, points, faces)
    result, tried = None, []
    for attempt in range(QUADRIFLOW_ATTEMPTS):
        command = [executable, "-i", source, "-o", destination, "-f", str(max(int(asked), 1))]
        if density_file:
            command += ["-density", density_file]
        if options.get("boundary"):
            # el borde abierto (el corte de la mitad) se respeta: sus aristas quedan alineadas con él
            command.append("-boundary")
        if options.get("adaptive"):
            command.append("-adaptive")
        if options.get("seed") is not None:
            command += ["-seed", str(int(options["seed"]))]
        _run_once(command, destination, timeout, os.path.join(work, "qf.log"), tick, label="QuadriFlow")
        mesh = read_obj(destination)
        got = len(mesh[1])
        tried.append((asked, got, mesh))
        if options.get("attempts") is not None:
            options["attempts"].append((int(asked), got))
        # con densidad variable el total se desvía: se corrige con la pendiente medida (caras / pedido)
        # y se repite hasta quedar dentro de la tolerancia
        if not got or abs(got - target) <= QUADRIFLOW_TOLERANCE * target:
            break
        asked = _next_request(tried, target)
    # la pasada que más se acerca al objetivo (la última no tiene por qué ser la mejor: QuadriFlow es caótico)
    usable = [t for t in tried if t[1]]
    best = min(usable, key=lambda t: abs(np.log(t[1] / float(target)))) if usable else tried[-1]
    return best[2]


def _next_request(tried, target):
    """Caras que pedir a continuación a partir de las pasadas hechas [(pedido, dado, malla)]."""
    asked, got = tried[-1][0], max(tried[-1][1], 1)
    slope = 1.0
    if len(tried) > 1:
        (a1, g1), (a2, g2) = tried[-2][:2], tried[-1][:2]
        if g1 and g2 and a1 != a2 and g1 != g2:
            slope = float(np.clip(np.log(g2 / float(g1)) / np.log(a2 / float(a1)), 0.4, 1.2))
    return max(asked * (float(target) / got) ** (1.0 / slope), 1.0)


ENGINES = {"instant_meshes": _instant_meshes, "quadriflow": _quadriflow}


def triangulated(points, faces):
    tris, _ = Mesh(points, faces).triangles()
    return tris.tolist()


def compact(points, faces):
    """Solo los vértices usados, renumerados (los sueltos confunden a los motores). (puntos, caras)."""
    mesh = Mesh(points, faces)
    used = np.nonzero(mesh.used_vertices)[0]
    remap = np.full(len(mesh.points), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return mesh.points[used], [[int(remap[v]) for v in f] for f in faces]


# un borde es el corte de la mitad si al menos esta fracción de sus vértices ya está sobre el plano
CUT_BORDER_FRACTION = 0.8


def snap_cut_border(points, faces, center, tolerance):
    """Pega al plano todos los vértices del borde del corte (el que ya está casi entero sobre él).

    El motor deja alguno algo más lejos que la tolerancia de soldar (esfera: uno a 0,32 aristas) y
    la costura quedaba con un hueco. Los agujeros del modelo que rozan el plano no se tocan."""
    points = np.array(points, dtype=float)
    mesh = Mesh(points, faces)
    for loop in mesh.boundary_loops():
        vertices = np.unique(mesh.edges[loop])
        near = np.abs(points[vertices, 0] - center) <= tolerance
        if near.mean() >= CUT_BORDER_FRACTION:
            points[vertices, 0] = center
    return points


class Prepared(object):
    """Lo que el retopo calcula de la entrada antes del motor y que no depende de la variante:
    piezas sueltas fuera, corte por simetría, malla reducida para el motor, rejilla del original y
    campo de grosor. best_retopo() lo hace una vez y lo comparten todas las variantes."""

    def __init__(self, **values):
        self.__dict__.update(values)


def prepare(points, faces, target_faces, engine, symmetric=None, timeout=600, tick=None, min_piece=0.01,
            options=None, log=None):
    """Prepara la entrada para retopo() (ver Prepared)."""
    log = log or (lambda message: None)
    options = options or {}
    trace = options.get("trace") or (lambda stage, p, f: None)
    report = {"engine": engine}
    source = Mesh(points, faces)
    faces = [list(f) for f in faces]
    faces, report["pieces_removed"] = _remove_small_pieces(faces, min_piece)
    source_kept = Mesh(source.points, faces)
    if symmetric is None:
        symmetric = symmetry.is_symmetric(source_kept)[0]
    report["symmetric"] = bool(symmetric)
    work_points, work_faces = source_kept.points, faces
    target = int(target_faces)
    center = None
    if symmetric:
        center = symmetry.plane_x(source_kept.points[source_kept.used_vertices])
        work_points, work_faces = symmetry.cut_half(work_points, work_faces, center)
        target = max(target // 2, 1)
        log("mitad +X: {} caras".format(len(work_faces)))

    work_points, work_faces = compact(work_points, work_faces)
    work_mesh = Mesh(work_points, work_faces)
    if "align_boundaries" not in options:
        # con bordes (el corte de la mitad, aberturas del modelo) los vértices tienen que caer sobre
        # ellos: si no, la costura central no se suelda entera
        options = dict(options, align_boundaries=bool(len(work_mesh.boundary_edges)))
    engine_points, engine_faces = work_points, work_faces
    tris = triangulated(engine_points, engine_faces)
    # el motor no necesita 528.000 triángulos para sacar 5.000 caras: se le da una versión reducida
    # (águila: 56 s a 15 s con el mismo error). El resto del proceso usa la malla original.
    decimator = decimate.find_decimator(options.get("decimator")) if options.get("decimate", True) else None
    if decimator and (not symmetric or engine == "quadriflow") and len(tris) > decimate.DECIMATE_ABOVE:
        engine_points, reduced = decimate.qem_decimate(engine_points, tris, decimate.DECIMATE_TO, decimator,
                                                        timeout=timeout, tick=tick)
        log("malla reducida para el motor: {} a {} triángulos".format(len(tris), len(reduced)))
        report["decimated"] = (len(tris), len(reduced))
        tris = reduced.tolist()
        trace("decimada", engine_points, tris)
    reference = metrics.SurfaceReference(work_mesh, FIT_SAMPLES)
    field = None
    if options.get("inflate") not in (None, False):
        # piezas más finas que la arista: se mide su grosor una vez (lo usan todas las variantes)
        edge = sizefield.base_edge(engine_points, tris, target)
        probe = None
        # con el buscador nativo se mide en la malla entera de ~100.000 triángulos; en Python (un minuto)
        # se mide en una versión reducida
        if not find_native() and decimator and len(tris) > sizefield.PROBE_PER_TARGET_FACE * target:
            probe = decimate.qem_decimate(engine_points, tris, sizefield.PROBE_PER_TARGET_FACE * target, decimator,
                                          timeout=timeout, tick=tick)
        field = sizefield.thickness_field(engine_points, tris, edge, probe)
    return Prepared(report=report, symmetric=bool(symmetric), center=center, target=target, work_mesh=work_mesh,
                    engine_points=engine_points, tris=tris, reference=reference, field=field, options=options)


def _ugliness(points, faces, source, grid):
    """Índice de fealdad (quality.defect_score) de una malla frente al original: elige entre dos acabados."""
    mesh = Mesh(points, faces)
    return quality.defect_score(quality.shape_stats(mesh), quality.uniformity_stats(mesh),
                                quality.defect_stats(mesh, source, grid), mesh.face_count)


def retopo(points, faces, target_faces, engine, executable, symmetric=None, timeout=600, tick=None,
           relax=10, pole_cleanup=True, min_piece=0.01, fill_holes=60, options=None, log=None):
    """Retopología de (points, faces). Devuelve (puntos, caras, informe).

    symmetric: None = detectar; True / False = forzar. engine: "instant_meshes" o "quadriflow".
    options["prepared"]: el resultado de prepare() si ya se hizo (variantes de best_retopo).
    """
    log = log or (lambda message: None)
    options = dict(options or {})
    prep = options.pop("prepared", None) or prepare(points, faces, target_faces, engine, symmetric, timeout, tick,
                                                    min_piece, options, log)
    options = dict(prep.options, **options)
    if prep.symmetric and engine == "quadriflow":
        options["boundary"] = True
    trace = options.get("trace") or (lambda stage, p, f: None)
    report = dict(prep.report)
    symmetric, center, target, work_mesh = prep.symmetric, prep.center, prep.target, prep.work_mesh
    engine_points, tris, reference = prep.engine_points, prep.tris, prep.reference
    if options.get("inflate") not in (None, False):
        # el motor ve una versión engordada de las piezas finas (la forma final no cambia, los vértices
        # se pegan al original más abajo)
        grow = options["inflate"] if isinstance(options["inflate"], dict) else {}
        edge = sizefield.base_edge(engine_points, tris, target)
        flat = engine_points
        engine_points, info = sizefield.inflate(engine_points, tris, edge, field=prep.field, **grow)
        if prep.symmetric:
            # los vértices del corte no se mueven: el borde tiene que seguir sobre el plano
            fixed = np.abs(flat[:, 0] - prep.center) <= CUT_PLANE_EDGES * edge
            engine_points = np.array(engine_points)
            engine_points[fixed] = flat[fixed]
        report["inflate"] = info
        log("piezas finas engordadas para el motor: {:.0f}% de los vértices".format(info["inflated_percent"]))
    if options.get("thin_features", False) and "density" not in options:
        density = thin_feature_density(engine_points, tris, target,
                                       options.get("thin_epr", THIN_EDGES_PER_RADIUS),
                                       options.get("thin_max", THIN_MAX_DENSITY))
        boosted = density > 1.0 + 1e-6
        if boosted.any():
            options = dict(options, density=density)
            log("piezas finas: más densidad en el {:.0f}% de los vértices (hasta {:.0f}x)".format(
                100.0 * boosted.mean(), density.max()))
    work = tempfile.mkdtemp(prefix="mbl_engine_")
    try:
        run = ENGINES[engine]
        new_points, new_faces = run(executable, engine_points, tris, target, work, timeout, tick, options)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not new_faces:
        raise EngineError("{} no devolvió ninguna malla.".format(engine))
    trace("motor", new_points, new_faces)

    # sin rellenar aquí: se cierran solo los agujeros que abrió el motor (los bordes de la entrada,
    # como el corte de la mitad o el cuello, se respetan; rellenarlos metía abanicos de triángulos)
    new_points, new_faces, repair = repair_topology(new_points, new_faces, 0.01, 0)
    if fill_holes > 0:
        border = work_mesh.points[np.unique(work_mesh.edges[work_mesh.boundary_edges])]
        new_points, new_faces, repair["holes"] = fill_new_holes(new_points, new_faces, border, fill_holes)
    report["repair"] = repair
    trace("reparada", new_points, new_faces)
    # los agujeros de borde impar que rellena el paso anterior dejan un triángulo cada uno (un quad no
    # cierra un borde impar): quedan como están, y la selección prefiere las variantes sin ellos
    mixed = bool(options.get("dominant"))
    if mixed:
        # el modo quad-dominante deja triángulos: se juntan por parejas antes de limpiar polos
        new_faces, report["triangles_merged"] = quadopt.merge_triangles(new_points, new_faces)
    grid = reference.grid
    project = lambda points_: grid.closest(points_)[0]  # noqa: E731
    new_points = np.asarray(new_points, dtype=float)
    if pole_cleanup:
        before = quadopt.pole_count(new_faces)
        new_points, new_faces, report["pole_operations"] = quadopt.clean_poles(new_points, new_faces, depth=int(options.get("pole_depth", 3)),
                                                                       mixed=mixed)
        log("polos {} -> {}".format(before, quadopt.pole_count(new_faces)))
    trace("polos", new_points, new_faces)
    regular = int(options.get("regular", REGULAR_ITERATIONS))
    relaxed = quadopt.relax(new_points, new_faces, relax, project) if relax > 0 else new_points
    plain, degenerate, left = quadopt.fix_degenerate(relaxed, new_faces, project)
    report["degenerate"] = (degenerate, left)
    new_points = fit.guarded_project(plain, new_faces, project)
    if regular and relax > 0:
        # dos formas de dejar los quads: relajado por centroides, o cuadrados (regularize + fix_deformed). Gana
        # la que da el menor índice de fealdad: en piezas muy finas (la criatura) cuadrar suma saltos de tamaño
        square = quadopt.regularize(relaxed, new_faces, regular, project)
        square, _, _ = quadopt.fix_degenerate(square, new_faces, project)
        square, deformed, left = quadopt.fix_deformed(square, new_faces, project,
                                                      spread=int(options.get("regular_spread", REGULAR_SPREAD)))
        square = fit.guarded_project(square, new_faces, project)
        ugly = [_ugliness(p, new_faces, work_mesh, grid) for p in (new_points, square)]
        report["regular"] = {"deformed": (deformed, left), "ugliness": ugly, "chosen": "square" if ugly[1] <= ugly[0] else "relax"}
        log("quads cuadrados: fealdad {:.2f} -> {:.2f} ({})".format(ugly[0], ugly[1], report["regular"]["chosen"]))
        if ugly[1] <= ugly[0]:
            new_points = square
    trace("proyectada", new_points, new_faces)
    if options.get("fit", True):
        # acerca el retopo a lo que ninguna cara cubre (puntas, bordes finos); los bordes abiertos no se mueven
        locked = Mesh(new_points, new_faces).boundary_vertices
        new_points, report["fit"] = fit.fit_to_source(new_points, new_faces, reference, project, locked=locked, log=log)
        trace("ajustada", new_points, new_faces)

    if symmetric:
        edge = symmetry.plane_edge(new_points, new_faces, center)
        new_points = snap_cut_border(new_points, new_faces, center, symmetry.WELD_EDGE_FRACTION * edge)
        new_points, new_faces, report["welded"] = symmetry.mirror_mesh(
            new_points, new_faces, center, symmetry.WELD_EDGE_FRACTION * edge)
        new_faces = _dissolve_doublets(new_faces)
    new_points, new_faces = compact(new_points, new_faces)
    return new_points, new_faces, report


# ------------------------------------------------------------------ mejor de varias variantes

# Los motores son caóticos con piezas finas: con los mismos ajustes los dedos se salvan o se pierden
# casi al azar (QuadriFlow en el humano: error de manos de 0,28 % a 3,1 % y total de caras de 3.400 a
# 8.000 para 5.000 pedidas). Se prueban variantes baratas y se queda la mejor (fuerza bruta acotada).
VARIANT_JITTER = 0.02
# no valen las variantes con más de este múltiplo de los polos de la que menos tiene
VARIANT_POLE_SLACK = 1.6
# entre las que valen, coste = calidad / mejor calidad + POLE_WEIGHT * polos / menos polos. Humano a 5.000
# caras: engordado 2,0 da error 0,053 % con 220 polos y engordado 1,0 da 0,114 % con 150: gana la forma
POLE_WEIGHT = 0.5
# cada 1 % de caras que no son quads suma esto al coste: un borde de agujero impar no se cierra solo con
# quads y deja un triángulo, así que a veces no hay variante pura; que gane la que menos deja
NON_QUAD_WEIGHT = 1.0
# peso de lo feo (quality.defect_score) en el coste: quads deformados, saltos de tamaño, caras dobladas...
DEFECT_WEIGHT = 1.0
# calidad de forma = error medio + QUALITY_MAX_WEIGHT * error máximo (en % de la diagonal): el máximo es
# lo que se ve (un dedo perdido) y apenas mueve la media
QUALITY_MAX_WEIGHT = 0.1
# entre variantes con calidad dentro de esta fracción de la mejor gana la de más loops cerrados
QUALITY_TIE = 0.03
# con simetría pedida se prueban además variantes sin ella (una por cada 3, y al menos una; con PLAIN_HALF_FROM
# variantes o más, la mitad): gana la simétrica solo si no deja más fallos visibles (quality.defect_score) que la
# mejor asimétrica. A 25.000 caras en la criatura sin simetría salían 0,3-0,9 % de caras volteadas; con ella, 3-5 %
PLAIN_HALF_FROM = 6
# solo cuentan las variantes de solo quads (% mínimo); si ninguna lo es, todas
VARIANT_MIN_QUADS = 99.99
# solo valen las variantes cuyo nº de caras cae en esta ventana alrededor del objetivo (si ninguna, todas)
VARIANT_FACE_WINDOW = (0.8, 1.25)
# (grosor mínimo en aristas, fracción del hueco que se puede ocupar) del engordado que prueba QuadriFlow, en
# este orden. Medido (humano y águila a 5.000 caras): con 2,0 y 0,25 la forma es mejor y estable entre
# semillas; con 1,0 salen menos polos; valores más altos del hueco o del grosor vuelven caótico el motor.
QUADRIFLOW_INFLATE_GRID = ((2.0, 0.25), (1.0, 0.25), (1.5, 0.25))
# Las rondas impares de 3 variantes (la 4 a la 6, la 10 a la 12...) repiten esos engordados con más densidad en los
# tubos finos (thin_features): en la criatura a 25.000 caras baja la fealdad media de 2,6 a 1,9 (sin simetría) y de 6,0
# a 4,8 (con ella) y el error máximo; en el humano y el águila no ayuda (por eso no va en todas): se ofrece como
# candidata más y la elección se queda con la mejor. Los tubos finos son donde QuadriFlow pliega las muñecas.
THIN_VARIANT = {"thin_features": True, "thin_epr": 3.0, "thin_max": 4.0}


def variant_plan(count, engine="instant_meshes"):
    """[(factor de caras, opciones)] de las variantes que se prueban."""
    plan = []
    for k in range(count):
        if engine == "quadriflow":
            ratio, gap = QUADRIFLOW_INFLATE_GRID[k % len(QUADRIFLOW_INFLATE_GRID)]
            extra = THIN_VARIANT if (k // len(QUADRIFLOW_INFLATE_GRID)) % 2 else {}
            plan.append((1.0, dict({"inflate": {"thickness_ratio": ratio, "gap_fraction": gap}, "seed": k}, **extra)))
        else:
            plan.append((1.0 + VARIANT_JITTER * (k // 2), {"thin_features": bool(k % 2)}))
    return plan


# una variante nueva empieza solo si, con la más lenta de las ya terminadas por este margen, acaba dentro del presupuesto
VARIANT_TIME_MARGIN = 1.1
# si aun así pasa de este múltiplo del presupuesto, se cancelan las variantes en marcha
HARD_TIME_FACTOR = 1.3


def can_start_variant(elapsed, finished_durations, time_budget):
    """True si conviene empezar otra variante: aún no hay ninguna terminada (no se sabe cuánto tarda) o, a lo que han
    tardado las demás, acabaría antes de `time_budget`. Así el tiempo total no pasa del presupuesto aunque las variantes
    sin simetría (modelo entero) tarden el doble."""
    if not finished_durations:
        return True
    return elapsed + VARIANT_TIME_MARGIN * max(finished_durations) <= time_budget


class _OutOfTime(EngineError):
    """La variante no empieza porque ya no cabe en el presupuesto de tiempo."""


def variant_quality(variant):
    """Calidad de forma de una variante (menos es mejor): error medio + QUALITY_MAX_WEIGHT * máximo."""
    return variant["error"] + QUALITY_MAX_WEIGHT * variant.get("error_max", 0.0)


def choose_variant(variants, target_faces, require_pure=True):
    """Índice de la mejor variante (ver _choose_variant). Si hay variantes simétricas y asimétricas (campo
    "symmetric"), se elige la mejor de cada clase y gana la simétrica salvo que deje más fallos visibles
    ("defects") que la asimétrica (en un empate gana la simétrica): así pedir simetría nunca empeora el resultado."""
    sym = [i for i, v in enumerate(variants) if v.get("symmetric") is True]
    plain = [i for i, v in enumerate(variants) if v.get("symmetric") is False]
    if not (sym and plain):
        return _choose_variant(variants, target_faces, require_pure)
    best_sym = sym[_choose_variant([variants[i] for i in sym], target_faces, require_pure)]
    best_plain = plain[_choose_variant([variants[i] for i in plain], target_faces, require_pure)]
    return best_sym if variants[best_sym].get("defects", 0.0) <= variants[best_plain].get("defects", 0.0) else best_plain


def with_plain_variants(plan):
    """(plan con variantes sin simetría añadidas, índices de esas). Las nuevas repiten ajustes de las simétricas
    (primero el de la segunda variante, que en el humano sale mejor; luego el de la primera, engordado 2,0, que en la
    criatura deja las alas limpias: sus plumas finas tenían 4-6 % de caras dobladas con 1,0 y 1 % con 2,0; luego la
    de tubos finos) y van alternadas con ellas, para que si se acaba el tiempo haya ya de las dos clases."""
    count = len(plan)
    sources = [k for k in (1, 0, 4, 3, 2, 5) if k < count] or [0]
    wanted = max(1, count // (2 if count >= PLAIN_HALF_FROM else 3))
    extra = [plan[sources[j % len(sources)]] for j in range(wanted)]
    full, plain = [], set()
    for k in range(max(count, wanted)):
        if k < count:
            full.append(plan[k])
        if k < wanted:
            plain.add(len(full))
            full.append(extra[k])
    return full, plain


def _choose_variant(variants, target_faces, require_pure=True):
    """Índice de la mejor variante, por este orden:
    1. las que caen en la ventana de caras y son solo quads (si no hay, todas);
    2. las que no pasan de VARIANT_POLE_SLACK veces los polos de la que menos tiene;
    3. la de menor coste: calidad de forma (error medio y máximo), polos y caras que no son quads; a
       igualdad (QUALITY_TIE), la de más loops cerrados.
    variants: lista de dicts con "faces_count", "poles", "error" y, si hay, "error_max", "quad_percent" y
    "closed_percent" (función pura)."""
    low, high = VARIANT_FACE_WINDOW
    inside = [i for i, v in enumerate(variants) if low * target_faces <= v["faces_count"] <= high * target_faces]
    pool = inside or list(range(len(variants)))
    pure = [i for i in pool if variants[i].get("quad_percent", 100.0) >= VARIANT_MIN_QUADS]
    pool = (pure if require_pure else None) or pool
    fewest = max(min(variants[i]["poles"] for i in pool), 1)
    allowed = [i for i in pool if variants[i]["poles"] <= VARIANT_POLE_SLACK * fewest]
    best_quality = max(min(variant_quality(variants[i]) for i in allowed), 1e-9)

    def cost(i):
        return (variant_quality(variants[i]) / best_quality + POLE_WEIGHT * variants[i]["poles"] / fewest
                + NON_QUAD_WEIGHT * (100.0 - variants[i].get("quad_percent", 100.0))
                + DEFECT_WEIGHT * variants[i].get("defects", 0.0))
    lowest = min(cost(i) for i in allowed)
    close = [i for i in allowed if cost(i) <= lowest * (1.0 + QUALITY_TIE)]
    return max(close, key=lambda i: (variants[i].get("closed_percent", 0.0), -cost(i)))


def best_retopo(points, faces, target_faces, engine, executable, variants=3, time_budget=120.0, tick=None,
                log=None, workers=None, good_enough=None, **kwargs):
    """La mejor de varias variantes de retopo(): menos error de forma sin pasarse de polos ni de caras.

    Las variantes van a la vez (resources.safe_workers: pocos procesos, solo con memoria de sobra).
    Los hilos de trabajo no llaman a `tick` (en Maya solo se puede desde el hilo principal): solo
    vigilan una bandera de cancelación, y es el hilo principal el que espera, llama a tick y cancela.
    variants=3 es una ronda (calidad normal); 6 es calidad alta (el doble de tiempo, error algo menor).
    Se detiene al agotar `time_budget` segundos (siempre espera a la primera que termina) o cuando
    `good_enough(variante)` da True. Devuelve (puntos, caras, informe) con informe["variants"].
    """
    import threading
    import time
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    from . import resources
    log = log or (lambda message: None)
    start = time.time()          # el presupuesto cuenta desde aquí: la preparación de la malla también tarda
    source = Mesh(points, faces)
    count = max(1, int(variants))
    base_options = kwargs.pop("options", None) or {}
    cancel = threading.Event()
    # lo común a todas las variantes (reducir la malla, rejilla del original, grosor) se hace una vez
    plan = variant_plan(count, engine)
    shared = prepare(points, faces, target_faces, engine, kwargs.get("symmetric"), kwargs.get("timeout", 600), tick,
                     kwargs.get("min_piece", 0.01),
                     dict(base_options, inflate=any(extra.get("inflate") for _, extra in plan)), log)
    shared_plain, plain_variants = None, set()
    if shared.symmetric:
        # red de seguridad: con simetría se prueban además variantes sin ella (modelo entero) y se entrega la
        # simétrica solo si no deja más fallos visibles; así pedir la simetría nunca devuelve algo peor
        plan, plain_variants = with_plain_variants(plan)
        shared_plain = prepare(points, faces, target_faces, engine, False, kwargs.get("timeout", 600), tick,
                               kwargs.get("min_piece", 0.01),
                               dict(base_options, inflate=any(extra.get("inflate") for _, extra in plan)), log)
    if shared.report["pieces_removed"] or shared.symmetric:
        reference = metrics.SurfaceReference(source, FIT_SAMPLES)
    else:
        reference = shared.reference

    def worker_tick():
        if cancel.is_set():
            raise EngineError("cancelado")

    durations = []

    def run(index):
        if not can_start_variant(time.time() - start, durations, time_budget):
            raise _OutOfTime("sin tiempo")
        began = time.time()
        factor, extra = plan[index]
        prepared = shared_plain if index in plain_variants else shared
        p, f, report = retopo(points, faces, int(round(target_faces * factor)), engine, executable,
                              tick=worker_tick, options=dict(base_options, prepared=prepared, **extra), **kwargs)
        durations.append(time.time() - began)
        result = Mesh(p, f)
        stats = metrics.topology_stats(result)
        shape = reference.error(result)
        look = (quality.shape_stats(result), quality.uniformity_stats(result),
                quality.defect_stats(result, source, grid=reference.grid))
        return {"defects": quality.defect_score(*look, faces=result.face_count), "index": index, "points": p, "faces": f, "report": report, "factor": factor, "options": extra,
                "symmetric": bool(prepared.symmetric),
                "error": shape["mean_percent"], "error_max": shape["max_percent"], "poles": stats["pole_count"],
                "faces_count": stats["faces"], "quad_percent": stats["quad_percent"],
                "closed_percent": metrics.loop_stats(result)["loop_closed_percent"]}

    # cada variante solo gasta memoria por la malla reducida que ve el motor, no por la de entrada
    workers = resources.safe_workers(min(source.face_count, len(shared.tris)), workers)
    log("{} variantes, {} a la vez".format(len(plan), workers))
    tried = []
    skipped = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    pending = {pool.submit(run, index) for index in range(len(plan))}
    try:
        while pending:
            done, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    tried.append(future.result())
                except _OutOfTime:
                    skipped += 1
                    continue
                except EngineError as exc:
                    if not cancel.is_set():
                        log("variante fallida: {}".format(exc))
                    continue
                v = tried[-1]
                log("variante {}{}: {} caras, {} polos, error {:.3f}% (máx. {:.2f}%), fallos visibles {:.2f}".format(
                    v["index"] + 1, " (con simetría)" if v["symmetric"] else "", v["faces_count"], v["poles"], v["error"],
                    v["error_max"], v["defects"]))
                if good_enough and good_enough(v):
                    log("variante suficientemente buena: se detiene la búsqueda")
                    cancel.set()
            if tick:
                tick()
            if tried and not cancel.is_set() and time.time() - start > HARD_TIME_FACTOR * time_budget:
                log("tiempo agotado tras {} variantes".format(len(tried)))
                cancel.set()
            if cancel.is_set():
                for future in pending:
                    future.cancel()
    finally:
        # si tick cancela (excepción) o algo falla, los procesos de motor se detienen y se espera a los hilos
        cancel.set()
        pool.shutdown(wait=True)
    if skipped:
        log("{} de {} variantes no se probaron para no pasar del tiempo máximo ({:.0f} s)".format(skipped, len(plan), time_budget))
    if not tried:
        raise EngineError("Ninguna variante del retopo terminó.")
    tried.sort(key=lambda v: v["index"])
    chosen = choose_variant(tried, target_faces)
    best = tried[chosen]
    symmetry_report = {"asked": bool(shared.symmetric), "applied": bool(best["symmetric"])}
    if shared.symmetric:
        for name, flag in (("symmetric", True), ("plain", False)):
            scores = [t["defects"] for t in tried if t["symmetric"] is flag]
            symmetry_report[name] = min(scores) if scores else None
        if not best["symmetric"]:
            log("simetría no aplicada: dejaba más fallos visibles ({:.2f} frente a {:.2f})".format(
                symmetry_report["symmetric"] if symmetry_report["symmetric"] is not None else float("nan"),
                symmetry_report["plain"]))
    report = dict(best["report"], chosen=chosen, symmetry=symmetry_report,
                  variants=[{k: t[k] for k in ("index", "factor", "options", "faces_count", "poles", "error",
                                               "error_max", "quad_percent", "closed_percent", "defects", "symmetric")}
                            for t in tried])
    return best["points"], best["faces"], report
