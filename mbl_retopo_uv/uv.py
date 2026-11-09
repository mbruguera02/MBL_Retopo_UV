# -*- coding: utf-8 -*-
"""UVs automáticas con Unfold3D: costuras, unfold, optimize y layout (0-1 o UDIM).

Flags comprobados en Maya 2026.3.2 con cmds.help() y con los scripts que usa el propio Maya
(performUnfold.mel, performPolyOptimizeUV.mel, performPolyLayoutUV.mel, performPolyAutoSeamUV.mel):
  u3dUnfold    -ite -p -bi -tf -ms -rs
  u3dOptimize  -ite -pow -sa -bi -tf -ms -rs
  u3dLayout    -res -scl (1 = preservar 3D) -ls (1 = sin escalar, 2 = uniforme)
               -spc/-mar (padding en unidades UV) -rmn/-rmx/-rst (rotación)
               -box minU maxU minV maxV (región de empaquetado; acepta una lista de caras)
               (-u/-v existen pero no respetan los bordes de tile: los UDIMs se reparten aquí)
  u3dAutoSeam  -s (split shells 0-1) -p (cortar tubos); actúa sobre la selección
"""

import math
import os

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from mbl_engine import uv_quality

from . import config
from .common import UVError, components, dag_path, default_log, flat_indices, mesh_shape, short_name

UDIM_COLUMNS = 10
# fracción de cada tile (por área real de las islas con su padding) que se espera llenar al calcular cuántos hacen falta:
# el empaquetador llega al 60-66 % con islas ya desplegadas
UDIM_FILL = 0.62
# si los tiles pedidos no dan para una densidad, se reintenta con esta fracción
UDIM_DENSITY_STEP = 0.97
UDIM_DENSITY_STEPS = 8
# rondas de pasar islas entre tiles para igualarlos, y diferencia de crecimiento con la que ya no merece la pena
UDIM_BALANCE_ROUNDS = 6
UDIM_BALANCE_GAIN = 1.05
# una isla frena el reparto si, a la densidad que llenaría los tiles, ocuparía más de esta fracción del lado de un tile.
# 0,5: midnight y humano en 3 tiles pasan del 35-41 % al 48-52 % de textura aprovechada, y con menos estiramiento
UDIM_SPLIT_SIZE = 0.5
UDIM_SPLIT_ROUNDS = 6


def ensure_unfold3d():
    try:
        if not cmds.pluginInfo("Unfold3D", query=True, loaded=True):
            cmds.loadPlugin("Unfold3D", quiet=True)
    except RuntimeError:
        raise UVError("No se pudo cargar el plugin Unfold3D. Actívalo en Windows > Settings/Preferences > "
                      "Plug-in Manager (Unfold3D.mll / Unfold3D.bundle) y vuelve a intentarlo.")
    if not cmds.pluginInfo("Unfold3D", query=True, loaded=True):
        raise UVError("El plugin Unfold3D no está cargado.")


def _mesh_fn(transform):
    return om.MFnMesh(dag_path(mesh_shape(transform)))


def has_uvs(transform):
    fn = _mesh_fn(transform)
    return fn.numUVs() > 0


def uv_seam_edges(transform, graph=None):
    """Aristas interiores donde las dos caras usan UVs distintas: las costuras que ya existen.
    Con un SeamGraph de la malla se saca de sus tablas de numpy (el recorrido por caras tardaba 40 ms)."""
    if graph is not None:
        return graph.uv_seams()
    shape = mesh_shape(transform)
    path = dag_path(shape)
    fn = om.MFnMesh(path)
    if fn.numUVs() == 0:
        return []

    # mapa (cara, vértice) -> uv id
    face_uv = {}
    counts, vertex_ids = fn.getVertices()
    uv_counts, uv_ids = fn.getAssignedUVs()
    offset = uv_offset = 0
    for face, count in enumerate(counts):
        if uv_counts[face] == count:
            for k in range(count):
                face_uv[(face, vertex_ids[offset + k])] = uv_ids[uv_offset + k]
            uv_offset += count
        offset += count

    seams = []
    edge_it = om.MItMeshEdge(path)
    while not edge_it.isDone():
        faces = edge_it.getConnectedFaces()
        if len(faces) == 2:
            for vertex in (edge_it.vertexId(0), edge_it.vertexId(1)):
                if face_uv.get((faces[0], vertex)) != face_uv.get((faces[1], vertex)):
                    seams.append(edge_it.index())
                    break
        edge_it.next()
    return seams


def hard_angle_edges(transform, threshold_degrees):
    """Aristas cuyo ángulo entre normales de cara supera el umbral.

    Mismo criterio que polySelectConstraint -type 0x8000 -angle 1 -anglebound <umbral> 180,
    pero calculado con OpenMaya para no tocar la selección ni dejar constraints activos.
    """
    path = dag_path(mesh_shape(transform))
    fn = om.MFnMesh(path)
    normals = [fn.getPolygonNormal(i, om.MSpace.kWorld) for i in range(fn.numPolygons)]
    threshold = math.radians(threshold_degrees)
    result = []
    edge_it = om.MItMeshEdge(path)
    while not edge_it.isDone():
        faces = edge_it.getConnectedFaces()
        if len(faces) == 2 and normals[faces[0]].angle(normals[faces[1]]) >= threshold:
            result.append(edge_it.index())
        edge_it.next()
    return result


# una cadena de aristas con ángulo agudo solo es un canto si mide al menos esta fracción de la diagonal del modelo
HARD_CHAIN_MIN = 0.05


def _long_chains(transform, edges):
    """De `edges`, las que forman cadenas largas. Las cortas son detalle o ruido: en un águila, cortar por todo ángulo de
    80° daba 290 islas."""
    if not edges:
        return []
    fn = _mesh_fn(transform)
    points = np.array([(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)])
    ends = [fn.getEdgeVertices(e) for e in edges]
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in ends:
        parent[find(a)] = find(b)
    lengths = {}
    for a, b in ends:
        root = find(a)
        lengths[root] = lengths.get(root, 0.0) + float(np.linalg.norm(points[a] - points[b]))
    limit = HARD_CHAIN_MIN * float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    return [e for e, (a, b) in zip(edges, ends) if lengths[find(a)] >= limit]


def material_edges(transform):
    """Aristas entre caras con materiales (shadingEngine) distintos. Vacío si la malla tiene un solo material.

    Cada material debe tener sus propias islas: así se puede pintar y exportar su textura por separado y
    ninguna isla mezcla dos materiales."""
    path = dag_path(mesh_shape(transform))
    _, per_face = om.MFnMesh(path).getConnectedShaders(path.instanceNumber())
    per_face = np.array(per_face, dtype=np.int64)
    if len(np.unique(per_face)) < 2:
        return []
    result = []
    edge_it = om.MItMeshEdge(path)
    while not edge_it.isDone():
        faces = edge_it.getConnectedFaces()
        if len(faces) == 2 and per_face[faces[0]] != per_face[faces[1]]:
            result.append(edge_it.index())
        edge_it.next()
    return result


def create_base_uvs(transform):
    """Proyección planar: una sola isla por pieza conectada y ninguna costura."""
    cmds.polyProjection(transform + ".f[*]", type="Planar", mapDirection="y", constructionHistory=True)


def seed_uvs(transform, seam_edges):
    """UVs de partida para Unfold3D: costuras solo en seam_edges.

    Proyección automática de 6 planos (ninguna cara queda aplastada) y después se cosen todas
    las aristas que no son costura. Con una sola proyección planar las caras perpendiculares
    quedaban sin área y Unfold3D no podía desplegarlas (las caras de un cubo acababan en un punto).
    """
    cmds.polyAutoProjection(transform + ".f[*]", planes=6, layoutMethod=0, layout=0, optimize=1,
                            percentageSpace=0.0, constructionHistory=True)
    seams = set(seam_edges)
    soft = [e for e in range(cmds.polyEvaluate(transform, edge=True)) if e not in seams]
    if soft:
        cmds.polyMapSewMove(components(transform, "e", soft), constructionHistory=True)


def fix_nonmanifold_uvs(transform):
    """Corta por las UVs no-manifold (islas que se tocan solo en un punto), como Mesh > Cleanup de Maya.

    u3dAutoSeam se niega a trabajar si existen. Devuelve cuántos componentes había.
    """
    shape = mesh_shape(transform)
    found = 0
    for _ in range(3):
        uvs = cmds.polyInfo(shape, nonManifoldUVs=True) or []
        edges = cmds.polyInfo(shape, nonManifoldUVEdges=True) or []
        if not uvs and not edges:
            break
        found += len(cmds.ls(uvs + edges, flatten=True) or [])
        cut = list(edges)
        if uvs:
            # mismos flags que cleanupNonManifoldUVGeometry en polyCleanupArgList.mel
            cut += cmds.polyListComponentConversion(uvs, fv=True, ff=True, fuv=True, fvf=True, te=True,
                                                    vfa=True) or []
        cmds.polyMapCut(cut, constructionHistory=True)
    return found


def _auto_seam_selection(split_shells, cut_pipes, log=None, name=""):
    """u3dAutoSeam sobre la selección. A veces falla por dentro con una malla sana (E62044 con un retopo de 21.000 caras)
    según lo que se le pida partir, y sin tocar nada: se reintenta pidiendo menos; lo que quede sin abrir lo cortan luego
    fix_charts y el despliegue. Devuelve False si no pudo con ninguna combinación."""
    wanted = (float(split_shells), bool(cut_pipes))
    tries = [wanted] + [t for t in ((wanted[0], False), (0.0, wanted[1]), (0.0, False)) if t != wanted]
    for index, (split, pipes) in enumerate(tries):
        try:
            cmds.u3dAutoSeam(splitShells=split, cutPipes=pipes)
        except RuntimeError as exc:
            # una malla que Unfold3D rechaza de verdad no se arregla pidiéndole menos: que lo diga
            if "non-manifold" in str(exc):
                raise
            if index == len(tries) - 1:
                if log:
                    log("{}: Unfold3D no pudo añadir costuras automáticas ({}).".format(
                        name, str(exc).splitlines()[0].strip()), "warning")
                return False
            continue
        if index and log:
            log("{}: Unfold3D falló con los ajustes pedidos; costuras automáticas con división {:g} y cortar tubos {}."
                .format(name, split, "sí" if pipes else "no"), "warning")
        return True
    return False


