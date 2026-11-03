# -*- coding: utf-8 -*-
"""Ejecuta limpieza, retopo y UVs sobre varias mallas, una a una, en un solo undo chunk."""

import contextlib
import copy
import time
import traceback

import maya.cmds as cmds
import numpy as np

from mbl_engine import duplicates

from . import cleanup, diagnostics, retopo, status, uv
from .common import Cancelled, MBLError, default_log, format_duration, short_name

STEPS = ("cleanup", "retopo", "uv")
STEP_LABELS = {"cleanup": "Limpieza", "retopo": "Retopo", "uv": "UVs"}


@contextlib.contextmanager
def undo_chunk(name="MBL Retopo UV"):
    """Todo lo que pase dentro se deshace con un solo Ctrl+Z. Se cierra siempre, aunque haya error."""
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


@contextlib.contextmanager
def viewport_paused():
    """Sin redibujar el viewport mientras se procesa: con una malla de 500.000 caras cada operación lo repintaba y la
    ventana parecía colgada. Se restaura siempre, aunque haya error."""
    paused = False
    try:
        cmds.refresh(suspend=True)
        paused = True
    except Exception:
        pass
    try:
        yield
    finally:
        if paused:
            try:
                cmds.refresh(suspend=False)
            except Exception:
                pass


def find_copies(meshes, steps, settings):
    """Mallas que son copia exacta (mismos vértices y caras, misma forma y tamaño salvo giro y movimiento) de otra de la
    selección: {copia: (representante, matriz 4x4 de mundo con vectores columna)}. El representante es la primera de
    cada grupo en el orden de la selección. Solo con retopo en los pasos: es el resultado nuevo lo que se copia."""
    if not settings.get("pipeline_reuse_duplicates", True) or "retopo" not in steps or len(meshes) < 2:
        return {}
    # primero un filtro barato (nº de vértices, aristas y caras): solo se leen los puntos de las que pueden coincidir
    buckets = {}
    for mesh in meshes:
        try:
            key = tuple(cmds.polyEvaluate(mesh, **{what: True}) for what in ("vertex", "edge", "face"))
        except RuntimeError:
            continue
        buckets.setdefault(key, []).append(mesh)
    copies = {}
    for members in buckets.values():
        if len(members) < 2:
            continue
        for group in duplicates.group_identical([retopo.mesh_arrays(m) for m in members]):
            for index, matrix in group[1:]:
                copies[members[index]] = (members[group[0][0]], matrix)
    return copies


def _world_matrix(node):
    """Matriz de mundo de un transform (4x4, vectores fila, como la da Maya)."""
    return np.array(cmds.xform(node, query=True, matrix=True, worldSpace=True), dtype=float).reshape(4, 4)


def _place_like(source, new):
    """Deja `new` en el grupo de `source`, justo después de él, con su transformación y sus pivotes, sin tocar los puntos
    (que ya están en el espacio de objeto de `source`). Devuelve el transform de `new`: su ruta cambia al cambiar de grupo."""
    uuid = cmds.ls(new, uuid=True)[0]
    parent = retopo._parent_of(source)
    if retopo._parent_of(new) != parent:
        if parent:
            cmds.parent(new, parent, relative=True)
        else:
            cmds.parent(new, world=True)
        new = cmds.ls(uuid, long=True)[0]
    if parent:
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
    for attr in retopo.PLACEMENT_ATTRS:
        cmds.setAttr(new + "." + attr, *cmds.getAttr(source + "." + attr)[0], type="double3")
    return new


