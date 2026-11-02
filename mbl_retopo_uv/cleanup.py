# -*- coding: utf-8 -*-
"""Limpieza de mallas importadas y detección de geometría problemática."""

import maya.api.OpenMaya as om
import maya.cmds as cmds

from .common import components, dag_path, default_log, flat_indices, mesh_shape, short_name

# Etiquetas legibles de cada problema que detecta analyze()
PROBLEM_LABELS = {
    "nonmanifold_edges": "aristas no-manifold",
    "nonmanifold_vertices": "vértices no-manifold",
    "lamina_faces": "caras láminas (duplicadas sobre sí mismas)",
    "zero_area_faces": "caras de área cero",
    "isolated_vertices": "vértices sueltos (sin caras)",
    "winged_vertices": "vértices con solo 2 aristas (sobran en mitad de una arista)",
}


def analyze(transform, relative_area_tolerance=1e-6):
    """Busca problemas sin modificar nada. Devuelve {problema: [componentes]}."""
    shape = mesh_shape(transform)
    report = {key: [] for key in PROBLEM_LABELS}

    report["nonmanifold_edges"] = cmds.ls(cmds.polyInfo(shape, nonManifoldEdges=True) or [], flatten=True) or []
    report["nonmanifold_vertices"] = cmds.ls(cmds.polyInfo(shape, nonManifoldVertices=True) or [], flatten=True) or []
    report["lamina_faces"] = cmds.ls(cmds.polyInfo(shape, laminaFaces=True) or [], flatten=True) or []

    path = dag_path(shape)

    # caras de área cero: relativas al área media para que funcione en cualquier escala
    areas = []
    poly_it = om.MItMeshPolygon(path)
    while not poly_it.isDone():
        areas.append(poly_it.getArea(om.MSpace.kWorld))
        poly_it.next()
    if areas:
        threshold = max(sum(areas) / len(areas) * relative_area_tolerance, 1e-12)
        zero = [i for i, area in enumerate(areas) if area <= threshold]
        report["zero_area_faces"] = components(shape, "f", zero)

    isolated, winged = [], []
    vert_it = om.MItMeshVertex(path)
    while not vert_it.isDone():
        if vert_it.numConnectedFaces() == 0:
            isolated.append(vert_it.index())
        elif vert_it.numConnectedEdges() == 2 and not vert_it.onBoundary():
            winged.append(vert_it.index())
        vert_it.next()
    report["isolated_vertices"] = components(shape, "vtx", isolated)
    report["winged_vertices"] = components(shape, "vtx", winged)
    return report


def count_problems(report):
    """Cuenta componentes reales (los rangos tipo f[0:9] cuentan como 10)."""
    return {key: len(flat_indices(value)) for key, value in report.items()}


def log_report(transform, report, log=default_log):
    counts = count_problems(report)
    found = False
    for key, count in counts.items():
        if count:
            found = True
            log("{}: {} {}.".format(short_name(transform), count, PROBLEM_LABELS[key]), "warning")
    if not found:
        log("{}: sin geometría problemática.".format(short_name(transform)))
    return counts


def problem_components(report):
    """Todos los componentes problemáticos juntos, para seleccionarlos en el viewport."""
    out = []
    for value in report.values():
        out.extend(value)
    return out


def nonmanifold_counts(transform):
    """(aristas, vértices) no-manifold. Rápido: se usa antes de las UVs."""
    shape = mesh_shape(transform)
    edges = cmds.ls(cmds.polyInfo(shape, nonManifoldEdges=True) or [], flatten=True) or []
    vertices = cmds.ls(cmds.polyInfo(shape, nonManifoldVertices=True) or [], flatten=True) or []
    return len(edges), len(vertices)