def auto_seams(transform, split_shells, cut_pipes, log=None):
    previous = cmds.ls(selection=True, long=True) or []
    try:
        # u3dAutoSeam no hace nada si se le pasa el nombre: trabaja sobre la selección
        cmds.select(transform, replace=True)
        return _auto_seam_selection(split_shells, cut_pipes, log, short_name(transform))
    finally:
        if previous:
            cmds.select(previous, replace=True)
        else:
            cmds.select(clear=True)


def texel_density(transform, map_size):
    """Píxeles por unidad de escena (cm por defecto) con una textura de map_size."""
    path = dag_path(mesh_shape(transform))
    world = uv = 0.0
    it = om.MItMeshPolygon(path)
    while not it.isDone():
        world += it.getArea(om.MSpace.kWorld)
        if it.hasUVs():
            uv += it.getUVArea()
        it.next()
    if world <= 0 or uv <= 0:
        return 0.0
    return math.sqrt(uv / world) * map_size


def stretch(transform):
    """Estiramiento de área: cuánto cambia el tamaño de cada cara en UV respecto a 3D.

    1.0 = perfecto. Devuelve la media ponderada por área y el % de caras con más de 1.5x.
    """
    path = dag_path(mesh_shape(transform))
    ratios = []
    world_total = uv_total = 0.0
    it = om.MItMeshPolygon(path)
    while not it.isDone():
        world = it.getArea(om.MSpace.kWorld)
        flat = it.getUVArea() if it.hasUVs() else 0.0
        ratios.append((world, flat))
        world_total += world
        uv_total += flat
        it.next()
    if world_total <= 0 or uv_total <= 0:
        return {"mean": 0.0, "over_1_5_percent": 0.0}
    reference = uv_total / world_total
    weighted = bad = 0.0
    for world, flat in ratios:
        if world <= 0:
            continue
        r = (flat / world) / reference
        distortion = max(r, 1.0 / r) if r > 0 else 10.0
        # la media se limita a 4x: unas pocas caras degeneradas la disparaban (5.3 en vez de ~1.4)
        weighted += min(distortion, 4.0) * world
        bad += distortion > 1.5
    return {"mean": weighted / world_total, "over_1_5_percent": 100.0 * bad / len(ratios)}


def face_stretch(transform):
    """Estiramiento de área de cada cara (1 = perfecto, 2 = el doble o la mitad de lo que toca), con la
    misma referencia que stretch(). Junto con la isla de cada cara: (array, array)."""
    fn = _mesh_fn(transform)
    path = dag_path(mesh_shape(transform))
    world, flat = [], []
    it = om.MItMeshPolygon(path)
    while not it.isDone():
        world.append(it.getArea(om.MSpace.kWorld))
        flat.append(it.getUVArea() if it.hasUVs() else 0.0)
        it.next()
    world, flat = np.array(world), np.array(flat)
    reference = flat.sum() / max(world.sum(), 1e-30)
    ratio = np.where(world > 0, flat / np.maximum(world, 1e-30) / max(reference, 1e-30), 1.0)
    distortion = np.where(ratio > 0, np.maximum(ratio, 1.0 / np.maximum(ratio, 1e-30)), 10.0)
    _, shell_ids = fn.getUvShellsIds()
    uv_counts, uv_ids = fn.getAssignedUVs()
    starts = np.concatenate([[0], np.cumsum(uv_counts)[:-1]]).astype(int)
    shells = np.array([shell_ids[uv_ids[st]] if n else -1 for st, n in zip(starts, uv_counts)])
    return distortion, shells


def uv_area(transform):
    path = dag_path(mesh_shape(transform))
    total = 0.0
    it = om.MItMeshPolygon(path)
    while not it.isDone():
        if it.hasUVs():
            total += it.getUVArea()
        it.next()
    return total


def shell_bounds(transform):
    """[(umin, umax, vmin, vmax), ...] por isla UV."""
    fn = _mesh_fn(transform)
    if fn.numUVs() == 0:
        return []
    count, shell_ids = fn.getUvShellsIds()
    us, vs = fn.getUVs()
    bounds = [[1e30, -1e30, 1e30, -1e30] for _ in range(count)]
    for index, shell in enumerate(shell_ids):
        b = bounds[shell]
        u, v = us[index], vs[index]
        b[0] = min(b[0], u)
        b[1] = max(b[1], u)
        b[2] = min(b[2], v)
        b[3] = max(b[3], v)
    return [tuple(b) for b in bounds]


def _face_shell_areas(transform):
    """Área UV con signo de cada cara y la isla a la que pertenece: [(cara, isla, área)]."""
    fn = _mesh_fn(transform)
    if fn.numUVs() == 0:
        return []
    _, shell_ids = fn.getUvShellsIds()
    us, vs = fn.getUVs()
    us, vs = np.asarray(us), np.asarray(vs)
    uv_counts, uv_ids = fn.getAssignedUVs()
    counts, ids = np.asarray(uv_counts, dtype=np.int64), np.asarray(uv_ids, dtype=np.int64)
    face = np.repeat(np.arange(len(counts)), counts)
    first = np.repeat(np.cumsum(counts) - counts, counts)
    following = ids[first + (np.arange(len(ids)) - first + 1) % np.repeat(counts, counts)]
    # suma de los productos cruzados de cada cara, en el orden de sus esquinas
    cross = us[ids] * vs[following] - us[following] * vs[ids]
    area = np.bincount(face, weights=cross, minlength=len(counts)) * 0.5
    faces = np.nonzero(counts >= 3)[0]
    shells = np.asarray(shell_ids, dtype=np.int64)[ids[(np.cumsum(counts) - counts)[faces]]]
    return list(zip(faces.tolist(), shells.tolist(), area[faces].tolist()))


def flipped_faces(transform):
    """Caras con las UVs en espejo (área con signo negativo)."""
    return [face for face, _, area in _face_shell_areas(transform) if area < 0]


def fix_flipped_shells(transform):
    """Voltea en U las islas que han quedado en espejo (darían normal maps invertidos). Devuelve cuántas."""
    totals = {}
    for _, shell, area in _face_shell_areas(transform):
        totals[shell] = totals.get(shell, 0.0) + area
    flipped = [shell for shell, area in totals.items() if area < 0]
    if not flipped:
        return 0
    fn = _mesh_fn(transform)
    _, shell_ids = fn.getUvShellsIds()
    by_shell = {}
    for uv_index, shell in enumerate(shell_ids):
        by_shell.setdefault(shell, []).append(uv_index)
    for shell in flipped:
        uvs = components(transform, "map", by_shell[shell])
        (umin, umax), (vmin, vmax) = cmds.polyEvaluate(uvs, boundingBoxComponent2d=True)
        cmds.polyEditUV(uvs, pivotU=(umin + umax) * 0.5, pivotV=(vmin + vmax) * 0.5, scaleU=-1.0, scaleV=1.0)
    return len(flipped)


def overlapping_faces(transform):
    return flat_indices(cmds.polyUVOverlap(transform + ".f[*]", overlappingComponents=True) or [])


def split_overlaps(transform, faces):
    """Separa en una isla nueva la zona que se solapa (ampliada un anillo de caras)."""
    grown = cmds.polyListComponentConversion(components(transform, "f", faces), toVertex=True)
    grown = cmds.polyListComponentConversion(grown, toFace=True)
    border = cmds.polyListComponentConversion(grown, toEdge=True, border=True)
    if not border:
        return None
    cmds.polyMapCut(border, constructionHistory=True)
    return grown


def shells_crossing_tiles(bounds, eps=1e-5):
    """Islas que no caben enteras en un único tile UDIM."""
    crossing = []
    for index, (umin, umax, vmin, vmax) in enumerate(bounds):
        if math.floor(umin + eps) != math.floor(umax - eps) or math.floor(vmin + eps) != math.floor(vmax - eps):
            crossing.append(index)
    return crossing


def check(transform, udim=False, known_overlaps=None):
    """Resumen de calidad: límites, solapes, islas y (en UDIM) islas que cruzan tiles.

    known_overlaps: nº de caras solapadas ya medido sin cambios posteriores. polyUVOverlap es lo más
    lento de la comprobación (2,5 s con 40.000 caras), así que no se repite si ya se sabe.
    """
    bounds = shell_bounds(transform)
    if known_overlaps is None:
        overlapping = cmds.polyUVOverlap(transform + ".f[*]", overlappingComponents=True) or []
        known_overlaps = len(cmds.ls(overlapping, flatten=True) or [])
    report = {
        "shells": len(bounds),
        "overlapping_faces": known_overlaps,
        "flipped_faces": len(flipped_faces(transform)),
        "bounds": cmds.polyEvaluate(transform, boundingBox2d=True),
        "outside_0_1": False,
        "crossing_tiles": [],
    }
    (umin, umax), (vmin, vmax) = report["bounds"]
    report["outside_0_1"] = umin < -1e-5 or vmin < -1e-5 or umax > 1 + 1e-5 or vmax > 1 + 1e-5
    if udim:
        report["crossing_tiles"] = shells_crossing_tiles(bounds)
        report["negative"] = umin < -1e-5 or vmin < -1e-5
    return report