def copy_result(source, done_result, matrix, log=default_log):
    """Resultado de `source` copiando el de su pieza idéntica (`done_result`, ya con retopo y UVs): misma malla y mismas
    UVs, puesta sobre la transformación, el grupo y los materiales de `source`, con su original oculto y enlazado.
    `matrix` lleva los puntos del original de `done_result` a los de `source` (vectores columna, mundo).
    Todo con comandos para que un solo Ctrl+Z lo deshaga y Rehacer lo repita igual. Devuelve el transform nuevo."""
    name = short_name(source)
    quiet = lambda message, level="info": None
    world_done, world_source = _world_matrix(done_result), _world_matrix(source)
    new = cmds.duplicate(done_result, name=name + "_retopo", returnRootsOnly=True)[0]
    uuid = cmds.ls(new, uuid=True)[0]
    try:
        # de los puntos (espacio de objeto) del resultado ya hecho a los que tendrá esta copia en su espacio de objeto:
        # la identidad cuando la copia es un duplicado de verdad (la misma malla con otra transformación)
        near = False
        if abs(np.linalg.det(world_source)) > 1e-12:
            local = world_done @ matrix.T @ np.linalg.inv(world_source)
            (x0, x1), (y0, y1), (z0, z1) = cmds.polyEvaluate(done_result, boundingBox=True)
            corners = np.array([[x, y, z, 1.0] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)])
            near = np.abs(corners @ local - corners)[:, :3].max() <= duplicates.TOLERANCE * max(x1 - x0, y1 - y0, z1 - z0, 1e-9)
        if near:
            # copias de verdad (la misma malla en el espacio de objeto): solo cambia la transformación
            new = _place_like(source, new)
        else:
            # la copia tiene otros puntos en su espacio de objeto (congelada, girada...): se pasan por el mundo.
            # Congelar una transformación con determinante negativo (espejo) da la vuelta a las caras: se devuelven
            if not retopo._at_origin(new):
                if retopo._parent_of(new):
                    cmds.parent(new, world=True)
                    new = cmds.ls(uuid, long=True)[0]
                retopo._freeze(new)
                if np.linalg.det(world_done) < 0:
                    cmds.polyNormal(new, normalMode=0, userNormalMode=0, constructionHistory=False)
            if not np.allclose(matrix, np.eye(4), rtol=0, atol=1e-12):
                for attr in retopo.PIVOT_ATTRS:
                    cmds.setAttr(new + "." + attr, 0, 0, 0, type="double3")
                cmds.setAttr(new + ".rotateOrder", 0)
                cmds.xform(new, matrix=matrix.T.flatten().tolist())
                retopo._freeze(new)
            new = retopo._match_placement(source, new, quiet)
        if not retopo._copy_materials(source, new):
            cmds.sets(new, edit=True, forceElement="initialShadingGroup")
        try:
            cmds.setAttr(source + ".visibility", 0)
        except RuntimeError:
            log("{}: no se pudo ocultar el original (visibilidad bloqueada o conectada).".format(name), "warning")
        diagnostics.link_source(new, source)
    except Exception:
        leftover = cmds.ls(uuid, long=True)
        if leftover:
            cmds.delete(leftover)
        raise
    return new


