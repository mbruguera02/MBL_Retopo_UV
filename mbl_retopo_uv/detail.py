# -*- coding: utf-8 -*-
"""Pincel de detalle: el usuario pinta en rojo (color set MBL_detalle del original) las zonas que
necesitan más caras: cara, manos... El retopo pone ahí `retopo_detail_density` veces más caras por
unidad de área y menos en el resto, con el mismo total.

polyRetopo no admite densidad variable (probado: ni escalando la zona, ni con más resolución de
entrada, ni con faceUniformity cambia el reparto), así que con zonas pintadas se usa nuestra versión
de Instant Meshes con --density (un valor por vértice, ver engine/README.md).
"""

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from .common import dag_path, mesh_shape

COLOR_SET = "MBL_detalle"
PAINT_CONTEXT = "mblDetailPaintCtx"
# rojo > este valor cuenta como pintado (para decir si una malla tiene detalle)
PAINTED_THRESHOLD = 0.05


def _shape(transform):
    return mesh_shape(transform)


def has_color_set(transform):
    return COLOR_SET in (cmds.polyColorSet(_shape(transform), query=True, allColorSets=True) or [])


def weights(transform):
    """Peso pintado de cada vértice (0 = normal, 1 = máximo detalle). None si no hay nada pintado."""
    if not has_color_set(transform):
        return None
    fn = om.MFnMesh(dag_path(_shape(transform)))
    colors = fn.getVertexColors(COLOR_SET)
    values = np.array([max(c.r, 0.0) if c.r >= 0 else 0.0 for c in colors], dtype=float)
    values = np.clip(values, 0.0, 1.0)
    if not len(values) or values.max() <= PAINTED_THRESHOLD:
        return None
    return values


def has_detail(transform):
    return weights(transform) is not None


def density_values(transform, multiplier):
    """Valores para --density de Instant Meshes: 1 en lo no pintado, `multiplier` en lo pintado."""
    w = weights(transform)
    count = cmds.polyEvaluate(transform, vertex=True)
    if w is None:
        return np.ones(count)
    return 1.0 + (float(multiplier) - 1.0) * w


def prepare_paint(transform):
    """Crea el color set (negro = sin detalle) si no existe, lo activa y lo muestra."""
    shape = _shape(transform)
    if not has_color_set(transform):
        # set propio: no pisa los colores que ya tuviera el modelo
        cmds.polyColorSet(shape, create=True, colorSet=COLOR_SET, representation="RGB")
        cmds.polyColorSet(shape, currentColorSet=True, colorSet=COLOR_SET)
        cmds.polyColorPerVertex(transform + ".vtx[*]", rgb=(0.0, 0.0, 0.0))
    cmds.polyColorSet(shape, currentColorSet=True, colorSet=COLOR_SET)
    cmds.setAttr(shape + ".displayColors", 1)


def start_paint(transform):
    """Abre la herramienta Paint Vertex Color pintando rojo sobre el color set de detalle (solo GUI)."""
    prepare_paint(transform)
    cmds.select(transform, replace=True)
    if not cmds.artAttrPaintVertexCtx(PAINT_CONTEXT, exists=True):
        cmds.artAttrPaintVertexCtx(PAINT_CONTEXT)
    cmds.artAttrPaintVertexCtx(PAINT_CONTEXT, edit=True, colorRGBValue=(1.0, 0.0, 0.0),
                               selectedattroper="absolute", opacity=1.0, value=1.0)
    cmds.setToolTo(PAINT_CONTEXT)


def clear_detail(transform):
    shape = _shape(transform)
    if not has_color_set(transform):
        return False
    cmds.polyColorSet(shape, delete=True, colorSet=COLOR_SET)
    if not cmds.polyColorSet(shape, query=True, allColorSets=True):
        cmds.setAttr(shape + ".displayColors", 0)
    return True


def paint_sphere_region(transform, center, radius, value=1.0):
    """Pinta por código los vértices a menos de `radius` de `center` (pruebas y scripts)."""
    prepare_paint(transform)
    fn = om.MFnMesh(dag_path(_shape(transform)))
    points = np.array([(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)])
    inside = np.flatnonzero(np.linalg.norm(points - np.asarray(center, dtype=float), axis=1) <= radius)
    if len(inside):
        cmds.polyColorPerVertex(["{}.vtx[{}]".format(transform, int(i)) for i in inside],
                                rgb=(float(value), 0.0, 0.0))
    return len(inside)
