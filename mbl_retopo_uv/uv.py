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

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from .common import UVError, components, dag_path, default_log, flat_indices, mesh_shape, short_name

UDIM_COLUMNS = 10
# fracción de cada tile (por área de la caja de las islas) que se llena antes de pasar al siguiente.
# 0.75: águila en 4 UDIMs 187 -> 214-226 px/unidad (0.5 dejaba medio tile vacío); 0.95 ya no cabe
UDIM_FILL = 0.75


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


def uv_seam_edges(transform):
    """Aristas interiores donde las dos caras usan UVs distintas: las costuras que ya existen."""
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


def auto_seams(transform, split_shells, cut_pipes):
    previous = cmds.ls(selection=True, long=True) or []
    try:
        # u3dAutoSeam no hace nada si se le pasa el nombre: trabaja sobre la selección
        cmds.select(transform, replace=True)
        cmds.u3dAutoSeam(splitShells=float(split_shells), cutPipes=bool(cut_pipes))
    finally:
        if previous:
            cmds.select(previous, replace=True)
        else:
            cmds.select(clear=True)


def split_big_shells(transform, max_fraction):
    """Vuelve a pasar u3dAutoSeam (al máximo) solo sobre las islas con más de `max_fraction` del área 3D.

    Las islas enormes empaquetan mal: con el águila de prueba, tres islas con el 90% del área dejaban
    la textura aprovechada en el 42%; partidas, 68% y además con menos estiramiento.
    Devuelve cuántas islas se partieron."""
    area = {}
    it = om.MItMeshPolygon(dag_path(mesh_shape(transform)))
    while not it.isDone():
        area[it.index()] = it.getArea(om.MSpace.kWorld)
        it.next()
    total = sum(area.values()) or 1.0
    by_shell = {}
    for face, shell, _ in _face_shell_areas(transform):
        by_shell.setdefault(shell, []).append(face)
    big = [faces for faces in by_shell.values() if sum(area[f] for f in faces) > max_fraction * total]
    if not big:
        return 0
    previous = cmds.ls(selection=True, long=True) or []
    try:
        # u3dAutoSeam trabaja sobre la selección: con caras seleccionadas solo corta esas
        cmds.select(components(transform, "f", [f for faces in big for f in faces]), replace=True)
        cmds.u3dAutoSeam(splitShells=1.0, cutPipes=True)
    finally:
        if previous:
            cmds.select(previous, replace=True)
        else:
            cmds.select(clear=True)
    return len(big)


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
    result = []
    uv_counts, uv_ids = fn.getAssignedUVs()
    offset = 0
    for face, count in enumerate(uv_counts):
        ids = uv_ids[offset:offset + count]
        offset += count
        if count < 3:
            continue
        area = 0.0
        for i in range(count):
            a, b = ids[i], ids[(i + 1) % count]
            area += us[a] * vs[b] - us[b] * vs[a]
        result.append((face, shell_ids[ids[0]], area * 0.5))
    return result


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
        "shellSpacing": float(settings["uv_padding_px"]) / map_size,
        "tileMargin": float(settings["uv_padding_px"]) / 2.0 / map_size,
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