def run(meshes, steps, settings, log=default_log, progress=None, is_cancelled=None):
    """Procesa cada malla con los pasos pedidos (en orden limpieza -> retopo -> UVs).

    progress(done, total, texto) e is_cancelled() son opcionales (los usa la UI).
    Un error en una malla se registra y se sigue con la siguiente.
    Devuelve {"results": [mallas finales], "errors": [(malla, mensaje)], "reports": {malla: {...}},
    "status": {malla: semáforo de status.evaluate()}, "timings": {malla: {paso: segundos, "total": segundos}}}.
    """
    steps = [s for s in STEPS if s in steps]
    if not meshes:
        raise MBLError("Selecciona al menos una malla poligonal.")
    if not steps:
        raise MBLError("No hay ningún paso marcado.")

    total = len(meshes) * len(steps)
    done = 0
    outcome = {"results": [], "errors": [], "reports": {}, "status": {}, "timings": {}}
    copies = find_copies(meshes, steps, settings)
    finished = {}       # malla original -> su resultado, para copiarlo a sus piezas repetidas
    copied_from = {}    # resultado copiado -> resultado de la pieza de la que se copió
    # misma densidad de texel entre mallas: se necesitan las UVs de todas, así que el semáforo espera a ese ajuste
    common = bool(settings.get("uv_common_texel")) and "uv" in steps and len(meshes) > 1

    def tick():
        if progress:
            progress(done, total, None)
        if is_cancelled and is_cancelled():
            raise Cancelled("Cancelado por el usuario.")

    def copy_of_done(mesh, began):
        """Si `mesh` repite una pieza ya hecha, copia su resultado y devuelve (resultado, informes, tiempos); si no
        (o si la copia falla), None y se procesa entera."""
        twin, matrix = copies.get(mesh, (None, None))
        if twin not in finished:
            return None
        tick()
        if progress:
            progress(done, total, "{}: copia de {}".format(short_name(mesh), short_name(twin)))
        original = finished[twin]
        try:
            result = copy_result(mesh, original, matrix, log)
        except Cancelled:
            raise
        except Exception:
            traceback.print_exc()
            log("{}: no se pudo copiar el resultado de {}; se procesa entera.".format(short_name(mesh), short_name(twin)),
                "warning")
            return None
        seconds = time.time() - began
        log("{}: misma pieza que {}; se copia su resultado ({}) en vez de repetirlo ({}).".format(
            short_name(result), short_name(twin), " + ".join(STEP_LABELS[s].lower() for s in steps if s != "cleanup"),
            format_duration(seconds)))
        copied_from[result] = original
        return result, copy.deepcopy(outcome["reports"].get(original, {})), {"copia": seconds, "total": seconds}

    def judge(result):
        """Semáforo del resultado (el de la pieza de la que se copió, si es una copia) y su resumen en el log."""
        if result in outcome["status"]:
            return
        twin = copied_from.get(result)
        if twin in outcome["status"]:
            outcome["status"][result] = copy.deepcopy(outcome["status"][twin])
            return
        try:
            results = status.evaluate(result, steps, outcome["reports"].get(result) or {}, settings)
            outcome["status"][result] = results
            status.log_status(result, results, log)
        except Exception:
            # el semáforo es informativo: si falla no estropea un resultado bueno
            traceback.print_exc()

    def match_texel_density():
        """Misma densidad de texel en todas las mallas con UVs (uv_common_texel). Las copias de una pieza repetida se
        quedan con las UVs de su pieza, que tienen la misma topología."""
        unique = [m for m in outcome["results"] if m not in copied_from and "uv" in outcome["reports"].get(m, {})]
        if len(unique) < 2:
            return
        try:
            changed = uv.common_texel(unique, settings, log)
        except (MBLError, RuntimeError) as exc:
            log("No se pudo igualar la densidad de texel entre las mallas: {}".format(exc), "warning")
            return
        for mesh, density in changed.items():
            report = outcome["reports"][mesh]["uv"]
            report.update(uv.check(mesh, udim=bool(settings["uv_udim"])))
            report["texel_density"] = density
            report["common_texel"] = True
        for result, twin in copied_from.items():
            if twin in changed:
                cmds.polyTransfer(result, uvSets=True, vertices=False, vertexColor=False, alternateObject=twin)
                outcome["reports"][result]["uv"] = copy.deepcopy(outcome["reports"][twin]["uv"])

    with undo_chunk(), viewport_paused():
        for mesh in meshes:
            current = mesh
            reports = {}
            timings = {}
            mesh_began = time.time()
            try:
                cloned = copy_of_done(mesh, mesh_began)
                if cloned:
                    current, reports, timings = cloned
                    done += len(steps)
                else:
                    for step in steps:
                        tick()
                        if progress:
                            progress(done, total, "{}: {}".format(short_name(current), STEP_LABELS[step]))
                        log("{}: {} empieza.".format(short_name(current), STEP_LABELS[step]))
                        step_began = time.time()
                        if step == "cleanup":
                            reports["cleanup"] = cleanup.cleanup(current, settings, log)
                        elif step == "retopo":
                            current = retopo.retopo(current, settings, log, tick)
                        elif step == "uv":
                            reports["uv"] = uv.unwrap(current, settings, log, tick)
                        timings[step] = time.time() - step_began
                        done += 1
                    timings["total"] = time.time() - mesh_began
                    log("{}: {} ha tardado {} ({}).".format(
                        short_name(current), " + ".join(STEP_LABELS[s].lower() for s in steps),
                        format_duration(timings["total"]),
                        ", ".join("{} {}".format(STEP_LABELS[s].lower(), format_duration(timings[s])) for s in steps)))
                outcome["timings"][current] = timings
                outcome["results"].append(current)
                outcome["reports"][current] = reports
                finished[mesh] = current
                if not common:
                    judge(current)
            except Cancelled:
                log("Proceso cancelado. Lo ya hecho se puede deshacer con Ctrl+Z.", "warning")
                outcome["errors"].append((mesh, "Cancelado"))
                break
            except MBLError as exc:
                log(str(exc), "error")
                outcome["errors"].append((mesh, str(exc)))
                done = (meshes.index(mesh) + 1) * len(steps)
            except Exception as exc:
                # error inesperado: mensaje corto en el log y traza completa en el Script Editor
                traceback.print_exc()
                message = "{}: error inesperado ({}). Detalles en el Script Editor.".format(short_name(mesh), exc)
                log(message, "error")
                outcome["errors"].append((mesh, message))
                done = (meshes.index(mesh) + 1) * len(steps)
            outcome["reports"][current] = reports

        if common:
            if not any(message == "Cancelado" for _, message in outcome["errors"]):
                match_texel_density()
            for result in outcome["results"]:
                judge(result)

        # dentro del chunk: si no, el primer Ctrl+Z solo desharía esta selección
        existing = [m for m in outcome["results"] if cmds.objExists(m)]
        if existing:
            cmds.select(existing, replace=True)

    if progress:
        progress(total, total, "Terminado")
    return outcome
