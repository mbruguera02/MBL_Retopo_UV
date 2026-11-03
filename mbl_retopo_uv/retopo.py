# -*- coding: utf-8 -*-
"""Retopología con Instant Meshes (https://github.com/wjakob/instant-meshes) por línea de comandos.

Flags usados, tal y como los lista Instant Meshes en su ayuda (src/main.cpp):
  -o <archivo>   salida OBJ/PLY (activa el modo batch, sin ventana)
  -f <n>         nº de caras objetivo
  -c <grados>    ángulo diedro para aristas duras (creases)
  -S <n>         iteraciones de suavizado y reproyección (por defecto 2)
  -r 4 -p 4      simetría de orientación y de posición 4 -> quads
  -D             malla quad-dominante en vez de quads puros
  -d             algoritmos deterministas (más lentos, mismo resultado siempre)
  -b             alinear a los bordes (solo si la malla está abierta)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from . import config, meshbuild
from .common import RetopoError, components, dag_path, default_log, mesh_shape, short_name
from mbl_engine import process as _process
from mbl_engine import retopo as _core
from mbl_engine.process import STALL_SECONDS  # noqa: F401
from mbl_engine.process import _cpu_seconds  # noqa: F401
from mbl_engine.repair import (  # noqa: F401  (núcleo del Motor MBL, sin Maya)
    read_obj,
    write_obj,
    write_ply,
    vertex_areas,
    _edges,
    _edge_faces,
    _remove_degenerate,
    _remove_overshared_edges,
    _dissolve_doublets,
    _orient_consistently,
    _split_bowties,
    _collapse_short_edges,
    _remove_small_pieces,
    _fill_holes,
    repair_topology,
    make_manifold,
    fill_new_holes,
    OPENING_MATCH_EDGES,
)

OBJ_EXPORT_OPTIONS = "groups=0;ptgroups=0;materials=0;smoothing=0;normals=0"


def build_command(executable, source, destination, settings):
    """Lista de argumentos para subprocess. Función pura (se prueba sin Maya)."""
    faces = int(settings.get("_im_faces") or settings["retopo_target_faces"])
    if settings["retopo_pure_quads"] and not settings.get("_im_faces"):
        # en modo quads puros Instant Meshes subdivide al final y multiplica las caras
        # (con el águila de prueba: 5000 pedidas -> 19298; 1250 -> 3943, unas 3,15 veces)
        faces = max(int(faces / 3.15), 1)
    command = [executable, source, "-o", destination,
               "-f", str(faces),
               "-r", "4", "-p", "4",
               "-S", str(int(settings["retopo_smooth"]))]
    if not settings["retopo_pure_quads"]:
        command.append("-D")
    if settings["retopo_crease_angle"] > 0:
        command += ["-c", "{:g}".format(float(settings["retopo_crease_angle"]))]
    if settings["retopo_deterministic"]:
        command.append("-d")
    if settings.get("_align_boundaries_now"):
        command.append("-b")
    if settings.get("_density_file"):
        # densidad pintada (nuestra versión de Instant Meshes): sustituye a la adaptativa
        command += ["--density", settings["_density_file"]]
    elif settings.get("retopo_adaptive", 0) > 0 and settings.get("_adaptive_supported"):
        # opciones de nuestra versión de Instant Meshes (engine/): densidad por curvatura
        command += ["--adaptive", "{:g}".format(float(settings["retopo_adaptive"])),
                    "--adaptive-ratio", "{:g}".format(float(settings.get("retopo_adaptive_ratio", 3.0)))]
    if settings.get("_orient_file"):
        # direcciones guía (curvatura principal) para que los loops sigan la forma
        command += ["--orient", settings["_orient_file"]]
    return command


_ENGINE_HELP = {}


def _engine_help(executable):
    """Ayuda (-h) del ejecutable, cacheada por ruta y fecha. b"" si no se puede leer."""
    try:
        key = (executable, os.path.getmtime(executable))
    except OSError:
        return b""
    if key not in _ENGINE_HELP:
        kwargs = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run([executable, "-h"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, timeout=20, **kwargs)
            _ENGINE_HELP[key] = result.stdout or b""
        except (OSError, subprocess.SubprocessError):
            _ENGINE_HELP[key] = b""
    return _ENGINE_HELP[key]


def engine_supports_adaptive(executable):
    """True si el ejecutable es nuestra versión de Instant Meshes (su ayuda lista --adaptive)."""
    return b"--adaptive" in _engine_help(executable)


def engine_supports_density(executable):
    """True si nuestra versión de Instant Meshes admite densidad pintada (--density)."""
    return b"--density" in _engine_help(executable)


def engine_supports_orient(executable):
    """True si nuestra versión de Instant Meshes admite direcciones guía (--orient)."""
    return b"--orient" in _engine_help(executable)


def run_instant_meshes(command, destination, timeout, log_path, tick=None):
    """Ejecuta Instant Meshes (núcleo: mbl_engine/process.py); errores como RetopoError."""
    try:
        return _process.run_instant_meshes(command, destination, timeout, log_path, tick)
    except _process.EngineError as exc:
        raise RetopoError(str(exc))


def _export_obj(transform, path):
    cmds.loadPlugin("objExport", quiet=True)
    previous = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(transform, replace=True)
        cmds.file(path, force=True, exportSelected=True, type="OBJexport",
                  options=OBJ_EXPORT_OPTIONS, preserveReferences=False)
    finally:
        if previous:
            cmds.select(previous, replace=True)
        else:
            cmds.select(clear=True)


def _has_border(transform):
    shape = mesh_shape(transform)
    edges = cmds.polyListComponentConversion(shape + ".f[*]", toEdge=True, border=True)
    return bool(edges)


# ---------------------------------------------------------------------------------------------
# Reparación de la topología que devuelve Instant Meshes (Python puro, sobre el OBJ, sin Maya)
#
# Con mallas sucias (escaneos, IA) Instant Meshes devuelve caras degeneradas o duplicadas, aristas
# con más de 2 caras, vértices de solo 2 aristas (dos quads que comparten dos aristas: Unfold3D
# los rechaza), vértices "pajarita" y caras con el orden de vértices al revés que sus vecinas.
# ---------------------------------------------------------------------------------------------

def face_pieces(transform):
    """Piezas conectadas de la malla: lista de listas de caras, de mayor a menor.

    Con numpy (enganche + salto de punteros): 0,24 s frente a 0,70 s en Python puro con 528.000 caras.
    """
    shape = mesh_shape(transform)
    fn = om.MFnMesh(dag_path(shape))
    if cmds.polyEvaluate(shape, shell=True) == 1:
        return [list(range(fn.numPolygons))]
    counts, vertices = fn.getVertices()
    counts = np.array(counts, dtype=np.int64)
    verts = np.array(vertices, dtype=np.int64)
    face_start = np.cumsum(counts) - counts
    first = verts[np.repeat(face_start, counts)]   # primer vértice de la cara de cada vértice
    parent = np.arange(fn.numVertices, dtype=np.int64)
    while True:
        pu, pv = parent[first], parent[verts]
        if np.array_equal(pu, pv):
            break
        np.minimum.at(parent, np.maximum(pu, pv), np.minimum(pu, pv))
        while True:
            jumped = parent[parent]
            if np.array_equal(jumped, parent):
                break
            parent = jumped
    face_label = parent[verts[face_start]]
    order = np.argsort(face_label, kind="stable")
    _, starts = np.unique(face_label[order], return_index=True)
    return sorted((group.tolist() for group in np.split(order, starts[1:])), key=len, reverse=True)


def delete_small_pieces(transform, min_ratio):
    """Borra las piezas sueltas con menos de min_ratio de las caras (nunca la mayor). Devuelve cuántas."""
    pieces = face_pieces(transform)
    total = sum(len(p) for p in pieces)
    small = [p for p in pieces[1:] if len(p) < total * min_ratio]
    if small:
        cmds.delete(components(transform, "f", [f for p in small for f in p]))
    return len(small)


def border_points(transform):
    """Posiciones (array Nx3, mundo) de los vértices de borde de una malla."""
    path = dag_path(mesh_shape(transform))
    fn = om.MFnMesh(path)
    points = fn.getPoints(om.MSpace.kWorld)
    border = set()
    edge_it = om.MItMeshEdge(path)
    while not edge_it.isDone():
        if edge_it.onBoundary():
            border.update((edge_it.vertexId(0), edge_it.vertexId(1)))
        edge_it.next()
    return np.array([(points[i].x, points[i].y, points[i].z) for i in sorted(border)], dtype=float).reshape(-1, 3)


def border_loops(transform):
    """Bordes abiertos agrupados en lazos: lista de listas de aristas."""
    path = dag_path(mesh_shape(transform))
    edge_it = om.MItMeshEdge(path)
    by_vertex, edges = {}, {}
    while not edge_it.isDone():
        if edge_it.onBoundary():
            a, b = edge_it.vertexId(0), edge_it.vertexId(1)
            edges[edge_it.index()] = (a, b)
            by_vertex.setdefault(a, []).append(edge_it.index())
            by_vertex.setdefault(b, []).append(edge_it.index())
        edge_it.next()
    loops, seen = [], set()
    for start in edges:
        if start in seen:
            continue
        loop, stack = [], [start]
        seen.add(start)
        while stack:
            edge = stack.pop()
            loop.append(edge)
            for vertex in edges[edge]:
                for other in by_vertex[vertex]:
                    if other not in seen:
                        seen.add(other)
                        stack.append(other)
        loops.append(loop)
    return loops


def faces_facing_away(source, target):
    """Caras de target cuya normal apunta al revés que la superficie más cercana de source."""
    source_path = dag_path(mesh_shape(source))
    intersector = om.MMeshIntersector()
    intersector.create(source_path.node(), source_path.inclusiveMatrix())
    target_path = dag_path(mesh_shape(target))
    fn = om.MFnMesh(target_path)
    away = []
    face_it = om.MItMeshPolygon(target_path)
    while not face_it.isDone():
        closest = intersector.getClosestPoint(face_it.center(om.MSpace.kWorld))
        if fn.getPolygonNormal(face_it.index(), om.MSpace.kWorld) * om.MVector(closest.normal) < 0:
            away.append(face_it.index())
        face_it.next()
    return away


def orient_pieces_like(source, target):
    """Da la vuelta a las piezas enteras que miran mayoritariamente al revés que el original.

    Se gira la pieza completa y no cara a cara: girar caras sueltas rompe la coherencia con sus
    vecinas y crea geometría no-manifold. Devuelve cuántas caras se han girado.
    """
    away = set(faces_facing_away(source, target))
    flip = []
    for piece in face_pieces(target):
        if sum(1 for f in piece if f in away) * 2 > len(piece):
            flip.extend(piece)
    if flip:
        # normalMode=0 invierte las caras indicadas
        cmds.polyNormal(components(target, "f", flip), normalMode=0, userNormalMode=0, constructionHistory=True)
    return len(flip)


ENGINES = {"auto": "Automático", "maya": "Maya (polyRetopo)", "instant_meshes": "Instant Meshes",
           "mbl": "Motor MBL"}


def _prepared_copy(transform, settings, log, tick):
    """Copia del original sin piezas sueltas pequeñas (restos flotantes de escaneos o de IA).

    Los motores se confunden con ellas; el original no se toca.
    """
    name = short_name(transform)
    prepared = cmds.ls(cmds.duplicate(transform, name=name + "_mblTmp", returnRootsOnly=True)[0], long=True)[0]
    cmds.delete(prepared, constructionHistory=True)
    removed = delete_small_pieces(prepared, settings["retopo_min_piece"] / 100.0)
    if removed:
        log("{}: {} piezas sueltas pequeñas ignoradas para el retopo.".format(name, removed))
    if tick:
        tick()
    return prepared


def _maya_engine(transform, settings, log, tick):
    """polyRetopo de Maya. Con el águila de prueba (5.000 caras) dio 100% quads, 5 veces menos polos
    de 6+ aristas y 2,7 veces menos error medio que Instant Meshes. Devuelve (malla, arreglos)."""
    name = short_name(transform)
    prepared = _prepared_copy(transform, settings, log, tick)
    target = int(settings["retopo_target_faces"])
    kwargs = {"topologyRegularity": MAYA_TOPOLOGY_REGULARITY}
    curves = settings.get("_guide_curves") or []
    if curves:
        from . import guides
        edges = guides.imprint(prepared, curves)
        tag = guides.tag_edges(prepared, edges)
        if tag:
            kwargs.update(useFeatureTags=True, featureTags=tag)
            log("{}: {} guía(s) de flujo marcadas sobre el modelo ({} aristas).".format(name, len(curves), len(edges)))
    log("{}: retopo de Maya (polyRetopo) con {} caras objetivo...".format(name, target))
    source_border = border_points(prepared)
    source_openings = len(border_loops(prepared))
    try:
        backup = cmds.duplicate(prepared)[0] if curves else None
        cmds.polyRetopo(prepared, targetFaceCount=target, replaceOriginal=True, **kwargs)
        if backup:
            # con guías polyRetopo es a veces inestable (esfera cortada: 400 caras pedidas -> 1.323):
            # si se dispara, se repite sin guías
            if cmds.polyEvaluate(prepared, face=True) > GUIDE_MAX_FACES_RATIO * target:
                log("{}: con las guías salieron {} caras (pedidas {}); se repite sin guías. Prueba a "
                    "simplificar los trazos.".format(name, cmds.polyEvaluate(prepared, face=True), target),
                    "warning")
                cmds.delete(prepared)
                prepared = backup
                cmds.polyRetopo(prepared, targetFaceCount=target, replaceOriginal=True,
                                topologyRegularity=MAYA_TOPOLOGY_REGULARITY)
            else:
                cmds.delete(backup)
        cmds.delete(prepared, constructionHistory=True)
    except RuntimeError as exc:
        cmds.delete(prepared)
        raise RetopoError("{}: el retopo de Maya falló: {}. Prueba con 'Limpiar' antes o con el motor "
                          "Instant Meshes.".format(name, str(exc).strip()))
    if tick:
        tick()
    fixes = []
    if len(border_loops(prepared)) > source_openings:
        points, faces = mesh_arrays(prepared)
        points, faces, filled = fill_new_holes(points, faces, source_border, int(settings["retopo_fill_holes"]))
        if filled:
            cmds.delete(prepared)
            prepared = meshbuild.create_mesh(points, faces, name + "_mblTmp")
            fixes.append("{} agujeros nuevos cerrados".format(filled))
    new = cmds.ls(cmds.rename(prepared, name + "_retopo"), long=True)[0]
    return new, fixes


# cifras del analizador de caras mínimas por malla original (nombre largo), para el semáforo
_RECOMMENDED = {}


def recommend_faces(transform, requested=0):
    """Caras mínimas y de máximo detalle de una malla (mbl_engine.analyze). Devuelve (cifras, aviso); el aviso es
    None si `requested` llega al mínimo. Tarda de menos de un segundo a unos 5 s según el tamaño de la malla."""
    from mbl_engine import analyze, decimate
    points, faces = mesh_arrays(transform)
    tris = _core.Mesh(points, faces).triangles()[0]
    report, warning = analyze.recommend(points, tris, int(requested), decimator=decimate.find_decimator())
    figures = {"minimum": report["minimum"], "detail": report["detail"], "sheet_percent": report["sheet_percent"]}
    _RECOMMENDED[cmds.ls(transform, long=True)[0]] = figures
    return figures, warning


def recommended_faces(transform):
    """Cifras ya calculadas de una malla original, o las calcula. None si no se pueden calcular."""
    if not transform or not cmds.objExists(transform):
        return None
    key = cmds.ls(transform, long=True)[0]
    if key not in _RECOMMENDED:
        try:
            recommend_faces(transform)
        except Exception:
            return None
    return _RECOMMENDED.get(key)


# a partir de estas caras objetivo el Motor MBL necesita varios GB de memoria por proceso (unos 54 KB por cara pedida)
HEAVY_FACES = 100000


def _log_recommendation(transform, settings, log):
    """Avisa antes del retopo si se piden menos caras de las recomendadas (o demasiadas). Es informativo: nunca detiene
    el proceso."""
    if int(settings["retopo_target_faces"]) > HEAVY_FACES and settings.get("retopo_engine") in ("auto", "mbl"):
        log("{}: pides {:,} caras: el Motor MBL necesita muchísima memoria con tantas (unos 54 KB por cara pedida) y tarda "
            "mucho. Si falta memoria se detiene solo y se usa otro motor.".format(
                short_name(transform), int(settings["retopo_target_faces"])).replace(",", "."), "warning")
    try:
        figures, warning = recommend_faces(transform, int(settings["retopo_target_faces"]))
    except Exception:
        import traceback
        traceback.print_exc()
        return
    from mbl_engine.analyze import thousands
    log("{}: caras recomendadas: al menos {} (máximo detalle {}).".format(
        short_name(transform), thousands(figures["minimum"]), thousands(figures["detail"])))
    if warning:
        log("{}: {}".format(short_name(transform), warning), "warning")


def retopo(transform, settings, log=default_log, tick=None):
    """Retopologiza una malla con el motor elegido. Devuelve el transform nuevo; el original queda oculto."""
    mesh_shape(transform)
    if not settings.get("_symmetry_half"):
        _log_recommendation(transform, settings, log)
    settings = _resolve_guides_and_detail(transform, settings, log)
    engine = settings.get("retopo_engine", "maya")
    if engine == "mbl":
        return _mbl_retopo(transform, settings, log, tick)
    if (engine == "auto" and not settings.get("_symmetry_half") and not settings.get("_guide_curves")
            and not settings.get("_detail") and config.find_quadriflow()):
        return _auto_with_mbl(transform, settings, log, tick)
    return _classic_retopo(transform, settings, log, tick)


def _classic_retopo(transform, settings, log, tick):
    """Retopo con Maya o Instant Meshes (con simetría y guías)."""
    mode = settings.get("retopo_symmetry", "off")
    if mode != "off" and settings.get("_guide_curves") and not settings.get("_symmetry_half"):
        # con guías, polyRetopo sobre la mitad cortada se dispara a veces (esfera: 800 -> 2.700 caras)
        log("{}: con guías de flujo el retopo se hace entero, sin reflejar la mitad.".format(short_name(transform)))
        mode = "off"
    if mode != "off" and not settings.get("_symmetry_half"):
        from . import symmetry
        symmetric, mean, p95 = symmetry.is_symmetric(transform)
        if symmetric or mode == "x":
            return _symmetric_retopo(transform, settings, log, tick, mean, p95)
        symmetry.log_skip(transform, mean, p95, log)
    engine = settings.get("retopo_engine", "maya")
    if engine in ("auto", "maya"):
        # Automático llega aquí cuando el Motor MBL no entra (mitades, guías, detalle pintado) o como el
        # candidato clásico que se compara con él: el clásico es Maya
        engine = "maya"
        new, fixes = _maya_engine(transform, settings, log, tick)
    else:
        engine = "instant_meshes"
        new, fixes = _instant_meshes_engine(transform, settings, log, tick)
    engine_name = ENGINES[engine]
    return _finish(transform, new, fixes, engine_name, settings, log, tick)


def _mbl_engine(transform, settings, log, tick):
    """Motor MBL (núcleo mbl_engine): QuadriFlow con las piezas finas engordadas, ajuste final y la mejor
    de varias variantes. Siempre sobre el modelo entero (QuadriFlow cierra los bordes). Devuelve la malla."""
    name = short_name(transform)
    executable = config.find_quadriflow()
    if not executable:
        raise RetopoError("No se encuentra QuadriFlow (bin/quadriflow.exe).")
    points, faces = mesh_arrays(transform)
    if tick:
        tick()
    try:
        new_points, new_faces, report = _core.best_retopo(
            points, faces, int(settings["retopo_target_faces"]), "quadriflow", executable,
            variants=int(settings.get("mbl_variants", 3)), time_budget=float(settings.get("mbl_time_budget", 270)),
            tick=tick, log=lambda message: log("{}: Motor MBL: {}".format(name, message)),
            min_piece=settings["retopo_min_piece"] / 100.0,
            symmetric={"auto": None, "x": True, "off": False}.get(settings.get("retopo_symmetry", "off")))
    except _process.EngineError as exc:
        raise RetopoError("Motor MBL: {}".format(exc))
    new = meshbuild.create_mesh(new_points, new_faces, name + "_retopo")
    chosen = report["variants"][report["chosen"]]
    log("{}: Motor MBL: variante {} de {} (engordado {}), {} caras, {:.2f}% quads{}.".format(
        name, report["chosen"] + 1, len(report["variants"]), chosen["options"]["inflate"]["thickness_ratio"],
        chosen["faces_count"], chosen["quad_percent"], ", con simetría" if chosen.get("symmetric") else ""))
    if report.get("symmetry", {}).get("asked") and not report["symmetry"]["applied"]:
        log("{}: no se aplicó la simetría porque dejaba más fallos en la costura central que el retopo sin ella."
            .format(name), "warning")
    repaired = _manifold_fixes(report.get("manifold") or {})
    if repaired:
        log("{}: Motor MBL: topología arreglada para Unfold3D: {}.".format(name, ", ".join(repaired)))
    flipped = orient_pieces_like(transform, new)
    if flipped:
        log("{}: {} caras giradas hacia fuera.".format(name, flipped))
    cmds.delete(new, constructionHistory=True)
    return new


def _mbl_retopo(transform, settings, log, tick):
    new = _mbl_engine(transform, settings, log, tick)
    return _present(transform, new, settings, log, tick)


def _candidate(transform, new):
    """Cifras de un resultado para compararlo con otro (mismo formato que las variantes del núcleo)."""
    from . import diagnostics
    from mbl_engine import quality
    from mbl_engine.mesh import Mesh
    stats = diagnostics.topology_stats(new)
    error = diagnostics.surface_error(transform, new)
    result = Mesh(*mesh_arrays(new))
    look = (quality.shape_stats(result), quality.uniformity_stats(result),
            quality.defect_stats(result, Mesh(*mesh_arrays(transform))))
    return {"faces_count": stats["faces"], "poles": stats["pole_count"], "error": error["mean_percent"],
            "error_max": error["max_percent"], "quad_percent": stats.get("quad_percent", 100.0),
            "defects": quality.defect_score(*look, faces=result.face_count), "shape_bad": look[0]["bad_percent"]}


def _auto_with_mbl(transform, settings, log, tick):
    """Automático con el Motor MBL: Maya (con simetría) frente al Motor MBL; gana el de menor coste (forma,
    polos y caras que no son quads, como entre las variantes del núcleo)."""
    name = short_name(transform)
    lines = []
    classic = _classic_retopo(transform, settings, lambda message, level="info": lines.append((message, level)), tick)
    try:
        if cmds.getAttr(transform + ".visibility") is False:
            cmds.setAttr(transform + ".visibility", 1)
    except RuntimeError:
        pass
    mbl = None
    try:
        mbl = _mbl_engine(transform, settings, lambda message, level="info": lines.append((message, level)), tick)
    except RetopoError as exc:
        log("{}: Motor MBL descartado: {}.".format(name, str(exc).strip().rstrip(".")), "warning")
    if mbl is None:
        for message, level in lines:
            log(message, level)
        return classic
    cases = [_candidate(transform, classic), _candidate(transform, mbl)]
    for label, case in zip(("Maya", "Motor MBL"), cases):
        log("{}: {}: {} caras, {} polos, {:.2f}% quads, error medio {:.3f}% (máx. {:.2f}%).".format(
            name, label, case["faces_count"], case["poles"], case["quad_percent"], case["error"], case["error_max"]))
    winner = _core.choose_variant(cases, int(settings["retopo_target_faces"]), require_pure=False)
    keep, drop = (classic, mbl) if winner == 0 else (mbl, classic)
    if cmds.objExists(drop):
        cmds.delete(drop)
    if winner == 0:
        for message, level in lines:
            log(message, level)
        try:
            cmds.setAttr(transform + ".visibility", 0)
        except RuntimeError:
            pass
    if short_name(keep) != name + "_retopo":
        keep = cmds.ls(cmds.rename(keep, name + "_retopo"), long=True)[0]
    log("{}: modo automático -> {}.".format(name, "Maya" if winner == 0 else "Motor MBL"))
    if winner == 1:
        keep = _present(transform, keep, settings, log, tick)
    return keep


GUIDE_MAX_FACES_RATIO = 1.6
# barrido de parámetros de polyRetopo (humano a 2.000 caras y águila a 5.000): con regularidad 1
# (Maya usa 0,5) salen menos polos (157 -> 142 y 593 -> 560), menos polos de 6+ en el águila
# (20 -> 13) y más loops cerrados en el humano; el error medio apenas cambia. anisotropy,
# faceUniformity y targetEdgeDeviation no mejoraron nada
MAYA_TOPOLOGY_REGULARITY = 1.0


def _resolve_guides_and_detail(transform, settings, log):
    """Busca guías y detalle pintado del original (una vez) y elige el motor que los admite.

    - Detalle pintado: solo Instant Meshes MBL admite densidad variable.
    - Guías: las siguen Maya (featureTags, aristas exactas sobre el trazo) e Instant Meshes MBL
      (--orient, el campo se alinea con el trazo). En la esfera de prueba Maya pone 36 aristas
      sobre el aro con 8 polos e Instant Meshes 22 con 41 polos: solo guías -> Maya; guías y
      detalle -> Instant Meshes con las dos cosas.
    """
    if settings.get("_inputs_resolved"):
        return settings
    from . import detail, guides
    name = short_name(transform)
    settings = dict(settings, _inputs_resolved=True)
    curves = guides.guide_curves(transform)
    painted = detail.has_detail(transform)
    settings["_guide_curves"] = curves
    settings["_detail"] = painted
    engine = settings.get("retopo_engine", "maya")
    if painted:
        if engine != "instant_meshes":
            log("{}: hay zonas de detalle pintadas; se usa Instant Meshes, el único motor con densidad "
                "variable ({}x más caras en lo pintado).".format(name, settings.get("retopo_detail_density", 4)))
            settings["retopo_engine"] = "instant_meshes"
        if curves:
            log("{}: con detalle pintado, las {} guía(s) las sigue el campo de Instant Meshes (menos "
                "exacto que el motor de Maya).".format(name, len(curves)))
    elif curves:
        if engine not in ("maya", "instant_meshes"):
            log("{}: {} guía(s) de flujo; se usa el motor de Maya, que es el que las sigue.".format(name, len(curves)))
            settings["retopo_engine"] = "maya"
    return settings


def _instant_meshes_engine(transform, settings, log, tick):
    """Instant Meshes por línea de comandos + reparación de su topología. Devuelve (malla, arreglos)."""
    name = short_name(transform)
    executable = config.find_instant_meshes(settings)
    if not executable:
        raise RetopoError(
            "No encuentro Instant Meshes. Descárgalo de {} e indica la ruta del ejecutable "
            "en la sección 'Instant Meshes' de la ventana.".format(config.DOWNLOAD_URL))

    temp_dir = tempfile.mkdtemp(prefix="mbl_retopo_")
    prepared = None
    try:
        source = os.path.join(temp_dir, "input.obj")
        destination = os.path.join(temp_dir, "output.obj")
        prepared = _prepared_copy(transform, settings, log, tick)
        run_settings = dict(settings)
        detail_now = settings.get("_detail") and engine_supports_density(executable)
        curves = settings.get("_guide_curves") or []
        orient_now = (curves or settings.get("retopo_orient", 0) > 0) and engine_supports_orient(executable)
        if curves and not orient_now:
            log("{}: este Instant Meshes no admite guías (hace falta la versión MBL); se ignoran.".format(name),
                "warning")
        if detail_now or orient_now:
            # PLY propio: los valores por vértice (densidad, direcciones) van en el mismo orden
            cmds.polyTriangulate(prepared, constructionHistory=False)
            points, faces = mesh_arrays(prepared)
            source = os.path.join(temp_dir, "input.ply")
            write_ply(source, points, faces)
        if orient_now:
            from . import orient
            # arista del retopo: área total / caras pedidas (quads)
            edge = float(np.sqrt(np.sum(vertex_areas(points, faces)) / max(int(settings["retopo_target_faces"]), 1)))
            directions, weights = np.zeros_like(points), np.zeros(len(points))
            if settings.get("retopo_orient", 0) > 0:
                directions, weights = orient.curvature_directions(points, faces, edge_length=edge)
                weights = weights * float(settings["retopo_orient"])
            if curves:
                # las guías del usuario mandan sobre la curvatura en su franja
                from . import guides
                samples, tangents = guides.curve_tangents(curves, 0.25 * edge)
                guide_dirs, guide_weights = guides.guide_directions(points, samples, tangents, edge)
                use = guide_weights > 0
                directions[use], weights[use] = guide_dirs[use], guide_weights[use]
                log("{}: {} guía(s) de flujo pasadas al campo de Instant Meshes ({:.0f}% de la superficie)."
                    .format(name, len(curves), 100.0 * float(np.mean(use))))
            run_settings["_orient_file"] = os.path.join(temp_dir, "orient.txt")
            orient.write_orientation(run_settings["_orient_file"], directions, weights)
        if detail_now:
            from . import detail
            values = detail.density_values(prepared, settings.get("retopo_detail_density", 4.0))
            run_settings["_density_file"] = os.path.join(temp_dir, "density.txt")
            np.savetxt(run_settings["_density_file"], values, fmt="%.4f")
            # Instant Meshes mantiene el total de caras; para que lo no pintado no pierda detalle (en el
            # humano el error medio subía del 0.20% al 0.36%) se le piden las caras extra de lo pintado
            areas = vertex_areas(points, faces)
            factor = float(np.sum(areas * values) / max(np.sum(areas), 1e-30))
            run_settings["retopo_target_faces"] = int(round(int(settings["retopo_target_faces"]) * factor))
            log("{}: detalle pintado en el {:.0f}% de la superficie: {} caras ({} extra para el detalle).".format(
                name, 100.0 * float(np.sum(areas * (values > 1.0 + 1e-3)) / max(np.sum(areas), 1e-30)),
                run_settings["retopo_target_faces"],
                run_settings["retopo_target_faces"] - int(settings["retopo_target_faces"])))
        elif settings.get("_detail"):
            log("{}: este Instant Meshes no admite detalle pintado (hace falta la versión MBL); "
                "se usa densidad uniforme.".format(name), "warning")
        if not (detail_now or orient_now):
            _export_obj(prepared, source)

        run_settings["_align_boundaries_now"] = settings["retopo_align_boundaries"] and _has_border(prepared)
        if settings.get("retopo_adaptive", 0) > 0:
            run_settings["_adaptive_supported"] = engine_supports_adaptive(executable)
            if not run_settings["_adaptive_supported"]:
                log("{}: este Instant Meshes no tiene densidad adaptativa (hace falta la versión MBL); "
                    "se usa densidad uniforme.".format(name), "warning")
        target = int(run_settings["retopo_target_faces"])
        log("{}: Instant Meshes con {} caras objetivo...".format(name, target))
        for attempt in range(2):
            command = build_command(executable, source, destination, run_settings)
            run_instant_meshes(command, destination, settings["retopo_timeout"],
                               os.path.join(temp_dir, "instant_meshes.log"), tick)
            points, faces = read_obj(destination)
            # el nº de caras real depende de la malla: si se desvía mucho se corrige y se repite una vez
            if attempt or not faces or abs(len(faces) - target) <= 0.2 * target:
                break
            asked = int(command[command.index("-f") + 1])
            run_settings["_im_faces"] = max(int(asked * float(target) / len(faces)), 1)
            log("{}: salieron {} caras; se repite ajustando para acercarse a {}.".format(name, len(faces), target))

        points, faces, repair = repair_topology(points, faces, 0.01, int(settings["retopo_fill_holes"]))
        if not faces:
            raise RetopoError("El OBJ que devolvió Instant Meshes no contiene ninguna malla.")
        # sin importar el OBJ: cmds.file(i=True) no se puede deshacer
        new = meshbuild.create_mesh(points, faces, name + "_retopo")
    finally:
        if prepared and cmds.objExists(prepared):
            cmds.delete(prepared)
        shutil.rmtree(temp_dir, ignore_errors=True)

    fixes = []
    if repair["doublets"]:
        fixes.append("{} vértices de 2 aristas quitados".format(repair["doublets"]))
    if repair["bowties"]:
        fixes.append("{} vértices pajarita separados".format(repair["bowties"]))
    if repair["pieces"]:
        fixes.append("{} restos sueltos borrados".format(repair["pieces"]))
    if repair["holes"]:
        fixes.append("{} agujeros rellenados".format(repair["holes"]))
    return new, fixes


def mesh_arrays(transform):
    """(puntos en mundo como array Nx3, lista de caras) de una malla."""
    fn = om.MFnMesh(dag_path(mesh_shape(transform)))
    points = np.array([(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)], dtype=float)
    counts, vertices = fn.getVertices()
    vertices = list(vertices)
    faces, start = [], 0
    for count in counts:
        faces.append(vertices[start:start + count])
        start += count
    return points, faces


def surface_projector(transform):
    """Función que pega puntos (array Nx3, en mundo) a la superficie de `transform`."""
    path = dag_path(mesh_shape(transform))
    matrix = path.inclusiveMatrix()
    intersector = om.MMeshIntersector()
    intersector.create(path.node(), matrix)

    def project(points):
        out = np.empty_like(points)
        for i, (x, y, z) in enumerate(points):
            # getClosestPoint devuelve el punto en espacio de objeto
            q = om.MPoint(intersector.getClosestPoint(om.MPoint(x, y, z)).point) * matrix
            out[i] = (q.x, q.y, q.z)
        return out
    return project


def _manifold_fixes(report):
    """Texto de lo que arregló make_manifold ({tipo: cantidad}), una frase por tipo con algo que contar."""
    names = (("fins", "{} caras sobrantes quitadas de aristas con más de 2 caras"),
             ("doublets", "{} vértices de 2 aristas quitados"),
             ("bowties", "{} vértices pajarita separados"),
             ("chords", "{} quads con la diagonal repetida partidos en triángulos"))
    return [text.format(report[key]) for key, text in names if report.get(key)]


def make_unfoldable(transform, log=default_log):
    """Aletas, pajaritas, vértices de 2 aristas y quads con la diagonal repetida (mbl_engine.repair.make_manifold).

    Maya no los marca en la malla que construye polyRetopo (polyCollapseEdge deja aristas duplicadas que polyInfo
    no ve) y se vuelven no-manifold al reconstruir la malla desde sus caras; Unfold3D rechaza además los quads cuya
    diagonal ya es arista ("Mesh has non-manifold UVs"). Devuelve la malla: un nodo nuevo solo si hubo que tocarla.
    """
    points, faces = mesh_arrays(transform)
    points, faces, report = make_manifold(points, faces)
    fixes = _manifold_fixes(report)
    if not fixes:
        return transform
    name = short_name(transform)
    cmds.delete(transform)
    new = meshbuild.create_mesh(points, faces, name)
    log("{}: topología arreglada para Unfold3D: {}.".format(name, ", ".join(fixes)))
    return new


def optimize_quads(source, new, settings, log=default_log):
    """Limpieza de polos + relajación sobre el original. Devuelve el transform resultante (nuevo nodo)."""
    from . import quadopt
    name = short_name(source)
    points, faces = mesh_arrays(new)
    before = quadopt.pole_count(faces)
    operations = 0
    curves = settings.get("_guide_curves") or []

    def guide_vertices(points, faces):
        # los vértices que polyRetopo puso sobre las guías no se mueven: si no, el loop se aparta
        if not curves:
            return None
        from . import guides, symmetry
        return guides.vertices_on_curves(points, curves, 0.1 * symmetry.average_edge(points, faces))

    if settings.get("retopo_pole_cleanup", True):
        points, faces, operations = quadopt.clean_poles(points, faces, depth=3,
                                                        locked=guide_vertices(points, faces))
    iterations = int(settings.get("retopo_relax", 0))
    if iterations > 0:
        points = quadopt.relax(points, faces, iterations, surface_projector(source),
                               fixed=guide_vertices(points, faces))
    # chequeo de sanidad: caras con ángulos muy agudos o aplastadas dan UVs solapadas
    points, degenerate, left = quadopt.fix_degenerate(points, faces, surface_projector(source))
    new_name = short_name(new)
    cmds.delete(new)
    result = meshbuild.create_mesh(points, faces, new_name)
    parts = []
    if settings.get("retopo_pole_cleanup", True):
        parts.append("polos {} -> {} ({} operaciones)".format(before, quadopt.pole_count(faces), operations))
    if iterations > 0:
        parts.append("quads relajados ({} pasadas)".format(iterations))
    if degenerate:
        parts.append("caras degeneradas {} -> {}".format(degenerate, left))
    log("{}: {}.".format(name, ", ".join(parts)))
    return result


# caras con menos de esta fracción del área mediana se consideran aplastadas
TINY_FACE_FRACTION = 0.02


def collapse_tiny_edges(transform, fraction=TINY_FACE_FRACTION):
    """Colapsa la arista más corta de cada cara aplastada. Devuelve cuántas aristas.

    En zonas con geometría fina del original (la cuenca del ojo del humano) polyRetopo deja a veces un
    grumo de caras microscópicas (área 0.001 frente a 3 de mediana). Al desplegar, Unfold3D no las
    puede separar y quedan como UVs solapadas (humano simétrico: 177 caras solapadas, todas en los ojos).
    Solo se tocan esas caras: colapsar toda arista corta (< 20% de la media) metía triángulos y polos
    de 6+ aristas donde las caras pequeñas son legítimas (águila a 20.000 caras: 877 colapsos, 54 polos 6+).
    """
    collapsed = 0
    for _ in range(3):
        path = dag_path(mesh_shape(transform))
        it = om.MItMeshPolygon(path)
        areas, edges = [], []
        while not it.isDone():
            areas.append(it.getArea(om.MSpace.kWorld))
            edges.append(list(it.getEdges()))
            it.next()
        if not areas:
            return collapsed
        limit = fraction * float(np.median(areas))
        tiny = [f for f, area in enumerate(areas) if area < limit]
        if not tiny:
            break
        fn = om.MFnMesh(path)
        points = fn.getPoints(om.MSpace.kWorld)
        chosen = set()
        for f in tiny:
            def length(e):
                a, b = fn.getEdgeVertices(e)
                return points[a].distanceTo(points[b])
            chosen.add(min(edges[f], key=length))
        cmds.polyCollapseEdge(components(transform, "e", sorted(chosen)), constructionHistory=False)
        collapsed += len(chosen)
    return collapsed


def _finish(transform, new, fixes, engine_name, settings, log, tick):
    """Pasos comunes a los dos motores: orientación, no-manifold, reproyección, material y diagnóstico."""
    name = short_name(transform)
    if tick:
        tick()
    # girar las piezas que miran hacia dentro (se ven negras en el viewport)
    flipped = orient_pieces_like(transform, new)
    collapsed = collapse_tiny_edges(new)
    if collapsed:
        fixes.append("{} aristas diminutas colapsadas".format(collapsed))
    from .cleanup import fix_nonmanifold, nonmanifold_counts
    leftover = sum(nonmanifold_counts(new))
    if leftover:
        fix_nonmanifold(new)
    cmds.delete(new, constructionHistory=True)
    if flipped:
        fixes.append("{} caras giradas hacia fuera".format(flipped))
    if leftover:
        fixes.append("{} componentes no-manifold reparados".format(leftover))
    if fixes:
        log("{}: arreglos del resultado de {}: {}.".format(name, engine_name, ", ".join(fixes)))
    if sum(nonmanifold_counts(new)):
        log("{}: el resultado aún tiene geometría no-manifold; pasa 'Limpiar' antes de las UVs."
            .format(name), "warning")
    loops = border_loops(new)
    if loops:
        log("{}: quedan {} bordes abiertos (agujeros grandes o aberturas del modelo).".format(name, len(loops)))

    if settings.get("retopo_pole_cleanup", True) or settings.get("retopo_relax", 0) > 0:
        if tick:
            tick()
        new = optimize_quads(transform, new, settings, log)
    new = make_unfoldable(new, log)

    if settings["retopo_reproject"]:
        if tick:
            tick()
        # proyecta cada vértice al punto más cercano del original (sampleSpace 0 = mundo, searchMethod 3 = punto más cercano)
        cmds.transferAttributes(transform, new, transferPositions=1, transferNormals=0, transferUVs=0,
                                transferColors=0, sampleSpace=0, searchMethod=3)
        cmds.delete(new, constructionHistory=True)
        log("{}: forma reproyectada sobre el original.".format(name))
    return _present(transform, new, settings, log, tick)


def _symmetric_retopo(transform, settings, log, tick, mean, p95):
    """Retopo de la mitad +X con el motor elegido, reflejado y soldado por la costura central."""
    from . import symmetry
    name = short_name(transform)
    center = symmetry.plane_x(transform)
    log("{}: modelo simétrico en X ({}); retopo de la mitad y reflejo.".format(name, symmetry.describe(mean, p95)))
    half = symmetry.cut_half(transform, center, name + "_mblHalf")
    half_name = short_name(half)
    half_retopo = None

    def half_log(message, level="info"):
        # el diagnóstico de la mitad confundiría: se mide al final sobre la malla completa
        if message.startswith(half_name + "_retopo") or "Original oculto" in message:
            return
        log(message.replace(half_name, name + " (mitad)"), level)

    try:
        run = dict(settings, _symmetry_half=True,
                   retopo_target_faces=max(int(settings["retopo_target_faces"]) // 2, 25))
        half_retopo = retopo(half, run, half_log, tick)
        points, faces = mesh_arrays(half_retopo)
    finally:
        for node in (half_retopo, half):
            if node and cmds.objExists(node):
                cmds.delete(node)
    half_faces = len(faces)
    tolerance = symmetry.WELD_EDGE_FRACTION * symmetry.plane_edge(points, faces, center)
    points, faces, welded = symmetry.mirror_mesh(points, faces, center, tolerance)
    # una esquina de la mitad con una sola cara sobre la costura (dentro de la boca del humano) queda,
    # al soldar, como dos caras pegadas por dos aristas: Unfold3D lo rechaza ("non-manifold UVs")
    faces = _dissolve_doublets(faces)
    new = meshbuild.create_mesh(points, faces, name + "_retopo")
    cmds.delete(new, constructionHistory=True)
    log("{}: mitad de {} caras reflejada; {} vértices soldados en la costura central.".format(
        name, half_faces, welded))
    # un agujero que toca el plano (boca, ojos en el centro) deja al soldar un vértice "pajarita"
    from .cleanup import fix_nonmanifold, nonmanifold_counts
    leftover = sum(nonmanifold_counts(new))
    if leftover:
        fix_nonmanifold(new)
        cmds.delete(new, constructionHistory=True)
        log("{}: {} componentes no-manifold de la costura reparados.".format(name, leftover))
    new = make_unfoldable(new, log)
    if tick:
        tick()
    return _present(transform, new, settings, log, tick)


# atributos del transform que dicen dónde está una malla y cómo gira y escala (con el orden de giro y el
# "hereda transformación"; cada pivote es local, relativo al origen del transform)
PLACEMENT_ATTRS = ("translate", "rotate", "scale", "shear", "rotateAxis", "rotatePivot", "scalePivot",
                   "rotatePivotTranslate", "scalePivotTranslate")
PIVOT_ATTRS = PLACEMENT_ATTRS[5:]


def _parent_of(node):
    return (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[0]


def _placement_values(node):
    values = [cmds.getAttr(node + ".rotateOrder"), cmds.getAttr(node + ".inheritsTransform")]
    for attr in PLACEMENT_ATTRS:
        values.extend(cmds.getAttr(node + "." + attr)[0])
    return np.array(values, dtype=float)


def _same_placement(source, new):
    """Mismo grupo, misma transformación y mismos pivotes (el caso del motor de Maya cuando no rehizo la malla)."""
    return (_parent_of(source) == _parent_of(new)
            and np.allclose(_placement_values(source), _placement_values(new), rtol=0, atol=1e-9))


def _at_origin(node):
    """Sin padre ni transformación: los puntos de la malla ya son los del mundo (así sale de meshbuild)."""
    if _parent_of(node):
        return False
    rest = {"translate": 0.0, "rotate": 0.0, "scale": 1.0, "shear": 0.0, "rotateAxis": 0.0}
    return all(abs(value - rest[attr]) < 1e-9 for attr in rest for value in cmds.getAttr(node + "." + attr)[0])


def _freeze(node):
    cmds.makeIdentity(node, apply=True, translate=True, rotate=True, scale=True, normal=0, preserveNormals=True)


def _match_placement(source, new, log=default_log):
    """Deja `new` en el grupo de `source`, con su transformación y sus pivotes, sin mover un vértice en el mundo.

    meshbuild (y con él casi todos los pasos del retopo) rehace la malla en la raíz, sin transformación y con el
    pivote en el origen: se perdía el grupo, el movimiento y el pivote que el artista había puesto. Se hace con
    comandos (parent, makeIdentity, setAttr) para que un solo Ctrl+Z lo deshaga y Rehacer lo repita igual.
    Devuelve el transform de `new`: su ruta larga cambia al cambiar de grupo."""
    if _same_placement(source, new):
        return new
    name = short_name(source)
    world = np.array(cmds.xform(source, query=True, matrix=True, worldSpace=True), dtype=float).reshape(4, 4)
    if abs(np.linalg.det(world)) < 1e-12:
        log("{}: el original tiene escala cero; el retopo no hereda su transformación.".format(name), "warning")
        return new
    uuid = cmds.ls(new, uuid=True)[0]
    before = mesh_arrays(new)[0]
    # 1) puntos del mundo, en la raíz y con el transform limpio
    if not _at_origin(new):
        if _parent_of(new):
            cmds.parent(new, world=True)
            new = cmds.ls(uuid, long=True)[0]
        _freeze(new)
    # 2) puntos en el espacio de objeto del original: se multiplican por la inversa de su matriz de mundo
    for attr in PIVOT_ATTRS:
        cmds.setAttr(new + "." + attr, 0, 0, 0, type="double3")
    cmds.setAttr(new + ".rotateOrder", 0)
    cmds.xform(new, matrix=np.linalg.inv(world).flatten().tolist())
    _freeze(new)
    if np.linalg.det(world) < 0:
        # original con escala negativa (espejo): el sentido de las caras se invierte al pasar al espacio de objeto
        cmds.polyNormal(new, normalMode=0, userNormalMode=0, constructionHistory=False)
    # 3) en el grupo del original (a continuación de él) y con su transformación y sus pivotes
    parent = _parent_of(source)
    if parent:
        cmds.parent(new, parent, relative=True)
        new = cmds.ls(uuid, long=True)[0]
        siblings = cmds.listRelatives(parent, children=True, type="transform", fullPath=True) or []
        source_long = cmds.ls(source, long=True)[0]
        if source_long in siblings and new in siblings:
            offset = siblings.index(source_long) + 1 - siblings.index(new)
            if offset < 0:
                cmds.reorder(new, relative=offset)
    for attr in ("rotateOrder", "inheritsTransform"):
        try:
            cmds.setAttr(new + "." + attr, cmds.getAttr(source + "." + attr))
        except RuntimeError:
            pass
    for attr in PLACEMENT_ATTRS:
        cmds.setAttr(new + "." + attr, *cmds.getAttr(source + "." + attr)[0], type="double3")
    # comprobación: ningún vértice debe haberse movido en el mundo
    drift = float(np.abs(mesh_arrays(new)[0] - before).max())
    if drift > 1e-4 * max(float(np.ptp(before, axis=0).max()), 1e-9):
        log("{}: al heredar la transformación del original el retopo se desplazó {:.4g}; revisa su posición."
            .format(name, drift), "warning")
    log("{}: el retopo hereda el grupo, la transformación y el pivote del original.".format(name))
    return new


def _copy_materials(source, new):
    """Materiales del original en `new`: cada cara recibe el de la cara más cercana del original (un modelo con
    dos o más materiales conserva cada uno en su zona); con uno solo, todo el objeto. Devuelve cuántos reparte."""
    path = dag_path(mesh_shape(source))
    shaders, per_face = om.MFnMesh(path).getConnectedShaders(path.instanceNumber())
    names = [om.MFnDependencyNode(shaders[i]).name() for i in range(len(shaders))]
    per_face = np.array(per_face, dtype=int)
    used = sorted(set(per_face[per_face >= 0].tolist()))
    if not used:
        return 0
    if len(used) == 1:
        cmds.sets(new, edit=True, forceElement=names[used[0]])
        return 1
    points, faces = mesh_arrays(new)
    intersector = om.MMeshIntersector()
    intersector.create(path.node(), path.inclusiveMatrix())
    owner = np.empty(len(faces), dtype=int)
    for i, face in enumerate(faces):
        x, y, z = points[face].mean(axis=0)
        owner[i] = per_face[intersector.getClosestPoint(om.MPoint(x, y, z)).face]
    # las caras sin material en el original se quedan con el de serie
    groups = {index: (names[index] if index >= 0 else "initialShadingGroup") for index in set(owner.tolist())}
    biggest = max(groups, key=lambda index: int(np.count_nonzero(owner == index)))
    cmds.sets(new, edit=True, forceElement=groups[biggest])
    for index, group in groups.items():
        if index != biggest:
            cmds.sets(components(new, "f", np.flatnonzero(owner == index).tolist()), edit=True, forceElement=group)
    return len(groups)


def _present(transform, new, settings, log, tick):
    """Aristas suaves, grupo/transformación/pivote/materiales del original, original oculto, enlace y diagnóstico."""
    name = short_name(transform)
    angle = settings["retopo_crease_angle"] if settings["retopo_crease_angle"] > 0 else 180
    cmds.polySoftEdge(new, angle=angle, constructionHistory=False)

    # lo que el artista tenía puesto en el original: grupo, transformación, pivote y materiales
    # (la mitad de un modelo simétrico es un resto que se tira: no hace falta)
    if not settings.get("_symmetry_half"):
        new = _match_placement(transform, new, log)
        materials = _copy_materials(transform, new)
        if materials > 1:
            log("{}: {} materiales del original repartidos por zonas.".format(name, materials))

    try:
        cmds.setAttr(transform + ".visibility", 0)
    except RuntimeError:
        log("{}: no se pudo ocultar el original (visibilidad bloqueada o conectada).".format(name), "warning")

    from . import diagnostics
    diagnostics.link_source(new, transform)
    faces = cmds.polyEvaluate(new, face=True)
    log("{}: retopo terminado -> {} ({} caras). Original oculto.".format(name, short_name(new), faces))
    if tick:
        tick()
    diagnostics.diagnose(new, source=transform, colorize=False, log=log)
    return new
