# -*- coding: utf-8 -*-
"""Pruebas de MBL Retopo UV en Maya sin interfaz (mayapy).

Windows:
    "C:\\Program Files\\Autodesk\\Maya2026\\bin\\mayapy.exe" tests\\run_tests.py
macOS:
    /Applications/Autodesk/maya2026/Maya.app/Contents/bin/mayapy tests/run_tests.py

Variables opcionales:
    MBL_INSTANT_MESHES=<ruta>  usa el Instant Meshes real en vez del sustituto de pruebas
    MBL_TEST_FBX=<ruta.fbx>    además procesa ese FBX completo (limpieza + retopo + UVs)
"""

import math
import os
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import maya.standalone  # noqa: E402

maya.standalone.initialize(name="python")

import maya.api.OpenMaya as om  # noqa: E402
import maya.cmds as cmds  # noqa: E402
import numpy as np  # noqa: E402

from mbl_retopo_uv import cleanup, config, diagnostics, orient, pipeline, retopo, status, uv  # noqa: E402
from mbl_retopo_uv.common import RetopoError  # noqa: E402

RESULTS = []


def test(func):
    """Registra la prueba y la ejecuta en una escena nueva."""
    def wrapper():
        cmds.file(new=True, force=True)
        start = time.time()
        try:
            func()
        except Exception:
            RESULTS.append((func.__name__, False, traceback.format_exc()))
            print("FALLO  {}\n{}".format(func.__name__, traceback.format_exc()))
        else:
            RESULTS.append((func.__name__, True, ""))
            print("OK     {} ({:.1f} s)".format(func.__name__, time.time() - start))
    wrapper.__name__ = func.__name__
    TESTS.append(wrapper)
    return wrapper


TESTS = []


def quiet_log(message, level="info"):
    print("    [{}] {}".format(level, message))


def fake_instant_meshes():
    os.environ["MBL_FAKE_PYTHON"] = sys.executable
    folder = os.path.join(HERE, "fake_instant_meshes")
    if sys.platform.startswith("win"):
        return os.path.join(folder, "Instant Meshes.cmd")
    path = os.path.join(folder, "instant-meshes.sh")
    os.chmod(path, 0o755)
    return path


def base_settings(**overrides):
    settings = dict(config.DEFAULTS)
    settings["instant_meshes_path"] = os.environ.get("MBL_INSTANT_MESHES") or fake_instant_meshes()
    settings["retopo_timeout"] = 180
    # las pruebas del circuito de Instant Meshes usan el motor por línea de comandos (o el sustituto)
    settings["retopo_engine"] = "instant_meshes"
    settings["uv_map_size"] = 2048
    settings["uv_padding_px"] = 8
    settings["uv_layout_quality"] = 256
    settings["retopo_target_faces"] = 2000
    # las primitivas de Maya traen UVs de serie: no son costuras puestas por el usuario
    settings["uv_respect_existing"] = False
    settings.update(overrides)
    return settings


def make_dense_mesh():
    """Esfera de 40.000 caras deformada con ondas: algo parecido a un escaneo orgánico."""
    mesh = cmds.polySphere(name="denseOrganic", radius=5, subdivisionsX=200, subdivisionsY=200,
                           constructionHistory=False)[0]
    fn = om.MFnMesh(om.MSelectionList().add(mesh).getDagPath(0))
    points = fn.getPoints()
    for i, p in enumerate(points):
        bump = 1.0 + 0.08 * math.sin(p.x * 2.1) * math.cos(p.y * 1.7) + 0.05 * math.sin(p.z * 3.3)
        points[i] = om.MPoint(p.x * bump, p.y * bump * 1.3, p.z * bump)
    fn.setPoints(points)
    return cmds.ls(mesh, long=True)[0]


def make_test_meshes():
    meshes = [
        cmds.polyCube(name="testCube", width=2, height=3, depth=1, constructionHistory=False)[0],
        cmds.polySphere(name="testSphere", radius=1.5, constructionHistory=False)[0],
        cmds.polyTorus(name="testTorus", radius=2, sectionRadius=0.6, constructionHistory=False)[0],
    ]
    cmds.move(5, 0, 0, meshes[1])
    cmds.move(-5, 0, 0, meshes[2])
    cmds.rotate(30, 10, 0, meshes[2])
    return cmds.ls(meshes, long=True) + [make_dense_mesh()]


def assert_good_uvs(mesh, udim=False):
    report = uv.check(mesh, udim=udim)
    name = mesh.split("|")[-1]
    assert report["overlapping_faces"] == 0, "{}: {} caras solapadas".format(name, report["overlapping_faces"])
    assert report["flipped_faces"] == 0, "{}: {} caras en espejo".format(name, report["flipped_faces"])
    if udim:
        assert not report["negative"], "{}: UVs negativas".format(name)
        assert not report["crossing_tiles"], "{}: islas cruzando tiles".format(name)
    else:
        assert not report["outside_0_1"], "{}: UVs fuera de 0-1 {}".format(name, report["bounds"])
    faces = cmds.polyEvaluate(mesh, face=True)
    mapped = len(cmds.ls(cmds.polyListComponentConversion(mesh + ".map[*]", toFace=True), flatten=True))
    assert mapped == faces, "{}: {} de {} caras sin UVs".format(name, faces - mapped, faces)
    return report


# ---------------------------------------------------------------- pruebas sin geometría

@test
def test_config_roundtrip():
    path = os.path.join(tempfile.mkdtemp(), "settings.json")
    settings = dict(config.DEFAULTS, retopo_target_faces=1234, uv_udim=True)
    config.save(settings, path)
    loaded = config.load(path)
    assert loaded["retopo_target_faces"] == 1234 and loaded["uv_udim"] is True
    with open(path, "w") as handle:
        handle.write('{"retopo_target_faces": "mal", "uv_udim": 1}')
    loaded = config.load(path)
    assert loaded["retopo_target_faces"] == config.DEFAULTS["retopo_target_faces"]
    assert loaded["uv_udim"] is config.DEFAULTS["uv_udim"]
    # ajustes de una versión anterior: las claves cuyo valor por defecto mejoró vuelven al nuevo
    with open(path, "w") as handle:
        handle.write('{"uv_split_shells": 0.0, "uv_optimize_iterations": 10, "retopo_target_faces": 777}')
    loaded = config.load(path)
    assert loaded["uv_split_shells"] == config.DEFAULTS["uv_split_shells"], loaded["uv_split_shells"]
    assert loaded["uv_optimize_iterations"] == config.DEFAULTS["uv_optimize_iterations"]
    assert loaded["retopo_target_faces"] == 777
    config.save(dict(config.DEFAULTS, uv_split_shells=0.2), path)
    assert config.load(path)["uv_split_shells"] == 0.2


@test
def test_config_removed_engine():
    """Ajustes guardados con un motor que ya no existe (QuadWild) pasan al motor por defecto (Maya, tras pasar por
    Automático, que era el defecto cuando se quitó); las demás elecciones se respetan."""
    path = os.path.join(tempfile.mkdtemp(), "settings.json")

    def load_with(engine, version=None):
        data = '"retopo_engine": "{}", "retopo_target_faces": 777'.format(engine)
        if version is not None:
            data = '"_version": {}, '.format(version) + data
        with open(path, "w") as handle:
            handle.write("{" + data + "}")
        return config.load(path)

    assert config.SETTINGS_VERSION >= 7
    assert set(retopo.ENGINES) == {"auto", "maya", "instant_meshes", "mbl"}, retopo.ENGINES
    assert config.DEFAULTS["retopo_engine"] in retopo.ENGINES
    # cualquier versión anterior a la que lo quitó (sin versión = 1), también las que ya conservaban el motor elegido (3 a 6)
    for version in (None, 3, 4, 5, 6):
        loaded = load_with("quadwild", version)
        assert loaded["retopo_engine"] == config.DEFAULTS["retopo_engine"] == "maya", (version, loaded["retopo_engine"])
        assert loaded["retopo_target_faces"] == 777, version
    # los motores que siguen existiendo se quedan como los eligió el usuario (Automático, desde la versión que cambió el defecto)
    for engine in ("maya", "mbl", "instant_meshes"):
        assert load_with(engine, 6)["retopo_engine"] == engine, engine
    assert load_with("auto", config.SETTINGS_VERSION)["retopo_engine"] == "auto"
    # tras guardar con la versión nueva el JSON ya no tiene el motor viejo y se lee igual
    config.save(load_with("quadwild", 6), path)
    with open(path, "r", encoding="utf-8") as handle:
        assert '"quadwild"' not in handle.read()
    assert config.load(path)["retopo_engine"] == "maya"


@test
def test_config_default_engine_migration():
    """El motor por defecto pasó de Automático a Maya (el banco: Maya rinde mejor que el Motor MBL): quien tenía el valor
    de antes pasa a Maya; quien eligió otro motor a mano conserva el suyo; a partir de la versión nueva Automático se respeta."""
    path = os.path.join(tempfile.mkdtemp(), "settings.json")

    def load_with(version, engine=None):
        data = '"_version": {}, "retopo_target_faces": 777'.format(version) if version is not None else '"retopo_target_faces": 777'
        if engine is not None:
            data += ', "retopo_engine": "{}"'.format(engine)
        with open(path, "w") as handle:
            handle.write("{" + data + "}")
        return config.load(path)

    assert config.DEFAULTS["retopo_engine"] == "maya" and config.SETTINGS_VERSION >= 8
    assert config.REPLACED_VALUES[8]["retopo_engine"] == {"auto": "maya"}
    # sin archivo, o con un archivo sin el motor: el defecto nuevo
    assert config.load(os.path.join(os.path.dirname(path), "no_existe.json"))["retopo_engine"] == "maya"
    assert load_with(7)["retopo_engine"] == "maya"
    # el defecto de antes (Automático), en cualquier versión anterior: Maya, y el resto de los ajustes se queda
    for version in (None, 3, 5, 6, 7):
        loaded = load_with(version, "auto")
        assert loaded["retopo_engine"] == "maya", (version, loaded["retopo_engine"])
        assert loaded["retopo_target_faces"] == 777, version
    # quien eligió otro motor a mano lo conserva
    for version in (6, 7):
        for engine in ("maya", "mbl", "instant_meshes"):
            assert load_with(version, engine)["retopo_engine"] == engine, (version, engine)
    # con la versión nueva, Automático es una elección y se respeta; al guardar y cargar la migración no se repite
    assert load_with(config.SETTINGS_VERSION, "auto")["retopo_engine"] == "auto"
    config.save(dict(config.DEFAULTS, retopo_engine="auto"), path)
    assert config.load(path)["retopo_engine"] == "auto"
    config.save(load_with(7, "auto"), path)
    with open(path, "r", encoding="utf-8") as handle:
        assert '"retopo_engine": "maya"' in handle.read()
    assert config.load(path)["retopo_engine"] == "maya"


@test
def test_resolve_executable():
    folder = tempfile.mkdtemp()
    exe = os.path.join(folder, "Instant Meshes.exe")
    open(exe, "w").close()
    assert config.resolve_executable(folder, "win32") == exe
    assert config.resolve_executable('"{}"'.format(exe), "win32") == exe
    assert config.resolve_executable(os.path.join(folder, "nada.exe")) is None
    # bundle de macOS
    app = os.path.join(folder, "Instant Meshes.app")
    os.makedirs(os.path.join(app, "Contents", "MacOS"))
    binary = os.path.join(app, "Contents", "MacOS", "Instant Meshes")
    open(binary, "w").close()
    assert config.resolve_executable(app, "darwin") == binary


@test
def test_build_command():
    settings = dict(config.DEFAULTS, retopo_target_faces=3000, retopo_crease_angle=35, retopo_smooth=3,
                    retopo_pure_quads=True, retopo_deterministic=True)
    command = retopo.build_command("im", "in.obj", "out.obj", settings)
    assert command[:4] == ["im", "in.obj", "-o", "out.obj"]
    joined = " ".join(command)
    # en modo quads puros se pide menos porque Instant Meshes subdivide al final
    for part in ("-f 952", "-r 4", "-p 4", "-S 3", "-c 35", "-d"):
        assert part in joined, part
    assert "-D" not in command and "-b" not in command
    settings.update(retopo_pure_quads=False, retopo_deterministic=False, retopo_crease_angle=0,
                    _align_boundaries_now=True)
    command = retopo.build_command("im", "in.obj", "out.obj", settings)
    assert "-D" in command and "-b" in command and "-d" not in command and "-c" not in command
    assert "-f 3000" in " ".join(command)
    # densidad adaptativa: solo si el motor la admite
    settings.update(retopo_adaptive=0.4, retopo_adaptive_ratio=3.0)
    assert "--adaptive" not in retopo.build_command("im", "a", "b", settings)
    settings["_adaptive_supported"] = True
    joined = " ".join(retopo.build_command("im", "a", "b", settings))
    assert "--adaptive 0.4" in joined and "--adaptive-ratio 3" in joined, joined


@test
def test_engine_detection():
    bin_dir = os.path.join(ROOT, "mbl_retopo_uv", "bin")
    mbl = os.path.join(bin_dir, "Instant Meshes.exe")
    official = os.path.join(bin_dir, "Instant Meshes (oficial).exe")
    if not os.path.isfile(mbl):
        print("    sin Instant Meshes en mbl_retopo_uv/bin: se omite")
        return
    assert retopo.engine_supports_adaptive(mbl), "la versión MBL debería admitir --adaptive"
    if os.path.isfile(official):
        assert not retopo.engine_supports_adaptive(official)
    assert not retopo.engine_supports_adaptive(fake_instant_meshes())


@test
def test_repair_topology():
    """Defectos típicos de Instant Meshes, reparados sin Maya sobre la lista de caras."""
    # rejilla 3x3 de quads (vértices 0..15), con defectos añadidos
    points = [(x, y, 0.0) for y in range(4) for x in range(4)]
    grid = [[y * 4 + x, y * 4 + x + 1, (y + 1) * 4 + x + 1, (y + 1) * 4 + x] for y in range(3) for x in range(3)]
    faces = [list(f) for f in grid]
    faces[4] = faces[4][::-1]                       # cara invertida respecto a sus vecinas
    faces.append(list(grid[0]))                     # cara duplicada
    faces.append([0, 1, 1, 5])                      # cara degenerada
    # vértice pajarita: un triángulo suelto que solo toca la rejilla en el vértice 15
    points += [(4.0, 4.0, 0.0), (4.0, 3.0, 0.0)]
    faces.append([15, 17, 16])
    fixed_points, fixed, report = retopo.repair_topology(points, faces, min_piece_ratio=0.0, max_hole_edges=0)
    assert report["bowties"] == 1, report
    edge_faces = retopo._edge_faces(fixed)
    assert all(len(owners) <= 2 for owners in edge_faces.values())
    directed = [e for f in fixed for e in retopo._edges(f)]
    assert len(directed) == len(set(directed)), "orientación incoherente"
    assert len(fixed) == 10, fixed

    # vértice de 2 aristas entre dos quads (dos caras que comparten dos aristas)
    points = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (0, 1, 0), (1, 0.5, 0)]
    faces = [[0, 1, 6, 4, 5], [1, 2, 3, 4, 6]]
    _, fixed, report = retopo.repair_topology(points, faces, 0.0, 0)
    assert report["doublets"] == 1 and all(6 not in f for f in fixed), (report, fixed)

    # agujero de 4 aristas en una rejilla cerrada en anillo: se rellena con triángulos
    points = [(x, y, 0.0) for y in range(4) for x in range(4)]
    faces = [list(f) for i, f in enumerate(grid) if i != 4]
    _, fixed, report = retopo.repair_topology(points, faces, 0.0, 4)
    assert report["holes"] == 1 and len(fixed) == 8 + 4, report