def fix_nonmanifold(transform):
    """Separa aristas y vértices no-manifold, como Mesh > Cleanup de Maya (polyCleanupArgList.mel).

    Devuelve cuántos componentes no-manifold quedan (0 si se arregló todo).
    """
    shape = mesh_shape(transform)
    for _ in range(3):
        edges = cmds.polyInfo(shape, nonManifoldEdges=True) or []
        if edges:
            cmds.polySplitEdge(edges, constructionHistory=True)
        vertices = cmds.polyInfo(shape, nonManifoldVertices=True) or []
        if vertices:
            cmds.polySplitVertex(vertices, constructionHistory=True)
        if sum(nonmanifold_counts(transform)) == 0:
            return 0
        # último recurso, igual que Maya: separar las caras que tocan esos componentes
        remaining = (cmds.polyInfo(shape, nonManifoldEdges=True) or []) + \
                    (cmds.polyInfo(shape, nonManifoldVertices=True) or [])
        faces = cmds.polyListComponentConversion(remaining, toFace=True)
        if faces:
            cmds.polyChipOff(faces, keepFacesTogether=False, duplicate=False, constructionHistory=True)
        if sum(nonmanifold_counts(transform)) == 0:
            return 0
    return sum(nonmanifold_counts(transform))


def has_history(transform):
    """Si la malla tiene historial de construcción (sin contar nodos de escena)."""
    return bool(cmds.listHistory(transform, pruneDagObjects=True))


def is_identity(transform):
    """Si el transform no tiene traslación, rotación ni escala que congelar."""
    values = (cmds.getAttr(transform + ".translate")[0], cmds.getAttr(transform + ".rotate")[0],
              cmds.getAttr(transform + ".scale")[0])
    return all(abs(a) < 1e-9 for a in values[0] + values[1]) and all(abs(a - 1.0) < 1e-9 for a in values[2])


def cleanup(transform, settings, log=default_log):
    """Limpieza básica según los ajustes. Devuelve el informe de problemas final.

    En una malla de 500.000 caras cada operación con historial se recalcula y guarda su deshacer entero: si el
    historial se va a borrar al final, las operaciones se hacen directamente (sin nodos de historial), y se
    salta lo que no cambiaría nada (borrar un historial que no existe, congelar un transform sin mover)."""
    name = short_name(transform)
    shape = mesh_shape(transform)
    keep_history = not settings["cleanup_delete_history"]

    if settings["cleanup_delete_history"]:
        if has_history(transform):
            cmds.delete(transform, constructionHistory=True)
            log("{}: historial borrado.".format(name))
        else:
            log("{}: sin historial que borrar.".format(name))

    if settings["cleanup_freeze"] and is_identity(transform):
        log("{}: transformaciones ya congeladas.".format(name))
    elif settings["cleanup_freeze"]:
        try:
            cmds.makeIdentity(transform, apply=True, translate=True, rotate=True, scale=True,
                              normal=0, preserveNormals=True)
            log("{}: transformaciones congeladas.".format(name))
        except RuntimeError as exc:
            # pasa con atributos bloqueados o conectados (rigs, constraints)
            log("{}: no se pudieron congelar las transformaciones ({}).".format(name, str(exc).strip()), "warning")

    if settings["cleanup_merge"]:
        before = cmds.polyEvaluate(shape, vertex=True)
        # texture=False: no fusiona UVs, así se conservan las costuras que ya existan
        cmds.polyMergeVertex(transform + ".vtx[*]", distance=settings["cleanup_merge_distance"],
                             texture=False, constructionHistory=keep_history)
        shape = mesh_shape(transform)
        merged = before - cmds.polyEvaluate(shape, vertex=True)
        log("{}: {} vértices fusionados (distancia {}).".format(name, merged, settings["cleanup_merge_distance"]))

    if settings["cleanup_conform_normals"]:
        # normalMode=2 es "Conform", el mismo valor que usa Mesh > Cleanup de Maya (polyCleanupArgList.mel)
        cmds.polyNormal(transform + ".f[*]", normalMode=2, userNormalMode=0, constructionHistory=keep_history)
        log("{}: normales conformadas.".format(name))

    if settings["cleanup_fix_nonmanifold"] and sum(nonmanifold_counts(transform)):
        edges, vertices = nonmanifold_counts(transform)
        left = fix_nonmanifold(transform)
        log("{}: {} aristas y {} vértices no-manifold separados{}.".format(
            name, edges, vertices, "" if not left else " ({} no se pudieron arreglar)".format(left)),
            "warning" if left else "info")

    if settings["cleanup_delete_history"] and has_history(transform):
        cmds.delete(transform, constructionHistory=True)

    report = analyze(transform)
    log_report(transform, report, log)
    return report
