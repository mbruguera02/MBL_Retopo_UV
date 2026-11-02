# -*- coding: utf-8 -*-
"""Ejecuta limpieza, retopo y UVs sobre varias mallas, una a una, en un solo undo chunk."""

import contextlib
import time
import traceback

import maya.cmds as cmds

from . import cleanup, retopo, status, uv
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

    def tick():
        if progress:
            progress(done, total, None)
        if is_cancelled and is_cancelled():
            raise Cancelled("Cancelado por el usuario.")

    with undo_chunk(), viewport_paused():
        for mesh in meshes:
            current = mesh
            reports = {}
            timings = {}
            mesh_began = time.time()
            try:
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
                outcome["timings"][current] = timings
                log("{}: {} ha tardado {} ({}).".format(
                    short_name(current), " + ".join(STEP_LABELS[s].lower() for s in steps), format_duration(timings["total"]),
                    ", ".join("{} {}".format(STEP_LABELS[s].lower(), format_duration(timings[s])) for s in steps)))
                outcome["results"].append(current)
                try:
                    results = status.evaluate(current, steps, reports, settings)
                    outcome["status"][current] = results
                    status.log_status(current, results, log)
                except Exception:
                    # el semáforo es informativo: si falla no estropea un resultado bueno
                    traceback.print_exc()
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

        # dentro del chunk: si no, el primer Ctrl+Z solo desharía esta selección
        existing = [m for m in outcome["results"] if cmds.objExists(m)]
        if existing:
            cmds.select(existing, replace=True)

    if progress:
        progress(total, total, "Terminado")
    return outcome
