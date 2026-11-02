# -*- coding: utf-8 -*-
"""Crear mallas desde listas de puntos y caras, de forma que Ctrl+Z las quite (ver mbl_mesh_command)."""

import os

import maya.api.OpenMaya as om
import maya.cmds as cmds

PLUGIN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mbl_mesh_command.py")
_pending = None


def take_pending():
    """Lo llama el comando: entrega los datos preparados y los olvida."""
    global _pending
    data, _pending = _pending, None
    return data


def _ensure_plugin():
    if not cmds.pluginInfo(PLUGIN_PATH, query=True, loaded=True):
        cmds.loadPlugin(PLUGIN_PATH, quiet=True)


def create_mesh(points, faces, name):
    """Crea una malla (transform + shape) y devuelve la ruta larga del transform.

    Solo se usan los vértices que aparecen en alguna cara. Se le asigna el material por defecto.
    """
    global _pending
    _ensure_plugin()
    remap = {}
    for face in faces:
        for v in face:
            if v not in remap:
                remap[v] = len(remap)
    ordered = sorted(remap, key=remap.get)
    point_array = om.MPointArray([om.MPoint(*[float(c) for c in points[v][:3]]) for v in ordered])
    counts = [len(face) for face in faces]
    connects = [remap[v] for face in faces for v in face]
    _pending = (point_array, counts, connects)
    try:
        result = cmds.mblCreateMesh(name)
    finally:
        _pending = None
    cmds.sets(result, edit=True, forceElement="initialShadingGroup")
    return cmds.ls(result, long=True)[0]