def _layout_kwargs(settings):
    map_size = settings["uv_map_size"]
    kwargs = {
        "resolution": int(settings["uv_layout_quality"]),
        # padding en píxeles -> unidades UV (como hace Maya con "Padding units: pixels")
        "shellSpacing": float(config.padding_px(settings)) / map_size,
        "tileMargin": float(config.padding_px(settings)) / 2.0 / map_size,
    }
    if settings.get("uv_straight_islands", True):
        # islas ya de pie (orient_upright): el empaquetador solo puede dejarlas así o tumbarlas 90°,
        # nunca boca abajo ni torcidas
        if settings["uv_allow_rotation"]:
            kwargs.update(rotateMin=0.0, rotateMax=90.0, rotateStep=90.0)
    elif settings["uv_allow_rotation"]:
        kwargs.update(rotateMin=0.0, rotateMax=360.0, rotateStep=float(settings.get("uv_rotate_step", 90.0)))
    if settings.get("uv_pack_iterations", 1) > 1:
        kwargs["mutations"] = int(settings["uv_pack_iterations"])
    if settings.get("uv_pre_rotate", 0) and not settings.get("uv_straight_islands", True):
        kwargs["preRotateMode"] = int(settings["uv_pre_rotate"])
    return kwargs


def shell_up_directions(transform):
    """Dirección en UV hacia la que "sube" el modelo (Y del mundo) en cada isla: {isla: (du, dv)}."""
    fn = _mesh_fn(transform)
    if fn.numUVs() == 0:
        return {}
    _, shell_ids = fn.getUvShellsIds()
    us, vs = fn.getUVs()
    points = fn.getPoints(om.MSpace.kWorld)
    counts, vertices = fn.getVertices()
    uv_counts, uv_ids = fn.getAssignedUVs()
    up = {}
    offset = uv_offset = 0
    for count, uv_count in zip(counts, uv_counts):
        if uv_count == count and count >= 3:
            v = [vertices[offset + k] for k in range(3)]
            t = [uv_ids[uv_offset + k] for k in range(3)]
            e1 = (us[t[1]] - us[t[0]], vs[t[1]] - vs[t[0]])
            e2 = (us[t[2]] - us[t[0]], vs[t[2]] - vs[t[0]])
            det = e1[0] * e2[1] - e1[1] * e2[0]
            if abs(det) > 1e-14:
                dy1 = points[v[1]].y - points[v[0]].y
                dy2 = points[v[2]].y - points[v[0]].y
                # gradiente de Y en UV: g . e1 = dy1, g . e2 = dy2
                gu = (dy1 * e2[1] - dy2 * e1[1]) / det
                gv = (e1[0] * dy2 - e2[0] * dy1) / det
                # se pondera por el área UV (|det|) para que las caras grandes manden
                du, dv = up.get(shell_ids[t[0]], (0.0, 0.0))
                up[shell_ids[t[0]]] = (du + gu * abs(det), dv + gv * abs(det))
        offset += count
        uv_offset += uv_count
    return up


def orient_upright(transform):
    """Gira cada isla para que el "arriba" del modelo apunte hacia arriba en UV (antes de empaquetar:
    después, girar una isla en su sitio pisaba a las vecinas encajadas en su caja). Devuelve cuántas."""
    up = shell_up_directions(transform)
    fn = _mesh_fn(transform)
    _, shell_ids = fn.getUvShellsIds()
    by_shell = {}
    for uv_index, shell in enumerate(shell_ids):
        by_shell.setdefault(shell, []).append(uv_index)
    turned = 0
    # islas sin "arriba" (tapas horizontales): su dirección es ruido numérico y no se giran
    noise = 1e-6 * max([abs(du) + abs(dv) for du, dv in up.values()] or [0.0])
    for shell, (du, dv) in up.items():
        if abs(du) + abs(dv) <= max(noise, 1e-12):
            continue
        # ángulo entre el "arriba" actual y +V; polyEditUV gira en sentido antihorario
        angle = math.degrees(math.atan2(du, dv))
        if abs(angle) < 1.0:
            continue
        uvs = components(transform, "map", by_shell[shell])
        (umin, umax), (vmin, vmax) = cmds.polyEvaluate(uvs, boundingBoxComponent2d=True)
        cmds.polyEditUV(uvs, pivotU=(umin + umax) * 0.5, pivotV=(vmin + vmax) * 0.5, angle=angle)
        turned += 1
    return turned


def align_islands_to_loops(transform):
    """Gira cada isla lo justo (menos de 45°) para que sus loops vayan rectos en U y V: la cuadrícula se ve ordenada y las
    islas encajan mejor. Devuelve cuántas se giraron."""
    fn = _mesh_fn(transform)
    if fn.numUVs() == 0:
        return 0
    count, shell_ids = fn.getUvShellsIds()
    shells = np.asarray(shell_ids, dtype=np.int64)
    us, vs = fn.getUVs()
    us, vs = np.asarray(us), np.asarray(vs)
    uv_counts, uv_ids = fn.getAssignedUVs()
    counts, ids = np.asarray(uv_counts, dtype=np.int64), np.asarray(uv_ids, dtype=np.int64)
    first = np.repeat(np.cumsum(counts) - counts, counts)
    following = ids[first + (np.arange(len(ids)) - first + 1) % np.repeat(counts, counts)]
    du, dv = us[following] - us[ids], vs[following] - vs[ids]
    length = np.hypot(du, dv)
    angle = np.arctan2(dv, du)
    # cuatro veces el ángulo: una arista en U y otra en V cuentan como la misma dirección
    owner = shells[ids]
    real = np.bincount(owner, weights=length * np.cos(4.0 * angle), minlength=count)
    imag = np.bincount(owner, weights=length * np.sin(4.0 * angle), minlength=count)
    total = np.bincount(owner, weights=length, minlength=count)
    order = np.hypot(real, imag) / np.maximum(total, 1e-30)
    turns = -np.degrees(np.arctan2(imag, real)) / 4.0
    by_shell = {}
    for uv_index, shell in enumerate(shell_ids):
        by_shell.setdefault(shell, []).append(uv_index)
    turned = 0
    for shell in np.nonzero((order >= ALIGN_MIN_ORDER) & (np.abs(turns) >= 0.5))[0].tolist():
        uvs = components(transform, "map", by_shell[shell])
        (umin, umax), (vmin, vmax) = cmds.polyEvaluate(uvs, boundingBoxComponent2d=True)
        cmds.polyEditUV(uvs, pivotU=(umin + umax) * 0.5, pivotV=(vmin + vmax) * 0.5, angle=float(turns[shell]))
        turned += 1
    return turned


def straighten_islands(transform, log=default_log):
    """Endereza las islas que son tubos cortados a lo largo (brazos, piernas, dedos, cables, colas): las dos orillas de
    cada corte pasan a ser paralelas y de la misma longitud, y el dibujo de una textura (vetas, rayones) sigue la misma
    dirección al cruzar la costura. Solo se tocan las islas que no estiran más que antes (ver mbl_engine.uv_straighten).
    Devuelve cuántas islas se enderezaron."""
    from mbl_engine import uv_straighten
    path = dag_path(mesh_shape(transform))
    fn = om.MFnMesh(path)
    if fn.numUVs() == 0:
        return 0
    points = np.array([(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)], dtype=float)
    counts, vertices = fn.getVertices()
    uv_counts, uv_ids = fn.getAssignedUVs()
    us, vs = fn.getUVs()
    faces, uv_faces, start, uv_start = [], [], 0, 0
    for count, uv_count in zip(counts, uv_counts):
        faces.append(list(vertices[start:start + count]))
        uv_faces.append(list(uv_ids[uv_start:uv_start + uv_count]))
        start += count
        uv_start += uv_count
    new, done = uv_straighten.straighten(points, faces, np.column_stack([us, vs]), uv_faces)
    # como Follow Active Quads o Straighten UV a mano, pero solo donde la isla es una rejilla sin polos
    new, grids = uv_straighten.rectify_grids(points, faces, new, uv_faces)
    if not done and not grids:
        return 0
    # las UVs de una malla con historial no se pueden escribir directamente: se escriben en una copia y se pasan
    copy = cmds.ls(cmds.duplicate(transform, name="mblStraightenTry")[0], long=True)[0]
    try:
        om.MFnMesh(dag_path(mesh_shape(copy))).setUVs(om.MFloatArray(new[:, 0].tolist()), om.MFloatArray(new[:, 1].tolist()))
        cmds.polyTransfer(transform, uvSets=True, vertices=False, vertexColor=False, alternateObject=copy)
    finally:
        cmds.delete(copy)
    if done:
        total = sum(d["length"] for d in done) or 1.0
        log("{}: {} isla(s) enderezadas (tubos cortados a lo largo): las orillas del corte casan {:.0f}% -> {:.0f}% "
            "de su longitud, sin estirar más.".format(
                short_name(transform), len(done), sum(d["matched"][0] * d["length"] for d in done) / total,
                sum(d["matched"][1] * d["length"] for d in done) / total))
    if grids:
        log("{}: {} isla(s) sin polos enderezadas como una cuadrícula recta.".format(short_name(transform), grids))
    return len(done) + grids


def _mesh_arrays(transform):
    """(puntos, caras, UV, caras de UV) de la malla como arrays, o None si no tiene UVs."""
    fn = om.MFnMesh(dag_path(mesh_shape(transform)))
    if fn.numUVs() == 0:
        return None
    points = np.array([(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)], dtype=float)
    counts, vertices = fn.getVertices()
    uv_counts, uv_ids = fn.getAssignedUVs()
    us, vs = fn.getUVs()
    faces, uv_faces, start, uv_start = [], [], 0, 0
    for count, uv_count in zip(counts, uv_counts):
        faces.append(list(vertices[start:start + count]))
        uv_faces.append(list(uv_ids[uv_start:uv_start + uv_count]))
        start += count
        uv_start += uv_count
    return points, faces, np.column_stack([us, vs]), uv_faces


