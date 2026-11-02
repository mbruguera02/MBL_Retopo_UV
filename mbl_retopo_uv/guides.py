# -*- coding: utf-8 -*-
"""Guías de flujo: curvas que el usuario dibuja sobre el modelo para decir por dónde deben ir los loops
(alrededor de los ojos, la boca, una costura de ropa...).

El retopo de Maya (polyRetopo) respeta como rasgo las aristas de una etiqueta de componentes
(featureTags). Para convertir una curva en aristas, sobre la copia que se retopologiza:
1. se muestrea la curva cada ~media arista y se busca el vértice más cercano a cada muestra;
2. se unen con el camino de aristas más corto (Dijkstra local);
3. los vértices del camino se pegan a la curva: si no, el camino hace escalera y polyRetopo, que
   respeta las aristas tal cual, llena la escalera de caras pequeñas (esfera: 800 -> 9.700 caras);
4. esas aristas se etiquetan y la etiqueta se pasa a polyRetopo.

Las curvas viven en un grupo "<malla>_mblGuides" enlazado al original con el atributo mblGuides.
"""

import heapq
import math

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from .common import dag_path, mesh_shape, short_name

GUIDES_ATTR = "mblGuides"
GROUP_SUFFIX = "_mblGuides"
CURVE_COLOR = 17   # amarillo en la paleta de Maya
# una muestra de curva a más de esta cantidad de aristas de la malla está fuera de ella
OFF_SURFACE_EDGES = 2.0
# un camino que vuelve a un vértice por el que pasó hace como mucho tantos vértices es un bucle a quitar
SHORT_LOOP_VERTICES = 12


# ------------------------------------------------------------------ guardar y encontrar guías

def guide_group(transform, create=False):
    """Grupo con las curvas guía de una malla (None si no tiene y no se pide crearlo)."""
    if cmds.attributeQuery(GUIDES_ATTR, node=transform, exists=True):
        linked = cmds.listConnections(transform + "." + GUIDES_ATTR, source=True, destination=False) or []
        if linked:
            return cmds.ls(linked[0], long=True)[0]
    if not create:
        return None
    group = cmds.group(empty=True, name=short_name(transform) + GROUP_SUFFIX)
    group = cmds.ls(group, long=True)[0]
    if not cmds.attributeQuery(GUIDES_ATTR, node=transform, exists=True):
        cmds.addAttr(transform, longName=GUIDES_ATTR, attributeType="message")
    cmds.connectAttr(group + ".message", transform + "." + GUIDES_ATTR, force=True)
    return group


def guide_curves(transform):
    """Curvas guía (transforms) de una malla."""
    group = guide_group(transform)
    if not group:
        return []
    shapes = cmds.listRelatives(group, allDescendents=True, type="nurbsCurve", fullPath=True) or []
    return sorted({cmds.listRelatives(s, parent=True, fullPath=True)[0] for s in shapes
                   if not cmds.getAttr(s + ".intermediateObject")})


def add_curves(transform, curves):
    """Mete curvas en el grupo de guías de la malla (amarillas, para distinguirlas)."""
    group = guide_group(transform, create=True)
    added = []
    for curve in curves:
        curve = cmds.ls(curve, long=True)[0]
        if cmds.listRelatives(curve, parent=True, fullPath=True) != [group]:
            curve = cmds.ls(cmds.parent(curve, group)[0], long=True)[0]
        for shape in cmds.listRelatives(curve, shapes=True, fullPath=True) or []:
            cmds.setAttr(shape + ".overrideEnabled", 1)
            cmds.setAttr(shape + ".overrideColor", CURVE_COLOR)
        added.append(curve)
    return added


def clear_guides(transform):
    group = guide_group(transform)
    if group:
        cmds.delete(group)
        return True
    return False


# ------------------------------------------------------------------ de curva a aristas

def _graph(points, faces):
    """Vecinos de cada vértice con la longitud de la arista."""
    neighbors = [dict() for _ in range(len(points))]
    for face in faces:
        for a, b in zip(face, face[1:] + face[:1]):
            length = float(np.linalg.norm(points[a] - points[b]))
            neighbors[a][b] = length
            neighbors[b][a] = length
    return neighbors


