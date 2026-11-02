# -*- coding: utf-8 -*-
"""Errores legibles y utilidades de mallas compartidas por todos los módulos."""

import time

import maya.api.OpenMaya as om
import maya.cmds as cmds


class MBLError(Exception):
    """Error con un mensaje pensado para el usuario (se muestra tal cual en el log)."""


class InvalidMeshError(MBLError):
    pass


class RetopoError(MBLError):
    pass


class UVError(MBLError):
    pass


class Cancelled(MBLError):
    pass


def stamp():
    """Hora actual HH:MM:SS, para las líneas del registro."""
    return time.strftime("%H:%M:%S")


def format_duration(seconds):
    """'35 s', '4 min 12 s' o '1 h 03 min' según lo largo que sea."""
    seconds = int(round(max(float(seconds), 0.0)))
    if seconds < 60:
        return "{} s".format(seconds)
    if seconds < 3600:
        return "{} min {:02d} s".format(seconds // 60, seconds % 60)
    return "{} h {:02d} min".format(seconds // 3600, (seconds % 3600) // 60)


def default_log(message, level="info"):
    prefix = {"warning": "AVISO: ", "error": "ERROR: "}.get(level, "")
    print("[MBL Retopo UV {}] ".format(stamp()) + prefix + message)


def short_name(node):
    return node.split("|")[-1]


def mesh_shape(transform):
    """Shape de malla (no intermedia) de un transform, o error legible."""
    if not cmds.objExists(transform):
        raise InvalidMeshError("'{}' ya no existe en la escena.".format(short_name(transform)))
    shapes = cmds.listRelatives(transform, shapes=True, type="mesh", noIntermediate=True, fullPath=True) or []
    if not shapes:
        raise InvalidMeshError("'{}' no es una malla poligonal.".format(short_name(transform)))
    # OpenMaya: instantáneo. polyEvaluate recorría la malla entera y se llamaba decenas de veces
    # por proceso (0,7 s con 528.000 caras)
    if om.MFnMesh(dag_path(shapes[0])).numPolygons == 0:
        raise InvalidMeshError("'{}' no tiene caras.".format(short_name(transform)))
    return shapes[0]


def selected_mesh_transforms():
    """Transforms con malla de la selección, en orden y sin duplicados.

    Si se selecciona un grupo se incluyen las mallas que cuelgan de él.
    Si se seleccionan componentes se usa el objeto al que pertenecen.
    """
    result = []
    for node in cmds.ls(selection=True, long=True, objectsOnly=True) or []:
        if cmds.nodeType(node) == "mesh":
            node = cmds.listRelatives(node, parent=True, fullPath=True)[0]
        shapes = cmds.listRelatives(node, allDescendents=True, type="mesh", noIntermediate=True, fullPath=True) or []
        for shape in shapes:
            parent = cmds.listRelatives(shape, parent=True, fullPath=True)[0]
            if parent not in result:
                result.append(parent)
    return result


def dag_path(node):
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def compress_indices(indices):
    """[1,2,3,7] -> [(1,3),(7,7)] para pasar listas largas de componentes a cmds."""
    ranges = []
    for index in sorted(set(indices)):
        if ranges and index == ranges[-1][1] + 1:
            ranges[-1][1] = index
        else:
            ranges.append([index, index])
    return [tuple(r) for r in ranges]


def components(node, kind, indices):
    """Nombres de componentes compactos: pCube1.e[0:3], pCube1.e[7]..."""
    out = []
    for start, end in compress_indices(indices):
        if start == end:
            out.append("{}.{}[{}]".format(node, kind, start))
        else:
            out.append("{}.{}[{}:{}]".format(node, kind, start, end))
    return out


def flat_indices(component_names):
    """Índices de una lista de componentes devuelta por cmds (con rangos)."""
    out = []
    for name in cmds.ls(component_names or [], flatten=True) or []:
        out.append(int(name.rsplit("[", 1)[1].rstrip("]")))
    return out