def layout_udim(transform, settings, log=default_log):
    """Reparte las islas en UDIMs. Con uv_udim_tiles > 0 se busca la densidad que llena ese nº de
    tiles; si no, se usa la densidad de texel indicada. Devuelve el nº de tiles usados."""
    tiles_wanted = int(settings.get("uv_udim_tiles", 0))
    if tiles_wanted <= 0:
        return _layout_udim(transform, settings, float(settings["uv_texel_density"]), log)
    name = short_name(transform)
    map_size = settings["uv_map_size"]
    layout_0_1(transform, settings)
    pad = float(settings["uv_padding_px"]) / map_size
    # área de las cajas de las islas (lo que cuenta al repartir en tiles) a la escala actual
    boxes = sum((b[1] - b[0] + pad) * (b[3] - b[2] + pad) for b in shell_bounds(transform))
    current = texel_density(transform, map_size)
    fill = float(settings.get("uv_udim_fill", UDIM_FILL))
    target = current * math.sqrt(tiles_wanted * fill / max(boxes, 1e-9))
    quiet = lambda message, level="info": None
    for _ in range(6):
        tiles = _layout_udim(transform, settings, target, quiet)
        if tiles <= tiles_wanted:
            break
        target *= 0.92
    log("{}: densidad de texel {:.2f} px/unidad para llenar {} UDIM(s).".format(
        name, texel_density(transform, map_size), tiles))
    if tiles < tiles_wanted:
        log("{}: con {} UDIMs ya no gana resolución: la isla más grande ocupa un tile entero. Para usar "
            "más UDIMs habría que partirla.".format(name, tiles), "warning")
    return tiles