# islas que se intentan corregir como máximo (las peores primero), y fuerza de la relajación
FIX_MAX_ISLANDS = 8
FIX_OPTIMIZE_ITERATIONS = 40
FIX_OPTIMIZE_POWER = 4
# un corte extra solo se acepta si la isla pierde al menos esta fracción de sus caras > 1,5x
FIX_CUT_GAIN = 0.5


def _island_state(arrays, face_ids, world):
    """(estiramiento propio, hay espejos) de un grupo de caras."""
    from mbl_engine import uv_metrics, uv_quality
    points, faces, uv, uv_faces = arrays
    sub = [uv_faces[f] for f in face_ids]
    signed = uv_quality.uv_face_areas(uv, sub)[0]
    return uv_metrics.own_stretch(world[face_ids], np.abs(signed)), bool((signed <= 0).any())


def fix_stretched_islands(transform, settings, log=default_log):
    """Mide el estiramiento de cada isla y corrige las malas (más del 10 % de caras > 1,5x o estiramiento medio > 1,3).

    Por isla, sobre una copia y como mucho dos intentos: (1) Optimize con más iteraciones y más potencia solo en esa isla;
    (2) si no basta, un corte extra desde la zona más estirada hasta la costura más cercana y se vuelve a desplegar. Se
    acepta un intento solo si la isla pierde caras estiradas sin subir su estiramiento medio, sin caras en espejo y sin
    solapes dentro de la isla. Devuelve cuántas islas mejoraron."""
    from mbl_engine import uv_metrics, uv_quality
    arrays = _mesh_arrays(transform)
    if arrays is None:
        return 0
    points, faces, uv, uv_faces = arrays
    bad = [s for s in uv_metrics.island_stats(points, faces, uv, uv_faces) if s["bad"] and s["faces"] >= 4]
    if not bad:
        return 0
    bad.sort(key=lambda s: -s["over_percent"] * s["faces"])
    island, _ = uv_quality.islands(uv_faces)
    world = uv_quality.face_areas(points, faces)
    members = [np.flatnonzero(island == s["island"]) for s in bad[:FIX_MAX_ISLANDS]]
    map_size = int(settings["uv_map_size"])
    pad = config.padding_px(settings)
    edge_of = {}
    it = om.MItMeshEdge(dag_path(mesh_shape(transform)))
    while not it.isDone():
        a, b = it.vertexId(0), it.vertexId(1)
        edge_of[(min(a, b), max(a, b))] = it.index()
        it.next()

    def optimize(mesh, face_ids, iterations, power):
        cmds.u3dOptimize(components(mesh, "f", [int(f) for f in face_ids]), iterations=iterations, power=power,
                         surfangle=1.0, borderintersection=1, triangleflip=1, mapsize=map_size, roomspace=pad)

    done = 0
    for face_ids in members:
        before, _ = _island_state(arrays, face_ids, world)
        for attempt in (1, 2):
            copy = cmds.ls(cmds.duplicate(transform, name="mblStretchTry")[0], long=True)[0]
            try:
                if attempt == 1:
                    optimize(copy, face_ids, FIX_OPTIMIZE_ITERATIONS, FIX_OPTIMIZE_POWER)
                    gain = 0.0
                else:
                    worst = int(face_ids[np.argmax(uv_metrics.face_stretch(points, faces, uv, uv_faces)[0][face_ids])])
                    path = uv_metrics.cut_path(points, faces, face_ids, worst)
                    if not path:
                        break
                    cmds.polyMapCut(components(copy, "e", sorted({edge_of[(min(a, b), max(a, b))] for a, b in path})),
                                    constructionHistory=True)
                    cmds.u3dUnfold(components(copy, "f", [int(f) for f in face_ids]), iterations=1, pack=0,
                                   borderintersection=1, triangleflip=1, mapsize=map_size, roomspace=pad)
                    optimize(copy, face_ids, int(settings["uv_optimize_iterations"]) or 5, 1)
                    fix_flipped_shells(copy)
                    gain = FIX_CUT_GAIN
                trial = _mesh_arrays(copy)
                own, flipped = _island_state(trial, face_ids, world)
                if flipped or not uv_metrics.improved(before, own, gain):
                    continue
                if int(uv_quality.overlapping_faces(trial[2], [trial[3][f] for f in face_ids]).sum()):
                    continue
                cmds.polyTransfer(transform, uvSets=True, vertices=False, vertexColor=False, alternateObject=copy)
                arrays = _mesh_arrays(transform)
                points, faces, uv, uv_faces = arrays
                done += 1
                log("{}: isla de {} caras: {} -> {} caras > 1.5x, estiramiento {:.3f} -> {:.3f}{}.".format(
                    short_name(transform), len(face_ids), before[1], own[1], before[0], own[0],
                    " (con un corte extra)" if attempt == 2 else ""))
                break
            finally:
                if cmds.objExists(copy):
                    cmds.delete(copy)
    return done


def layout_0_1(transform, settings):
    cmds.u3dLayout(transform, preScaleMode=1, packBox=(0.0, 1.0, 0.0, 1.0), **_layout_kwargs(settings))


# sin tumbar islas, por debajo de esta fracción de textura aprovechada (formas sencillas con pocas islas, como una
# esfera) se prueba también a tumbarlas, y se queda ese resultado si gana por al menos ROTATION_GAIN
POOR_LAYOUT = 0.50
ROTATION_GAIN = 0.05


def best_layout(transform, settings):
    """Empaqueta en 0-1 con _best_layout. Si las islas no se pueden tumbar y la textura queda mal aprovechada,
    prueba también a tumbarlas sobre una copia. Devuelve el % de textura aprovechada."""
    area = _best_layout(transform, settings)
    if settings["uv_allow_rotation"] or area >= 100.0 * POOR_LAYOUT:
        return area
    copy = cmds.duplicate(transform, name="mblRotateTry")[0]
    try:
        rotated = _best_layout(copy, dict(settings, uv_allow_rotation=True))
        if rotated - area >= 100.0 * ROTATION_GAIN:
            cmds.polyTransfer(transform, uvSets=True, vertices=False, vertexColor=False, alternateObject=copy)
            return rotated
    finally:
        if cmds.objExists(copy):
            cmds.delete(copy)
    return area


def _best_layout(transform, settings):
    """Empaqueta en 0-1 probando varias combinaciones sobre una copia y aplica la que más textura
    aprovecha. u3dLayout es determinista pero muy sensible a los ajustes: con el mismo humano daba
    entre el 48% y el 66% de la textura según pasadas, resolución y rotación.
    Devuelve el % de textura aprovechada."""
    tries = int(settings.get("uv_pack_tries", 1))
    base = dict(settings)
    candidates = [base]
    if tries > 1:
        quality = int(settings["uv_layout_quality"])
        # ordenadas por lo que más ganó en las pruebas (águila y humano) frente a lo que tardan
        # con islas rectas no se prueba rotar 45° (quedarían torcidas)
        fourth = (dict(base, uv_pack_iterations=20) if settings.get("uv_straight_islands", True)
                  else dict(base, uv_rotate_step=45.0))
        candidates += [dict(base, uv_layout_quality=max(quality * 2, 1024)),
                       dict(base, uv_pack_iterations=10),
                       fourth,
                       dict(base, uv_pack_iterations=10, uv_layout_quality=max(quality * 2, 1024))][:tries - 1]
    if len(candidates) == 1:
        layout_0_1(transform, base)
        return 100.0 * uv_area(transform)
    best, best_area = None, -1.0
    try:
        for candidate in candidates:
            copy = cmds.duplicate(transform, name="mblLayoutTry")[0]
            layout_0_1(copy, candidate)
            area = uv_area(copy)
            if area > best_area:
                if best:
                    cmds.delete(best)
                best, best_area = copy, area
            else:
                cmds.delete(copy)
        # misma topología: se copian las UVs de la copia ganadora (sin repetir el empaquetado)
        cmds.polyTransfer(transform, uvSets=True, vertices=False, vertexColor=False, alternateObject=best)
    finally:
        if best and cmds.objExists(best):
            cmds.delete(best)
    return 100.0 * best_area


def _shell_footprints(transform, pad):
    """{isla: lo que ocupa en la textura}: su área UV más el anillo de padding (aprox. caja: pad x (ancho + alto))."""
    sizes = {shell: pad * (b[1] - b[0] + b[3] - b[2]) for shell, b in enumerate(shell_bounds(transform))}
    for shell, area in _areas_by_shell(transform).items():
        sizes[shell] += abs(area)
    return sizes


def _areas_by_shell(transform):
    """{isla: suma del área UV con signo de sus caras}."""
    areas = {}
    for _, shell, area in _face_shell_areas(transform):
        areas[shell] = areas.get(shell, 0.0) + area
    return areas


def _balanced_groups(sizes, count):
    """Reparte las islas en `count` grupos de tamaño parecido: de mayor a menor, cada una al grupo que menos lleva."""
    groups = [{"shells": [], "used": 0.0} for _ in range(count)]
    for shell in sorted(sizes, key=sizes.get, reverse=True):
        group = min(groups, key=lambda g: g["used"])
        group["shells"].append(shell)
        group["used"] += sizes[shell]
    return [group["shells"] for group in groups if group["shells"]]


def _shell_elements(transform):
    """({isla: UVs}, {isla: caras})."""
    _, shell_ids = _mesh_fn(transform).getUvShellsIds()
    uvs_by_shell, faces_by_shell = {}, {}
    for uv_index, shell in enumerate(shell_ids):
        uvs_by_shell.setdefault(shell, []).append(uv_index)
    for face, shell, _ in _face_shell_areas(transform):
        faces_by_shell.setdefault(shell, []).append(face)
    return uvs_by_shell, faces_by_shell


