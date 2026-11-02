# -*- coding: utf-8 -*-
"""Relleno de agujeros con quads por patrones (Motor MBL).

Un agujero de n lados (n par) se cierra con quads de varias formas, las de la tabla de referencia del
modelado poligonal: la estrella (un centro con n/2 quads: valencia n/2, mala para agujeros grandes), las
rayas paralelas (cuerdas entre lados opuestos), la rejilla (a x b quads, con el interior regular) y las
cuadrangulaciones por cuerdas sin vértices interiores. Aquí se prueban todas y se elige la que deja
menos polos teniendo en cuenta la valencia que ya tiene cada vértice del borde: las aristas del agujero ya
existen, así que cada cuerda o arista hacia dentro les suma una.

Cada patrón son caras sobre los ids 0..n-1 (el borde, en orden) y n.. (vértices nuevos), orientadas en el
sentido del ciclo del borde (quien lo use da la vuelta si hace falta).

Todo es numpy y Python puro (se prueba sin Maya).
"""

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

# coste de un vértice con valencia final v: 0 si es 4, 1 si es 3 o 5, y BAD_COST si es peor
POLE_COST = 1
BAD_COST = 4
# más lados que esto no se enumeran las cuadrangulaciones por cuerdas (crece muy deprisa)
MAX_CHORD_SIDES = 16


@dataclass
class Pattern:
    """Patrón de relleno: nombre, caras (ids 0..n-1 del borde y n.. interiores) y cómo colocar los interiores
    (("star",) o ("grid", r, a, b, i, j) por vértice, en el orden de los ids)."""
    name: str
    n: int
    faces: list
    interior: list = field(default_factory=list)


