# -*- coding: utf-8 -*-
"""Pruebas del núcleo nativo (engine/mbl_core), sin Maya y con mallas pequeñas: se pueden pasar con el PC en uso.

    python tests/test_core.py [ruta de mbl_core.exe]
"""

import json
import math
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "engine", "build-core", "Release", "mbl_core.exe")


def write_obj(path, points, faces):
    with open(path, "w") as handle:
        for p in points:
            handle.write("v {} {} {}\n".format(*p))
        for f in faces:
            handle.write("f " + " ".join(str(i + 1) for i in f) + "\n")


def torus(major=2.0, minor=0.7, nu=48, nv=24):
    points = [((major + minor * math.cos(2 * math.pi * j / nv)) * math.cos(2 * math.pi * i / nu),
               (major + minor * math.cos(2 * math.pi * j / nv)) * math.sin(2 * math.pi * i / nu),
               minor * math.sin(2 * math.pi * j / nv)) for i in range(nu) for j in range(nv)]
    faces = [[i * nv + j, ((i + 1) % nu) * nv + j, ((i + 1) % nu) * nv + (j + 1) % nv, i * nv + (j + 1) % nv]
             for i in range(nu) for j in range(nv)]
    return points, faces


def grid_box(n=12, scale=(1.0, 1.0, 1.0)):
    """Cubo cerrado de quads, n por lado, con los vértices soldados."""
    index, points, faces = {}, [], []

    def vertex(p):
        if p not in index:
            index[p] = len(points)
            points.append(tuple(c * s / n for c, s in zip(p, scale)))
        return index[p]
    for axis in range(3):
        u, v = (axis + 1) % 3, (axis + 2) % 3
        for side in (0, n):
            for i in range(n):
                for j in range(n):
                    corners = []
                    for a, b in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)):
                        p = [0, 0, 0]
                        p[axis], p[u], p[v] = side, a, b
                        corners.append(vertex(tuple(p)))
                    faces.append(corners if side == n else corners[::-1])
    return points, faces


def uv_sphere(nu=48, nv=24):
    points = [(0.0, 0.0, 1.0)]
    for j in range(1, nv):
        t = math.pi * j / nv
        for i in range(nu):
            a = 2 * math.pi * i / nu
            points.append((math.sin(t) * math.cos(a), math.sin(t) * math.sin(a), math.cos(t)))
    points.append((0.0, 0.0, -1.0))
    last = len(points) - 1
    faces = [[0, 1 + i, 1 + (i + 1) % nu] for i in range(nu)]
    for j in range(nv - 2):
        for i in range(nu):
            a, b = 1 + j * nu + i, 1 + j * nu + (i + 1) % nu
            faces.append([a, a + nu, b + nu, b])
    faces += [[last, 1 + (nv - 2) * nu + (i + 1) % nu, 1 + (nv - 2) * nu + i] for i in range(nu)]
    return points, faces


def plane(n=30, bump=0.0):
    points = [(i / n, j / n, bump * math.sin(5.0 * i / n) * j / n) for i in range(n + 1) for j in range(n + 1)]
    faces = [[i * (n + 1) + j, (i + 1) * (n + 1) + j, (i + 1) * (n + 1) + j + 1, i * (n + 1) + j + 1]
             for i in range(n) for j in range(n)]
    return points, faces


def run(points, faces, *extra):
    work = tempfile.mkdtemp(prefix="mblcore")
    obj, out = os.path.join(work, "m.obj"), os.path.join(work, "r.json")
    write_obj(obj, points, faces)
    code = subprocess.call([EXE, obj, out] + list(extra))
    if code != 0:
        return code, None
    with open(out) as handle:
        return 0, json.load(handle)


def test_torus_needs_no_poles():
    """Poincaré-Hopf: χ = 0. Un toro admite un campo sin polos."""
    _, r = run(*torus())
    assert r["euler"] == 0 and r["pieces"] == 1 and r["border_edges"] == 0, r
    assert abs(r["index_sum"]) < 1e-6 and r["vertices_skipped"] == 0, r
    assert r["singular_total"] == 0, r


def test_sphere_index_sum():
    _, r = run(*uv_sphere())
    assert r["euler"] == 2 and abs(r["index_sum"] - 2.0) < 1e-6 and r["vertices_skipped"] == 0, r


def test_box_poles_at_corners():
    """Un cubo: los 12 cantos mandan y los 8 polos caen en las esquinas (índice +1/4 cada uno), ninguno más."""
    _, r = run(*grid_box())
    assert r["features"] == 12 * 12, r
    assert abs(r["index_sum"] - 2.0) < 1e-6, r
    assert r["feature_error_degrees"] < 1.0, r
    assert r["singular_by_quarters"] == {"1": 8}, r


def test_flat_plane_follows_border():
    """Plano abierto: las cruces siguen el borde y no hay giro."""
    _, r = run(*plane())
    assert r["border_edges"] == 4 * 30 and r["singular_total"] == 0, r
    assert r["mean_turn_degrees"] < 0.01 and r["feature_error_degrees"] < 0.01, r


def test_deterministic():
    points, faces = torus()
    _, a = run(points, faces)
    _, b = run(points, faces)
    assert a["hash"] == b["hash"], (a["hash"], b["hash"])


def test_scale_does_not_matter():
    """La misma forma en otra escala da el mismo campo (el análisis es relativo al tamaño)."""
    points, faces = grid_box()
    _, a = run(points, faces)
    _, b = run([tuple(c * 1000.0 for c in p) for p in points], faces)
    assert a["hash"] == b["hash"] and a["features"] == b["features"], (a, b)


def test_symmetry_detected():
    points, faces = uv_sphere()
    _, r = run(points, faces)
    assert min(r["mirror"]) > 0.99, r
    # se estira solo el lado +X: deja de ser simétrica en X, y sigue siéndolo en Y y Z
    bent = [(x * (1.0 + 0.5 * x) if x > 0 else x, y, z) for x, y, z in points]
    _, r = run(bent, faces)
    assert r["mirror"][0] < 0.8 and r["mirror"][1] > 0.99, r


def test_memory_limit_is_respected():
    """Con un tope imposible se niega y sale con un código de error, sin colgarse ni tumbar nada."""
    code, _ = run(*torus(), "--memory-mb", "1")
    assert code != 0, code


def test_stays_small():
    """El objetivo del motor: poco gasto. 20.000 triángulos en menos de 100 MB y 5 s, a un hilo."""
    points, faces = torus(nu=100, nv=100)
    _, r = run(points, faces)
    assert r["triangles"] == 20000 and r["peak_memory_mb"] < 100 and r["seconds"] < 5, r


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, func in tests:
        try:
            func()
            print("OK    ", name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("FALLO ", name, str(exc)[:600])
    print("{} pruebas, {} correctas, {} fallidas".format(len(tests), len(tests) - failed, failed))
    sys.exit(1 if failed else 0)