def _tile_box(tile):
    tile_u, tile_v = tile % UDIM_COLUMNS, tile // UDIM_COLUMNS
    return float(tile_u), tile_u + 1.0, float(tile_v), tile_v + 1.0


def _fitting_density(transform, settings, count):
    """Densidad de texel más alta con la que caben las islas en `count` tiles, y el reparto que la da: empieza con grupos
    de área parecida, deja que el empaquetador escale cada grupo hasta llenar su tile (layoutScaleMode=2; menos de un
    segundo con 20.000 caras) y pasa islas pequeñas del grupo que menos crece al que más: una isla muy abierta llena su
    tile mucho antes de lo que dice su área. Estropea la colocación: quien llama vuelve a empaquetar."""
    map_size = settings["uv_map_size"]
    pad = float(config.padding_px(settings)) / map_size
    layout_0_1(transform, settings)
    current = texel_density(transform, map_size)
    sizes = _shell_footprints(transform, pad)
    groups = _balanced_groups(sizes, count)
    _, faces_by_shell = _shell_elements(transform)
    kwargs = _layout_kwargs(settings)
    before = _areas_by_shell(transform)
    best, best_growth = groups, 0.0
    for _ in range(UDIM_BALANCE_ROUNDS):
        for tile, shells in enumerate(groups):
            faces = [face for shell in shells for face in faces_by_shell.get(shell, [])]
            cmds.u3dLayout(components(transform, "f", faces), preScaleMode=0, layoutScaleMode=2, packBox=_tile_box(tile),
                           **kwargs)
        after = _areas_by_shell(transform)
        growths = [math.sqrt(sum(abs(after[s]) for s in shells) / max(sum(abs(before[s]) for s in shells), 1e-12))
                   for shells in groups]
        if min(growths) > best_growth:
            best, best_growth = [list(shells) for shells in groups], min(growths)
        low, high = growths.index(min(growths)), growths.index(max(growths))
        if len(groups) < 2 or growths[high] < growths[low] * UDIM_BALANCE_GAIN:
            break
        # sale del grupo que frena lo que le sobra para igualar, de las islas pequeñas (la mayor se queda)
        mean = sum(growths) / len(growths)
        excess = sum(sizes[s] for s in groups[low]) * (1.0 - (growths[low] / mean) ** 2)
        moved = 0.0
        for shell in sorted(groups[low], key=sizes.get)[:-1]:
            if moved + sizes[shell] > excess:
                break
            moved += sizes[shell]
            groups[low].remove(shell)
            groups[high].append(shell)
        if not moved:
            break
    return current * best_growth, best


def layout_udim(transform, settings, log=default_log):
    """Reparte las islas en UDIMs. Con uv_udim_tiles > 0 se busca la densidad que llena ese nº de
    tiles; si no, se usa la densidad de texel indicada. Devuelve el nº de tiles usados."""
    tiles_wanted = int(settings.get("uv_udim_tiles", 0))
    if tiles_wanted <= 0:
        return _layout_udim(transform, settings, float(settings["uv_texel_density"]), log)
    name = short_name(transform)
    map_size = settings["uv_map_size"]
    target, groups = _fitting_density(transform, settings, tiles_wanted)
    tiles = _layout_udim(transform, settings, target, log, max_tiles=tiles_wanted, groups=groups)
    log("{}: densidad de texel {:.2f} px/unidad para llenar {} UDIM(s).".format(
        name, texel_density(transform, map_size), tiles))
    if tiles < tiles_wanted:
        log("{}: con {} UDIMs ya no gana resolución: la isla más grande ocupa un tile entero. Para usar "
            "más UDIMs habría que partirla.".format(name, tiles), "warning")
    return tiles


def _layout_udim(transform, settings, target, log=default_log, boost=1, max_tiles=0, groups=None):
    """Escala a la densidad objetivo y reparte las islas en los UDIMs necesarios (hasta `max_tiles`, si se da: se usan
    menos si con menos ya caben; si no caben, se baja la densidad)."""
    name = short_name(transform)
    map_size = settings["uv_map_size"]
    margin = 2.0 * config.padding_px(settings) / map_size

    # primero un layout 0-1 normal: islas con escala 3D uniforme y sin solapes
    layout_0_1(transform, settings)
    # solapes que ya traía el unfold (dentro de una misma isla): el layout no los puede arreglar
    baseline_overlaps = check(transform)["overlapping_faces"]
    current = texel_density(transform, map_size)
    if current <= 0:
        raise UVError("{}: no se pudo medir la densidad de texel.".format(name))
    factor = target / current

    # ninguna isla puede ser más grande que un tile
    biggest = max(max(b[1] - b[0], b[3] - b[2]) for b in shell_bounds(transform))
    if biggest * factor > 1.0 - margin:
        new_factor = (1.0 - margin) * 0.98 / biggest
        log("{}: la isla más grande no cabe en un tile a {:g} px/unidad; se baja a {:.2f} px/unidad."
            .format(name, target, current * new_factor), "warning")
        factor = new_factor
    cmds.polyEditUV(transform + ".map[*]", pivotU=0.0, pivotV=0.0, scaleU=factor, scaleV=factor)

    # Agrupamos las islas por tile nosotros: u3dLayout con -u/-v trata todos los tiles como una
    # sola región y deja islas a caballo entre dos UDIMs (comprobado en Maya 2026.3.2).
    uvs_by_shell, faces_by_shell = _shell_elements(transform)
    pad = float(config.padding_px(settings)) / map_size
    sizes = _shell_footprints(transform, pad)

    kwargs = _layout_kwargs(settings)
    if boost > 1:
        # reintento: el empaquetador dejó islas más juntas que el padding; se le pide más separación y más resolución
        kwargs["resolution"] = int(kwargs["resolution"]) * 2
        kwargs["shellSpacing"] *= 1.0 + 0.5 * (boost - 1)
    fill = float(settings.get("uv_udim_fill", UDIM_FILL))
    count = max(1, int(math.ceil(sum(sizes.values()) / fill)))
    if max_tiles:
        count = min(count, max_tiles)
    steps = 0
    wanted_groups = groups
    areas = _areas_by_shell(transform)
    while True:
        groups = wanted_groups if wanted_groups and len(wanted_groups) == count else _balanced_groups(sizes, min(count, len(sizes)))
        fits = True
        for tile, shells in enumerate(groups):
            box = _tile_box(tile)
            faces = [face for shell in shells for face in faces_by_shell.get(shell, [])]
            cmds.u3dLayout(components(transform, "f", faces), preScaleMode=0, layoutScaleMode=0, packBox=box, **kwargs)
            # una isla sin área no la sabe colocar el empaquetador: no debe hacer creer que el tile no da para más
            placed = [i for shell in shells if abs(areas.get(shell, 0.0)) >= COLLAPSED_AREA for i in uvs_by_shell[shell]]
            if not placed:
                continue
            uv_list = components(transform, "map", placed)
            (umin, umax), (vmin, vmax) = cmds.polyEvaluate(uv_list, boundingBoxComponent2d=True)
            if not (umin >= box[0] - 1e-5 and umax <= box[1] + 1e-5 and vmin >= box[2] - 1e-5 and vmax <= box[3] + 1e-5):
                if len(shells) == 1:
                    raise UVError("{}: una isla no cabe en un UDIM. Baja la densidad de texel o el padding.".format(name))
                fits = False
                break
        if fits:
            break
        if max_tiles and count >= max_tiles and steps < UDIM_DENSITY_STEPS:
            # los tiles pedidos ya no dan para esta densidad: se baja un poco y se vuelve a repartir
            steps += 1
            cmds.polyEditUV(transform + ".map[*]", pivotU=0.0, pivotV=0.0, scaleU=UDIM_DENSITY_STEP, scaleV=UDIM_DENSITY_STEP)
            factor *= UDIM_DENSITY_STEP
            sizes = _shell_footprints(transform, pad)
            continue
        if count >= len(sizes):
            raise UVError("{}: no se consiguió repartir las islas en UDIMs. Baja la densidad de texel o el padding."
                          .format(name))
        count += 1
    tile = len(groups)

    report = check(transform, udim=True)
    if report["crossing_tiles"]:
        raise UVError("{}: no se consiguió repartir las islas en UDIMs sin que crucen un tile. "
                      "Baja la densidad de texel o el padding.".format(name))
    if report["overlapping_faces"] > baseline_overlaps:
        if boost < 3:
            return _layout_udim(transform, settings, current * factor, log, boost + 1, max_tiles, wanted_groups)
        # no se pierde la pasada entera por unas caras: el semáforo las marca y se ven en el log
        log("{}: {} caras solapadas tras repartir en UDIMs ({} antes).".format(
            name, report["overlapping_faces"], baseline_overlaps), "warning")
    # el margen real (en píxeles de la textura final) debe respetar el padding; si no, se reempaqueta con más separación
    from . import status
    margin_px = uv_quality.island_margin(*status.uv_arrays(transform)[2:]) if tile > 1 or len(sizes) > 1 else None
    if margin_px is not None and margin_px * map_size < config.padding_px(settings) - 1.0 and boost < 3:
        log("{}: margen entre islas de {:.1f} px (padding {} px); se reempaqueta con más separación."
            .format(name, margin_px * map_size, config.padding_px(settings)), "warning")
        return _layout_udim(transform, settings, target, log, boost + 1, max_tiles, wanted_groups)
    return tile


# una malla que ya está a menos de esta fracción de la densidad común no se toca
COMMON_TEXEL_TOLERANCE = 0.02