# ---------------------------------------------------------------- limpieza

@test
def test_cleanup_and_detection():
    cube = cmds.polyCube(name="dirty", constructionHistory=False)[0]
    cmds.move(1, 2, 3, cube)
    cmds.scale(2, 2, 2, cube)
    # vértices duplicados: separar una cara y volver a fusionar
    cmds.polyChipOff(cube + ".f[0]", duplicate=False, keepFacesTogether=True)
    before = cmds.polyEvaluate(cube, vertex=True)
    report = cleanup.cleanup(cube, dict(config.DEFAULTS), quiet_log)
    assert cmds.polyEvaluate(cube, vertex=True) < before, "no se fusionaron vértices"
    assert cmds.getAttr(cube + ".translate")[0] == (0.0, 0.0, 0.0), "no se congelaron transformaciones"
    assert not cmds.listHistory(cube, pruneDagObjects=True), "queda historial"
    assert not any(cleanup.count_problems(report).values()), report

    # malla con problemas: 3 caras compartiendo arista (no-manifold) y una cara de área cero
    points = [om.MPoint(*p) for p in [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, -1, 0),
                                      (1, -1, 0), (0, 0, 1), (1, 0, 1),
                                      (3, 0, 0), (3, 0, 0), (3, 0, 0)]]
    counts = [4, 4, 4, 3]
    connects = [0, 1, 3, 2, 0, 4, 5, 1, 0, 1, 7, 6, 8, 9, 10]
    fn = om.MFnMesh()
    fn.create(points, counts, connects)
    bad = cmds.ls(cmds.listRelatives(fn.fullPathName(), parent=True, fullPath=True), long=True)[0]
    counts = cleanup.count_problems(cleanup.analyze(bad))
    assert counts["nonmanifold_edges"] >= 1, counts
    assert counts["zero_area_faces"] == 1, counts

    # las UVs se niegan con un mensaje claro y sin tocar la malla
    uvs_before = cmds.polyEvaluate(bad, uvcoord=True)
    outcome = pipeline.run([bad], ["uv"], base_settings(), quiet_log)
    assert outcome["errors"] and "no-manifold" in outcome["errors"][0][1], outcome
    assert cmds.polyEvaluate(bad, uvcoord=True) == uvs_before
    # la limpieza lo repara y después las UVs funcionan
    cleanup.cleanup(bad, dict(config.DEFAULTS, cleanup_merge=False), quiet_log)
    assert cleanup.nonmanifold_counts(bad) == (0, 0), cleanup.nonmanifold_counts(bad)
    outcome = pipeline.run([bad], ["uv"], base_settings(), quiet_log)
    assert not outcome["errors"], outcome["errors"]


@test
def test_timers_and_cheap_cleanup():
    """Duraciones legibles, hora en el registro, cronómetro por paso y limpieza que no repite lo que no cambia nada."""
    from mbl_retopo_uv import common
    assert common.format_duration(35) == "35 s"
    assert common.format_duration(252) == "4 min 12 s"
    assert common.format_duration(420.4) == "7 min 00 s"
    assert common.format_duration(3780) == "1 h 03 min"
    now = common.stamp()
    assert len(now) == 8 and now[2] == ":" and now[5] == ":", now
    cube = cmds.polyCube(name="tidy", constructionHistory=False)[0]
    assert cleanup.is_identity(cube) and not cleanup.has_history(cube)
    cmds.move(1, 0, 0, cube)
    assert not cleanup.is_identity(cube)
    cmds.move(0, 0, 0, cube)
    messages = []
    outcome = pipeline.run([cube], ["cleanup"], base_settings(), lambda m, level="info": messages.append(m))
    assert not outcome["errors"], outcome["errors"]
    assert any("sin historial" in m for m in messages) and any("ya congeladas" in m for m in messages), messages
    assert any("Limpieza empieza" in m for m in messages) and any("ha tardado" in m and "limpieza" in m for m in messages), messages
    timing = outcome["timings"][outcome["results"][0]]
    assert timing["total"] >= timing["cleanup"] >= 0.0, timing
    # con historial y transformaciones por congelar sí se hacen
    dirty = cmds.polyCube(name="dirty2")[0]
    cmds.move(1, 2, 3, dirty)
    assert cleanup.has_history(dirty)
    cleanup.cleanup(dirty, dict(config.DEFAULTS), quiet_log)
    assert cmds.getAttr(dirty + ".translate")[0] == (0.0, 0.0, 0.0) and not cleanup.has_history(dirty)


# ---------------------------------------------------------------- UVs

@test
def test_hard_edges_match_polySelectConstraint():
    """Nuestro cálculo por ángulo coincide con polySelectConstraint (el método del prototipo)."""
    for mesh, angle in [(cmds.polyCube(constructionHistory=False)[0], 80),
                        (cmds.polyTorus(subdivisionsX=12, subdivisionsY=6, constructionHistory=False)[0], 40)]:
        ours = set(uv.hard_angle_edges(mesh, angle))
        cmds.select(mesh + ".e[*]")
        cmds.polySelectConstraint(mode=2, type=0x8000, angle=1, anglebound=(angle, 180))
        theirs = set(int(e.rsplit("[", 1)[1][:-1]) for e in cmds.ls(selection=True, flatten=True))
        cmds.polySelectConstraint(disable=True)
        cmds.polySelectConstraint(angle=0, mode=0)
        cmds.select(clear=True)
        assert ours == theirs, (mesh, len(ours), len(theirs))


@test
def test_respect_existing_seams():
    cube = cmds.ls(cmds.polyCube(constructionHistory=False)[0], long=True)[0]
    uv.create_base_uvs(cube)
    cmds.polyMapCut(cube + ".e[0:7]")
    before = set(uv.uv_seam_edges(cube))
    assert before, "no se crearon costuras de prueba"
    uv.unwrap(cube, base_settings(uv_respect_existing=True), quiet_log)
    assert set(uv.uv_seam_edges(cube)) == before, "se añadieron o quitaron costuras"
    assert_good_uvs(cube)


@test
def test_uv_only_all_meshes():
    meshes = make_test_meshes()
    outcome = pipeline.run(meshes, ["uv"], base_settings(), quiet_log)
    assert not outcome["errors"], outcome["errors"]
    for mesh in outcome["results"]:
        assert_good_uvs(mesh)


@test
def test_uv_udim():
    # una malla con 8 piezas sueltas (8 islas) que a esta densidad no caben en un solo tile
    pieces = []
    for i in range(8):
        piece = cmds.polySphere(radius=1.0, subdivisionsX=24, subdivisionsY=16, constructionHistory=False)[0]
        cmds.move(i * 3.0, 0, 0, piece)
        pieces.append(piece)
    mesh = cmds.ls(cmds.polyUnite(pieces, constructionHistory=False, name="udimPieces")[0], long=True)[0]
    settings = base_settings(uv_udim=True, uv_udim_tiles=0, uv_texel_density=400.0)
    outcome = pipeline.run([mesh], ["uv"], settings, quiet_log)
    assert not outcome["errors"], outcome["errors"]
    report = assert_good_uvs(mesh, udim=True)
    assert report["bounds"][0][1] > 1.0, "con esta densidad debería ocupar más de un UDIM"
    density = uv.texel_density(mesh, settings["uv_map_size"])
    assert abs(density - 400.0) / 400.0 < 0.05, density

    # por nº de tiles: el plugin elige la densidad para llenar 3 UDIMs sin pasarse
    settings = base_settings(uv_udim=True, uv_udim_tiles=3)
    outcome = pipeline.run([mesh], ["uv"], settings, quiet_log)
    assert not outcome["errors"], outcome["errors"]
    report = assert_good_uvs(mesh, udim=True)
    (umin, umax), (vmin, vmax) = report["bounds"]
    assert 1.0 < umax <= 3.0 + 1e-4 and vmax <= 1.0 + 1e-4, report["bounds"]
    assert uv.texel_density(mesh, settings["uv_map_size"]) > density * 0.5

    # una sola isla enorme: se baja la densidad para que quepa en un tile, con aviso y sin error
    dense = make_dense_mesh()
    outcome = pipeline.run([dense], ["uv"], base_settings(uv_udim=True, uv_udim_tiles=0, uv_texel_density=1000.0),
                           quiet_log)
    assert not outcome["errors"], outcome["errors"]
    assert_good_uvs(dense, udim=True)


@test
def test_hidden_seams():
    """Modo 'costuras escondidas': UVs válidas y costuras más baratas (menos visibles) que las de Unfold3D."""
    from mbl_retopo_uv import uv_seams
    mesh = cmds.ls(cmds.polyTorus(radius=3, sectionRadius=1, subdivisionsX=40, subdivisionsY=20,
                                  constructionHistory=False)[0], long=True)[0]
    outcome = pipeline.run([mesh], ["uv"], base_settings(uv_seam_style="hidden"), quiet_log)
    assert not outcome["errors"], outcome["errors"]
    assert_good_uvs(mesh)
    assert not cmds.ls("mblSeamTry*"), "quedó la copia de prueba en la escena"
    # las islas que deja optimize_seams se pueden aplanar (discos)
    graph = uv_seams.SeamGraph(mesh)
    seams = set(uv.uv_seam_edges(mesh))
    assert graph.all_disks(seams)[0]
    new, info = uv_seams.optimize_seams(mesh, seams)
    assert graph.all_disks(set(new))[0] and info["cost_after"] <= info["cost_before"] + 1e-9, info


def _reference_charts(graph, seams):
    """charts() de siempre (union-find en Python), para comparar con la versión de numpy."""
    parent = list(range(graph.face_count))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for e, faces in enumerate(graph.edge_f):
        if len(faces) == 2 and e not in seams:
            a, b = find(faces[0]), find(faces[1])
            if a != b:
                parent[a] = b
    roots = {}
    return [roots.setdefault(find(f), len(roots)) for f in range(graph.face_count)], len(roots)


def _reference_euler(graph, seams):
    """all_disks() de siempre (recorrido por caras, aristas y vértices): característica de Euler de cada isla."""
    chart, count = _reference_charts(graph, seams)
    euler = [0] * count
    seam_vertices = {v for e in seams for v in graph.edge_v[e]}
    for loop in graph.loops:
        if loop & seam_vertices:
            continue
        edge = next(e for e in graph.vertex_edges[next(iter(loop))] if e in graph.border)
        euler[chart[graph.edge_f[edge][0]]] += 1
    for f in range(graph.face_count):
        euler[chart[f]] += 1
    for e, faces in enumerate(graph.edge_f):
        if len(faces) == 2 and e not in seams:
            euler[chart[faces[0]]] -= 1
        else:
            for f in faces:
                euler[chart[f]] -= 1
    for v, edges in enumerate(graph.vertex_edges):
        faces = set()
        for e in edges:
            faces.update(graph.edge_f[e])
        parent = {f: f for f in faces}

        def find(x):
            while parent[x] != x:
                x = parent[x]
            return x
        for e in edges:
            ef = graph.edge_f[e]
            if len(ef) == 2 and e not in seams:
                a, b = find(ef[0]), find(ef[1])
                if a != b:
                    parent[a] = b
        for root in {find(f) for f in faces}:
            euler[chart[root]] += 1
    return euler, count


def _reference_paths(graph, start, blocked_vertices, blocked_edges):
    """_paths() de siempre (Dijkstra completo, sin parada anticipada)."""
    import heapq
    starts = set(start) if isinstance(start, (set, frozenset, list, tuple)) else {start}
    best = {(v, -1): 0.0 for v in starts}
    prev, dist, arrival = {}, {v: 0.0 for v in starts}, {v: (v, -1) for v in starts}
    heap = [(0.0, v, -1) for v in starts]
    heapq.heapify(heap)
    while heap:
        d, v, came = heapq.heappop(heap)
        if d > best.get((v, came), math.inf):
            continue
        if v in blocked_vertices and v not in starts:
            continue
        came_faces = set(graph.edge_f[came]) if came >= 0 else ()
        for e in graph.vertex_edges[v]:
            if e in blocked_edges or e == came:
                continue
            a, b = graph.edge_v[e]
            w = b if a == v else a
            step = graph.cost[e]
            if came_faces and came_faces.intersection(graph.edge_f[e]):
                step += graph.TURN_PENALTY * graph.cost[e]
            nd = d + step
            if nd < best.get((w, e), math.inf):
                best[(w, e)] = nd
                prev[(w, e)] = (v, came)
                heapq.heappush(heap, (nd, w, e))
                if nd < dist.get(w, math.inf):
                    dist[w] = nd
                    arrival[w] = (w, e)
    return dist, (prev, arrival)