def star_pattern(n):
    return Pattern("estrella", n, [[2 * i, 2 * i + 1, (2 * i + 2) % n, n] for i in range(n // 2)], [("star",)])


def _rotated(faces, n, shift):
    return [[(v + shift) % n if v < n else v for v in face] for face in faces]


def ladder_pattern(n, shift=0):
    """Rayas: cuerdas paralelas entre los lados opuestos (sin vértices interiores)."""
    k = n // 2
    faces = [[i, i + 1, n - 2 - i, n - 1 - i] for i in range(k - 1)]
    return Pattern("rayas", n, _rotated(faces, n, shift))


def grid_pattern(n, a, b, shift=0):
    """Rejilla de a x b quads: las esquinas del rectángulo caen en los vértices shift, shift + a, shift + a + b y
    shift + 2a + b del borde; los demás lados se unen por filas rectas."""
    def vertex(i, j):
        if j == 0:
            return (shift + i) % n
        if i == a:
            return (shift + a + j) % n
        if j == b:
            return (shift + a + b + (a - i)) % n
        if i == 0:
            return (shift + 2 * a + b + (b - j)) % n
        return n + (i - 1) * (b - 1) + (j - 1)
    faces = [[vertex(i, j), vertex(i + 1, j), vertex(i + 1, j + 1), vertex(i, j + 1)]
             for i in range(a) for j in range(b)]
    interior = [("grid", shift, a, b, i, j) for i in range(1, a) for j in range(1, b)]
    return Pattern("rejilla {}x{}".format(a, b), n, faces, interior)


@lru_cache(maxsize=None)
def _quadrangulations(p, q):
    """Todas las cuadrangulaciones (sin vértices interiores) del polígono de vértices p..q cerrado por la
    arista (p, q), como tuplas de quads. Un quad que contiene la arista base es (p, x, y, q)."""
    if q - p == 1:
        return ((),)
    if (q - p) % 2 == 0:
        return ()
    result = []
    for x in range(p + 1, q):
        for y in range(x + 1, q):
            for a in _quadrangulations(p, x):
                for b in _quadrangulations(x, y):
                    for c in _quadrangulations(y, q):
                        result.append(((p, x, y, q),) + a + b + c)
    return tuple(result)


def _valences(faces):
    neighbors = {}
    for face in faces:
        for i, v in enumerate(face):
            neighbors.setdefault(v, set()).update((face[i - 1], face[(i + 1) % len(face)]))
    return {v: len(n) for v, n in neighbors.items()}


def pattern_cost(pattern, valences):
    """Polos que deja el patrón: valencia final de cada vértice del borde (la que tiene + las aristas que le
    añade el patrón) y de cada vértice interior; 0 por vértice regular, 1 por polo de 3 o 5, 4 si es peor."""
    patch = _valences(pattern.faces)
    cost = 0
    for v, val in patch.items():
        final = valences[v] + val - 2 if v < pattern.n else val
        if final != 4:
            cost += POLE_COST if final in (3, 5) else BAD_COST
    return cost


def interior_valences(pattern):
    patch = _valences(pattern.faces)
    return [val for v, val in patch.items() if v >= pattern.n]


def best_pattern(valences):
    """El patrón de menos polos para un agujero cuyos vértices del borde tienen las valencias `valences`
    (en orden). Lanza ValueError si el nº de lados es impar o menor que 4."""
    n = len(valences)
    if n % 2 or n < 4:
        raise ValueError("un agujero de {} lados no se cierra solo con quads".format(n))
    if n == 4:
        return Pattern("quad", 4, [[0, 1, 2, 3]])
    candidates = [star_pattern(n)]
    candidates += [ladder_pattern(n, shift) for shift in range(n)]
    half = n // 2
    for a in range(2, half - 1):
        b = half - a
        if b >= 2 and a <= b:
            candidates += [grid_pattern(n, a, b, shift) for shift in range(n)]
            if a != b:
                candidates += [grid_pattern(n, b, a, shift) for shift in range(n)]
    if n <= MAX_CHORD_SIDES:
        for quads in _quadrangulations(0, n - 1):
            candidates.append(Pattern("cuerdas", n, [list(q) for q in quads]))
    return min(candidates, key=lambda p: (pattern_cost(p, valences), len(p.interior), p.name != "cuerdas"))


def check_pattern(pattern, n):
    """Comprueba un patrón: all_quads, manifold (aristas con 1 o 2 caras, el borde es el ciclo 0..n-1 y se recorre
    en el sentido del ciclo) y charge_ok (disco: suma de 4 - valencia en interiores y 3 - valencia en el borde = 4)."""
    faces = pattern.faces
    directed, undirected = {}, {}
    for face in faces:
        for i, v in enumerate(face):
            w = face[(i + 1) % len(face)]
            directed[(v, w)] = directed.get((v, w), 0) + 1
            key = (min(v, w), max(v, w))
            undirected[key] = undirected.get(key, 0) + 1
    border = {k for k, c in undirected.items() if c == 1}
    cycle = {(min(i, (i + 1) % n), max(i, (i + 1) % n)) for i in range(n)}
    forward = all(directed.get((i, (i + 1) % n), 0) == 1 for i in range(n))
    manifold = (all(c <= 2 for c in undirected.values()) and all(c == 1 for c in directed.values())
                and border == cycle and forward)
    valence = _valences(faces)
    charge = sum(4 - val for v, val in valence.items() if v >= n) + sum(3 - val for v, val in valence.items() if v < n)
    return {"all_quads": all(len(f) == 4 for f in faces), "manifold": manifold, "charge_ok": charge == 4}


def interior_points(pattern, ring):
    """Posición de los vértices interiores (k x 3) a partir de los puntos `ring` (n x 3) del borde: la media para
    la estrella y la interpolación transfinita de Coons de los cuatro lados para la rejilla."""
    ring = np.asarray(ring, dtype=float)
    n = pattern.n
    out = []
    for item in pattern.interior:
        if item[0] == "star":
            out.append(ring.mean(axis=0))
            continue
        _, shift, a, b, i, j = item
        u, v = i / float(a), j / float(b)

        def edge(k, direction):
            return ring[direction(k) % n]
        bottom = lambda t: ring[(shift + t) % n]
        right = lambda t: ring[(shift + a + t) % n]
        top = lambda t: ring[(shift + a + b + (a - t)) % n]
        left = lambda t: ring[(shift + 2 * a + b + (b - t)) % n]
        p00, p10, p11, p01 = bottom(0), bottom(a), top(0), top(a)
        point = ((1 - v) * bottom(i) + v * top(i) + (1 - u) * left(j) + u * right(j)
                 - ((1 - u) * (1 - v) * p00 + u * (1 - v) * p10 + u * v * p11 + (1 - u) * v * p01))
        out.append(point)
    return np.array(out).reshape(-1, 3)