def scale_to_density(transform, target, settings, log=default_log):
    """Lleva las islas a `target` píxeles por unidad de escena sin cambiar cómo están cortadas: las escala y las vuelve a
    colocar con su tamaño nuevo (u3dLayout sin escalar, con el mismo padding y los mismos giros de siempre). En 0-1 la
    malla ocupa solo una parte de su textura; en UDIM, los tiles que necesite. Devuelve la densidad que queda."""
    map_size = int(settings["uv_map_size"])
    current = texel_density(transform, map_size)
    if current <= 0 or target <= 0:
        return current
    if settings["uv_udim"]:
        _layout_udim(transform, settings, target, log)
    else:
        factor = target / current
        cmds.polyEditUV(transform + ".map[*]", pivotU=0.0, pivotV=0.0, scaleU=factor, scaleV=factor)
        cmds.u3dLayout(transform, preScaleMode=0, layoutScaleMode=1, packBox=(0.0, 1.0, 0.0, 1.0),
                       **_layout_kwargs(settings))
    return texel_density(transform, map_size)


def common_texel(meshes, settings, log=default_log):
    """Misma densidad de texel en varias mallas ya desplegadas (personaje, ropa, accesorios). Cada una llenó su 0-1 por
    separado, así que la más grande es la que menos píxeles da por unidad de escena: esa densidad es la común (la más
    alta que le cabe en su textura) y las demás bajan a ella y ocupan solo la parte de su textura que les toca.
    Devuelve {malla: densidad que quedó} de las que cambió; con menos de dos mallas no hace nada."""
    map_size = int(settings["uv_map_size"])
    densities = {mesh: texel_density(mesh, map_size) for mesh in meshes}
    densities = {mesh: value for mesh, value in densities.items() if value > 0}
    if len(densities) < 2:
        return {}
    reference = min(densities, key=densities.get)
    target = densities[reference]
    changed = {}
    for mesh, density in densities.items():
        if density <= target * (1.0 + COMMON_TEXEL_TOLERANCE):
            continue
        changed[mesh] = scale_to_density(mesh, target, settings, log)
        log("{}: densidad de texel de {:.2f} a {:.2f} px/unidad ({}% de la textura).".format(
            short_name(mesh), density, changed[mesh], int(round(100.0 * uv_area(mesh)))))
    log("Densidad de texel común: {:.2f} px/unidad a {} px, la de {} (la que menos textura da por unidad). "
        "Ajustadas {} de {} mallas.".format(target, map_size, short_name(reference), len(changed), len(densities)))
    return changed


def symmetric_seams(transform, graph=None):
    """Si la malla es simétrica en X, deja las costuras simétricas. Devuelve True si cambió algo."""
    from .uv_seams import symmetrize_seams
    current = set(uv_seam_edges(transform, graph))
    wanted = symmetrize_seams(transform, current, graph)
    if wanted is None or set(wanted) == current:
        return False
    _swap_seams(transform, current, set(wanted))
    return True


# rondas de partir las islas que siguen estirando, y % de caras > 1,5x con el que ya se para: un poco de estiramiento se
# acepta a cambio de islas grandes y pocos cortes (5 % dejaba un águila en decenas de trozos)
REFINE_ROUNDS = 3
REFINE_TARGET = 10.0
# por debajo de esta área en UV una isla está aplastada
COLLAPSED_AREA = 1e-10
# hasta cuántas caras solapadas se separan al acabar los intentos
OVERLAP_LAST_RESORT = 200
# por debajo de esta fracción del área total una isla se cose a su vecina, si la pieza no va suelta
SMALL_ISLAND = 0.003
# los loops de una isla solo se alinean con los ejes si van más o menos en una dirección (0 = sin orden, 1 = rejilla)
ALIGN_MIN_ORDER = 0.3


def split_limiting_islands(transform, settings, graph, seams):
    """Costuras que parten en dos las islas que no caben en un tile a la densidad que llenaría los UDIM pedidos.
    Devuelve (costuras, caras de esas islas), sin caras si ninguna frena."""
    from .uv_seams import CHART_MIN_FACES, flat_regions
    layout_0_1(transform, settings)
    grow = math.sqrt(int(settings["uv_udim_tiles"]) * float(settings.get("uv_udim_fill", UDIM_FILL)) / max(uv_area(transform), 1e-9))
    _, faces_by_shell = _shell_elements(transform)
    limiting = [faces_by_shell[shell] for shell, b in enumerate(shell_bounds(transform))
                if shell in faces_by_shell and max(b[1] - b[0], b[3] - b[2]) * grow > UDIM_SPLIT_SIZE
                and len(faces_by_shell[shell]) >= 2 * CHART_MIN_FACES]
    if not limiting:
        return seams, []
    mask = graph.seam_mask(seams)
    added = set()
    for faces in limiting:
        edges, _ = flat_regions(graph, np.array(faces, dtype=np.int64), 2, mask)
        added.update(edges)
    wanted = set(seams) | added
    if not graph.all_disks(wanted)[0]:
        wanted = graph.make_disks(wanted)
    return wanted, [face for faces in limiting for face in faces]


def _collapsed_faces(transform):
    """Caras de las islas que no tienen área en UV (aplastadas en un punto o una línea)."""
    areas, by_shell = {}, {}
    for face, shell, area in _face_shell_areas(transform):
        areas[shell] = areas.get(shell, 0.0) + abs(area)
        by_shell.setdefault(shell, []).append(face)
    return [face for shell, area in areas.items() if area < COLLAPSED_AREA for face in by_shell[shell]]


def seed_charts(transform, faces=None):
    """Aplana cada isla (o solo las de `faces`) sin caras invertidas (mbl_engine.uv_seed) para que Unfold3D no parta de
    algo arrugado."""
    from mbl_engine import uv_seed
    fn = _mesh_fn(transform)
    points = np.array([(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)], dtype=float)
    counts, vertices = fn.getVertices()
    uv_counts, uv_ids = fn.getAssignedUVs()
    positions = np.zeros((fn.numUVs(), 3))
    # una cara sin UVs (degenerada: la proyección la salta) no tiene esquinas en uv_ids
    mapped = np.repeat(np.asarray(uv_counts) == np.asarray(counts), np.asarray(counts))
    positions[np.asarray(uv_ids, dtype=np.int64)] = points[np.asarray(vertices, dtype=np.int64)[mapped]]
    uv_faces, start = [], 0
    uv_ids = list(uv_ids)
    for count in uv_counts:
        uv_faces.append(uv_ids[start:start + count])
        start += count
    used = None
    if faces is not None:
        uv_faces = [uv_faces[f] for f in faces]
        used = np.unique(np.fromiter((i for f in uv_faces for i in f), dtype=np.int64))
    new = uv_seed.tutte(positions, uv_faces)
    if used is not None:
        us, vs = fn.getUVs()
        old = np.column_stack([us, vs])
        old[used] = new[used]
        new = old
    # las UVs de una malla con historial no se pueden escribir directamente: se escriben en una copia y se pasan
    copy = cmds.ls(cmds.duplicate(transform, name="mblSeedTry")[0], long=True)[0]
    try:
        _mesh_fn(copy).setUVs(om.MFloatArray(new[:, 0].tolist()), om.MFloatArray(new[:, 1].tolist()))
        cmds.polyTransfer(transform, uvSets=True, vertices=False, vertexColor=False, alternateObject=copy)
    finally:
        cmds.delete(copy)


def _swap_seams(transform, current, wanted):
    """Cose las costuras que sobran y corta las nuevas, sobre las UVs ya desplegadas (partir de una
    proyección nueva dejaba un punto de partida tan arrugado que el unfold no lo deshacía)."""
    if current - wanted:
        cmds.polyMapSewMove(components(transform, "e", sorted(current - wanted)), constructionHistory=True)
    if wanted - current:
        cmds.polyMapCut(components(transform, "e", sorted(wanted - current)), constructionHistory=True)
    fix_nonmanifold_uvs(transform)


def unwrap(transform, settings, log=default_log, tick=None):
    """Costuras + unfold + optimize + layout. Devuelve el informe de check()."""
    ensure_unfold3d()
    name = short_name(transform)
    faces = cmds.polyEvaluate(mesh_shape(transform), face=True)
    limit = int(settings.get("uv_max_faces", 0))
    if limit and faces > limit:
        raise UVError("{}: tiene {} caras. Las UVs de una malla tan densa pueden agotar la memoria y cerrar Maya. "
                      "Haz antes el retopo, o sube el límite 'Máx. caras para UVs' si sabes que tu equipo puede."
                      .format(name, faces))
    # Unfold3D se niega a trabajar con geometría no-manifold: se avisa antes de tocar nada
    from .cleanup import nonmanifold_counts
    bad_edges, bad_vertices = nonmanifold_counts(transform)
    if bad_edges or bad_vertices:
        raise UVError("{}: tiene {} aristas y {} vértices no-manifold y Unfold3D no puede trabajar así. "
                      "Pulsa 'Limpiar' con 'Reparar no-manifold' activado (o haz el retopo) y repite."
                      .format(name, bad_edges, bad_vertices))
    # la pasada es atómica: si falla (o se cancela) la malla vuelve a las UVs y costuras que tenía. Antes, una pasada
    # fallida dejaba las costuras a medias y la siguiente las tomaba como del usuario (uv_respect_existing): sin costuras
    # automáticas, sin arreglo de islas ni separación de solapes
    transforms_before = set(cmds.ls(type="transform", long=True))
    backup = _backup_uvs(transform)
    try:
        return _unwrap(transform, settings, log, tick)
    except Exception as exc:
        _restore_uvs(transform, backup, transforms_before, log)
        if isinstance(exc, RuntimeError):
            raise UVError("{}: Unfold3D falló: {}".format(name, str(exc).strip()))
        raise
    finally:
        if backup and cmds.objExists(backup):
            cmds.delete(backup)


def _backup_uvs(transform):
    """Copia oculta de la malla con sus UVs de ahora (misma topología y orden de vértices y caras)."""
    backup = cmds.ls(cmds.duplicate(transform, name="mblUVBackup")[0], long=True)[0]
    cmds.hide(backup)
    return backup


