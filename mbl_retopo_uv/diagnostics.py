# -*- coding: utf-8 -*-
"""Diagnóstico objetivo de un retopo: topología, polos y error de forma frente al original."""

import math

import maya.api.OpenMaya as om
import maya.cmds as cmds

from .common import components, dag_path, default_log, mesh_shape, short_name

# la métrica de loops es del núcleo del Motor MBL (sin Maya); aquí solo se le pasan las caras
from mbl_engine.metrics import loop_stats  # noqa: E402,F401

SOURCE_ATTR = "mblSource"
POLE_COLOR_SET = "MBL_poles"
POLE_COLORS = {
    "regular": (0.75, 0.75, 0.75),
    3: (0.15, 0.35, 1.0),      # azul: 3 aristas
    5: (1.0, 0.15, 0.15),      # rojo: 5 aristas
    6: (1.0, 0.1, 1.0),        # magenta: 6 o más
}


# ------------------------------------------------------------------ enlace retopo -> original

def link_source(retopo_mesh, source):
    """Guarda en el retopo de qué malla sale (para que el diagnóstico la encuentre)."""
    if not cmds.attributeQuery(SOURCE_ATTR, node=retopo_mesh, exists=True):
        cmds.addAttr(retopo_mesh, longName=SOURCE_ATTR, attributeType="message")
    cmds.connectAttr(source + ".message", retopo_mesh + "." + SOURCE_ATTR, force=True)


def find_source(mesh):
    """Malla original de un retopo: por el enlace o, si no lo hay, por el nombre (pieza_retopo -> pieza)."""
    if cmds.attributeQuery(SOURCE_ATTR, node=mesh, exists=True):
        linked = cmds.listConnections(mesh + "." + SOURCE_ATTR, source=True, destination=False) or []
        if linked:
            return cmds.ls(linked[0], long=True)[0]
    name = short_name(mesh)
    if "_retopo" in name:
        base = name.rsplit("_retopo", 1)[0]
        candidates = [c for c in cmds.ls(base, long=True, type="transform") or [] if c != mesh]
        if len(candidates) == 1:
            return candidates[0]
    return None


# ------------------------------------------------------------------ topología

def topology_stats(transform):
    """Caras por tipo, polos y % de vértices regulares.

    Regular = vértice interior con 4 aristas o vértice de borde con 3. Polo = vértice interior con
    otro número de aristas (los de borde no cuentan como polo).
    """
    path = dag_path(mesh_shape(transform))
    sizes = {"tris": 0, "quads": 0, "ngons": 0}
    face_it = om.MItMeshPolygon(path)
    while not face_it.isDone():
        count = face_it.polygonVertexCount()
        sizes["quads" if count == 4 else "tris" if count == 3 else "ngons"] += 1
        face_it.next()

    poles = {3: [], 5: [], 6: []}
    regular = total = 0
    vert_it = om.MItMeshVertex(path)
    while not vert_it.isDone():
        valence = vert_it.numConnectedEdges()
        if vert_it.numConnectedFaces():
            total += 1
            if vert_it.onBoundary():
                regular += valence == 3
            elif valence == 4:
                regular += 1
            elif valence <= 3:
                poles[3].append(vert_it.index())
            elif valence == 5:
                poles[5].append(vert_it.index())
            else:
                poles[6].append(vert_it.index())
        vert_it.next()

    faces = sum(sizes.values())
    return {
        "faces": faces,
        "tris": sizes["tris"],
        "quads": sizes["quads"],
        "ngons": sizes["ngons"],
        "quad_percent": 100.0 * sizes["quads"] / faces if faces else 0.0,
        "vertices": total,
        "regular_percent": 100.0 * regular / total if total else 0.0,
        "poles": poles,
        "pole_count": sum(len(v) for v in poles.values()),
    }


# ------------------------------------------------------------------ calidad de loops

def mesh_faces(transform):
    """Caras (listas de índices de vértice) de una malla."""
    counts, indices = om.MFnMesh(dag_path(mesh_shape(transform))).getVertices()
    faces, start = [], 0
    for count in counts:
        faces.append(list(indices[start:start + count]))
        start += count
    return faces


# ------------------------------------------------------------------ error de forma

def _sample_points(transform, max_points):
    """Vértices y centros de cara (en mundo), submuestreados de forma uniforme."""
    path = dag_path(mesh_shape(transform))
    fn = om.MFnMesh(path)
    # copia de cada punto: los elementos de un MPointArray son vistas del array, y si el array
    # temporal se libera apuntan a memoria liberada (llegó a cerrar Maya en las pruebas)
    array = fn.getPoints(om.MSpace.kWorld)
    points = [om.MPoint(p) for p in array]
    face_it = om.MItMeshPolygon(path)
    while not face_it.isDone():
        points.append(face_it.center(om.MSpace.kWorld))
        face_it.next()
    if len(points) > max_points:
        step = len(points) / float(max_points)
        points = [points[int(i * step)] for i in range(max_points)]
    return points


def _distances(points, target):
    path = dag_path(mesh_shape(target))
    intersector = om.MMeshIntersector()
    intersector.create(path.node(), path.inclusiveMatrix())
    return [p.distanceTo(om.MPoint(intersector.getClosestPoint(p).point) * path.inclusiveMatrix())
            for p in points]


