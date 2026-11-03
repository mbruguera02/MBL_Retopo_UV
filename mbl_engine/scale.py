# -*- coding: utf-8 -*-
"""Tamaño de trabajo de los motores de retopo.

Los motores tienen números absolutos (polyRetopo, QuadriFlow, Instant Meshes) y el OBJ que se les escribe lleva 6 decimales
(repair.write_obj): el mismo modelo en centímetros, en metros o en milímetros no sale igual, y en los extremos falla. Medido con
polyRetopo en un toro regular (400 caras pedidas): con diagonal 1,1e5 tarda 3,4 s en vez de 0,3 s, con 1,1e6 no termina en más de 5
minutos y con 1,1e-4 el resultado sale al 45 % del tamaño; en una esfera con ondas (800 pedidas) la diagonal 1,2e5 da 104 polos y
10 s (22 y 1 s con tamaño normal). En el núcleo (esfera de diagonal 8, 600 pedidas) QuadriFlow da 574, 621, 648, 617 y 696 caras al
multiplicar el modelo por 1, 0,01, 100, 0,0001 y 10.000; Instant Meshes, 628, 598, 567, 610 y 644.

Antes del motor el modelo se multiplica por una potencia de 10 que lleva su diagonal a [DIAGONAL_MIN, DIAGONAL_MAX) y al terminar se
devuelve a su tamaño. El rango abarca justo un decenio, así que el mismo modelo en cm, m o mm (cualquier relación de potencia de 10)
llega al motor con exactamente el mismo tamaño. Con la diagonal ya dentro del rango no se toca nada: es la identidad y el resultado es
el de siempre. Los modelos del banco caen dentro (la malla que se retopologiza, ya limpia: hardsurface 38,4; prop 55,6; criatura 251,8;
midnight 247,1; human 257,6), por eso el rango es [30, 300): con un rango más ancho que un decenio habría tamaños que se quedan como
están mientras sus versiones x10 se llevan a otro sitio.
"""

import functools

import numpy as np

# diagonales de la caja (en las unidades del modelo) entre las que el modelo ya se considera de tamaño razonable: un decenio
DIAGONAL_MIN = 30.0
DIAGONAL_MAX = 300.0
# tamaños que no tienen sentido como modelo (degenerado o con coordenadas absurdas): se dejan como están
SANE_SIZES = (1e-9, 1e9)


def diagonal(points):
    """Diagonal de la caja que contiene los puntos (0 si no hay ninguno)."""
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    if not len(points):
        return 0.0
    return float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))


def work_factor(size):
    """Factor por el que se multiplica un modelo de diagonal `size` antes del motor (1.0 = no tocarlo)."""
    if not np.isfinite(size) or not SANE_SIZES[0] <= size <= SANE_SIZES[1] or DIAGONAL_MIN <= size < DIAGONAL_MAX:
        return 1.0
    exponent = 0
    while size * 10.0 ** exponent < DIAGONAL_MIN:
        exponent += 1
    while size * 10.0 ** exponent >= DIAGONAL_MAX:
        exponent -= 1
    return 10.0 ** exponent


def normalized(points):
    """(puntos a la escala de trabajo, factor). Con factor 1.0 devuelve los mismos puntos, sin copiar."""
    factor = work_factor(diagonal(points))
    if factor == 1.0:
        return points, 1.0
    return np.asarray(points, dtype=float) * factor, factor


def restore(points, factor):
    """Devuelve unos puntos de la escala de trabajo a la del modelo."""
    return points if factor == 1.0 else np.asarray(points, dtype=float) / factor


def at_work_scale(function):
    """Para las funciones de retopo (puntos, caras, ...) -> (puntos, caras, informe): las ejecuta a la escala de trabajo y
    devuelve los puntos a la escala del modelo. Anidadas (best_retopo llama a retopo) no hacen nada la segunda vez."""
    @functools.wraps(function)
    def wrapper(points, faces, *args, **kwargs):
        scaled, factor = normalized(points)
        if factor == 1.0:
            return function(points, faces, *args, **kwargs)
        log = kwargs.get("log")
        if log:
            log("modelo de diagonal {:.4g}: se trabaja a escala x{:g} y el resultado vuelve a su tamaño.".format(
                diagonal(points), factor))
        new_points, new_faces, report = function(scaled, faces, *args, **kwargs)
        return restore(new_points, factor), new_faces, dict(report, scale=factor)
    return wrapper