def _restore_uvs(transform, backup, transforms_before, log):
    """Deja `transform` con las UVs de `backup` y sin el historial ni las copias de trabajo de la pasada fallida.
    No lanza nada: lo que importa es el error original."""
    name = short_name(transform)
    try:
        # copias de trabajo que la pasada no llegó a borrar (mblSeamTry, mblLayoutTry...)
        for node in cmds.ls(type="transform", long=True):
            if node not in transforms_before and node != backup and short_name(node).startswith("mbl"):
                cmds.delete(node)
        if _mesh_fn(backup).numUVs() == 0:
            cmds.polyMapDel(transform + ".f[*]")
        else:
            cmds.polyTransfer(transform, uvSets=True, vertices=False, vertexColor=False, alternateObject=backup)
        cmds.bakePartialHistory(transform, prePostDeformers=True)
        log("{}: la pasada de UVs falló; la malla se queda con las UVs y costuras que tenía.".format(name),
            "warning")
    except Exception as exc:
        log("{}: la pasada de UVs falló y no se pudieron devolver las UVs que tenía la malla ({}). "
            "Deshaz con Ctrl+Z.".format(name, exc), "error")


def _warn_before_unwrap(transform, log):
    """Lo que estropea un despliegue sin dar error: escala sin aplicar y n-gons."""
    name = short_name(transform)
    scale = cmds.getAttr(transform + ".scale")[0]
    if max(scale) - min(scale) > 1e-3:
        log("{}: la escala no es uniforme ({:.3g}, {:.3g}, {:.3g}). Pulsa 'Limpiar' para aplicarla: las UVs pueden salir "
            "deformadas.".format(name, *scale), "warning")
    counts, _ = _mesh_fn(transform).getVertices()
    ngons = sum(1 for count in counts if count > 4)
    if ngons:
        log("{}: {} n-gons (caras de más de 4 lados). Pueden romper el desplegado en zonas curvas.".format(name, ngons),
            "warning")


def _unwrap(transform, settings, log, tick):
    name = short_name(transform)
    _warn_before_unwrap(transform, log)
    map_size = int(settings["uv_map_size"])
    pad = config.padding_px(settings)

    graphs = []
    # bordes entre materiales: costuras fijas (el optimizador de costuras no las mueve ni las quita)
    materials = material_edges(transform) if settings.get("uv_material_seams", True) else []

    def seam_graph():
        """Un solo SeamGraph para todo el desplegado: la geometría no cambia, solo las UVs. Antes se construía
        seis veces (y dos de ellas lanzaban los rayos de visibilidad)."""
        if not graphs:
            from .uv_seams import SeamGraph
            graphs.append(SeamGraph(transform))
            graphs[0].fixed = frozenset(materials)
        return graphs[0]

    existing = uv_seam_edges(transform) if has_uvs(transform) else []
    automatic = not (settings["uv_respect_existing"] and existing)
    if not automatic:
        log("{}: se respetan las {} costuras que ya tiene la malla.".format(name, len(existing)))
    else:
        hard = hard_angle_edges(transform, settings["uv_seam_angle"]) if settings["uv_seam_angle"] < 180 else []
        hard = _long_chains(transform, hard)
        seed_uvs(transform, sorted(set(hard) | set(materials)))
        log("{}: {} costuras por ángulo (>= {:g}°).".format(name, len(hard), settings["uv_seam_angle"]))
        if materials:
            # la proyección de partida puede dejar un borde de material dentro de un mismo plano: se corta
            cmds.polyMapCut(components(transform, "e", materials), constructionHistory=True)
            log("{}: {} aristas entre materiales distintos son costura (cada material con sus islas).".format(
                name, len(materials)))
    # al coser las UVs de partida (o en UVs que ya traía la malla) pueden quedar islas tocándose en un punto
    fixed = fix_nonmanifold_uvs(transform)
    if fixed:
        log("{}: {} puntos UV no-manifold separados.".format(name, fixed))
    if automatic and settings["uv_auto_seams"]:
        # sin pedirle que parta islas (con mallas densas rompe Maya): solo los cortes que hacen falta para abrir la malla
        if auto_seams(transform, 0.0, settings["uv_cut_pipes"], log):
            log("{}: costuras automáticas de Unfold3D añadidas donde hacían falta para desplegar.".format(name))
        # Unfold3D a veces deja islas que no se pueden aplanar (un toro abierto por un solo tajo)
        from .uv_seams import fix_charts, optimize_seams, split_big_charts
        current = set(uv_seam_edges(transform, seam_graph()))
        fixed = fix_charts(transform, current, seam_graph())
        if fixed:
            _swap_seams(transform, current, set(fixed[0]))
            log("{}: {} aristas de corte añadidas para que todas las islas se puedan aplanar.".format(
                name, fixed[1]))
            current = set(fixed[0])
        wanted = current
        big_fraction = float(settings.get("uv_split_big_shells", 0.0)) * (1.75 - float(settings["uv_split_shells"]))
        if settings["uv_split_shells"] > 0 and big_fraction > 0 and settings.get("uv_seam_style") != "hidden":
            wanted, split = split_big_charts(seam_graph(), current, big_fraction)
            if split:
                log("{}: {} isla(s) muy grandes partidas en zonas casi planas.".format(name, split))
        if settings.get("uv_tailor_seams", True):
            # antes de desplegar: esconde y endereza los cortes y cose las islas diminutas
            wanted, info = optimize_seams(transform, wanted, remove_exposed=(
                True if settings.get("uv_seam_style") == "hidden" else "light"), graph=seam_graph())
            wanted = set(wanted)
            log("{}: costuras colocadas: {} cortes de tubo llevados atrás o bajo los brazos, {} tramos enderezados o "
                "escondidos, {} uniones de islas.".format(name, info["rerouted"], info["moved"], info["merged"]))
            # coser restos diminutos puede cerrar una pieza suelta (un cubito sin costuras): no se podría aplanar
            fixed = fix_charts(transform, wanted, seam_graph())
            if fixed:
                wanted = set(fixed[0])
        if wanted != current:
            _swap_seams(transform, current, wanted)
        if settings.get("uv_symmetric", True) and symmetric_seams(transform, seam_graph()):
            log("{}: costuras simétricas: cada isla de un lado tiene su gemela en el otro.".format(name))
        seed_charts(transform)
    symmetric = automatic and settings["uv_auto_seams"] and settings.get("uv_symmetric", True)
    hidden = automatic and settings["uv_auto_seams"] and settings.get("uv_seam_style") == "hidden"
    if tick:
        tick()

    quick_layout = dict(settings, uv_pack_iterations=1)

    def unfold(mesh, target=None):
        cmds.u3dUnfold(target or mesh, iterations=1, pack=0, borderintersection=1, triangleflip=1,
                       mapsize=map_size, roomspace=pad)
        if tick:
            tick()
        if settings["uv_optimize_iterations"] > 0:
            cmds.u3dOptimize(target or mesh, iterations=int(settings["uv_optimize_iterations"]), power=1,
                             surfangle=1.0, borderintersection=1, triangleflip=1, mapsize=map_size, roomspace=pad)
        flipped = fix_flipped_shells(mesh)
        if flipped:
            log("{}: {} isla(s) en espejo volteadas.".format(name, flipped))
        # layout rápido para medir solapes; el bueno (con más pasadas de empaquetado) va al final
        layout_0_1(mesh, quick_layout)
        if tick:
            tick()

    def unfold_and_split(mesh=transform, target=None):
        """Despliega todo (o `target`) y separa las zonas que se pisan. Devuelve los solapes medidos (o None)."""
        unfold(mesh, target)
        # Unfold3D a veces deja cortes que no abren del todo la malla y la isla se pisa a sí misma:
        # se separa esa zona en una isla nueva y se despliega solo ella (volver a desplegar toda la
        # malla movía el problema a otra parte: con el águila de prueba los solapes pasaban de 23 a 134)
        for attempt in range(3):
            if not automatic:
                return None
            bad = overlapping_faces(mesh)
            if not bad:
                return 0
            # la isla que se pisa se parte en dos partes grandes: pelarle solo lo que se pisa dejaba decenas de micro islas
            from .uv_seams import split_charts_in_two
            current = set(uv_seam_edges(mesh, seam_graph()))
            wanted, faces = split_charts_in_two(seam_graph(), current, bad)
            if not faces:
                break
            log("{}: {} caras solapadas, se parte la isla (intento {}).".format(name, len(bad), attempt + 1))
            _swap_seams(mesh, current, wanted)
            seed_charts(mesh, faces)
            unfold(mesh, components(mesh, "f", faces))
        separate_stuck(mesh)
        return None

    def separate_stuck(mesh=transform):
        """Lo poco que sigue pisándose (siempre las mismas caras) va aparte: primero las zonas enteras, para no dejar islas
        de una cara, y cara a cara solo si aun así se pisan."""
        for border in (True, False):
            bad = overlapping_faces(mesh)
            if not bad or len(bad) > OVERLAP_LAST_RESORT:
                return
            faces = components(mesh, "f", bad)
            cut = cmds.polyListComponentConversion(faces, toEdge=True, border=border)
            if cut:
                cmds.polyMapCut(cut, constructionHistory=True)
            seed_charts(mesh, bad)
            unfold(mesh, faces)
            log("{}: {} caras que seguían solapadas separadas en islas propias.".format(name, len(bad)))

    # solapes ya medidos sin cambios posteriores (evita repetir polyUVOverlap)
    known_overlaps = unfold_and_split()

    if automatic and settings["uv_auto_seams"] and settings["uv_split_shells"] > 0 and not hidden:
        # lo que sigue estirando pide más cortes: se parten solo esas islas y se despliegan solo ellas
        from .uv_seams import symmetrize_seams, split_stretched_charts
        for _ in range(REFINE_ROUNDS):
            values, _shells = face_stretch(transform)
            if 100.0 * float(np.mean(values > 1.5)) <= REFINE_TARGET:
                break
            current = set(uv_seam_edges(transform, seam_graph()))
            wanted, split = split_stretched_charts(seam_graph(), current, values)
            if not split:
                break
            if symmetric:
                wanted = set(symmetrize_seams(transform, wanted, seam_graph()) or wanted)
            _swap_seams(transform, current, wanted)
            chart, _count = seam_graph().charts(wanted)
            touched = [f for e in current ^ wanted for f in seam_graph().edge_f[e]]
            faces = np.nonzero(np.isin(chart, np.unique(chart[touched])))[0].tolist()
            seed_charts(transform, faces)
            known_overlaps = unfold_and_split(transform, components(transform, "f", faces))
            log("{}: {} isla(s) que estiraban partidas otra vez ({:.1f}% de caras > 1.5x antes de partirlas).".format(
                name, split, 100.0 * float(np.mean(values > 1.5))))
        if settings["uv_udim"] and int(settings.get("uv_udim_tiles", 0)) > 0:
            # una isla tan grande o tan abierta que no cabe en un tile a la densidad pedida frena a todas las demás
            for _ in range(UDIM_SPLIT_ROUNDS):
                current = set(uv_seam_edges(transform, seam_graph()))
                wanted, faces = split_limiting_islands(transform, settings, seam_graph(), current)
                if not faces:
                    break
                if symmetric:
                    wanted = set(symmetrize_seams(transform, wanted, seam_graph()) or wanted)
                _swap_seams(transform, current, wanted)
                chart, _count = seam_graph().charts(wanted)
                touched = [f for e in current ^ wanted for f in seam_graph().edge_f[e]]
                faces = np.nonzero(np.isin(chart, np.unique(chart[touched])))[0].tolist()
                seed_charts(transform, faces)
                known_overlaps = unfold_and_split(transform, components(transform, "f", faces))

    if automatic and settings["uv_auto_seams"] and settings["uv_split_shells"] > 0 and not hidden:
        # los restos pequeños que han quedado se cosen a su vecina
        from .uv_seams import clean_small_cuts
        current = set(uv_seam_edges(transform, seam_graph()))
        wanted, slits, merged = clean_small_cuts(seam_graph(), current, SMALL_ISLAND)
        if symmetric:
            wanted = set(symmetrize_seams(transform, wanted, seam_graph()) or wanted)
        if wanted != current:
            _swap_seams(transform, current, wanted)
            chart, _count = seam_graph().charts(wanted)
            touched = [f for e in current ^ wanted for f in seam_graph().edge_f[e]]
            faces = np.nonzero(np.isin(chart, np.unique(chart[touched])))[0].tolist()
            seed_charts(transform, faces)
            known_overlaps = unfold_and_split(transform, components(transform, "f", faces))
            log("{}: micro cortes quitados: {} cortes en punta y {} islas pequeñas cosidas a su vecina.".format(
                name, slits, merged))
    collapsed = _collapsed_faces(transform)
    if collapsed:
        seed_charts(transform, collapsed)
    fixed_stretch = 0
    if settings.get("uv_fix_stretch", True):
        fixed_stretch = fix_stretched_islands(transform, settings, log)
        if fixed_stretch:
            known_overlaps = None
    straightened = 0
    if settings.get("uv_straighten_strips", True):
        straightened = straighten_islands(transform, log)
        if straightened:
            known_overlaps = None
            # enderezar mira el estiramiento, no los solapes
            # (antes de medirlo se empaqueta: una isla que ha cambiado de forma pisa a su vecina hasta que se recoloca, y eso
            # no es un solape que haya que cortar)
            if automatic:
                layout_0_1(transform, quick_layout)
                separate_stuck()
    if settings.get("uv_straight_islands", True):
        turned = orient_upright(transform)
        if turned:
            log("{}: {} isla(s) puestas de pie (el arriba del modelo, arriba en la textura).".format(name, turned))
        aligned = align_islands_to_loops(transform)
        if aligned:
            log("{}: {} isla(s) con sus loops alineados a los ejes de la textura.".format(name, aligned))
    if settings["uv_udim"]:
        tiles = layout_udim(transform, settings, log)
        log("{}: layout en {} UDIM(s).".format(name, tiles))
        known_overlaps = None
    elif settings.get("uv_pack_iterations", 1) > 1 or settings.get("uv_pack_tries", 1) > 1:
        best_layout(transform, settings)
        known_overlaps = None
        if tick:
            tick()
    elif straightened or fixed_stretch:
        # sin empaquetado final: lo enderezado puede pisar a las islas vecinas
        layout_0_1(transform, settings)

    # borra el historial de UVs pero conserva deformadores (skin, blendshapes) si los hubiera
    cmds.bakePartialHistory(transform, prePostDeformers=True)
    report = check(transform, udim=settings["uv_udim"], known_overlaps=known_overlaps)
    report["stretch"] = stretch(transform)
    density = texel_density(transform, map_size)
    # la prueba del tablero: cuadrados que siguen siendo cuadrados (el estiramiento solo mira el área)
    from . import status
    _shape_mean, shape_bad = uv_quality.shape_stats(*status.uv_arrays(transform))
    report["shape_bad_percent"] = shape_bad
    log("{}: {:.1f}% del área con cuadrados deformados más de 1.5x.".format(name, shape_bad))
    log("{}: {} islas, densidad {:.2f} px/unidad a {} px, estiramiento medio {:.2f} ({:.1f}% de caras > 1.5x)."
        .format(name, report["shells"], density, map_size, report["stretch"]["mean"],
                report["stretch"]["over_1_5_percent"]))
    if report["overlapping_faces"]:
        log("{}: {} caras con UVs solapadas.".format(name, report["overlapping_faces"]), "warning")
    if report["flipped_faces"]:
        log("{}: {} caras con UVs en espejo.".format(name, report["flipped_faces"]), "warning")
    if not settings["uv_udim"] and report["outside_0_1"]:
        log("{}: hay UVs fuera del rango 0-1.".format(name), "warning")
    if report["crossing_tiles"]:
        log("{}: {} islas cruzan el borde de un UDIM.".format(name, len(report["crossing_tiles"])), "warning")
    report["texel_density"] = density
    return report