def surface_error(source, retopo_mesh, max_points=30000):
    """Distancia entre superficies en los dos sentidos (retopo -> original y original -> retopo).

    Es la medida estándar de fidelidad de forma (distancia de Hausdorff). No es un error de
    silueta en pantalla, pero si la superficie está cerca, la silueta desde cualquier vista también.
    """
    there = _distances(_sample_points(retopo_mesh, max_points), source)
    back = _distances(_sample_points(source, max_points), retopo_mesh)
    both = there + back
    (xmin, ymin, zmin, xmax, ymax, zmax) = cmds.exactWorldBoundingBox(source)
    diagonal = math.sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2) or 1.0
    max_error = max(both) if both else 0.0
    mean_error = sum(both) / len(both) if both else 0.0
    return {
        "max": max_error,
        "mean": mean_error,
        "max_percent": 100.0 * max_error / diagonal,
        "mean_percent": 100.0 * mean_error / diagonal,
        "diagonal": diagonal,
    }


# ------------------------------------------------------------------ colores de polos

def color_poles(transform, stats=None):
    """Pinta los polos en el color set MBL_poles y lo muestra en el viewport."""
    stats = stats or topology_stats(transform)
    shape = mesh_shape(transform)
    if POLE_COLOR_SET in (cmds.polyColorSet(shape, query=True, allColorSets=True) or []):
        cmds.polyColorSet(shape, delete=True, colorSet=POLE_COLOR_SET)
    cmds.polyColorSet(shape, create=True, colorSet=POLE_COLOR_SET, representation="RGB")
    cmds.polyColorSet(shape, currentColorSet=True, colorSet=POLE_COLOR_SET)
    cmds.polyColorPerVertex(transform + ".vtx[*]", rgb=POLE_COLORS["regular"])
    for kind, vertices in stats["poles"].items():
        if vertices:
            cmds.polyColorPerVertex(components(transform, "vtx", vertices), rgb=POLE_COLORS[kind])
    cmds.setAttr(shape + ".displayColors", 1)


def clear_pole_colors(transform):
    """Quita el color set de polos (hazlo antes de exportar a Substance). Devuelve True si había."""
    shape = mesh_shape(transform)
    if POLE_COLOR_SET not in (cmds.polyColorSet(shape, query=True, allColorSets=True) or []):
        return False
    cmds.polyColorSet(shape, delete=True, colorSet=POLE_COLOR_SET)
    if not cmds.polyColorSet(shape, query=True, allColorSets=True):
        cmds.setAttr(shape + ".displayColors", 0)
    return True


# ------------------------------------------------------------------ informe

# últimas métricas de diagnose() por UUID de la malla: el semáforo las reutiliza para no repetir el
# error de forma (unos segundos en mallas grandes). Las UVs no mueven vértices, así que siguen valiendo
_LAST = {}


def cached_stats(transform):
    """Métricas de diagnose() de esta malla si no ha cambiado de nº de vértices desde entonces."""
    uuid = (cmds.ls(transform, uuid=True) or [None])[0]
    stats = _LAST.get(uuid)
    if stats and stats.get("_vertex_count") == cmds.polyEvaluate(transform, vertex=True):
        return stats
    return None


def diagnose(transform, source=None, colorize=True, log=default_log):
    """Calcula y escribe en el log todas las métricas. Devuelve un dict con ellas."""
    name = short_name(transform)
    stats = topology_stats(transform)
    stats["_vertex_count"] = cmds.polyEvaluate(transform, vertex=True)
    uuid = (cmds.ls(transform, uuid=True) or [None])[0]
    if uuid:
        _LAST[uuid] = stats
    poles = stats["poles"]
    log("{}: {} caras ({:.1f}% quads, {} tris, {} n-gons).".format(
        name, stats["faces"], stats["quad_percent"], stats["tris"], stats["ngons"]))
    log("{}: {:.1f}% de vértices regulares; {} polos (3 aristas: {}, 5: {}, 6 o más: {}).".format(
        name, stats["regular_percent"], stats["pole_count"], len(poles[3]), len(poles[5]), len(poles[6])))
    loops = loop_stats(mesh_faces(transform))
    stats.update(loops)
    log("{}: edge loops de {:.0f} aristas de media; {:.0f}% de las aristas en loops cerrados (anillos).".format(
        name, loops["loop_mean"], loops["loop_closed_percent"]))

    source = source or find_source(transform)
    if source and cmds.objExists(source):
        before = cmds.polyEvaluate(source, face=True)
        stats["source_faces"] = before
        error = surface_error(source, transform)
        stats["error"] = error
        log("{}: original {} caras -> {} ({:.1f}% de las caras).".format(
            name, before, stats["faces"], 100.0 * stats["faces"] / before if before else 0.0))
        log("{}: error de forma frente al original: máximo {:.4g} ({:.2f}% del tamaño), medio {:.4g} ({:.3f}%)."
            .format(name, error["max"], error["max_percent"], error["mean"], error["mean_percent"]))
    else:
        log("{}: no encuentro la malla original; solo se mide la topología.".format(name), "warning")

    if colorize:
        color_poles(transform, stats)
        log("{}: polos coloreados (azul 3, rojo 5, magenta 6+). 'Quitar colores' antes de exportar.".format(name))
    return stats