@test
def test_uv_seam_graph_matches_reference():
    """charts, all_disks y _paths (con y sin parada anticipada) dan lo mismo que los recorridos en Python de siempre,
    en un cubo, un toro y una esfera con agujero."""
    import random
    from mbl_retopo_uv import uv_seams
    rng = random.Random(11)
    sphere = cmds.polySphere(subdivisionsX=12, subdivisionsY=8, constructionHistory=False)[0]
    cmds.delete(sphere + ".f[20:22]")
    meshes = [cmds.polyCube(constructionHistory=False)[0],
              cmds.polyTorus(radius=3, sectionRadius=1, subdivisionsX=16, subdivisionsY=8, constructionHistory=False)[0],
              sphere]
    for mesh in meshes:
        graph = uv_seams.SeamGraph(mesh)
        edges = list(range(len(graph.edge_v)))
        for _ in range(30):
            seams = set(rng.sample(edges, rng.randint(0, max(1, len(edges) // 3))))
            chart, count = graph.charts(seams)
            ref_chart, ref_count = _reference_charts(graph, seams)
            assert count == ref_count and list(chart) == ref_chart, (mesh, "charts")
            ok, n = graph.all_disks(seams)
            euler, ref_n = _reference_euler(graph, seams)
            assert n == ref_n and list(graph._euler) == euler, (mesh, "euler", list(graph._euler), euler)
            assert ok == (sum(1 for x in euler if x != 1) <= graph.tolerated)
        vertices = range(len(graph.points))
        for _ in range(20):
            picked = rng.sample(vertices, min(6, len(vertices)))
            sources, targets = set(picked[:2]), set(picked[2:])
            blocked = set(rng.sample(vertices, 3)) - sources
            blocked_edges = set(rng.sample(edges, len(edges) // 6))
            ref_dist, ref_trail = _reference_paths(graph, sources, blocked, blocked_edges)
            dist, trail = graph._paths(sources, blocked, blocked_edges)
            assert dist == ref_dist, (mesh, "dijkstra")
            allowed = [e not in blocked_edges for e in edges]
            dist_stop, trail_stop = graph._paths(sources, blocked, (), allowed, targets)
            reach = [v for v in targets if v in ref_dist]
            assert [v for v in targets if v in dist_stop] or not reach, (mesh, "parada: sin destino")
            if not reach:
                continue
            end = min(reach, key=ref_dist.get)
            end_stop = min([v for v in targets if v in dist_stop], key=dist_stop.get)
            assert end_stop == end and dist_stop[end] == ref_dist[end], (mesh, "parada: otro destino")
            assert graph._trace(trail_stop, None, end) == graph._trace(ref_trail, None, end), (mesh, "parada: otro camino")


@test
def test_uv_seam_edges_and_areas_match_reference():
    """uv_seam_edges (con SeamGraph) y _face_shell_areas dan lo mismo que los recorridos por caras de siempre,
    también con caras sin UVs."""
    from mbl_retopo_uv import uv_seams
    for make in (lambda: cmds.polyTorus(radius=3, sectionRadius=1, subdivisionsX=12, subdivisionsY=8,
                                        constructionHistory=False)[0],
                 lambda: cmds.polySphere(subdivisionsX=10, subdivisionsY=6, constructionHistory=False)[0]):
        mesh = make()
        cmds.polyAutoProjection(mesh + ".f[*]", planes=6, layoutMethod=0, layout=0, optimize=1, percentageSpace=0.0,
                                constructionHistory=False)
        graph = uv_seams.SeamGraph(mesh)
        assert uv.uv_seam_edges(mesh, graph) == uv.uv_seam_edges(mesh) and uv.uv_seam_edges(mesh), mesh
        # caras sin UVs: el recorrido de siempre las trata como una UV distinta de cualquiera
        cmds.polyMapDel(mesh + ".f[3:5]")
        assert uv.uv_seam_edges(mesh, graph) == uv.uv_seam_edges(mesh), mesh
    # área con signo por cara, como se calculaba con un bucle de Python
    fn = om.MFnMesh(uv.dag_path(uv.mesh_shape(mesh)))
    _, shell_ids = fn.getUvShellsIds()
    us, vs = fn.getUVs()
    uv_counts, uv_ids = fn.getAssignedUVs()
    expected, offset = [], 0
    for face, count in enumerate(uv_counts):
        ids = uv_ids[offset:offset + count]
        offset += count
        if count < 3:
            continue
        area = 0.0
        for i in range(count):
            a, b = ids[i], ids[(i + 1) % count]
            area += us[a] * vs[b] - us[b] * vs[a]
        expected.append((face, shell_ids[ids[0]], area * 0.5))
    assert uv._face_shell_areas(mesh) == expected


@test
def test_uv_quality_matches_maya():
    """mbl_engine.uv_quality (numpy, sin Maya) da el mismo estiramiento, el mismo aprovechado, las mismas islas y los
    mismos solapes y espejos que uv.py sobre mallas desplegadas con uv.unwrap, y también sobre UVs estropeadas."""
    from mbl_engine import uv_quality

    def arrays(mesh):
        points, faces = retopo.mesh_arrays(mesh)
        fn = om.MFnMesh(uv.dag_path(uv.mesh_shape(mesh)))
        us, vs = fn.getUVs()
        uv_counts, uv_ids = fn.getAssignedUVs()
        uv_faces, start = [], 0
        for count in uv_counts:
            uv_faces.append(list(uv_ids[start:start + count]))
            start += count
        return points, faces, np.column_stack([us, vs]), uv_faces

    def shells(mesh):
        return om.MFnMesh(uv.dag_path(uv.mesh_shape(mesh))).getUvShellsIds()[1]

    meshes = [cmds.polySphere(radius=2, subdivisionsX=14, subdivisionsY=10, constructionHistory=False)[0],
              cmds.polyTorus(radius=3, sectionRadius=1, subdivisionsX=14, subdivisionsY=8, constructionHistory=False)[0]]
    cmds.move(8, 0, 0, meshes[1])
    for mesh in cmds.ls(meshes, long=True):
        uv.unwrap(mesh, base_settings(), quiet_log)
        stretch, check = uv.stretch(mesh), uv.check(mesh)
        rep = uv_quality.report(*arrays(mesh))
        name = mesh.split("|")[-1]
        assert check["overlapping_faces"] == 0 and rep["overlapping_faces"] == 0, (name, check, rep)
        assert check["flipped_faces"] == rep["flipped_faces"] == 0, (name, check, rep)
        assert abs(rep["stretch_mean"] - stretch["mean"]) <= 0.01, (name, rep["stretch_mean"], stretch["mean"])
        assert abs(rep["over_1_5_percent"] - stretch["over_1_5_percent"]) <= 0.5, (name, rep, stretch)
        assert abs(rep["used_percent"] - 100.0 * uv.uv_area(mesh)) <= 0.5, (name, rep["used_percent"], uv.uv_area(mesh))
        assert rep["islands"] == check["shells"], (name, rep["islands"], check["shells"])
        assert rep["mapped_faces"] == rep["faces"] == cmds.polyEvaluate(mesh, face=True), (name, rep)
        level, reasons = uv_quality.grade(rep)
        assert level < 2, (name, level, reasons, rep)

    # UVs estropeadas a propósito: una isla encima de otra y otra en espejo; Maya y el núcleo cuentan lo mismo
    mesh = cmds.ls(meshes[0], long=True)[0]
    ids = list(shells(mesh))
    assert max(ids) >= 1, "la esfera tendría que salir en varias islas"
    first = [i for i, s in enumerate(ids) if s == 0]
    second = [i for i, s in enumerate(ids) if s == 1]
    fn = om.MFnMesh(uv.dag_path(uv.mesh_shape(mesh)))
    us, vs = fn.getUVs()
    shift_u = sum(us[i] for i in second) / len(second) - sum(us[i] for i in first) / len(first)
    shift_v = sum(vs[i] for i in second) / len(second) - sum(vs[i] for i in first) / len(first)
    cmds.polyEditUV(uv.components(mesh, "map", first), uValue=shift_u, vValue=shift_v, relative=True)
    check = uv.check(mesh)
    rep = uv_quality.report(*arrays(mesh))
    assert check["overlapping_faces"] > 0 and rep["overlapping_faces"] > 0, (check, rep)
    assert abs(rep["overlapping_faces"] - check["overlapping_faces"]) <= 0.1 * check["overlapping_faces"], (check, rep)
    assert uv_quality.grade(rep)[0] == 2, uv_quality.grade(rep)
    umin, umax = min(us[i] for i in second), max(us[i] for i in second)
    cmds.polyEditUV(uv.components(mesh, "map", second), pivotU=(umin + umax) * 0.5, pivotV=0.5, scaleU=-1.0, scaleV=1.0)
    check = uv.check(mesh)
    rep = uv_quality.report(*arrays(mesh))
    assert check["flipped_faces"] > 0 and rep["flipped_faces"] == check["flipped_faces"], (check, rep)
    assert "caras en espejo" in " ".join(uv_quality.grade(rep)[1]), uv_quality.grade(rep)


@test
def test_pole_cleanup():
    """Un giro de arista en una rejilla crea 4 polos; la limpieza los quita."""
    from mbl_retopo_uv import quadopt
    n = 8
    points = [(x, y, 0.0) for y in range(n + 1) for x in range(n + 1)]
    faces = [[y * (n + 1) + x, y * (n + 1) + x + 1, (y + 1) * (n + 1) + x + 1, (y + 1) * (n + 1) + x]
             for y in range(n) for x in range(n)]
    assert quadopt.pole_count(faces) == 0
    # girar la arista central a mano (sin la comprobación geométrica, que lo impediría)
    mesh = quadopt._Mesh(points, faces)
    u, w = 4 * (n + 1) + 4, 4 * (n + 1) + 5
    option = mesh.rotations(u, w)[0]
    mesh.set_face(option[1], option[3])
    mesh.set_face(option[2], option[4])
    damaged = [f for f in mesh.faces if f is not None]
    assert quadopt.pole_count(damaged) == 4, quadopt.pole_count(damaged)
    _, cleaned, operations = quadopt.clean_poles(points, damaged)
    assert quadopt.pole_count(cleaned) == 0 and operations >= 1, (quadopt.pole_count(cleaned), operations)
    directed = [(f[i], f[(i + 1) % 4]) for f in cleaned for i in range(4)]
    assert len(directed) == len(set(directed)), "orientación incoherente"


@test
def test_relax_keeps_corners():
    """La relajación no redondea las esquinas de una pieza dura ni mueve los bordes."""
    from mbl_retopo_uv import quadopt
    cube = cmds.polyCube(subdivisionsX=4, subdivisionsY=4, subdivisionsZ=4, constructionHistory=False)[0]
    points, faces = retopo.mesh_arrays(cube)
    relaxed = quadopt.relax(points, faces, 20)
    corners = [i for i, p in enumerate(points) if all(abs(abs(c) - 0.5) < 1e-6 for c in p)]
    assert len(corners) == 8
    assert np.abs(relaxed[corners] - points[corners]).max() < 1e-9
    plane = cmds.polyPlane(subdivisionsX=6, subdivisionsY=6, constructionHistory=False)[0]
    points, faces = retopo.mesh_arrays(plane)
    cmds.move(0.03, 0, 0.02, plane + ".vtx[24]", relative=True)
    points, faces = retopo.mesh_arrays(plane)
    relaxed = quadopt.relax(points, faces, 30)
    border = [i for i, p in enumerate(points) if abs(abs(p[0]) - 0.5) < 1e-6 or abs(abs(p[2]) - 0.5) < 1e-6]
    assert np.abs(relaxed[border] - points[border]).max() < 1e-9
    assert np.linalg.norm(relaxed[24] - (0.0, 0.0, 0.0)) < np.linalg.norm(points[24])


# ---------------------------------------------------------------- retopo y pipeline completo

@test
def test_full_pipeline():
    meshes = make_test_meshes()
    outcome = pipeline.run(meshes, ["cleanup", "retopo", "uv"], base_settings(), quiet_log)
    assert not outcome["errors"], outcome["errors"]
    assert len(outcome["results"]) == len(meshes)
    for original, result in zip(meshes, outcome["results"]):
        assert result.endswith("_retopo"), result
        assert cmds.getAttr(original + ".visibility") is False, "el original no está oculto"
        assert_good_uvs(result)


@test
def test_maya_engine():
    """Motor polyRetopo de Maya: quads, sin restos de historial y con poco error frente al original."""
    meshes = make_test_meshes()[1:]   # esfera, torus y malla densa
    settings = base_settings(retopo_engine="maya", retopo_target_faces=1500)
    outcome = pipeline.run(meshes, ["cleanup", "retopo", "uv"], settings, quiet_log)
    assert not outcome["errors"], outcome["errors"]
    for original, result in zip(meshes, outcome["results"]):
        stats = diagnostics.topology_stats(result)
        assert stats["quad_percent"] > 95, (result, stats["quad_percent"])
        assert 700 < stats["faces"] < 3000, (result, stats["faces"])
        assert not cmds.listHistory(result, pruneDagObjects=True), "queda historial de polyRetopo"
        assert diagnostics.find_source(result) == original
        error = diagnostics.surface_error(original, result)
        assert error["mean_percent"] < 1.0, (result, error)
        assert_good_uvs(result)


def make_bumpy_sphere(name, scale=1.0, subdivisions=60):
    """Esfera de radio 3 deformada con ondas (un escaneo orgánico pequeño), con los puntos multiplicados por `scale`.

    Se construye con meshbuild (puntos exactos): modificar una primitiva con setPoints deja el error de la deformación
    en un tweak de ~1e-7 que a escalas pequeñas ya es ruido visible y cambia el resultado de polyRetopo."""
    from mbl_retopo_uv import meshbuild
    base = cmds.polySphere(name="bumpyBase", radius=3, subdivisionsX=subdivisions, subdivisionsY=subdivisions,
                           constructionHistory=False)[0]
    points, faces = retopo.mesh_arrays(base)
    cmds.delete(base)
    bump = 1.0 + 0.08 * np.sin(points[:, 0] * 3.5) * np.cos(points[:, 1] * 2.8) + 0.05 * np.sin(points[:, 2] * 5.5)
    return meshbuild.create_mesh(points * bump[:, None] * np.array([1.0, 1.3, 1.0]) * scale, faces, name)


def world_box(node):
    """(centro, diagonal) de la caja del objeto en el mundo."""
    box = np.array(cmds.exactWorldBoundingBox(node), dtype=float)
    return (box[:3] + box[3:]) / 2.0, float(np.linalg.norm(box[3:] - box[:3]))


def make_scaled_torus(name, scale):
    """Toro regular (48 x 24) con los puntos multiplicados por `scale`, construido con meshbuild (puntos exactos)."""
    from mbl_retopo_uv import meshbuild
    base = cmds.polyTorus(name="torusBase", radius=3, sectionRadius=1.0, subdivisionsX=48, subdivisionsY=24,
                          constructionHistory=False)[0]
    points, faces = retopo.mesh_arrays(base)
    cmds.delete(base)
    return meshbuild.create_mesh(points * scale, faces, name)


@test
def test_retopo_does_not_depend_on_the_units():
    """El mismo modelo en cm, en m (x0,01), x10, x100 y x10.000 da con polyRetopo las mismas caras y polos (1 %) y cada resultado
    queda con el tamaño y el sitio de su original. polyRetopo ve el modelo siempre a la escala de trabajo (mbl_engine/scale.py:
    con diagonales de 1e5 se cuelga y por debajo de 1e-3 deforma el resultado) y con un tamaño razonable no se toca nada."""
    seen = []
    real = cmds.polyRetopo

    def spy(node, *args, **kwargs):
        seen.append(float(np.linalg.norm(np.ptp(retopo._object_points(node), axis=0))))
        return real(node, *args, **kwargs)

    outcomes = []
    cmds.polyRetopo = spy
    try:
        for index, factor in enumerate((1.0, 0.01, 100.0, 1e4, 10.0)):
            source = make_scaled_torus("units_{}".format(index), factor)
            cmds.move(4 * factor, 1 * factor, -2 * factor, source)
            cmds.rotate(20, 35, 0, source)
            result = retopo.retopo(source, base_settings(retopo_engine="maya", retopo_target_faces=400), quiet_log)
            stats = diagnostics.topology_stats(result)
            (centre_a, size_a), (centre_b, size_b) = world_box(source), world_box(result)
            assert 0.9 < size_b / size_a < 1.05, (factor, size_a, size_b)
            assert np.linalg.norm(centre_a - centre_b) < 0.05 * size_a, (factor, centre_a, centre_b)
            assert len(seen) == index + 1
            outcomes.append((stats["faces"], stats["pole_count"], size_b / size_a, seen[-1]))
    finally:
        cmds.polyRetopo = real
    base = outcomes[0]
    for faces_count, poles, ratio, _ in outcomes[1:-1]:
        assert abs(faces_count - base[0]) <= 0.01 * base[0] and abs(poles - base[1]) <= 0.01 * base[1], outcomes
        assert abs(ratio / base[2] - 1.0) < 0.01, outcomes
    # x10 llega a polyRetopo sin tocar y sus puntos float32 no son exactamente los de x1 por 10: polyRetopo reacciona a ese ruido
    # de 1e-7 (392 caras con unos puntos, 406 con otros), así que aquí solo se pide que salga algo equivalente
    assert abs(outcomes[-1][0] - base[0]) <= 0.05 * base[0] and outcomes[-1][1] == base[1], outcomes
    # lo que vio polyRetopo: siempre el mismo tamaño (diagonal 114,9), sea cual sea la escala del original; x10 ya lo
    # tenía y no se tocó
    assert all(abs(o[3] / 114.9 - 1.0) < 0.01 for o in outcomes), outcomes


def hard_edge_fraction(transform):
    """Fracción de las aristas interiores de la malla marcadas como duras (normales duras)."""
    it = om.MItMeshEdge(om.MSelectionList().add(transform).getDagPath(0))
    inner = hard = 0
    while not it.isDone():
        if not it.onBoundary():
            inner += 1
            hard += 0 if it.isSmooth else 1
        it.next()
    return hard / float(max(inner, 1))


@test
def test_all_hard_edges_do_not_change_the_retopo():
    """Los escaneos y las importaciones llegan con todas las aristas duras: el plugin no usa la dureza del modelo (polyRetopo
    con preserveHardEdges apagado y nada más la lee), así que con todas duras sale lo mismo que con todas suaves."""
    outcomes = {}
    for mode, angle in (("soft", 180), ("hard", 0)):
        source = make_bumpy_sphere("edges_" + mode, 1.0, 40)
        cmds.polySoftEdge(source, angle=angle, constructionHistory=False)
        assert hard_edge_fraction(source) == (1.0 if mode == "hard" else 0.0), mode
        result = retopo.retopo(source, base_settings(retopo_engine="maya", retopo_target_faces=500), quiet_log)
        stats = diagnostics.topology_stats(result)
        points = np.array(cmds.xform(result + ".vtx[*]", query=True, translation=True, worldSpace=True)).reshape(-1, 3)
        outcomes[mode] = (stats["faces"], stats["pole_count"], points)
    assert outcomes["soft"][:2] == outcomes["hard"][:2], (outcomes["soft"][:2], outcomes["hard"][:2])
    assert np.allclose(outcomes["soft"][2], outcomes["hard"][2], atol=1e-6)


def make_dressed_model():
    """Esfera de 384 caras con dos materiales por zonas (arriba rojo, abajo azul), dentro de un grupo movido y
    girado, con el pivote fuera del centro y una transformación propia (trasladada, girada y escalada)."""
    sphere = cmds.polySphere(name="vestida", radius=1.5, subdivisionsX=24, subdivisionsY=16,
                             constructionHistory=False)[0]
    groups = {}
    for name, color in (("rojo", (1, 0, 0)), ("azul", (0, 0, 1))):
        shader = cmds.shadingNode("lambert", asShader=True, name=name)
        cmds.setAttr(shader + ".color", *color, type="double3")
        groups[name] = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=name + "SG")
        cmds.connectAttr(shader + ".outColor", groups[name] + ".surfaceShader")
    it = om.MItMeshPolygon(om.MSelectionList().add(sphere).getDagPath(0))
    top, bottom = [], []
    while not it.isDone():
        (top if it.center(om.MSpace.kObject).y > 0 else bottom).append("{}.f[{}]".format(sphere, it.index()))
        it.next()
    cmds.sets(top, forceElement=groups["rojo"])
    cmds.sets(bottom, forceElement=groups["azul"])
    group = cmds.group(sphere, name="grupoVestida")
    cmds.xform(group, translation=(10, 0, 0), rotation=(0, 45, 0))
    cmds.xform(sphere, translation=(3, 1, -2), rotation=(20, 35, 10), scale=(1, 1.5, 1))
    cmds.xform(sphere, worldSpace=True, pivots=(0.5, -1, 0.2))
    return cmds.ls(sphere, long=True)[0]


def material_zones(result, original):
    """{material: (caras, fracción de ellas en su zona)}: arriba (y local del original > -0,2) el rojo, abajo el azul."""
    path = retopo.dag_path(retopo.mesh_shape(result))
    shaders, per_face = om.MFnMesh(path).getConnectedShaders(0)
    names = [om.MFnDependencyNode(shaders[i]).name() for i in range(len(shaders))]
    points, faces = retopo.mesh_arrays(result)
    inverse = np.linalg.inv(np.array(cmds.xform(original, query=True, matrix=True, worldSpace=True)).reshape(4, 4))
    out = {}
    for name in ("rojoSG", "azulSG"):
        mine = [f for f, s in enumerate(per_face) if s >= 0 and names[s] == name]
        local_y = [(np.append(points[faces[f]].mean(axis=0), 1.0) @ inverse)[1] for f in mine]
        inside = [(y > -0.2) if name == "rojoSG" else (y < 0.2) for y in local_y]
        out[name] = (len(mine), float(np.mean(inside)) if mine else 0.0)
    return out


def assert_same_placement(original, result):
    """Mismo grupo, mismos pivotes y transformación y misma caja envolvente del mundo."""
    assert cmds.listRelatives(result, parent=True, fullPath=True) == cmds.listRelatives(original, parent=True,
                                                                                         fullPath=True), result
    for attr in ("translate", "rotate", "scale", "rotatePivot", "scalePivot"):
        assert np.allclose(cmds.getAttr(original + "." + attr)[0], cmds.getAttr(result + "." + attr)[0],
                           atol=1e-6), (attr, cmds.getAttr(original + "." + attr), cmds.getAttr(result + "." + attr))
    for flag in ("rotatePivot", "scalePivot"):
        assert np.allclose(cmds.xform(original, query=True, worldSpace=True, **{flag: True}),
                           cmds.xform(result, query=True, worldSpace=True, **{flag: True}), atol=1e-6), flag
    a, b = retopo.mesh_arrays(original)[0], retopo.mesh_arrays(result)[0]
    diagonal = float(np.linalg.norm(np.ptp(a, axis=0)))
    assert np.abs(a.min(axis=0) - b.min(axis=0)).max() < 0.02 * diagonal, (a.min(axis=0), b.min(axis=0))
    assert np.abs(a.max(axis=0) - b.max(axis=0)).max() < 0.02 * diagonal, (a.max(axis=0), b.max(axis=0))


def assert_keeps_everything(original, result):
    """El _retopo no pierde nada del original: nombre base, grupo, transformación, pivote y materiales por zonas."""
    assert result.split("|")[-1] == "vestida_retopo", result
    assert_same_placement(original, result)
    zones = material_zones(result, original)
    total = cmds.polyEvaluate(result, face=True)
    for name, (count, inside) in zones.items():
        assert count > 0.25 * total, "el material {} casi no está en el retopo: {}".format(name, zones)
        assert inside > 0.95, "el material {} se salió de su zona: {}".format(name, zones)


@test
def test_retopo_keeps_materials_pivot_transform_and_group():
    """El _retopo hereda del original los dos materiales (cada uno en su zona), el pivote, la transformación y el grupo;
    con el motor de Maya (que rehace la malla en la raíz al limpiar polos) y con un solo Ctrl+Z."""
    original = make_dressed_model()
    cmds.undoInfo(state=True)
    cmds.flushUndo()
    outcome = pipeline.run([original], ["retopo"], base_settings(retopo_engine="maya", retopo_target_faces=300),
                           quiet_log)
    assert not outcome["errors"], outcome["errors"]
    result = outcome["results"][0]
    assert_keeps_everything(original, result)
    assert diagnostics.find_source(result) == original
    cmds.undo()
    assert not cmds.ls("vestida_retopo*"), "quedó el retopo tras deshacer"
    assert cmds.getAttr(original + ".visibility"), "el original sigue oculto tras deshacer"


@test
def test_loops_and_curvature():
    """Métrica de loops y direcciones de curvatura del Motor MBL."""
    # rejilla 4x4 de quads: 10 loops abiertos de 4 aristas
    faces = [[y * 5 + x, y * 5 + x + 1, y * 5 + x + 6, y * 5 + x + 5] for y in range(4) for x in range(4)]
    loops = diagnostics.loop_stats(faces)
    assert loops["loops"] == 10 and abs(loops["loop_mean"] - 4.0) < 1e-9, loops
    # cilindro sin tapas: los anillos son loops cerrados
    cylinder = cmds.polyCylinder(radius=1, height=6, subdivisionsX=24, subdivisionsY=12,
                                 subdivisionsCaps=0, constructionHistory=False)[0]
    cmds.delete(cylinder + ".f[288:289]")
    assert diagnostics.loop_stats(diagnostics.mesh_faces(cylinder))["loop_closed_percent"] > 40
    # direcciones: alrededor del tubo con peso alto; en la esfera, sin dirección (peso ~0)
    cmds.polyTriangulate(cylinder, constructionHistory=False)
    points, faces = retopo.mesh_arrays(cylinder)
    directions, weights = orient.curvature_directions(points, faces)
    middle = np.abs(points[:, 1]) < 1.5
    assert weights[middle].mean() > 0.9 and np.abs(directions[middle, 1]).mean() < 0.1
    sphere = cmds.polySphere(radius=2, subdivisionsX=30, subdivisionsY=20, constructionHistory=False)[0]
    cmds.polyTriangulate(sphere, constructionHistory=False)
    points, faces = retopo.mesh_arrays(sphere)
    assert orient.curvature_directions(points, faces)[1].mean() < 0.1
    cmds.delete(cylinder, sphere)


@test
def test_fill_new_holes():
    """Solo se cierran los agujeros que no estaban en el original, y con quads (función pura)."""
    # rejilla 6x6 de quads con un agujero de 2x2 caras en el centro (borde de 8 aristas)
    points = [(float(x), float(y), 0.0) for y in range(7) for x in range(7)]
    faces = [[y * 7 + x, y * 7 + x + 1, y * 7 + x + 8, y * 7 + x + 7]
             for y in range(6) for x in range(6) if not (2 <= x <= 3 and 2 <= y <= 3)]
    outer = [p for p in points if p[0] in (0, 6) or p[1] in (0, 6)]
    new_points, new_faces, filled = retopo.fill_new_holes(points, faces, outer, 60)
    assert filled == 1 and len(new_faces) == len(faces) + 4, (filled, len(new_faces))
    assert all(len(f) == 4 for f in new_faces)
    # si el original ya tenía ese agujero, se respeta
    hole = [p for p in points if 2 <= p[0] <= 4 and 2 <= p[1] <= 4 and (p[0] in (2, 4) or p[1] in (2, 4))]
    assert retopo.fill_new_holes(points, faces, outer + hole, 60)[2] == 0


@test
def test_degenerate_faces_and_guide_directions():
    """Chequeo de sanidad (caras con ángulos muy agudos) y guías para --orient (funciones puras)."""
    from mbl_retopo_uv import guides, quadopt
    # rejilla 6x6 con un vértice interior casi encima de su vecino: caras con ángulos de pocos grados
    points = [[float(x), float(y), 0.0] for y in range(7) for x in range(7)]
    faces = [[y * 7 + x, y * 7 + x + 1, y * 7 + x + 8, y * 7 + x + 7] for y in range(6) for x in range(6)]
    points[3 * 7 + 3] = [3.97, 3.97, 0.0]
    assert quadopt.degenerate_faces(points, faces)
    fixed, before, after = quadopt.fix_degenerate(points, faces)
    assert before > 0 and after == 0, (before, after)
    # guía recta en X a la altura y = 0: dirección X con peso 1 cerca, 0 lejos
    samples = np.array([[x * 0.5, 0.0, 0.0] for x in range(13)])
    tangents = np.tile([1.0, 0.0, 0.0], (len(samples), 1))
    near_far = np.array([[3.0, 0.5, 0.0], [3.0, 10.0, 0.0]])
    directions, weights = guides.guide_directions(near_far, samples, tangents, edge_length=1.0)
    assert weights[0] == 1.0 and weights[1] == 0.0, weights
    assert np.allclose(directions[0], [1.0, 0.0, 0.0])


@test
def test_engine_matches_maya_metrics():
    """El núcleo sin Maya (mbl_engine) da las mismas métricas que la versión con la API de Maya."""
    from mbl_engine import metrics
    from mbl_engine.mesh import Mesh
    source = cmds.polySphere(radius=2, subdivisionsX=60, subdivisionsY=40, constructionHistory=False)[0]
    target = cmds.polySphere(radius=2, subdivisionsX=16, subdivisionsY=10, constructionHistory=False)[0]
    cmds.delete(target + ".f[0:15]")   # un agujero, para que haya bordes
    maya_topo = diagnostics.topology_stats(target)
    mesh_t, mesh_s = Mesh(*retopo.mesh_arrays(target)), Mesh(*retopo.mesh_arrays(source))
    engine_topo = metrics.topology_stats(mesh_t)
    for key in ("faces", "quads", "tris", "ngons", "pole_count"):
        assert maya_topo[key] == engine_topo[key], (key, maya_topo[key], engine_topo[key])
    assert abs(maya_topo["regular_percent"] - engine_topo["regular_percent"]) < 1e-9
    assert {k: sorted(v) for k, v in maya_topo["poles"].items()} == engine_topo["poles"]
    maya_err = diagnostics.surface_error(source, target)
    engine_err = metrics.surface_error(mesh_s, mesh_t)
    assert abs(maya_err["mean"] - engine_err["mean"]) < 0.05 * maya_err["mean"], (maya_err, engine_err)
    cmds.delete(source, target)


@test
def test_auto_engine():
    """Modo automático: prueba Maya y el Motor MBL, deja una sola malla "_retopo" y se deshace de una vez."""
    mesh = make_dense_mesh()
    settings = base_settings(retopo_engine="auto", retopo_target_faces=1500)
    messages = []
    cmds.undoInfo(state=True)
    cmds.flushUndo()
    meshes_before = sorted(cmds.ls(type="mesh", long=True))
    outcome = pipeline.run([mesh], ["retopo"], settings, lambda m, level="info": messages.append(m))
    assert not outcome["errors"], outcome["errors"]
    result = outcome["results"][0]
    assert result.split("|")[-1] == "denseOrganic_retopo", result
    assert len(cmds.ls("denseOrganic_retopo*", type="transform")) == 1, cmds.ls("denseOrganic_retopo*")
    assert any("modo automático ->" in m for m in messages), messages
    assert diagnostics.find_source(result) == mesh
    assert cmds.getAttr(mesh + ".visibility") is False
    cmds.undo()
    assert sorted(cmds.ls(type="mesh", long=True)) == meshes_before, "quedaron mallas tras deshacer"


@test
def test_mbl_engine():
    """Motor MBL: una sola malla "_retopo" de solo quads, original oculto, enlace al original, y se deshace de una vez."""
    from mbl_retopo_uv import config
    if not config.find_quadriflow():
        print("    (sin QuadriFlow: se omite)")
        return
    mesh = make_dense_mesh()
    settings = base_settings(retopo_engine="mbl", retopo_target_faces=1500, mbl_variants=2)
    messages = []
    cmds.undoInfo(state=True)
    cmds.flushUndo()
    meshes_before = sorted(cmds.ls(type="mesh", long=True))
    outcome = pipeline.run([mesh], ["retopo"], settings, lambda m, level="info": messages.append(m))
    assert not outcome["errors"], outcome["errors"]
    result = outcome["results"][0]
    assert result.split("|")[-1] == "denseOrganic_retopo", result
    assert len(cmds.ls("denseOrganic_retopo*", type="transform")) == 1
    assert any("Motor MBL: variante" in m for m in messages), messages
    assert diagnostics.topology_stats(result)["quad_percent"] > 98.0
    assert diagnostics.find_source(result) == mesh
    assert cmds.getAttr(mesh + ".visibility") is False
    cmds.undo()
    assert sorted(cmds.ls(type="mesh", long=True)) == meshes_before, "quedaron mallas tras deshacer"


@test
def test_auto_with_mbl():
    """Automático con el Motor MBL: compara con el retopo de Maya y deja una sola malla."""
    from mbl_retopo_uv import config
    if not config.find_quadriflow():
        print("    (sin QuadriFlow: se omite)")
        return
    mesh = make_dense_mesh()
    settings = base_settings(retopo_engine="auto", retopo_target_faces=1500, mbl_variants=2)
    messages = []
    outcome = pipeline.run([mesh], ["retopo"], settings, lambda m, level="info": messages.append(m))
    assert not outcome["errors"], outcome["errors"]
    result = outcome["results"][0]
    assert result.split("|")[-1] == "denseOrganic_retopo", result
    assert len(cmds.ls("denseOrganic_retopo*", type="transform")) == 1, cmds.ls("denseOrganic_retopo*")
    assert any("Motor MBL:" in m and "caras" in m for m in messages), messages
    assert any("modo automático ->" in m for m in messages), messages
    assert cmds.getAttr(mesh + ".visibility") is False


@test
def test_status():
    """Semáforo: verde en un resultado bueno, rojo con el problema y el consejo en los casos malos."""
    sphere = cmds.ls(cmds.polySphere(subdivisionsX=30, subdivisionsY=20, constructionHistory=False)[0], long=True)[0]
    outcome = pipeline.run([sphere], ["cleanup", "retopo", "uv"],
                           base_settings(retopo_engine="maya", retopo_target_faces=600), quiet_log)
    result = outcome["results"][0]
    results = outcome["status"][result]
    assert [r["step"] for r in results] == ["cleanup", "retopo", "uv"]
    assert status.overall(results) == status.GREEN, results
    # sin indicar pasos se deduce lo que tiene la malla (original enlazado y UVs)
    assert [r["step"] for r in status.evaluate(result)] == ["cleanup", "retopo", "uv"]

    def levels(results, step):
        return [c for r in results if r["step"] == step for c in r["checks"] if c["level"] != status.GREEN]

    # UVs solapadas (proyección plana sobre una esfera) -> rojo con consejo
    other = cmds.ls(cmds.polySphere(constructionHistory=False)[0], long=True)[0]
    cmds.polyProjection(other + ".f[*]", type="Planar", mapDirection="z", constructionHistory=False)
    bad = levels(status.evaluate(other), "uv")
    assert any(c["level"] == status.RED and "solapadas" in c["text"] and c["advice"] for c in bad), bad

    # sin UVs -> rojo
    cmds.polyMapDel(other + ".f[*]", constructionHistory=False)
    assert status.uv_status(other)["level"] == status.RED

    # un "retopo" que no se parece al original -> error de forma en rojo
    cube = cmds.ls(cmds.polyCube(width=3, height=0.2, depth=3, constructionHistory=False)[0], long=True)[0]
    diagnostics.link_source(cube, sphere)
    bad = levels([status.retopo_status(cube)], "retopo")
    assert any(c["level"] == status.RED and "Error de forma" in c["text"] for c in bad), bad

    # no-manifold -> limpieza en rojo
    points = [om.MPoint(*p) for p in [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, -1, 0),
                                      (1, -1, 0), (0, 0, 1), (1, 0, 1)]]
    fn = om.MFnMesh()
    fn.create(points, [4, 4, 4], [0, 1, 3, 2, 0, 4, 5, 1, 0, 1, 7, 6])
    nonmanifold = cmds.listRelatives(fn.fullPathName(), parent=True, fullPath=True)[0]
    assert status.cleanup_status(nonmanifold)["level"] == status.RED


def make_uv_mesh(points, faces, uvs, uv_faces):
    """Malla con las UVs exactas que se le dan (para probar el semáforo sin depender del despliegue de Unfold3D)."""
    fn = om.MFnMesh()
    fn.create([om.MPoint(*p) for p in points], [len(f) for f in faces], [i for f in faces for i in f])
    fn.setUVs([u for u, _ in uvs], [v for _, v in uvs])
    fn.assignUVs([len(f) for f in uv_faces], [i for f in uv_faces for i in f])
    return cmds.listRelatives(fn.fullPathName(), parent=True, fullPath=True)[0]


def make_uv_planes(rects, n=4):
    """Un plano suelto de 1x1 (n x n quads) por rectángulo (u0, v0, u1, v1), con las UVs planares dentro de él.
    Con u0 > u1 la isla sale en espejo."""
    points, faces, uvs, uv_faces = [], [], [], []
    for k, (u0, v0, u1, v1) in enumerate(rects):
        base = len(points)
        for j in range(n + 1):
            for i in range(n + 1):
                points.append((2.0 * k + i / float(n), j / float(n), 0.0))
                uvs.append((u0 + (u1 - u0) * i / float(n), v0 + (v1 - v0) * j / float(n)))
        for j in range(n):
            for i in range(n):
                a = base + j * (n + 1) + i
                faces.append([a, a + 1, a + n + 2, a + n + 1])
        uv_faces.extend(list(f) for f in faces[-n * n:])
    return make_uv_mesh(points, faces, uvs, uv_faces)


@test
def test_status_uv_judge():
    """Semáforo de UVs con el juez de UVs (uv_quality.grade): UVs buenas -> verde; solapadas, en espejo, con densidad distinta,
    islas casi pegadas, costuras que no casan o poca textura -> no verde, con su consejo; nunca más verde que la nota del juez."""
    from mbl_engine import uv_quality
    left, right = (0.02, 0.02, 0.49, 0.98), (0.51, 0.02, 0.98, 0.98)

    def judged(mesh, hidden_seams=False, report=None):
        result = status.uv_status(mesh, report, 2048, hidden_seams)
        grade = uv_quality.grade(uv_quality.report(*status.uv_arrays(mesh)))[0]
        assert result["level"] >= grade, (result["level"], grade, result["checks"])
        return result

    def bad_checks(result):
        return [c for c in result["checks"] if c["level"] != status.GREEN]

    def lying_report(mesh):
        """El informe de uv.check dice que no hay solapes ni espejos: solo el juez los puede ver."""
        return dict(uv.check(mesh), overlapping_faces=0, flipped_faces=0)

    # dos islas con la misma densidad, bien separadas y llenando la textura: verde, con la línea del juez
    result = judged(make_uv_planes([left, right]))
    assert result["level"] == status.GREEN, bad_checks(result)
    assert any("Densidad de texel igual entre islas" in c["text"] for c in result["checks"]), result["checks"]
    # una sola isla que llena el cuadro (sin margen ni costuras que medir): verde
    assert judged(make_uv_planes([(0.0, 0.0, 1.0, 1.0)]))["level"] == status.GREEN
    # UDIM: dos islas en dos tiles, sin falsa alarma de textura (la suma de áreas pasa de 100 %)
    udim = make_uv_planes([(0.02, 0.02, 0.98, 0.98), (1.02, 0.02, 1.98, 0.98)])
    assert uv.check(udim, udim=True)["shells"] == 2
    assert judged(udim)["level"] == status.GREEN, bad_checks(judged(udim))

    # una isla encima de la otra: rojo, y con el informe de Maya diciendo que no hay solapes lo ve el juez
    overlapped = make_uv_planes([left, left])
    result = judged(overlapped)
    assert result["level"] == status.RED and any("solapadas" in c["text"] and c["advice"] for c in bad_checks(result)), result
    result = judged(overlapped, report=lying_report(overlapped))
    assert any(c["level"] == status.RED and "solapadas" in c["text"] and c["advice"] for c in bad_checks(result)), bad_checks(result)

    # una isla en espejo: rojo (idem)
    mirrored = make_uv_planes([left, (0.98, 0.02, 0.51, 0.98)])
    result = judged(mirrored, report=lying_report(mirrored))
    assert any(c["level"] == status.RED and "espejo" in c["text"] and c["advice"] for c in bad_checks(result)), bad_checks(result)

    # una isla con la mitad de densidad que la otra: aviso de densidad (amarillo; con tanta diferencia también salta el estiramiento)
    result = judged(make_uv_planes([left, (0.51, 0.02, 0.745, 0.5)]))
    assert any(c["level"] == status.YELLOW and "Densidad de texel" in c["text"] and "Repite 'UVs'" in c["advice"]
               for c in bad_checks(result)), bad_checks(result)

    # islas separadas por 0,0005 en UV (1 px a 2048): amarillo, y el consejo habla del Padding
    result = judged(make_uv_planes([(0.02, 0.02, 0.4998, 0.98), (0.5003, 0.02, 0.98, 0.98)]))
    assert result["level"] == status.YELLOW, bad_checks(result)
    assert any("casi pegadas" in c["text"] and "Padding" in c["advice"] for c in bad_checks(result)), bad_checks(result)

    # con Costuras escondidas el umbral de textura aprovechada de antes (35 %) deja pasar un 38 %; el juez (40 %), no
    small = make_uv_planes([(0.19, 0.19, 0.81, 0.81)])
    assert status._grade(38.44, status.UV_COVERAGE_HIDDEN_PERCENT, higher_is_worse=False) == status.GREEN
    result = judged(small, hidden_seams=True)
    assert result["level"] == status.YELLOW, bad_checks(result)
    assert any("textura aprovechada" in c["text"] and c["advice"] for c in bad_checks(result)), bad_checks(result)

    # dos quads vecinos con el segundo girado 90 en UV: el corte no casa -> amarillo con consejo (no rojo: no es un fallo grave)
    points = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0), (1, 1, 0), (2, 1, 0)]
    uvs = [(0.02, 0.02), (0.49, 0.02), (0.49, 0.49), (0.02, 0.49),
           (0.98, 0.02), (0.98, 0.49), (0.51, 0.49), (0.51, 0.02)]
    turned = make_uv_mesh(points, [[0, 1, 4, 3], [1, 2, 5, 4]], uvs, [[0, 1, 2, 3], [4, 5, 6, 7]])
    result = judged(turned)
    assert any(c["level"] == status.YELLOW and "no casan" in c["text"] and "Dividir islas" in c["advice"]
               for c in bad_checks(result)), bad_checks(result)
    # lo mismo con el segundo quad sin girar (UVs contiguas, una sola isla): sin avisos
    flat = make_uv_mesh(points, [[0, 1, 4, 3], [1, 2, 5, 4]], [(0.02, 0.02), (0.49, 0.02), (0.98, 0.02), (0.02, 0.98),
                                                              (0.49, 0.98), (0.98, 0.98)], [[0, 1, 4, 3], [1, 2, 5, 4]])
    assert judged(flat)["level"] == status.GREEN, bad_checks(judged(flat))

    # si el juez falla, el paso sale en amarillo con un aviso (nunca verde sin saberlo) y las comprobaciones de antes siguen
    original = status.uv_quality.report
    status.uv_quality.report = lambda *args: 1 / 0
    try:
        result = status.uv_status(make_uv_planes([left, right]), None, 2048, False)
    finally:
        status.uv_quality.report = original
    assert result["level"] == status.YELLOW, result
    assert any("No he podido calcular la calidad de las UVs" in c["text"] and c["advice"] for c in result["checks"]), result


def make_cube_sphere(name, subdivisions=16, radius=1.0):
    """Esfera de solo quads (un cubo subdividido y redondeado): un retopo limpio, sin triángulos ni polos de 5."""
    cube = cmds.polyCube(width=2, height=2, depth=2, subdivisionsX=subdivisions, subdivisionsY=subdivisions,
                         subdivisionsZ=subdivisions, constructionHistory=False, name=name)[0]
    fn = om.MFnMesh(om.MSelectionList().add(cube).getDagPath(0))
    points = fn.getPoints()
    for i, p in enumerate(points):
        length = math.sqrt(p.x ** 2 + p.y ** 2 + p.z ** 2)
        points[i] = om.MPoint(p.x * radius / length, p.y * radius / length, p.z * radius / length)
    fn.setPoints(points)
    return cmds.ls(cube, long=True)[0]


@test
def test_status_judge():
    """Semáforo con el juez del motor (quality.grade): limpio -> verde; caras dadas la vuelta, dobladas o con
    triángulos de más -> no verde, con su consejo; nunca más verde que la nota del juez."""
    from mbl_engine import quality
    from mbl_engine.mesh import Mesh
    from mbl_retopo_uv import meshbuild
    source = cmds.ls(cmds.polySphere(radius=1.0, subdivisionsX=60, subdivisionsY=40, constructionHistory=False)[0],
                     long=True)[0]
    clean = make_cube_sphere("limpia")
    points, faces = retopo.mesh_arrays(clean)

    def judged(points, faces, name, link=True):
        mesh = meshbuild.create_mesh(points, faces, name)
        if link:
            diagnostics.link_source(mesh, source)
        result = status.retopo_status(mesh)
        grade = quality.grade(quality.report(Mesh(*retopo.mesh_arrays(mesh)), Mesh(*retopo.mesh_arrays(source))))[0]
        assert result["level"] >= grade, (name, result["level"], grade)
        return result

    def bad_checks(result):
        return [c for c in result["checks"] if c["level"] != status.GREEN]

    # limpia: verde, con su línea del juez
    result = judged(points, faces, "limpia_retopo")
    assert result["level"] == status.GREEN, bad_checks(result)
    assert any("Sin caras dadas la vuelta ni dobladas" in c["text"] for c in result["checks"]), result["checks"]

    # el retopo entero con las normales hacia dentro: el semáforo de antes lo daba por bueno (forma y quads perfectos)
    result = judged(points, [f[::-1] for f in faces], "alReves_retopo")
    bad = bad_checks(result)
    assert result["level"] == status.RED, bad
    assert any(c["level"] == status.RED and "dadas la vuelta" in c["text"] and c["advice"] for c in bad), bad

    # 10% de las caras con el orden de vértices al revés: dadas la vuelta (y dobladas contra sus vecinas) -> rojo
    reversed_faces = [f[::-1] if i % 10 == 0 else f for i, f in enumerate(faces)]
    result = judged(points, reversed_faces, "dadaVuelta_retopo")
    bad = bad_checks(result)
    assert result["level"] == status.RED, bad
    assert any(c["level"] == status.RED and "dadas la vuelta" in c["text"] and c["advice"] for c in bad), bad
    assert any(c["level"] == status.RED and "dobladas" in c["text"] and c["advice"] for c in bad), bad

    # sin el original no se ven las caras dadas la vuelta, pero sí las dobladas (y avisa de que falta el original)
    lonely = meshbuild.create_mesh(points, reversed_faces, "sinOriginal")
    bad = bad_checks(status.retopo_status(lonely))
    assert any("dobladas" in c["text"] for c in bad), bad
    assert any("No encuentro la malla original" in c["text"] for c in bad), bad

    # unos pocos triángulos (1,3%): el semáforo viejo no avisaba (98,7% quads), el juez sí -> amarillo
    def with_triangles(every):
        out = []
        for i, f in enumerate(faces):
            out.extend([[f[0], f[1], f[2]], [f[0], f[2], f[3]]] if i % every == 0 else [f])
        return out
    result = judged(points, with_triangles(150), "pocosTris_retopo")
    bad = bad_checks(result)
    assert result["level"] == status.YELLOW, bad
    assert any("quads" in c["text"] and c["advice"] for c in bad), bad
    # muchos (9,5%): rojo
    result = judged(points, with_triangles(20), "muchosTris_retopo")
    assert result["level"] == status.RED, bad_checks(result)

    # original demasiado grande para el juez: no se compara con él y lo dice (amarillo, nunca verde sin saberlo)
    limit = status.JUDGE_MAX_SOURCE_TRIANGLES
    status.JUDGE_MAX_SOURCE_TRIANGLES = 10
    try:
        result = judged(points, faces, "grande_retopo")
    finally:
        status.JUDGE_MAX_SOURCE_TRIANGLES = limit
    assert result["level"] == status.YELLOW, bad_checks(result)
    assert any("no se comprueba" in c["text"] for c in bad_checks(result)), result["checks"]


@test
def test_mirror_mesh():
    """Reflejo de la mitad: suelda la costura, invierte las caras y no deja vértices al otro lado."""
    from mbl_retopo_uv import symmetry
    # rejilla 2x2 de quads en x [0, 2], con la columna x=0 algo desplazada (como deja un retopo)
    points = [(x + (0.05 if x == 0 else 0.0), y, 0.0) for y in range(3) for x in range(3)]
    faces = [[y * 3 + x, y * 3 + x + 1, y * 3 + x + 4, y * 3 + x + 3] for y in range(2) for x in range(2)]
    out_points, out_faces, welded = symmetry.mirror_mesh(points, faces, 0.0, 0.3)
    assert welded == 3, welded
    assert len(out_points) == 9 + 6 and len(out_faces) == 8
    assert min(p[0] for p in out_points[:9]) >= 0.0
    # cada arista interior la comparten exactamente dos caras: la costura está soldada
    count = {}
    for face in out_faces:
        for a, b in zip(face, face[1:] + face[:1]):
            count[(min(a, b), max(a, b))] = count.get((min(a, b), max(a, b)), 0) + 1
    seam = [e for e, n in count.items() if n == 2 and out_points[e[0]][0] == 0 and out_points[e[1]][0] == 0]
    assert len(seam) == 2, seam
    # orientación: la cara reflejada recorre los vértices al revés
    # cara 0 = [0, 1, 4, 3]; su reflejo es [3, 4', 1', 0] con 0 y 3 soldados (columna x=0)
    assert out_faces[4][0] == 3 and out_faces[4][3] == 0 and out_faces[4][1] >= 9 and out_faces[4][2] >= 9, out_faces[4]
    # una tapa sobre el plano (el relleno de agujeros cerrando el corte) se quita y el borde se suelda
    flat = [(0.0 if x == 0 else p[0], p[1], p[2]) for p, x in zip(points, [i % 3 for i in range(9)])]
    capped = faces + [[0, 3, 6]]
    out_points, out_faces, welded = symmetry.mirror_mesh(flat, capped, 0.0, 0.3)
    assert welded == 3 and len(out_faces) == 8, (welded, len(out_faces))
    # un quad estrecho junto al plano (no está sobre él) se conserva
    narrow = [(0.0, 0, 0), (0.1, 0, 0), (0.1, 1, 0), (0.0, 1, 0)]
    assert len(symmetry.mirror_mesh(narrow, [[0, 1, 2, 3]], 0.0, 0.3)[1]) == 2


@test
def test_symmetric_retopo():
    """Simetría automática (hay que pedirla: por defecto va apagada): una esfera se hace por mitades y sale idéntica a su reflejo, sin grietas."""
    from mbl_retopo_uv import symmetry
    sphere = cmds.ls(cmds.polySphere(radius=2, subdivisionsX=40, subdivisionsY=30,
                                     constructionHistory=False)[0], long=True)[0]
    cmds.move(3, 1, 0, sphere)
    assert symmetry.is_symmetric(sphere)[0]
    lumpy = make_dense_mesh()
    assert not symmetry.is_symmetric(lumpy)[0], symmetry.mirror_error(lumpy)
    messages = []
    outcome = pipeline.run([sphere], ["retopo"], base_settings(retopo_engine="maya", retopo_target_faces=800,
                                                    retopo_symmetry="auto"),
                           lambda m, level="info": messages.append(m))
    assert not outcome["errors"], outcome["errors"]
    result = outcome["results"][0]
    assert any("reflejada" in m for m in messages), messages
    assert symmetry.mirror_error(result, symmetry.plane_x(sphere))[0] < 1e-4
    assert not retopo.border_loops(result), "quedó una grieta en la costura central"
    assert cleanup.nonmanifold_counts(result) == (0, 0)
    assert 500 < cmds.polyEvaluate(result, face=True) < 1300
    assert diagnostics.find_source(result) == sphere
    # con la simetría desactivada no se refleja
    messages = []
    pipeline.run([sphere], ["retopo"], base_settings(retopo_engine="maya", retopo_target_faces=800,
                                                     retopo_symmetry="off"),
                 lambda m, level="info": messages.append(m))
    assert not any("reflejada" in m for m in messages)


@test
def test_guides():
    """Una curva guía sobre la esfera hace que el retopo de Maya ponga un loop siguiéndola (también por mitades)."""
    from mbl_retopo_uv import guides
    normal = np.array([1.0, 1.0, 0.0]) / math.sqrt(2)

    def edges_on_ring(mesh):
        fn = om.MFnMesh(om.MSelectionList().add(mesh).getDagPath(0))
        pts = np.array([(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)])
        d = pts @ normal
        return sum(1 for e in range(fn.numEdges) if all(abs(d[v]) < 0.01 for v in fn.getEdgeVertices(e)))

    results = {}
    for with_guide in (False, True):
        sphere = cmds.ls(cmds.polySphere(radius=2, subdivisionsX=80, subdivisionsY=60,
                                         constructionHistory=False)[0], long=True)[0]
        if with_guide:
            ring = cmds.circle(normal=(1, 1, 0), radius=2.0, sections=24, constructionHistory=False)[0]
            guides.add_curves(sphere, [ring])
            assert len(guides.guide_curves(sphere)) == 1
        messages = []
        outcome = pipeline.run([sphere], ["retopo"], base_settings(retopo_engine="auto", retopo_target_faces=800,
                                                        retopo_symmetry="auto"),
                               lambda m, level="info": messages.append(m))
        assert not outcome["errors"], outcome["errors"]
        result = outcome["results"][0]
        results[with_guide] = (edges_on_ring(result), cmds.polyEvaluate(result, face=True), messages)
        if with_guide:
            assert guides.clear_guides(sphere) and not guides.guide_curves(sphere)
        cmds.delete(sphere, result)
    plain, guided = results[False], results[True]
    assert plain[0] < 5, plain[0]
    assert guided[0] >= 20, guided[0]
    assert 500 < guided[1] < 1400, guided[1]
    assert any("se usa el motor de Maya" in m for m in guided[2]), guided[2]
    assert any("sin reflejar" in m for m in guided[2]), "con guías el retopo no debe ir por mitades"


@test
def test_detail_brush():
    """Zona pintada -> más caras por unidad de área ahí (Instant Meshes MBL con --density)."""
    from mbl_retopo_uv import detail
    executable = config.find_instant_meshes({})
    if not executable or not retopo.engine_supports_density(executable):
        print("    (sin Instant Meshes MBL con --density: se omite)")
        return
    points = [(0, 0, 0), (2, 0, 0), (0, 2, 0)]
    assert abs(retopo.vertex_areas(points, [[0, 1, 2]]).sum() - 2.0) < 1e-9

    def cap_share(mesh):
        it = om.MItMeshPolygon(om.MSelectionList().add(mesh).getDagPath(0))
        inside = total = 0
        while not it.isDone():
            inside += it.center(om.MSpace.kWorld).x > 1.0
            total += 1
            it.next()
        return inside / float(total)

    shares = {}
    for painted in (False, True):
        sphere = cmds.ls(cmds.polySphere(radius=2, subdivisionsX=60, subdivisionsY=40,
                                         constructionHistory=False)[0], long=True)[0]
        if painted:
            assert detail.paint_sphere_region(sphere, (2, 0, 0), 1.4) > 0
            assert detail.has_detail(sphere)
        messages = []
        settings = base_settings(retopo_engine="instant_meshes" if not painted else "auto",
                                 instant_meshes_path=executable, retopo_target_faces=1200,
                                 retopo_symmetry="off")
        outcome = pipeline.run([sphere], ["retopo"], settings, lambda m, level="info": messages.append(m))
        assert not outcome["errors"], outcome["errors"]
        shares[painted] = cap_share(outcome["results"][0])
        if painted:
            assert any("detalle pintadas" in m for m in messages), messages
            assert detail.clear_detail(sphere) and not detail.has_detail(sphere)
        cmds.delete(sphere, outcome["results"][0])
    # el casquete x > 1 es un 25% del área; pintado (radio 1.4 alrededor de su punta) se lleva
    # claramente más caras (medido: 25% -> 38%)
    assert shares[True] > 1.3 * shares[False], shares


@test
def test_symmetric_upright_uvs():
    """Malla simétrica: costuras simétricas (cada costura tiene su reflejo) e islas nunca boca abajo."""
    from mbl_retopo_uv.uv_seams import SeamGraph, mirror_edges
    body = cmds.ls(cmds.polyCylinder(radius=1, height=6, subdivisionsX=24, subdivisionsY=12,
                                     subdivisionsCaps=2, constructionHistory=False)[0], long=True)[0]
    arms = cmds.polyCylinder(radius=0.4, height=7, subdivisionsX=12, subdivisionsY=14, constructionHistory=False)[0]
    cmds.rotate(0, 0, 90, arms)
    cmds.move(0, 2, 0, arms)
    body = cmds.ls(cmds.polyUnite(body, arms, constructionHistory=False)[0], long=True)[0]
    cmds.polyMergeVertex(body, distance=1e-4, constructionHistory=False)
    report = uv.unwrap(body, base_settings(), quiet_log)
    assert report["overlapping_faces"] == 0, report
    graph = SeamGraph(body)
    mirror, _ = mirror_edges(graph)
    assert mirror is not None, "la malla de prueba debería ser simétrica"
    seams = set(uv.uv_seam_edges(body))
    asymmetric = [e for e in seams if mirror[e] is not None and mirror[e] not in seams]
    assert not asymmetric, "{} costuras sin su reflejo".format(len(asymmetric))
    ups = uv.shell_up_directions(body)
    biggest = max(abs(du) + abs(dv) for du, dv in ups.values())
    for shell, (du, dv) in ups.items():
        # las tapas horizontales no tienen "arriba": su dirección es ruido numérico
        if abs(du) + abs(dv) < 1e-6 * biggest:
            continue
        assert not (dv < 0 and abs(dv) > abs(du)), "isla {} boca abajo".format(shell)


def uv_arrays(mesh):
    """(puntos, caras, puntos UV, caras UV) de la malla, como los espera mbl_engine.uv_quality."""
    points, faces = retopo.mesh_arrays(mesh)
    fn = om.MFnMesh(uv.dag_path(uv.mesh_shape(mesh)))
    us, vs = fn.getUVs()
    uv_counts, uv_ids = fn.getAssignedUVs()
    uv_faces, start = [], 0
    for count in uv_counts:
        uv_faces.append(list(uv_ids[start:start + count]))
        start += count
    return points, faces, np.column_stack([us, vs]), uv_faces


@test
def test_config_uv_padding_automatic():
    """El padding por defecto es automático (0): 1/256 del lado de la textura. Quien tenía el 16 antiguo pasa a
    automático y quien eligió otro número lo conserva."""
    assert config.DEFAULTS["uv_padding_px"] == 0 and config.SETTINGS_VERSION >= 8
    for size, expected in ((256, 2), (512, 2), (1024, 4), (2048, 8), (4096, 16), (8192, 32)):
        assert config.padding_px({"uv_map_size": size, "uv_padding_px": 0}) == expected, size
    assert config.padding_px({"uv_map_size": 8192, "uv_padding_px": 5}) == 5
    path = os.path.join(tempfile.mkdtemp(), "settings.json")

    def load_with(padding, version=None):
        data = '"uv_padding_px": {}, "retopo_target_faces": 777'.format(padding)
        if version is not None:
            data = '"_version": {}, '.format(version) + data
        with open(path, "w") as handle:
            handle.write("{" + data + "}")
        return config.load(path)

    for version in (None, 2, 6, 7):
        assert load_with(16, version)["uv_padding_px"] == 0, version
        assert load_with(24, version)["uv_padding_px"] == 24, version
        assert load_with(24, version)["retopo_target_faces"] == 777, version
    # con la versión nueva, 16 es una elección del usuario
    assert load_with(16, config.SETTINGS_VERSION)["uv_padding_px"] == 16
    config.save(load_with(16, 7), path)
    assert config.load(path)["uv_padding_px"] == 0


@test
def test_uv_padding_follows_resolution():
    """Con el padding automático, las islas distintas quedan al menos a 1/256 del lado de la textura (8, 16 y 32 px a
    2048, 4096 y 8192, con 1 px de tolerancia) y a 1/8 de la resolución (mipmap) siguen sin tocarse. Un valor
    manual (24 px a 2048) se respeta."""
    from mbl_engine import uv_quality
    for size, manual in ((2048, 0), (4096, 0), (8192, 0), (2048, 24)):
        pieces = [cmds.polySphere(radius=1.2, subdivisionsX=12, subdivisionsY=8, constructionHistory=False)[0],
                  cmds.polyCube(width=2, height=3, depth=1, constructionHistory=False)[0],
                  cmds.polyCylinder(radius=0.8, height=3, subdivisionsX=12, constructionHistory=False)[0]]
        for i, piece in enumerate(pieces):
            cmds.move(i * 4.0, 0, 0, piece)
        mesh = cmds.ls(cmds.polyUnite(pieces, constructionHistory=False, name="padding{}_{}".format(size, manual))[0],
                       long=True)[0]
        settings = base_settings(uv_map_size=size, uv_padding_px=manual)
        pad = config.padding_px(settings)
        assert pad == (manual or size // 256), (size, pad)
        report = uv.unwrap(mesh, settings, quiet_log)
        assert report["overlapping_faces"] == 0 and report["flipped_faces"] == 0, (size, report)
        margin = uv_quality.island_margin(*uv_arrays(mesh)[2:])
        assert margin is not None, "la malla de prueba debería tener varias islas"
        pixels = margin * size
        print("    {} px: padding {} px, margen mínimo {:.2f} px ({:.2f} px a 1/8)".format(size, pad, pixels, pixels / 8))
        assert pixels >= pad - 1.0, "{}: margen {:.2f} px, se pedían {} px".format(size, pixels, pad)
        assert pixels / 8.0 >= 1.0, "{}: a 1/8 de la resolución el margen es {:.2f} px".format(size, pixels / 8)


def make_material_sphere(name, octants=False):
    """Esfera con tres materiales en bandas horizontales o, con `octants`, con ocho materiales (uno por octante)."""
    mesh = cmds.ls(cmds.polySphere(name=name, radius=2, subdivisionsX=24, subdivisionsY=18,
                                   constructionHistory=False)[0], long=True)[0]
    points, faces = retopo.mesh_arrays(mesh)
    centers = np.array([points[face].mean(axis=0) for face in faces])
    if octants:
        label = (centers[:, 0] > 0) * 1 + (centers[:, 1] > 0) * 2 + (centers[:, 2] > 0) * 4
    else:
        label = np.minimum((np.argsort(np.argsort(centers[:, 1])) * 3) // len(faces), 2)
    for i in np.unique(label):
        shader = cmds.shadingNode("lambert", asShader=True, name="{}Mat{}".format(name, i))
        group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=shader + "SG")
        cmds.connectAttr(shader + ".outColor", group + ".surfaceShader", force=True)
        cmds.sets(uv.components(mesh, "f", np.flatnonzero(label == i).tolist()), edit=True, forceElement=group)
    return mesh


@test
def test_uv_material_borders_are_seams():
    """Los bordes entre materiales distintos son costura (cada material con sus islas), sin solapes ni espejos: esfera
    de tres bandas con el estilo de costuras normal y con el escondido, y esfera de ocho octantes (donde el optimizador
    de costuras quitaba el 27 % de los bordes si no los trataba como fijos). Con un solo material las UVs salen
    igual que sin la opción."""
    from mbl_retopo_uv.uv_seams import SeamGraph
    for style, octants in (("resolution", False), ("hidden", False), ("resolution", True)):
        mesh = make_material_sphere("mat_{}_{}".format(style, int(octants)), octants)
        borders = uv.material_edges(mesh)
        assert borders, "la esfera de tres materiales debería tener bordes de material"
        length = SeamGraph(mesh).length
        total = float(sum(length[e] for e in borders))

        def covered(settings):
            report = uv.unwrap(mesh, settings, quiet_log)
            seams = set(uv.uv_seam_edges(mesh))
            return report, sum(length[e] for e in borders if e in seams) / total

        report, share = covered(base_settings(uv_seam_style=style))
        print("    {}{}: {} aristas de borde, {:.1f}% de su longitud es costura, {} islas".format(
            style, " (octantes)" if octants else "", len(borders), 100.0 * share, report["shells"]))
        assert share >= 0.95, (style, share)
        assert_good_uvs(mesh)
        # ninguna isla mezcla dos materiales
        _, per_face = om.MFnMesh(uv.dag_path(uv.mesh_shape(mesh))).getConnectedShaders(0)
        owners = {}
        for face, shell, _ in uv._face_shell_areas(mesh):
            owners.setdefault(shell, set()).add(per_face[face])
        assert all(len(m) == 1 for m in owners.values()), owners
        # sin la opción, los bordes de material no son costura (la prueba no pasaría por casualidad)
        _, off = covered(base_settings(uv_seam_style=style, uv_material_seams=False))
        assert off < 0.95, off

    def uvs(mesh, settings):
        uv.unwrap(mesh, settings, quiet_log)
        fn = om.MFnMesh(uv.dag_path(uv.mesh_shape(mesh)))
        us, vs = fn.getUVs()
        return np.array(us), np.array(vs), np.array(fn.getAssignedUVs()[1])

    plain = cmds.ls(cmds.polySphere(name="onePlain", radius=2, subdivisionsX=24, subdivisionsY=18,
                                    constructionHistory=False)[0], long=True)[0]
    assert uv.material_edges(plain) == []
    with_option = uvs(plain, base_settings())
    without = uvs(plain, base_settings(uv_material_seams=False))
    for a, b in zip(with_option, without):
        assert a.shape == b.shape and np.array_equal(a, b), "un solo material: las UVs deben salir iguales"


@test
def test_retopo_result_is_manifold():
    """Si el resultado del retopo trae no-manifold, se repara y las UVs funcionan sin limpiar a mano."""
    points = [om.MPoint(*p) for p in [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, -1, 0),
                                      (1, -1, 0), (0, 0, 1), (1, 0, 1)]]
    fn = om.MFnMesh()
    fn.create(points, [4, 4, 4], [0, 1, 3, 2, 0, 4, 5, 1, 0, 1, 7, 6])
    bad = cmds.ls(cmds.listRelatives(fn.fullPathName(), parent=True, fullPath=True), long=True)[0]
    # siempre con el sustituto: lo que se prueba es la reparación posterior, no Instant Meshes
    settings = base_settings(retopo_reproject=False, instant_meshes_path=fake_instant_meshes())
    outcome = pipeline.run([bad], ["retopo", "uv"], settings, quiet_log)
    assert not outcome["errors"], outcome["errors"]
    assert cleanup.nonmanifold_counts(outcome["results"][0]) == (0, 0)


@test
def test_make_unfoldable_fixes_what_maya_does_not_flag():
    """Una aleta pegada a una arista (criatura y midnight con polyRetopo) y un quad con la diagonal repetida (midnight
    con Maya y con el Motor MBL) los rechaza Unfold3D; make_unfoldable los deja arreglados y las UVs salen."""
    from mbl_retopo_uv import meshbuild
    from mbl_retopo_uv.common import MBLError
    # aleta: dos triángulos con los mismos vértices y la orientación contraria sobre la arista de un quad de una esfera
    sphere = cmds.polySphere(subdivisionsX=12, subdivisionsY=8, constructionHistory=False)[0]
    points, faces = retopo.mesh_arrays(sphere)
    quad = next(f for f in faces if len(f) == 4)
    tip = 1.1 * (points[quad[0]] + points[quad[1]]) / 2
    finned = meshbuild.create_mesh(np.vstack([points, tip]), faces + [[quad[0], quad[1], len(points)],
                                                                      [quad[1], quad[0], len(points)]], "conAleta")
    assert sum(cleanup.nonmanifold_counts(finned)) > 0
    fixed = retopo.make_unfoldable(finned)
    assert cleanup.nonmanifold_counts(fixed) == (0, 0) and cmds.polyEvaluate(fixed, face=True) == len(faces)
    # quad con la diagonal repetida: maya no lo marca y Unfold3D falla con "Mesh has non-manifold UVs"
    corners = [(-6.852, -3.066, -5.872), (-7.089, -2.727, -4.985), (-5.174, -1.223, -5.69), (-5.343, -2.697, -7.536),
               (-5.872, -2.266, -8.828), (-6.634, -3.769, -7.311), (-7.906, -4.691, -6.327), (-8.574, -3.001, -6.942)]
    patch = meshbuild.create_mesh(corners, [[0, 1, 2, 3], [3, 5, 6, 0], [0, 5, 7, 1], [3, 4, 7, 5]], "conDiagonal")
    assert cleanup.nonmanifold_counts(patch) == (0, 0)
    try:
        uv.unwrap(patch, base_settings(), quiet_log)
        raise AssertionError("Unfold3D debía rechazar el quad con la diagonal repetida")
    except MBLError as exc:
        assert "non-manifold UVs" in str(exc), exc
    patch = retopo.make_unfoldable(patch)
    assert cmds.polyEvaluate(patch, face=True) == 5
    uv.unwrap(patch, base_settings(), quiet_log)


@test
def test_diagnostics():
    plane = cmds.polyPlane(subdivisionsX=4, subdivisionsY=4, constructionHistory=False)[0]
    stats = diagnostics.topology_stats(plane)
    assert stats["faces"] == 16 and stats["quad_percent"] == 100.0 and stats["pole_count"] == 0, stats
    cube = cmds.polyCube(constructionHistory=False)[0]
    stats = diagnostics.topology_stats(cube)
    assert len(stats["poles"][3]) == 8 and stats["pole_count"] == 8, stats
    sphere = cmds.polySphere(constructionHistory=False)[0]
    stats = diagnostics.topology_stats(sphere)
    assert len(stats["poles"][6]) == 2, stats

    # copia exacta con transformaciones sin congelar: error 0
    a = cmds.ls(cmds.polySphere(subdivisionsX=40, subdivisionsY=30, constructionHistory=False)[0], long=True)[0]
    cmds.move(5, 1, 0, a)
    cmds.scale(2, 2, 2, a)
    b = cmds.ls(cmds.duplicate(a)[0], long=True)[0]
    error = diagnostics.surface_error(a, b)
    assert error["max"] < 1e-4, error
    # esferas de radio 1 y 1,1: error medio cercano a 0,1
    c = cmds.ls(cmds.polySphere(radius=1.0, subdivisionsX=80, subdivisionsY=60, constructionHistory=False)[0], long=True)[0]
    d = cmds.ls(cmds.polySphere(radius=1.1, subdivisionsX=80, subdivisionsY=60, constructionHistory=False)[0], long=True)[0]
    error = diagnostics.surface_error(c, d)
    assert 0.09 < error["mean"] < 0.11 and error["max"] < 0.12, error

    # enlace retopo -> original, colores y limpieza
    diagnostics.link_source(d, c)
    assert diagnostics.find_source(d) == c
    report = diagnostics.diagnose(cube, colorize=True, log=quiet_log)
    assert diagnostics.POLE_COLOR_SET in cmds.polyColorSet(cube, query=True, allColorSets=True)
    assert diagnostics.clear_pole_colors(cube)
    assert not cmds.polyColorSet(cube, query=True, allColorSets=True)
    assert report["pole_count"] == 8


@test
def test_redo_after_undo_keeps_the_scene():
    """Ctrl+Z y Ctrl+Y alrededor de un retopo: vuelve la misma malla (antes Maya se cerraba al rehacer)."""
    mesh = make_dense_mesh()
    settings = base_settings(retopo_engine="maya", retopo_target_faces=800)
    cmds.undoInfo(state=True)
    cmds.flushUndo()
    meshes_before = sorted(cmds.ls(type="mesh", long=True))
    outcome = pipeline.run([mesh], ["retopo"], settings, lambda m, level="info": None)
    assert not outcome["errors"], outcome["errors"]
    meshes_after = sorted(cmds.ls(type="mesh", long=True))
    for _ in range(2):
        cmds.undo()
        assert sorted(cmds.ls(type="mesh", long=True)) == meshes_before, "quedaron mallas tras deshacer"
        cmds.redo()
        assert sorted(cmds.ls(type="mesh", long=True)) == meshes_after, "no volvió la malla al rehacer"
    result = outcome["results"][0]
    assert cmds.objExists(result) and cmds.getAttr(mesh + ".visibility") is False
    assert cmds.listConnections(result, type="shadingEngine") or cmds.listConnections(cmds.listRelatives(result, shapes=True)[0], type="shadingEngine")


@test
def test_undo_restores_scene():
    cube = cmds.ls(cmds.polyCube(constructionHistory=False)[0], long=True)[0]
    cmds.move(1, 1, 1, cube)
    uvs_before = cmds.polyEvaluate(cube, uvcoord=True)
    cmds.undoInfo(state=True)
    cmds.flushUndo()
    pipeline.run([cube], ["cleanup", "uv"], base_settings(), quiet_log)
    assert cmds.getAttr(cube + ".translate")[0] == (0.0, 0.0, 0.0)
    cmds.undo()
    assert cmds.getAttr(cube + ".translate")[0] == (1.0, 1.0, 1.0), "un solo Ctrl+Z no lo deshizo todo"
    assert cmds.polyEvaluate(cube, uvcoord=True) == uvs_before

    # proceso completo con el motor de Maya (limpieza de polos, relajación y mejor empaquetado incluidos)
    sphere = cmds.ls(cmds.polySphere(subdivisionsX=30, subdivisionsY=20, constructionHistory=False)[0], long=True)[0]
    meshes_before = sorted(cmds.ls(type="mesh", long=True))
    cmds.flushUndo()
    outcome = pipeline.run([sphere], ["cleanup", "retopo", "uv"],
                           base_settings(retopo_engine="maya", retopo_target_faces=500), quiet_log)
    assert not outcome["errors"], outcome["errors"]
    assert sorted(cmds.ls(type="mesh", long=True)) != meshes_before
    cmds.undo()
    assert sorted(cmds.ls(type="mesh", long=True)) == meshes_before, "quedaron mallas tras deshacer"
    assert cmds.getAttr(sphere + ".visibility"), "el original sigue oculto tras deshacer"


@test
def test_errors_are_readable():
    cube = cmds.ls(cmds.polyCube(constructionHistory=False)[0], long=True)[0]
    sphere = cmds.ls(cmds.polySphere(constructionHistory=False)[0], long=True)[0]

    missing = base_settings(instant_meshes_path=os.path.join(tempfile.mkdtemp(), "no_existe.exe"))
    if config.find_instant_meshes(missing) is None:
        outcome = pipeline.run([cube], ["retopo"], missing, quiet_log)
        assert outcome["errors"] and "Instant Meshes" in outcome["errors"][0][1], outcome

    os.environ["MBL_FAKE_IM_FAIL"] = "1"
    try:
        settings = base_settings(instant_meshes_path=fake_instant_meshes())
        try:
            retopo.retopo(cube, settings, quiet_log)
        except RetopoError as exc:
            assert "falló" in str(exc) and "fallo simulado" in str(exc), exc
        else:
            raise AssertionError("no se detectó el fallo de Instant Meshes")
        # una malla que falla no impide procesar las demás, y el undo chunk queda cerrado
        outcome = pipeline.run([cube, sphere], ["retopo"], settings, quiet_log)
        assert len(outcome["errors"]) == 2
    finally:
        del os.environ["MBL_FAKE_IM_FAIL"]
    outcome = pipeline.run([sphere], ["uv"], base_settings(), quiet_log)
    assert not outcome["errors"]
    cmds.undo()


@test
def test_installer_keeps_existing_usersetup():
    sys.path.insert(0, ROOT)
    import install
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "userSetup.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("print('lo que ya tenía el usuario')")
    install.write_user_setup(folder, ROOT)
    install.write_user_setup(folder, ROOT)
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    assert content.startswith("print('lo que ya tenía el usuario')"), content
    assert content.count(install.BEGIN) == 1 and content.count(install.END) == 1, content
    compile(content, path, "exec")
    # al desinstalar, lo que había antes se queda tal cual
    assert install.remove_user_setup_block(folder)
    with open(path, "r", encoding="utf-8") as handle:
        assert handle.read().strip() == "print('lo que ya tenía el usuario')"


UI_CHECK = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from PySide6 import QtWidgets
app = QtWidgets.QApplication([])
from mbl_retopo_uv import config, ui
window = ui.RetopoUVWindow(parent=None, settings_file=sys.argv[2])
window.apply_settings(dict(config.DEFAULTS, uv_udim=True, retopo_target_faces=777, uv_map_size=8192))
collected = window.collect_settings()
assert collected["uv_udim"] is True and collected["retopo_target_faces"] == 777, collected
assert collected["uv_map_size"] == 8192, collected
window.save_settings()
assert config.load(sys.argv[2])["retopo_target_faces"] == 777
# el desplegable de motores: solo los que existen, Maya primero y recomendado, y el Motor MBL como experimental con su aviso
# (un motor que ya no existe se queda en el de por defecto, Maya)
combo = window.widgets["retopo_engine"]
assert [combo.itemData(i) for i in range(combo.count())] == ["maya", "auto", "mbl", "instant_meshes"]
assert "recomendado" in combo.itemText(0) and "experimental" in combo.itemText(combo.findData("mbl")), combo.itemText(0)
assert "EXPERIMENTAL" in ui.ENGINE_NOTES["mbl"] and "Recomendado" in ui.ENGINE_NOTES["maya"]
window.apply_settings(dict(config.DEFAULTS))
assert window.collect_settings()["retopo_engine"] == "maya" and "Recomendado" in window.engine_note.text()
for engine in ("auto", "mbl", "instant_meshes"):
    window.apply_settings(dict(config.DEFAULTS, retopo_engine=engine))
    assert window.collect_settings()["retopo_engine"] == engine
window.apply_settings(dict(config.DEFAULTS, retopo_engine="mbl"))
assert "EXPERIMENTAL" in window.engine_note.text()
window.apply_settings(dict(config.DEFAULTS, retopo_engine="quadwild"))
assert window.collect_settings()["retopo_engine"] == "maya"
# lo principal fuera de «Avanzado» y todo lo demás dentro, plegado; ninguna opción se quita
internal = {"uv_split_big_shells", "retopo_orient", "uv_rotate_step", "uv_pack_iterations", "uv_pre_rotate"}  # sin casilla en la ventana
assert set(window.widgets) == set(config.DEFAULTS) - internal, set(window.widgets) ^ (set(config.DEFAULTS) - internal)
assert not window.advanced_box.isChecked() and window.advanced_box.title() == "Avanzado"
main = ("retopo_engine", "retopo_target_faces", "uv_map_size")
for key, widget in window.widgets.items():
    assert window.advanced_box.isAncestorOf(widget) != (key in main), key
# Instant Meshes sin su programa: «Avanzado» se despliega para que se vea dónde indicar la ruta
real_find = config.find_instant_meshes
config.find_instant_meshes = lambda settings, platform=None: None
try:
    window.apply_settings(dict(config.DEFAULTS, retopo_engine="instant_meshes"))
    assert window.advanced_box.isChecked()
finally:
    config.find_instant_meshes = real_find
window.advanced_box.setChecked(False)
window.apply_settings(dict(config.DEFAULTS))
window.show_status({"|pieza": [{"step": "uv", "level": 2,
                                 "checks": [{"level": 2, "text": "3 caras solapadas", "advice": "Repite 'UVs'."}]}]})
top = window.status_tree.topLevelItem(0)
assert window.status_tree.topLevelItemCount() == 1 and "UVs arreglar" in top.text(0), top.text(0)
assert top.child(0).child(0).child(0).text(0).startswith("→ Repite")
print("UI_OK")
"""


@test
def test_ui_builds():
    """La ventana se construye y guarda ajustes (en un proceso aparte: maya.standalone no tiene QApplication)."""
    import subprocess
    script = os.path.join(tempfile.mkdtemp(), "ui_check.py")
    with open(script, "w", encoding="utf-8") as handle:
        handle.write(UI_CHECK)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, script, ROOT, os.path.join(tempfile.mkdtemp(), "s.json")],
                          capture_output=True, text=True, env=env, timeout=300)
    assert "UI_OK" in proc.stdout, proc.stdout + proc.stderr


# ---------------------------------------------------------------- piezas repetidas (B4)

def counting_retopo():
    """Cambia retopo.retopo por una versión que apunta cada malla que se rehace; devuelve (lista, función para restaurar)."""
    calls, real = [], retopo.retopo

    def counted(transform, *args, **kwargs):
        calls.append(transform)
        return real(transform, *args, **kwargs)
    retopo.retopo = counted
    return calls, lambda: setattr(retopo, "retopo", real)


def uv_arrays(mesh):
    fn = om.MFnMesh(retopo.dag_path(retopo.mesh_shape(mesh)))
    us, vs = fn.getUVs()
    counts, ids = fn.getAssignedUVs()
    return np.column_stack([us, vs]), list(counts), list(ids)


def outward_fraction(mesh):
    """Fracción de caras cuya normal mira hacia fuera del centro (1 en una esfera bien orientada). En el espacio del
    objeto: con escala negativa Maya da la vuelta a las caras al dibujarlas, y es el sentido en el objeto el que cuenta."""
    path = retopo.dag_path(retopo.mesh_shape(mesh))
    points = np.array([(p.x, p.y, p.z) for p in om.MFnMesh(path).getPoints(om.MSpace.kObject)])
    centre = points.mean(axis=0)
    it, outward, total = om.MItMeshPolygon(path), 0, 0
    while not it.isDone():
        normal = it.getNormal(om.MSpace.kObject)
        c = it.center(om.MSpace.kObject)
        outward += (normal.x * (c.x - centre[0]) + normal.y * (c.y - centre[1]) + normal.z * (c.z - centre[2])) > 0
        total += 1
        it.next()
    return outward / float(total)


@test
def test_duplicates_are_retopologized_once_and_copied():
    """Cinco copias del mismo modelo (con dos materiales, grupo y pivote): una movida, otra en otro grupo con otra
    transformación, otra con otro material y otra congelada y girada. El retopo y las UVs se hacen una vez; cada copia sale
    con las mismas caras y UVs, sobre su transformación, con su material y enlazada a su original; un Ctrl+Z lo deshace todo."""
    original = make_dressed_model()
    moved = cmds.ls(cmds.duplicate(original, name="vestida_b")[0], long=True)[0]
    cmds.xform(moved, translation=(-6, 2, 1))
    other_group = cmds.group(empty=True, name="otroGrupo")
    cmds.xform(other_group, translation=(0, 0, 25), rotation=(10, 120, 0))
    reparented = cmds.ls(cmds.parent(cmds.duplicate(original, name="vestida_c")[0], other_group), long=True)[0]
    cmds.xform(reparented, rotation=(70, 5, 33))
    painted = cmds.ls(cmds.duplicate(original, name="vestida_d")[0], long=True)[0]
    cmds.sets(painted, forceElement="azulSG")                      # un solo material, el azul
    frozen = cmds.ls(cmds.duplicate(original, name="vestida_e")[0], long=True)[0]
    cmds.xform(frozen, worldSpace=True, rotation=(30, 200, -40), translation=(5, 5, 5))
    cmds.makeIdentity(frozen, apply=True, translate=True, rotate=True, scale=True)
    sources = [original, moved, reparented, painted, frozen]

    cmds.undoInfo(state=True)
    cmds.flushUndo()
    calls, restore = counting_retopo()
    try:
        outcome = pipeline.run(sources, ["retopo", "uv"], base_settings(retopo_engine="maya", retopo_target_faces=300),
                               quiet_log)
    finally:
        restore()
    assert not outcome["errors"], outcome["errors"]
    assert calls == [original], "el retopo se repitió en las copias: {}".format(calls)
    results = outcome["results"]
    assert len(results) == 5 and len(set(results)) == 5, results
    first = results[0]
    faces = retopo.mesh_arrays(first)[1]
    uvs = uv_arrays(first)
    for source, result in zip(sources, results):
        assert diagnostics.find_source(result) == source and not cmds.getAttr(source + ".visibility"), result
        assert result.split("|")[-1].startswith(source.split("|")[-1] + "_retopo"), result
        assert_same_placement(source, result)
        assert retopo.mesh_arrays(result)[1] == faces, "otras caras en " + result
        other = uv_arrays(result)
        assert np.array_equal(other[0], uvs[0]) and other[1:] == uvs[1:], "otras UVs en " + result
        assert uv.check(result)["overlapping_faces"] == 0, result
        assert outward_fraction(result) > 0.99, result
    # cada copia con sus materiales: las dos zonas en las que tienen dos, solo el azul en la pintada
    for source, result in zip(sources, results):
        zones = material_zones(result, source)
        if source == painted:
            assert zones["rojoSG"][0] == 0 and zones["azulSG"][0] > 100, zones
        elif source == frozen:
            # congelada y girada: "arriba" y "abajo" ya no son los ejes locales del original, solo se miran las cuentas
            assert zones["rojoSG"][0] > 20 and zones["azulSG"][0] > 20, zones
        else:
            for name, (count, inside) in zones.items():
                assert count > 20 and inside > 0.95, (result, name, count, inside)


@test
def test_duplicates_undo_and_redo_in_one_step():
    """Con copias (en otro grupo y congelada, por los dos caminos de colocación), un solo Ctrl+Z deja la escena como estaba
    y Rehacer devuelve el mismo resultado."""
    original = make_dressed_model()
    moved = cmds.ls(cmds.duplicate(original, name="vestida_b")[0], long=True)[0]
    cmds.xform(moved, translation=(-6, 2, 1))
    frozen = cmds.ls(cmds.duplicate(original, name="vestida_e")[0], long=True)[0]
    cmds.xform(frozen, worldSpace=True, rotation=(30, 200, -40), translation=(5, 5, 5))
    cmds.makeIdentity(frozen, apply=True, translate=True, rotate=True, scale=True)
    sources = [original, moved, frozen]
    cmds.undoInfo(state=True)
    cmds.flushUndo()
    outcome = pipeline.run(sources, ["retopo"], base_settings(retopo_engine="maya", retopo_target_faces=300), quiet_log)
    assert not outcome["errors"], outcome["errors"]
    before = [(r, retopo.mesh_arrays(r)) for r in outcome["results"]]
    assert len(before) == 3
    cmds.undo()
    assert not cmds.ls("*_retopo*"), "quedaron copias tras deshacer: {}".format(cmds.ls("*_retopo*"))
    assert all(cmds.getAttr(s + ".visibility") for s in sources)
    cmds.redo()
    for result, (points, faces) in before:
        assert cmds.objExists(result), result
        again = retopo.mesh_arrays(result)
        assert again[1] == faces and np.allclose(again[0], points, atol=1e-6), result


@test
def test_duplicates_deformed_copy_is_its_own_group():
    """Una copia con un vértice desplazado un poco no se agrupa con las demás (se rehace entera); con la opción apagada
    ninguna se copia; y sin retopo entre los pasos no se agrupa nada."""
    spheres = []
    for i in range(4):
        sphere = cmds.polySphere(name="esfera{}".format(i), radius=1.0, subdivisionsX=20, subdivisionsY=14,
                                 constructionHistory=False)[0]
        cmds.xform(sphere, translation=(4 * i, 0, 0), rotation=(0, 15 * i, 0))
        spheres.append(cmds.ls(sphere, long=True)[0])
    cmds.xform(spheres[2] + ".vtx[40]", relative=True, translation=(0.15, 0, 0))     # 7,5 % del diámetro
    for reuse, expected in ((True, [spheres[0], spheres[2]]), (False, spheres)):
        calls, restore = counting_retopo()
        try:
            outcome = pipeline.run(spheres, ["retopo"], base_settings(retopo_engine="maya", retopo_target_faces=200,
                                                                       pipeline_reuse_duplicates=reuse), quiet_log)
        finally:
            restore()
        assert not outcome["errors"], outcome["errors"]
        assert calls == expected, (reuse, calls)
        assert len(outcome["results"]) == 4
        for source, result in zip(spheres, outcome["results"]):
            assert_same_placement(source, result)
    assert pipeline.find_copies(spheres, ["uv"], base_settings()) == {}
    assert sorted(pipeline.find_copies(spheres, ["retopo", "uv"], base_settings())) == [spheres[1], spheres[3]]


@test
def test_duplicates_mirrored_copies_keep_their_normals():
    """Dos esferas con escala negativa (espejo) y otra normal: las dos reflejadas son la misma pieza y salen bien orientadas;
    la normal no se agrupa con ellas (el espejo invierte el sentido de las caras)."""
    sources = []
    for i, scale in enumerate(((-1, 1, 1), (-1, 1, 1), (1, 1, 1))):
        sphere = cmds.polySphere(name="espejo{}".format(i), radius=1.0, subdivisionsX=20, subdivisionsY=14,
                                 constructionHistory=False)[0]
        cmds.xform(sphere, translation=(5 * i, 0, 0), scale=scale)
        sources.append(cmds.ls(sphere, long=True)[0])
    # y un duplicado del espejo con el giro congelado: sus puntos son otros en el espacio de objeto (camino por el mundo)
    turned = cmds.ls(cmds.duplicate(sources[0], name="espejo3")[0], long=True)[0]
    cmds.xform(turned, translation=(15, 0, 0), rotation=(0, 40, 25))
    cmds.makeIdentity(turned, apply=True, rotate=True)
    assert cmds.getAttr(turned + ".scale")[0] == (-1.0, 1.0, 1.0)
    sources.append(turned)
    calls, restore = counting_retopo()
    try:
        outcome = pipeline.run(sources, ["retopo"], base_settings(retopo_engine="maya", retopo_target_faces=200), quiet_log)
    finally:
        restore()
    assert not outcome["errors"], outcome["errors"]
    assert calls == [sources[0], sources[2]], calls
    for source, result in zip(sources, outcome["results"]):
        assert outward_fraction(result) > 0.99, result
        assert_same_placement(source, result)


def make_screws(count):
    """`count` toros idénticos (copias de uno), cada uno movido y girado de otra forma."""
    rng = np.random.RandomState(1)
    base = cmds.polyTorus(name="tornillo", radius=2, sectionRadius=0.7, subdivisionsX=24, subdivisionsY=12,
                          constructionHistory=False)[0]
    sources = [cmds.ls(base, long=True)[0]]
    for i in range(1, count):
        copy = cmds.duplicate(base, name="tornillo{}".format(i))[0]
        cmds.xform(copy, translation=rng.uniform(-20, 20, 3).tolist(), rotation=rng.uniform(0, 360, 3).tolist())
        sources.append(cmds.ls(copy, long=True)[0])
    return sources


@test
def test_duplicates_twenty_copies_cost_about_one():
    """20 copias de un toro: una sola pasada de retopo y UVs. El tiempo de 1 pieza y el de 20 se anotan en la salida
    (objetivo: 20 copias en 1 + 10 %); aquí se exige solo que cueste muy por debajo de repetirlo 20 veces."""
    settings = base_settings(retopo_engine="maya", retopo_target_faces=600)
    pipeline.run(make_screws(1), ["cleanup", "retopo", "uv"], settings, quiet_log)           # calentamiento
    cmds.file(new=True, force=True)
    began = time.time()
    pipeline.run(make_screws(1), ["cleanup", "retopo", "uv"], settings, quiet_log)
    one = time.time() - began
    cmds.file(new=True, force=True)
    sources = make_screws(20)
    calls, restore = counting_retopo()
    began = time.time()
    try:
        outcome = pipeline.run(sources, ["cleanup", "retopo", "uv"], settings, quiet_log)
    finally:
        restore()
    twenty = time.time() - began
    print("    1 pieza {:.2f} s; 20 copias {:.2f} s ({:.2f}x)".format(one, twenty, twenty / one))
    assert not outcome["errors"] and len(outcome["results"]) == 20, outcome["errors"]
    assert len(calls) == 1, calls
    assert twenty < 2.0 * one, (one, twenty)


@test
def test_config_reuse_duplicates_and_common_texel_defaults():
    """Los dos ajustes nuevos: copiar piezas repetidas encendido, densidad común apagada; un JSON de antes (sin ellos) los
    carga con esos valores y uno que los trae los respeta."""
    assert config.DEFAULTS["pipeline_reuse_duplicates"] is True and config.DEFAULTS["uv_common_texel"] is False
    path = os.path.join(tempfile.mkdtemp(), "settings.json")
    with open(path, "w") as handle:
        handle.write('{"_version": 8, "retopo_target_faces": 777}')
    loaded = config.load(path)
    assert loaded["pipeline_reuse_duplicates"] is True and loaded["uv_common_texel"] is False
    config.save(dict(config.DEFAULTS, pipeline_reuse_duplicates=False, uv_common_texel=True), path)
    loaded = config.load(path)
    assert loaded["pipeline_reuse_duplicates"] is False and loaded["uv_common_texel"] is True


@test
def test_optional_fbx():
    path = os.environ.get("MBL_TEST_FBX")
    if not path:
        print("    MBL_TEST_FBX no definido: se omite")
        return
    cmds.loadPlugin("fbxmaya", quiet=True)
    nodes = cmds.file(path, i=True, type="FBX", returnNewNodes=True, ignoreVersion=True) or []
    meshes = sorted({cmds.listRelatives(n, parent=True, fullPath=True)[0]
                     for n in cmds.ls(nodes, type="mesh", long=True, noIntermediate=True)})
    assert meshes, "el FBX no tiene mallas"
    outcome = pipeline.run(meshes, ["cleanup", "retopo", "uv"], base_settings(uv_respect_existing=False), quiet_log)
    assert not outcome["errors"], outcome["errors"]
    for mesh in outcome["results"]:
        assert_good_uvs(mesh)


if __name__ == "__main__":
    # mayapy tests/run_tests.py [parte_del_nombre ...]: solo las pruebas cuyo nombre contenga alguna
    only = sys.argv[1:]
    for run_test in TESTS:
        if not only or any(word in run_test.__name__ for word in only):
            run_test()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n{} pruebas, {} correctas, {} fallidas".format(len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    for name in failed:
        print("  - " + name)
    maya.standalone.uninitialize()
    os._exit(1 if failed else 0)
