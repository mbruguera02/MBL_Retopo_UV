# -*- coding: utf-8 -*-
"""Perfil del Motor MBL: dónde se va el tiempo de prepare() y de UNA variante (cProfile en el hilo principal).

    mayapy profile_engine.py <raiz_del_repo> <modelo> <caras> [variantes_a_perfilar]
"""
import cProfile
import io
import os
import pstats
import sys
import time

root, path, faces = sys.argv[1], sys.argv[2], int(sys.argv[3])
sys.path.insert(0, root)
from tests.bench import HeavyLock  # noqa: E402

_lock = HeavyLock().__enter__()      # una sola medición pesada a la vez en todo el PC
import atexit  # noqa: E402
atexit.register(lambda: _lock.__exit__(None, None, None))
import maya.standalone  # noqa: E402

maya.standalone.initialize(name="python")
import maya.cmds as cmds  # noqa: E402

from mbl_engine import retopo as core  # noqa: E402
from mbl_retopo_uv import config, retopo  # noqa: E402

cmds.file(new=True, force=True)
ext = os.path.splitext(path)[1].lower()
if ext == ".obj":
    cmds.loadPlugin("objExport", quiet=True)
    cmds.file(path, i=True, type="OBJ", ignoreVersion=True, options="mo=1")
else:
    cmds.loadPlugin("fbxmaya", quiet=True)
    cmds.file(path, i=True, type="FBX", ignoreVersion=True, options="v=0;")
shapes = cmds.ls(type="mesh", noIntermediate=True, long=True)
transform = max({cmds.listRelatives(s, parent=True, fullPath=True)[0] for s in shapes}, key=lambda t: cmds.polyEvaluate(t, face=True))
points, mesh_faces = retopo.mesh_arrays(transform)
executable = config.find_quadriflow()
print("modelo", os.path.basename(path), len(mesh_faces), "caras;", "objetivo", faces, flush=True)

plan = core.variant_plan(3, "quadriflow")
t0 = time.time()
pr = cProfile.Profile()
pr.enable()
# el plugin hace el modelo entero por defecto (retopo_symmetry "off"): con None un modelo simétrico se perfilaba por la mitad
shared = core.prepare(points, mesh_faces, faces, "quadriflow", False, 600, None, 0.01,
                      {"inflate": any(extra.get("inflate") for _, extra in plan)}, print)
pr.disable()
print("prepare: {:.1f} s".format(time.time() - t0))
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(14)
print(s.getvalue()[:3500])

factor, extra = plan[0]
t1 = time.time()
pr = cProfile.Profile()
pr.enable()
p, f, report = core.retopo(points, mesh_faces, int(round(faces * factor)), "quadriflow", executable, symmetric=False,
                           options=dict(prepared=shared, **extra), log=print)
pr.disable()
print("variante 1: {:.1f} s, {} caras".format(time.time() - t1, len(f)))
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(22)
print(s.getvalue()[:5500])