def shortest_path(neighbors, start, goal, limit):
    """Dijkstra de start a goal sin pasar de `limit` de distancia. Lista de vértices o None."""
    if start == goal:
        return [start]
    best = {start: 0.0}
    previous = {}
    heap = [(0.0, start)]
    while heap:
        cost, v = heapq.heappop(heap)
        if v == goal:
            path = [v]
            while v in previous:
                v = previous[v]
                path.append(v)
            return path[::-1]
        if cost > best.get(v, 1e30) or cost > limit:
            continue
        for w, length in neighbors[v].items():
            new = cost + length
            if new < best.get(w, 1e30):
                best[w] = new
                previous[w] = v
                heapq.heappush(heap, (new, w))
    return None


def _curve_samples(curve, spacing):
    shape = cmds.listRelatives(curve, shapes=True, fullPath=True, noIntermediate=True)[0]
    fn = om.MFnNurbsCurve(dag_path(shape))
    length = fn.length()
    count = max(int(length / max(spacing, 1e-9)) + 1, 2)
    samples = []
    for i in range(count + 1):
        param = fn.findParamFromLength(min(length * i / count, length))
        p = fn.getPointAtParam(param, om.MSpace.kWorld)
        samples.append((p.x, p.y, p.z))
    closed = fn.form != om.MFnNurbsCurve.kOpen
    if closed:
        # la última muestra es la primera: el cierre se hace aparte
        samples = samples[:-1]
    return np.array(samples), fn, closed


def imprint(transform, curves):
    """Convierte las curvas en caminos de aristas de `transform` (se mueven sus vértices).

    Devuelve los índices de arista de todas las guías. `transform` debe ser una copia de trabajo.
    """
    path = dag_path(mesh_shape(transform))
    fn = om.MFnMesh(path)
    points = np.array([(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)])
    counts, flat = fn.getVertices()
    faces, start = [], 0
    for count in counts:
        faces.append(list(flat[start:start + count]))
        start += count
    neighbors = _graph(points, faces)
    lengths = [l for n in neighbors for l in n.values()]
    edge = float(np.mean(lengths)) if lengths else 1.0
    intersector = om.MMeshIntersector()
    intersector.create(path.node(), path.inclusiveMatrix())
    matrix = path.inclusiveMatrix()

    def nearest_vertex(p):
        """Vértice más cercano a la muestra, o None si la curva va por fuera de la malla."""
        hit =intersector.getClosestPoint(om.MPoint(*p))
        closest = om.MPoint(hit.point) * matrix
        if closest.distanceTo(om.MPoint(*p)) > OFF_SURFACE_EDGES * edge:
            return None
        face = faces[hit.face]
        return min(face, key=lambda v: float(np.linalg.norm(points[v] - p)))

    guide_edges = set()
    moved = {}
    for curve in curves:
        samples, curve_fn, closed = _curve_samples(curve, 0.5 * edge)
        verts = [nearest_vertex(p) for p in samples]
        # None separa tramos: la guía se corta ahí
        chain = []
        for v in verts:
            if v is None:
                if chain and chain[-1] is not None:
                    chain.append(None)
                continue
            if not chain or chain[-1] is None:
                chain.append(v)
                continue
            if v == chain[-1]:
                continue
            step = shortest_path(neighbors, chain[-1], v, 20.0 * edge)
            if step is None:
                chain += [None, v]   # salto (pieza separada)
                continue
            chain.extend(step[1:])
        if not chain:
            continue
        closed = closed and None not in chain
        # bucles cortos del camino (ida y vuelta por el mismo vértice): se recortan. Volver al vértice
        # inicial de una curva cerrada es cerrarla (si se recortara, se perdería el camino entero)
        clean = []
        reached_start = False
        for v in chain:
            if v is not None and v in clean:
                index = len(clean) - 1 - clean[::-1].index(v)
                if closed and index == 0 and len(clean) > 2:
                    reached_start = True
                    break
                if len(clean) - index <= SHORT_LOOP_VERTICES:
                    del clean[index + 1:]
                    continue
            clean.append(v)
        if reached_start:
            clean.append(clean[0])
        # cierre de las curvas cerradas (aparte: si no, el recorte de bucles se comería el camino)
        elif closed and len(clean) > 2 and clean[-1] is not None and clean[0] is not None:
            step = shortest_path(neighbors, clean[-1], clean[0], 20.0 * edge)
            if step:
                inner = [v for v in step[1:-1] if v not in clean]
                clean.extend(inner + [clean[0]])
        for a, b in zip(clean, clean[1:]):
            if a is None or b is None or b not in neighbors[a]:
                continue
            guide_edges.add((min(a, b), max(a, b)))
        # pegar a la curva
        for v in clean:
            if v is None or v in moved:
                continue
            q = curve_fn.closestPoint(om.MPoint(*points[v]), space=om.MSpace.kWorld)[0]
            moved[v] = (q.x, q.y, q.z)

    if moved:
        inverse = matrix.inverse()
        new_points = fn.getPoints(om.MSpace.kObject)
        for v, p in moved.items():
            new_points[v] = om.MPoint(*p) * inverse
        fn.setPoints(new_points, om.MSpace.kObject)

    index = {}
    it = om.MItMeshEdge(path)
    while not it.isDone():
        a, b = it.vertexId(0), it.vertexId(1)
        index[(min(a, b), max(a, b))] = it.index()
        it.next()
    return sorted(index[e] for e in guide_edges if e in index)


