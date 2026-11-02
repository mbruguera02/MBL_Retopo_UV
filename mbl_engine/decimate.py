# -*- coding: utf-8 -*-
"""Reducir una malla de triángulos antes de dárselo a un motor de retopo, sin Maya.

Un motor que saca 5.000 caras no necesita 528.000 triángulos: el águila de prueba tardaba 56 s con
la malla entera y unos 15 s con 100.000 triángulos, con el mismo error de forma (medido contra el
original). La reducción solo sirve para calcular el campo; el resultado se reproyecta siempre sobre
la malla original.

La hace engine/mbl_decimate (C++): colapso de aristas con cuádricas, Garland y Heckbert, "Surface
Simplification Using Quadric Error Metrics", SIGGRAPH 1997. Conserva la topología: el águila
reducida a 100.000 triángulos en 2,3 s tiene los mismos 3 bordes abiertos que el original. (Agrupar
vértices en una rejilla era más rápido de escribir, pero fundía las plumas finas y dejaba 1.372
agujeros.)
"""

import os
import shutil
import tempfile

import numpy as np

from .process import EngineError, _run_once
from .repair import read_obj, write_obj

# a partir de cuántos triángulos de entrada se reduce, y a cuántos (el motor no gana nada por debajo)
DECIMATE_ABOVE = 150000
DECIMATE_TO = 100000

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NAMES = ("mbl_decimate.exe", "mbl_decimate")


def find_decimator(extra=None):
    """Ruta de mbl_decimate o None: la variable MBL_DECIMATE, `extra`, o las carpetas del proyecto."""
    folders = [os.path.join(_ROOT, "mbl_retopo_uv", "bin"), os.path.join(_ROOT, "bin"),
               os.path.join(_ROOT, "engine", "build-decimate", "Release"),
               os.path.join(_ROOT, "engine", "build-decimate")]
    candidates = [os.environ.get("MBL_DECIMATE"), extra]
    candidates += [os.path.join(folder, name) for folder in folders for name in _NAMES]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def qem_decimate(points, tris, target_tris, executable, timeout=300, tick=None):
    """Reduce (points N x 3, tris T x 3) a `target_tris` triángulos. Devuelve (puntos, triángulos).

    Si la malla ya tiene menos, la devuelve igual. Los vértices del resultado son los usados, en orden.
    """
    points = np.asarray(points, dtype=float)
    tris = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    if len(tris) <= target_tris:
        return points, tris
    work = tempfile.mkdtemp(prefix="mbl_decimate_")
    try:
        source, destination = os.path.join(work, "in.obj"), os.path.join(work, "out.obj")
        write_obj(source, points, tris)
        _run_once([executable, source, destination, str(int(target_tris))], destination, timeout,
                  os.path.join(work, "decimate.log"), tick, label="mbl_decimate")
        reduced_points, reduced_faces = read_obj(destination)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not reduced_faces:
        raise EngineError("mbl_decimate no devolvió ninguna malla.")
    return np.asarray(reduced_points, dtype=float), np.asarray(reduced_faces, dtype=np.int64).reshape(-1, 3)