def _layout_udim(transform, settings, target, log=default_log):
    """Escala a la densidad objetivo y reparte las islas en los UDIMs necesarios."""
    name = short_name(transform)
    map_size = settings["uv_map_size"]
    margin = 2.0 * settings["uv_padding_px"] / map_size

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
    fn = _mesh_fn(transform)
    _, shell_ids = fn.getUvShellsIds()
    uvs_by_shell, faces_by_shell = {}, {}
    for uv_index, shell in enumerate(shell_ids):
        uvs_by_shell.setdefault(shell, []).append(uv_index)
    for face, shell, _ in _face_shell_areas(transform):
        faces_by_shell.setdefault(shell, []).append(face)
    pad = float(settings["uv_padding_px"]) / map_size
    sizes = {shell: (b[1] - b[0] + pad) * (b[3] - b[2] + pad) for shell, b in enumerate(shell_bounds(transform))}

    fill = float(settings.get("uv_udim_fill", UDIM_FILL))
    groups = []
    for shell in sorted(sizes, key=sizes.get, reverse=True):
        for group in groups:
            if group["used"] + sizes[shell] <= fill:
                group["shells"].append(shell)
                group["used"] += sizes[shell]
                break
        else:
            groups.append({"shells": [shell], "used": sizes[shell]})
    pending = [group["shells"] for group in groups]

    kwargs = _layout_kwargs(settings)
    tile = 0
    while pending:
        shells = pending.pop(0)
        tile_u, tile_v = tile % UDIM_COLUMNS, tile // UDIM_COLUMNS
        faces = [face for shell in shells for face in faces_by_shell.get(shell, [])]
        cmds.u3dLayout(components(transform, "f", faces), preScaleMode=0, layoutScaleMode=1,
                       packBox=(float(tile_u), tile_u + 1.0, float(tile_v), tile_v + 1.0), **kwargs)
        uv_list = components(transform, "map", [i for shell in shells for i in uvs_by_shell[shell]])
        (umin, umax), (vmin, vmax) = cmds.polyEvaluate(uv_list, boundingBoxComponent2d=True)
        inside = (umin >= tile_u - 1e-5 and umax <= tile_u + 1 + 1e-5 and
                  vmin >= tile_v - 1e-5 and vmax <= tile_v + 1 + 1e-5)
        if not inside:
            if len(shells) == 1:
                raise UVError("{}: una isla no cabe en un UDIM. Baja la densidad de texel o el padding.".format(name))
            # no caben todas: la cuarta parte más pequeña pasa a un tile nuevo y se repite este
            # (las grandes son las que definen el tile; las pequeñas rellenan huecos en otro)
            ordered = sorted(shells, key=sizes.get, reverse=True)
            keep = max(1, (len(ordered) * 3) // 4)
            pending.insert(0, ordered[:keep])
            pending.append(ordered[keep:])
            continue
        tile += 1

    report = check(transform, udim=True)
    if report["crossing_tiles"] or report["overlapping_faces"] > baseline_overlaps:
        raise UVError("{}: no se consiguió repartir las islas en UDIMs sin solapes. "
                      "Baja la densidad de texel o el padding.".format(name))
    return tile


def symmetric_seams(transform):
    """Si la malla es simétrica en X, deja las costuras simétricas. Devuelve True si cambió algo."""
    from .uv_seams import symmetrize_seams
    current = set(uv_seam_edges(transform))
    wanted = symmetrize_seams(transform, current)
    if wanted is None or set(wanted) == current:
        return False
    _swap_seams(transform, current, set(wanted))
    return True


def _swap_seams(transform, current, wanted):
    """Cose las costuras que sobran y corta las nuevas, sobre las UVs ya desplegadas (partir de una
    proyección nueva dejaba un punto de partida tan arrugado que el unfold no lo deshacía)."""
    if current - wanted:
        cmds.polyMapSewMove(components(transform, "e", sorted(current - wanted)), constructionHistory=True)
    if wanted - current:
        cmds.polyMapCut(components(transform, "e", sorted(wanted - current)), constructionHistory=True)
    fix_nonmanifold_uvs(transform)


# estiramiento extra que se acepta a cambio de esconder costuras (puntos de % de caras > 1.5x)
HIDDEN_SEAMS_MAX_EXTRA_STRETCH = 4.0
# por encima de este extra se buscan las islas culpables y se les devuelven sus costuras originales
TAILOR_MAX_EXTRA_STRETCH = 1.0
TAILOR_LOCAL_ROUNDS = 2
# una isla es culpable si gana caras estiradas (> 1.5x) en al menos esta fracción de sus caras
TAILOR_SHELL_EXTRA = 0.05


def _revert_stretched(copy, transform, seams, old, before_faces):
    """Costuras con los cambios deshechos en las islas de `copy` que estiran más que antes."""
    after_faces, shells = face_stretch(copy)
    worse = (after_faces > 1.5) & ~(before_faces > 1.5)
    better = (before_faces > 1.5) & ~(after_faces > 1.5)
    culprit = []
    for shell in np.unique(shells[worse]):
        members = shells == shell
        if (worse[members].sum() - better[members].sum()) >= max(2, TAILOR_SHELL_EXTRA * members.sum()):
            culprit.append(shell)
    if not culprit:
        return seams, 0
    faces = np.nonzero(np.isin(shells, culprit))[0]
    edges = set()
    path = dag_path(mesh_shape(transform))
    it = om.MItMeshPolygon(path)
    for f in faces:
        it.setIndex(int(f))
        edges.update(it.getEdges())
    return (seams - edges) | (old & edges), len(culprit)


def _tailor_seams(transform, unfold_and_split, known_overlaps, log, symmetric=False, hidden=False):
    """Costuras "de sastre": recoloca las de Unfold3D (uv_seams) y vuelve a desplegar.

    - Los cortes que abren un tubo (torso, brazos) se llevan a la espalda o bajo los brazos.
    - Los tramos se enderezan y se llevan a zonas poco visibles.
    - Las islas diminutas se cosen a su vecina.
    - Solo con 'Costuras escondidas': se quitan tramos visibles que unen islas pequeñas (menos islas,
      algo menos de resolución).
    Se prueba sobre una copia: si estira bastante más que con las costuras de Unfold3D (con el águila
    de prueba pasaba al mover los cortes de las plumas), la malla se queda como estaba. Antes se
    volvía atrás cosiendo y desplegando otra vez, y el resultado salía peor que el original.
    """
    from .uv_seams import fix_charts, optimize_seams
    name = short_name(transform)
    before = stretch(transform)
    old = set(uv_seam_edges(transform))
    # tras partir las islas grandes puede quedar alguna que no es un disco (Unfold3D la despliega
    # igual) y optimize_seams no tocaba nada: en el humano completo, 0 cambios. Se parte de las
    # costuras con los cortes que faltan
    completed = fix_charts(transform, old)
    start = set(completed[0]) if completed else old
    seams, info = optimize_seams(transform, start, remove_exposed=True if hidden else "light")
    if not (info["moved"] or info["merged"] or info["rerouted"]):
        return known_overlaps
    if symmetric:
        from .uv_seams import symmetrize_seams
        seams = symmetrize_seams(transform, seams) or seams
    seams = set(seams)
    before_faces, _ = face_stretch(transform)

    def attempt(wanted):
        copy = cmds.ls(cmds.duplicate(transform, name="mblSeamTry")[0], long=True)[0]
        _swap_seams(copy, old, wanted)
        return copy, unfold_and_split(copy), stretch(copy)

    copy, overlaps, after = attempt(seams)
    try:
        # tolerancia por isla: si algunas islas estiran más que con las costuras de Unfold3D, se
        # deshacen solo los cambios de costura de esas islas y se vuelve a desplegar
        for _ in range(TAILOR_LOCAL_ROUNDS):
            if after["over_1_5_percent"] <= before["over_1_5_percent"] + TAILOR_MAX_EXTRA_STRETCH:
                break
            seams, reverted = _revert_stretched(copy, transform, seams, old, before_faces)
            if not reverted:
                break
            cmds.delete(copy)
            copy, overlaps, after = attempt(seams)
            log("{}: {} isla(s) estiraban con las costuras nuevas; se les devuelven las de Unfold3D ({:.1f}% "
                "de caras > 1.5x).".format(name, reverted, after["over_1_5_percent"]))
        if after["over_1_5_percent"] > before["over_1_5_percent"] + HIDDEN_SEAMS_MAX_EXTRA_STRETCH:
            log("{}: recolocar las costuras estiraba demasiado ({:.1f}% de caras > 1.5x frente a {:.1f}%); se "
                "dejan las de Unfold3D.".format(name, after["over_1_5_percent"], before["over_1_5_percent"]),
                "warning")
            return known_overlaps
        # ni un solape más que con las costuras de Unfold3D (malla densa de prueba: 0 -> 2)
        base = known_overlaps if known_overlaps is not None else len(overlapping_faces(transform))
        overlaps = overlaps if overlaps is not None else len(overlapping_faces(copy))
        if overlaps > base:
            log("{}: con las costuras recolocadas quedaban {} caras solapadas (frente a {}); se dejan las de "
                "Unfold3D.".format(name, overlaps, base), "warning")
            return known_overlaps
        cmds.polyTransfer(transform, uvSets=True, vertices=False, vertexColor=False, alternateObject=copy)
    finally:
        cmds.delete(copy)
    log("{}: costuras recolocadas: {} cortes de tubo llevados atrás o bajo los brazos, {} tramos "
        "enderezados o escondidos, {} uniones de islas; visibilidad de las costuras {:.0f}% menor.".format(
            name, info["rerouted"], info["moved"], info["merged"],
            100.0 * (1.0 - info["cost_after"] / max(info["cost_before"], 1e-9))))
    return overlaps


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
    try:
        return _unwrap(transform, settings, log, tick)
    except RuntimeError as exc:
        raise UVError("{}: Unfold3D falló: {}".format(name, str(exc).strip()))


def _unwrap(transform, settings, log, tick):
    name = short_name(transform)
    map_size = int(settings["uv_map_size"])
    pad = int(settings["uv_padding_px"])

    existing = uv_seam_edges(transform) if has_uvs(transform) else []
    automatic = not (settings["uv_respect_existing"] and existing)
    if not automatic:
        log("{}: se respetan las {} costuras que ya tiene la malla.".format(name, len(existing)))
    else:
        hard = hard_angle_edges(transform, settings["uv_seam_angle"]) if settings["uv_seam_angle"] < 180 else []
        seed_uvs(transform, hard)
        log("{}: {} costuras por ángulo (>= {:g}°).".format(name, len(hard), settings["uv_seam_angle"]))
    # al coser las UVs de partida (o en UVs que ya traía la malla) pueden quedar islas tocándose en un punto
    fixed = fix_nonmanifold_uvs(transform)
    if fixed:
        log("{}: {} puntos UV no-manifold separados.".format(name, fixed))
    if automatic and settings["uv_auto_seams"]:
        auto_seams(transform, settings["uv_split_shells"], settings["uv_cut_pipes"])
        log("{}: costuras automáticas de Unfold3D añadidas donde hacían falta para desplegar.".format(name))
        # Unfold3D a veces deja islas que no se pueden aplanar (un toro abierto por un solo tajo)
        from .uv_seams import fix_charts
        current = set(uv_seam_edges(transform))
        fixed = fix_charts(transform, current)
        if fixed:
            _swap_seams(transform, current, set(fixed[0]))
            log("{}: {} aristas de corte añadidas para que todas las islas se puedan aplanar.".format(
                name, fixed[1]))
        if settings.get("uv_symmetric", True) and symmetric_seams(transform):
            log("{}: costuras simétricas: cada isla de un lado tiene su gemela en el otro.".format(name))
    symmetric = automatic and settings["uv_auto_seams"] and settings.get("uv_symmetric", True)
    hidden =automatic and settings["uv_auto_seams"] and settings.get("uv_seam_style") == "hidden"
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

    def unfold_and_split(mesh=transform):
        """Despliega todo y separa las zonas que se pisan. Devuelve los solapes medidos (o None)."""
        unfold(mesh)
        # Unfold3D a veces deja cortes que no abren del todo la malla y la isla se pisa a sí misma:
        # se separa esa zona en una isla nueva y se despliega solo ella (volver a desplegar toda la
        # malla movía el problema a otra parte: con el águila de prueba los solapes pasaban de 23 a 134)
        for attempt in range(3):
            if not automatic:
                return None
            bad = overlapping_faces(mesh)
            region = split_overlaps(mesh, bad) if bad else None
            if not region:
                return len(bad)
            log("{}: {} caras solapadas, se separan en una isla nueva (intento {}).".format(
                name, len(bad), attempt + 1))
            unfold(mesh, region)
        return None

    # solapes ya medidos sin cambios posteriores (evita repetir polyUVOverlap)
    known_overlaps = unfold_and_split()
    big_fraction = float(settings.get("uv_split_big_shells", 0.0))
    if automatic and settings["uv_auto_seams"] and not hidden and big_fraction > 0:
        split = split_big_shells(transform, big_fraction)
        if split:
            log("{}: {} isla(s) muy grandes partidas para aprovechar mejor la textura.".format(name, split))
            fixed = fix_nonmanifold_uvs(transform)
            if symmetric:
                symmetric_seams(transform)
            known_overlaps = unfold_and_split()
    if automatic and settings["uv_auto_seams"] and settings.get("uv_tailor_seams", True):
        known_overlaps = _tailor_seams(transform, unfold_and_split, known_overlaps, log, symmetric, hidden)

    if settings.get("uv_straight_islands", True):
        turned = orient_upright(transform)
        if turned:
            log("{}: {} isla(s) puestas de pie (el arriba del modelo, arriba en la textura).".format(name, turned))
    if settings["uv_udim"]:
        tiles = layout_udim(transform, settings, log)
        log("{}: layout en {} UDIM(s).".format(name, tiles))
        known_overlaps = None
    elif settings.get("uv_pack_iterations", 1) > 1 or settings.get("uv_pack_tries", 1) > 1:
        best_layout(transform, settings)
        known_overlaps = None
        if tick:
            tick()

    # borra el historial de UVs pero conserva deformadores (skin, blendshapes) si los hubiera
    cmds.bakePartialHistory(transform, prePostDeformers=True)
    report = check(transform, udim=settings["uv_udim"], known_overlaps=known_overlaps)
    report["stretch"] = stretch(transform)
    density = texel_density(transform, map_size)
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