def tag_edges(transform, edges):
    """Etiqueta de componentes con esas aristas (para polyRetopo featureTags). Devuelve su nombre."""
    if not edges:
        return None
    previous = cmds.ls(selection=True, long=True) or []
    try:
        cmds.select(["{}.e[{}]".format(transform, e) for e in edges], replace=True)
        return cmds.componentTag(create=True)
    finally:
        if previous:
            cmds.select(previous, replace=True)
        else:
            cmds.select(clear=True)


def vertices_on_curves(points, curves, tolerance):
    """Índices de los vértices a menos de `tolerance` de alguna curva (los que siguen una guía)."""
    found = []
    shapes = []
    for curve in curves:
        for shape in cmds.listRelatives(curve, shapes=True, fullPath=True, noIntermediate=True) or []:
            shapes.append(om.MFnNurbsCurve(dag_path(shape)))
    if not shapes:
        return found
    for i, p in enumerate(np.asarray(points, dtype=float)):
        point = om.MPoint(*p)
        for fn in shapes:
            if fn.closestPoint(point, space=om.MSpace.kWorld)[0].distanceTo(point) <= tolerance:
                found.append(i)
                break
    return found


# ------------------------------------------------------------------ guías para el Motor MBL (--orient)

# hasta cuántas aristas del retopo llega la influencia de una guía (peso 1 en la mitad, 0 al final)
ORIENT_REACH_EDGES = 3.0


def curve_tangents(curves, spacing):
    """(muestras Nx3, tangentes Nx3 unitarias) a lo largo de las curvas, en mundo."""
    points, tangents = [], []
    for curve in curves:
        samples, fn, closed = _curve_samples(curve, spacing)
        for p in samples:
            param = fn.closestPoint(om.MPoint(*p), space=om.MSpace.kWorld)[1]
            t = fn.tangent(param, om.MSpace.kWorld)
            length = math.sqrt(t.x * t.x + t.y * t.y + t.z * t.z) or 1.0
            points.append(p)
            tangents.append((t.x / length, t.y / length, t.z / length))
    return np.array(points, dtype=float).reshape(-1, 3), np.array(tangents, dtype=float).reshape(-1, 3)


def guide_directions(points, samples, tangents, edge_length, reach=ORIENT_REACH_EDGES):
    """(direcciones Nx3, pesos N) para --orient: la tangente de la guía más cercana a cada vértice.

    Peso 1 hasta reach/2 aristas de la guía y bajando a 0 en reach aristas: el campo sigue la guía
    en su franja y, más allá, queda libre (el campo de 4 direcciones pone también los loops
    perpendiculares). Función pura, por bloques para no disparar la memoria con mallas grandes.
    """
    points = np.asarray(points, dtype=float)
    directions = np.zeros_like(points)
    weights = np.zeros(len(points))
    if not len(samples):
        return directions, weights
    radius = reach * float(edge_length)
    lo, hi = samples.min(axis=0) - radius, samples.max(axis=0) + radius
    candidates = np.nonzero(np.all((points >= lo) & (points <= hi), axis=1))[0]
    for start in range(0, len(candidates), 2000):
        idx = candidates[start:start + 2000]
        d2 = np.sum((points[idx, None, :] - samples[None, :, :]) ** 2, axis=2)
        nearest = np.argmin(d2, axis=1)
        dist = np.sqrt(d2[np.arange(len(idx)), nearest])
        directions[idx] = tangents[nearest]
        weights[idx] = np.clip(2.0 - 2.0 * dist / radius, 0.0, 1.0)
    return directions, weights
