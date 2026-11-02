# -*- coding: utf-8 -*-
"""Adaptador de Maya para la simetría del Motor MBL (mbl_engine/symmetry.py).

Retopo simétrico: se retopologiza la mitad +X del modelo y se refleja, soldando la costura central.
Aquí solo queda lo que necesita Maya (leer la malla de la escena y cortarla); la detección, el
reflejo y la soldadura son del núcleo.
"""

import maya.cmds as cmds

from mbl_engine import symmetry as _core
from mbl_engine.mesh import Mesh
from mbl_engine.symmetry import (  # noqa: F401  (núcleo del Motor MBL, sin Maya)
    SYMMETRIC_MEAN_PERCENT,
    SYMMETRIC_P95_PERCENT,
    SAMPLES,
    WELD_EDGE_FRACTION,
    CAP_TOLERANCE_FRACTION,
    LOOSE_OFFSET_FRACTION,
    _border_edges,
    _border_vertices,
    mirror_mesh,
    average_edge,
    plane_edge,
    describe,
)

from .common import default_log, short_name


def plane_x(transform):
    """X del plano de simetría: el centro de la caja del modelo en mundo."""
    box = cmds.exactWorldBoundingBox(transform)
    return 0.5 * (box[0] + box[3])


def mirror_error(transform, center=None, samples=SAMPLES):
    """(media, percentil 95) de la distancia entre el modelo reflejado en X y el original, en % de la
    diagonal. Lo calcula el núcleo: mismas cifras que con MMeshIntersector (humano 0,0050 %, águila
    0,0570 %)."""
    from .retopo import mesh_arrays
    return _core.mirror_error(Mesh(*mesh_arrays(transform)), center, samples)


def is_symmetric(transform):
    mean, p95 = mirror_error(transform)
    return mean <= SYMMETRIC_MEAN_PERCENT and p95 <= SYMMETRIC_P95_PERCENT, mean, p95


def cut_half(transform, center, name):
    """Copia con solo la mitad +X (cortada justo en el plano). Devuelve el transform nuevo."""
    half = cmds.ls(cmds.duplicate(transform, name=name, returnRootsOnly=True)[0], long=True)[0]
    cmds.delete(half, constructionHistory=True)
    # plano YZ (rotado 90° en Y); deleteFaces borra el lado -X
    cmds.polyCut(half + ".f[*]", cutPlaneCenter=(center, 0, 0), cutPlaneRotate=(0, 90, 0),
                 deleteFaces=True, constructionHistory=False)
    return half


def log_skip(transform, mean, p95, log=default_log):
    log("{}: el modelo no es simétrico en X ({}); retopo normal.".format(short_name(transform),
                                                                          describe(mean, p95)))