# ---------------------------------------------------------------- «Ver cuadrícula»

CHECKER_NODE = "mblCheckerShader"
# la textura ya trae 8 x 8 casillas: una copia por tile (en UDIM se repite igual en cada uno)
CHECKER_TEXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "textures", "uv_checker.png")
# material anterior de cada malla mientras se ve la cuadrícula: {malla: [(shadingEngine, [componentes])]}
_checker_saved = {}


def has_checker(transform):
    return transform in _checker_saved


def _own_members(sg, shape):
    """Elementos del shadingEngine que son de esta malla (la forma, su transform o sus caras)."""
    owners = {shape.split("|")[-1], cmds.listRelatives(shape, parent=True)[0]}
    return [m for m in (cmds.sets(sg, query=True) or []) if m.split(".")[0].split("|")[-1] in owners]


def toggle_checker(transform):
    """Pone a la malla un material temporal con la textura de cuadros numerados (textures/uv_checker.png) para ver a ojo
    el estiramiento y la orientación de las UVs; una segunda llamada lo quita y devuelve el material de antes. No cambia
    las UVs.
    Devuelve True si queda la cuadrícula puesta, False si se quitó."""
    shape = mesh_shape(transform)
    if transform not in _checker_saved and not os.path.isfile(CHECKER_TEXTURE):
        raise UVError("No se encuentra la textura de la cuadrícula ({}). Vuelve a instalar el plugin.".format(CHECKER_TEXTURE))
    if transform in _checker_saved:
        saved = _checker_saved.pop(transform)
        for sg, members in saved:
            if cmds.objExists(sg) and members:
                cmds.sets(members, edit=True, forceElement=sg)
        if not saved:
            cmds.sets(transform, edit=True, forceElement="initialShadingGroup")
        _drop_checker_nodes(transform)
        return False
    saved = []
    for sg in cmds.listConnections(shape, type="shadingEngine") or []:
        if sg not in [s for s, _ in saved]:
            saved.append((sg, _own_members(sg, shape)))
    _checker_saved[transform] = saved
    tag = short_name(transform)
    check = cmds.shadingNode("file", asTexture=True, isColorManaged=True, name="mblChecker_" + tag)
    cmds.setAttr(check + ".fileTextureName", CHECKER_TEXTURE.replace("\\", "/"), type="string")
    place = cmds.shadingNode("place2dTexture", asUtility=True, name="mblChecker_place_" + tag)
    cmds.connectAttr(place + ".outUV", check + ".uvCoord")
    cmds.connectAttr(place + ".outUvFilterSize", check + ".uvFilterSize")
    for attr in ("repeatUV", "rotateUV", "offset", "stagger", "mirrorU", "mirrorV", "wrapU", "wrapV",
                 "coverage", "translateFrame", "rotateFrame", "noiseUV", "vertexUvOne", "vertexUvTwo",
                 "vertexUvThree", "vertexCameraOne"):
        if cmds.attributeQuery(attr, node=place, exists=True) and cmds.attributeQuery(attr, node=check, exists=True):
            cmds.connectAttr("{}.{}".format(place, attr), "{}.{}".format(check, attr), force=True)
    shader = cmds.shadingNode("lambert", asShader=True, name="mblChecker_mat_" + tag)
    cmds.connectAttr(check + ".outColor", shader + ".color")
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=shader + "SG")
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
    cmds.sets(transform, edit=True, forceElement=sg)
    return True


def _drop_checker_nodes(transform):
    tag = short_name(transform)
    for name in ("mblChecker_mat_{}SG".format(tag), "mblChecker_mat_" + tag, "mblChecker_" + tag,
                 "mblChecker_place_" + tag):
        if cmds.objExists(name):
            cmds.delete(name)
