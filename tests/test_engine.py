# -*- coding: utf-8 -*-
"""Pruebas del núcleo del Motor MBL (mbl_engine) sin Maya: solo Python y numpy.

    "C:\\Program Files\\Autodesk\\Maya2026\\bin\\mayapy.exe" tests\\test_engine.py [parte_del_nombre ...]

Sirve cualquier Python 3 con numpy (no se importa nada de Maya).
"""

import os
import sys
import tempfile
import time
import traceback

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbl_engine import analyze, cleanup, closest, decimate, fit, metrics, orient, quality, quadopt, repair, resources, retopo, sizefield, symmetry  # noqa: E402
from mbl_engine.mesh import Mesh  # noqa: E402

TESTS, RESULTS = [], []


def test(func):
    TESTS.append(func)
    return func


# ------------------------------------------------------------------ mallas de prueba

def grid(n, size=1.0, hole=None):
    """Rejilla n x n de quads en el plano XY. hole=(x0, x1, y0, y1): caras que se quitan."""
    points = [(x * size, y * size, 0.0) for y in range(n + 1) for x in range(n + 1)]
    faces = [[y * (n + 1) + x, y * (n + 1) + x + 1, (y + 1) * (n + 1) + x + 1, (y + 1) * (n + 1) + x]
             for y in range(n) for x in range(n)
             if not (hole and hole[0] <= x <= hole[1] and hole[2] <= y <= hole[3])]
    return Mesh(points, faces)


def cube():
    points = [(x, y, z) for z in (0, 1) for y in (0, 1) for x in (0, 1)]
    faces = [[0, 2, 3, 1], [4, 5, 7, 6], [0, 1, 5, 4], [2, 6, 7, 3], [0, 4, 6, 2], [1, 3, 7, 5]]
    return Mesh(points, faces)


def uv_sphere(radius=1.0, rings=12, segments=16, triangulate=True):
    points = [(0.0, radius, 0.0)]
    for r in range(1, rings):
        phi = np.pi * r / rings
        for s in range(segments):
            theta = 2 * np.pi * s / segments
            points.append((radius * np.sin(phi) * np.cos(theta), radius * np.cos(phi),
                           radius * np.sin(phi) * np.sin(theta)))
    points.append((0.0, -radius, 0.0))
    bottom = len(points) - 1

    def ring(r, s):
        return 1 + (r - 1) * segments + s % segments
    faces = []
    for s in range(segments):
        faces.append([0, ring(1, s + 1), ring(1, s)])
        faces.append([bottom, ring(rings - 1, s), ring(rings - 1, s + 1)])
        for r in range(1, rings - 1):
            quad = [ring(r, s), ring(r, s + 1), ring(r + 1, s + 1), ring(r + 1, s)]
            faces += [quad[:3], [quad[0], quad[2], quad[3]]] if triangulate else [quad]
    return Mesh(points, faces)


def tube(radius=1.0, height=4.0, rings=8, segments=12):
    """Cilindro de quads sin tapas (dos bordes abiertos)."""
    points = [(radius * np.cos(2 * np.pi * s / segments), height * r / rings, radius * np.sin(2 * np.pi * s / segments))
              for r in range(rings + 1) for s in range(segments)]
    faces = [[r * segments + s, r * segments + (s + 1) % segments,
              (r + 1) * segments + (s + 1) % segments, (r + 1) * segments + s]
             for r in range(rings) for s in range(segments)]
    return Mesh(points, faces)


# ------------------------------------------------------------------ malla

@test
def test_mesh_basics():
    """Aristas, bordes, valencias, áreas y normales."""
    c = cube()
    assert c.face_count == 6 and len(c.edges) == 12
    assert not len(c.boundary_edges) and not c.boundary_vertices.any()
    assert (c.valence == 3).all()
    assert abs(c.face_areas.sum() - 6.0) < 1e-12
    # normales hacia fuera: el centro de cada cara menos el del cubo apunta como su normal
    out = c.face_centers() - 0.5
    assert (np.einsum("ij,ij->i", out, c.face_normals) > 0).all()
    g = grid(4)
    assert len(g.boundary_loops()) == 1 and len(g.boundary_edges) == 16
    holed = grid(6, hole=(2, 3, 2, 3))
    assert len(holed.boundary_loops()) == 2
    # n-gon en abanico: un hexágono son 4 triángulos
    hexagon = Mesh([(np.cos(a), np.sin(a), 0) for a in np.linspace(0, 2 * np.pi, 7)[:-1]], [list(range(6))])
    tris, owner = hexagon.triangles()
    assert tris.shape == (4, 3) and (owner == 0).all()
    assert abs(sum(0.5 * np.linalg.norm(np.cross(hexagon.points[t[1]] - hexagon.points[t[0]],
                                                  hexagon.points[t[2]] - hexagon.points[t[0]])) for t in tris)
               - hexagon.face_areas[0]) < 1e-12


# ------------------------------------------------------------------ punto más cercano

@test
def test_closest_point_regions():
    """Punto más cercano a un triángulo en sus siete regiones (interior, aristas, vértices)."""
    a, b, c = np.array([[0.0, 0, 0]]), np.array([[1.0, 0, 0]]), np.array([[0.0, 1, 0]])
    cases = [((0.2, 0.2, 1.0), (0.2, 0.2, 0.0)),    # encima del interior
             ((-1.0, -1.0, 0.0), (0.0, 0.0, 0.0)),  # vértice a
             ((2.0, -0.5, 0.0), (1.0, 0.0, 0.0)),   # vértice b
             ((-0.5, 2.0, 0.0), (0.0, 1.0, 0.0)),   # vértice c
             ((0.5, -1.0, 0.0), (0.5, 0.0, 0.0)),   # arista ab
             ((-1.0, 0.5, 0.0), (0.0, 0.5, 0.0)),   # arista ac
             ((1.0, 1.0, 0.0), (0.5, 0.5, 0.0))]    # arista bc
    for p, expected in cases:
        got = closest.closest_on_triangles(np.array([p]), a, b, c)[0]
        assert np.allclose(got, expected), (p, got, expected)


@test
def test_triangle_grid_matches_brute_force():
    """La rejilla da exactamente la misma distancia que mirar todos los triángulos."""
    rng = np.random.default_rng(7)
    sphere = uv_sphere(2.0, rings=20, segments=24)
    tris = sphere.triangles()[0]
    grid_index = closest.TriangleGrid(sphere.points, tris)
    queries = np.concatenate([rng.normal(size=(150, 3)) * 1.5,      # dentro y alrededor
                              rng.normal(size=(50, 3)) * 20.0])     # lejos (anillos grandes)
    _, dist, _ = grid_index.closest(queries)
    a, b, c = (sphere.points[tris[:, k]] for k in range(3))
    for q, d in zip(queries, dist):
        qq = np.broadcast_to(q, (len(tris), 3))
        brute = np.linalg.norm(closest.closest_on_triangles(qq, a, b, c) - q, axis=1).min()
        assert abs(brute - d) < 1e-9, (q, brute, d)


# ------------------------------------------------------------------ métricas

@test
def test_topology_stats():
    """Regulares y polos con los mismos criterios que la versión de Maya."""
    g = metrics.topology_stats(grid(4))
    assert g["faces"] == 16 and g["quads"] == 16 and g["regular_percent"] > 99.9 - 4 * 100.0 / 25
    assert g["pole_count"] == 0   # las esquinas son de borde: no cuentan como polo
    c = metrics.topology_stats(cube())
    assert len(c["poles"][3]) == 8 and c["regular_percent"] == 0.0
    s = metrics.topology_stats(uv_sphere(triangulate=False))
    assert len(s["poles"][6]) == 2 and s["tris"] == 32   # los dos polos de la esfera, de 16 aristas


@test
def test_loop_stats():
    """Rejilla: 10 loops de 4 aristas. Tubo: los anillos son loops cerrados."""
    loops = metrics.loop_stats(grid(4))
    assert loops["loops"] == 10 and abs(loops["loop_mean"] - 4.0) < 1e-9, loops
    t = metrics.loop_stats(tube())
    # 9 anillos (incluidos los dos bordes) de 12 aristas cerrados, 12 columnas abiertas de 8
    assert abs(t["loop_closed_percent"] - 100.0 * 108 / (108 + 96)) < 1e-9, t


@test
def test_surface_error():
    """Misma malla: error 0. Copia desplazada d en la normal: error d."""
    plane = grid(10, 0.1)
    assert metrics.surface_error(plane, plane)["max"] < 1e-12
    lifted = Mesh(plane.points + (0, 0, 0.05), plane.faces())
    err = metrics.surface_error(plane, lifted)
    assert abs(err["mean"] - 0.05) < 1e-9 and abs(err["max"] - 0.05) < 1e-9, err
    # esfera gruesa frente a fina: error pequeño pero no nulo, y menor que la distancia entre radios
    fine, coarse = uv_sphere(1.0, 40, 48), uv_sphere(1.0, 10, 12)
    err = metrics.surface_error(fine, coarse)
    assert 0 < err["mean_percent"] < 5, err


# ------------------------------------------------------------------ limpieza

def bad_mesh():
    """3 quads compartiendo una arista (no-manifold) y un triángulo de área cero aparte."""
    points = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (0, -1, 0), (1, -1, 0), (0, 0, 1), (1, 0, 1),
              (3, 0, 0), (3, 0, 0), (3, 0, 0), (9, 9, 9)]
    faces = [[0, 1, 3, 2], [0, 4, 5, 1], [0, 1, 7, 6], [8, 9, 10]]
    return Mesh(points, faces)


@test
def test_cleanup_analyze():
    """Detecta los mismos problemas que polyInfo y el análisis del plugin."""
    counts = cleanup.count_problems(cleanup.analyze(bad_mesh()))
    assert counts["nonmanifold_edges"] == 1, counts
    assert counts["zero_area_faces"] == 1, counts
    assert counts["isolated_vertices"] == 1, counts     # el (9, 9, 9)
    clean = cleanup.count_problems(cleanup.analyze(cube()))
    assert not any(clean.values()), clean
    # pajarita: dos triángulos que solo se tocan en un vértice
    bowtie = Mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)], [[0, 1, 2], [0, 3, 4]])
    assert cleanup.count_problems(cleanup.analyze(bowtie))["nonmanifold_vertices"] == 1


@test
def test_merge_and_fix_nonmanifold():
    """Fusionar vértices duplicados cierra un cubo abierto; separar deja la malla sin no-manifold."""
    c = cube()
    # cada cara con sus propios vértices (como un cubo exportado sin soldar)
    points, faces = [], []
    for face in c.faces():
        faces.append(list(range(len(points), len(points) + len(face))))
        points += [tuple(c.points[v]) for v in face]
    welded_points, welded_faces, merged = cleanup.merge_vertices(points, faces, 1e-3)
    assert merged == 24 - 8, merged
    welded = Mesh(welded_points, welded_faces)
    assert not len(welded.boundary_edges) and int(welded.used_vertices.sum()) == 8
    mesh, report = cleanup.clean(bad_mesh().points, bad_mesh().faces())
    assert report["nonmanifold_split"] >= 1, report
    assert not len(cleanup.analyze_nonmanifold(mesh))


# ------------------------------------------------------------------ simetría

@test
def test_symmetry():
    """Detectar, cortar la mitad +X y reflejarla devuelve la misma superficie."""
    sphere = uv_sphere(2.0, 16, 24, triangulate=False)
    symmetric, mean, p95 = symmetry.is_symmetric(sphere)
    assert symmetric and mean < 0.01, (mean, p95)
    # solo el lado +X estirado en Y: ya no es simétrica
    squashed = Mesh(sphere.points * np.where(sphere.points[:, :1] > 0, (1.0, 1.5, 1.0), (1.0, 1.0, 1.0)),
                    sphere.faces())
    assert not symmetry.is_symmetric(squashed)[0]
    center = symmetry.plane_x(sphere.points)
    points, faces = symmetry.cut_half(sphere.points, sphere.faces(), center)
    half = Mesh(points, faces)
    used = half.points[half.used_vertices]
    assert used[:, 0].min() >= center - 1e-9
    assert abs(half.face_areas.sum() - 0.5 * sphere.face_areas.sum()) < 1e-6 * sphere.face_areas.sum()
    assert len(half.boundary_loops()) == 1     # el corte, sin grietas
    edge = symmetry.plane_edge(points, faces, center)
    whole_points, whole_faces, welded = symmetry.mirror_mesh(points, faces, center,
                                                             symmetry.WELD_EDGE_FRACTION * edge)
    whole = Mesh(whole_points, whole_faces)
    assert welded > 0 and not len(whole.boundary_edges)
    assert abs(whole.face_areas.sum() - sphere.face_areas.sum()) < 1e-6 * sphere.face_areas.sum()
    assert metrics.surface_error(sphere, whole)["max_percent"] < 1e-6


@test
def test_fill_new_holes():
    """Solo se cierran los agujeros que no estaban en el original, y con quads."""
    g = grid(6, hole=(2, 3, 2, 3))
    outer = [tuple(p) for p in g.points if p[0] in (0, 6) or p[1] in (0, 6)]
    points, faces, filled = repair.fill_new_holes(g.points, g.faces(), outer, 60)
    assert filled == 1 and all(len(f) == 4 for f in faces)
    assert not len(Mesh(points, faces).boundary_loops()) == 2


# ------------------------------------------------------------------ retopo

INSTANT_MESHES = os.path.join(ROOT, "mbl_retopo_uv", "bin", "Instant Meshes.exe")
QUADRIFLOW = os.path.join(ROOT, "engine", "build-quadriflow", "Release", "quadriflow.exe")


@test
def test_core_retopo():
    """Retopo completo del núcleo sobre una esfera: cerrada, toda de quads, simétrica y fiel."""
    sphere = uv_sphere(2.0, 40, 48)
    ran = 0
    for engine, executable in (("instant_meshes", INSTANT_MESHES), ("quadriflow", QUADRIFLOW)):
        if not os.path.isfile(executable):
            print("    (sin {}: se omite)".format(engine))
            continue
        # QuadriFlow se prueba aquí entero; la simetría con tapa tiene su propia prueba
        points, faces, report = retopo.retopo(sphere.points, sphere.faces(), 600, engine, executable,
                                              symmetric=False if engine == "quadriflow" else None)
        result = Mesh(points, faces)
        stats = metrics.topology_stats(result)
        assert not len(result.boundary_edges), (engine, "agujeros")
        assert stats["quad_percent"] > 95, (engine, stats["quad_percent"])
        assert 400 < stats["faces"] < 1000, (engine, stats["faces"])
        assert metrics.surface_error(sphere, result)["mean_percent"] < 0.2, engine
        # Instant Meshes hace la mitad y la refleja; QuadriFlow, el modelo entero
        assert report["symmetric"] == (engine == "instant_meshes"), (engine, report["symmetric"])
        ran += 1
    if ran and os.path.isfile(INSTANT_MESHES):
        # misma entrada, mismo resultado (determinista)
        a = retopo.retopo(sphere.points, sphere.faces(), 600, "instant_meshes", INSTANT_MESHES)
        b = retopo.retopo(sphere.points, sphere.faces(), 600, "instant_meshes", INSTANT_MESHES)
        assert a[1] == b[1] and np.allclose(a[0], b[0])



@test
def test_best_retopo():
    """Mejor de varias variantes: la elección respeta la ventana de caras y el tope de polos, y
    best_retopo devuelve la variante elegida con las cifras de todas."""
    variants = [
        {"faces_count": 5000, "poles": 200, "error": 0.30},   # dentro, pocos polos
        {"faces_count": 5200, "poles": 230, "error": 0.10},   # dentro, algo más de polos: gana
        {"faces_count": 4900, "poles": 400, "error": 0.05},   # demasiados polos
        {"faces_count": 9000, "poles": 150, "error": 0.01},   # fuera de la ventana de caras
    ]
    assert retopo.choose_variant(variants, 5000) == 1
    assert retopo.choose_variant(variants[3:], 5000) == 0     # si ninguna cae dentro, valen todas
    # el error máximo cuenta: un dedo perdido (5 %) pierde frente a un error medio algo mayor sin pérdidas
    lost_finger = [{"faces_count": 5000, "poles": 200, "error": 0.10, "error_max": 5.0},
                   {"faces_count": 5000, "poles": 200, "error": 0.11, "error_max": 1.0}]
    assert retopo.choose_variant(lost_finger, 5000) == 1
    # solo quads: la que deja triángulos no gana aunque tenga menos error
    with_tris = [{"faces_count": 5000, "poles": 200, "error": 0.05, "quad_percent": 97.0},
                 {"faces_count": 5000, "poles": 200, "error": 0.10, "quad_percent": 100.0}]
    assert retopo.choose_variant(with_tris, 5000) == 1
    # sin ninguna pura, gana la que deja menos triángulos si lo demás es parecido
    few_tris = [{"faces_count": 5000, "poles": 200, "error": 0.10, "quad_percent": 99.3},
                {"faces_count": 5000, "poles": 210, "error": 0.10, "quad_percent": 99.96}]
    assert retopo.choose_variant(few_tris, 5000) == 1
    # forma mejor con algo más de polos gana (humano: 0,053 % con 220 polos frente a 0,114 % con 150)
    shape_first = [{"faces_count": 5000, "poles": 150, "error": 0.114, "error_max": 1.7},
                   {"faces_count": 5000, "poles": 220, "error": 0.053, "error_max": 0.75}]
    assert retopo.choose_variant(shape_first, 5000) == 1
    # a calidad casi igual gana la de más loops cerrados
    rings = [{"faces_count": 5000, "poles": 200, "error": 0.100, "error_max": 1.0, "closed_percent": 2.0},
             {"faces_count": 5000, "poles": 200, "error": 0.101, "error_max": 1.0, "closed_percent": 20.0}]
    assert retopo.choose_variant(rings, 5000) == 1
    plan = retopo.variant_plan(4, "quadriflow")
    assert len(plan) == 4 and all(o["inflate"] for _, o in plan) and len({o["seed"] for _, o in plan}) == 4
    assert len({(tuple(o["inflate"].values()), o.get("thin_features", False)) for _, o in plan}) == 4   # todas distintas
    assert not any(o.get("thin_features") for _, o in plan[:3]) and all(o["thin_features"] for _, o in plan[3:])
    assert [o.get("thin_features", False) for _, o in retopo.variant_plan(9, "quadriflow")] == [False] * 3 + [True] * 3 + [False] * 3
    if not os.path.isfile(INSTANT_MESHES):
        print("    (sin Instant Meshes: se omite el resto)")
        return
    sphere = uv_sphere(2.0, 40, 48)
    points, faces, report = retopo.best_retopo(sphere.points, sphere.faces(), 600, "instant_meshes",
                                               INSTANT_MESHES, variants=3)
    assert len(report["variants"]) == 4      # la esfera es simétrica: 3 con simetría y una sin ella
    assert report["chosen"] == retopo.choose_variant(report["variants"], 600)
    assert not len(Mesh(points, faces).boundary_edges)



@test
def test_merge_triangles():
    """Dos triángulos de un cuadrado se juntan en un quad; una flecha cóncava no."""
    square = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
    faces, merged = quadopt.merge_triangles(square, [[0, 1, 2], [0, 2, 3]])
    assert merged == 1 and len(faces) == 1 and len(faces[0]) == 4 and sorted(faces[0]) == [0, 1, 2, 3]
    arrow = np.array([[0, 0, 0], [2, 1, 0], [0, 2, 0], [0.5, 1, 0]], float)
    faces, merged = quadopt.merge_triangles(arrow, [[0, 1, 3], [1, 2, 3]])
    assert merged == 0 and len(faces) == 2


def subdivided_cube(n=5):
    """Cubo cerrado de quads, n x n por cara, con las normales hacia fuera."""
    axes = [((1, 0, 0), (0, 1, 0), (0, 0, 1)), ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
            ((0, 1, 0), (0, 0, 1), (1, 0, 0)), ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
            ((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (0, 1, 0), (1, 0, 0))]
    points, faces = [], []
    for normal, u, v in axes:
        base = len(points)
        for j in range(n + 1):
            for i in range(n + 1):
                points.append(tuple(0.5 * np.array(normal) + (i / float(n) - 0.5) * np.array(u)
                                    + (j / float(n) - 0.5) * np.array(v)))
        faces += [[base + j * (n + 1) + i, base + j * (n + 1) + i + 1,
                   base + (j + 1) * (n + 1) + i + 1, base + (j + 1) * (n + 1) + i] for j in range(n) for i in range(n)]
    points, faces, _ = cleanup.merge_vertices(points, faces, 1e-6)
    return Mesh(points, faces)


def total_energy(mesh):
    """Suma de (aristas - 4)^2 de los vértices interiores más (lados - 4)^2 de las caras."""
    valence = mesh.valence[mesh.used_vertices]
    return int(((valence - 4) ** 2).sum() + ((mesh.face_sizes - 4) ** 2).sum())


@test
def test_mixed_pole_cleanup():
    """Una pareja de quads de un cubo cerrado se parte en triángulo + pentágono: el limpiador de
    caras mixtas lo deja otra vez en quads, y una malla ya limpia no cambia."""
    cube_mesh = subdivided_cube(5)
    clean_points, clean_faces, operations = quadopt.clean_poles(cube_mesh.points, cube_mesh.faces(), mixed=True)
    assert operations == 0 and len(clean_faces) == cube_mesh.face_count
    # dos quads vecinos en medio de una cara (todos sus vértices con 4 aristas)
    mesh = quadopt._Mesh(cube_mesh.points, cube_mesh.faces(), mixed=True)
    found = None
    for fi, face in enumerate(mesh.faces):
        if all(mesh.valence(v) == 4 for v in face):
            u, w = face[0], face[1]
            polygon = mesh._polygon(u, w)
            if polygon and all(mesh.valence(v) == 4 for v in polygon[2]):
                found = (fi, polygon)
                break
    assert found, "no hay una pareja de quads regulares"
    f1, f2, poly = found[1]
    broken = [list(face) for face in cube_mesh.faces()]
    broken[f1], broken[f2] = poly[0:3], poly[2:] + poly[:1]       # triángulo + pentágono
    damaged = Mesh(cube_mesh.points, broken)
    assert sorted(damaged.face_sizes.tolist()).count(3) == 1 and sorted(damaged.face_sizes.tolist()).count(5) == 1
    points, faces, operations = quadopt.clean_poles(damaged.points, broken, mixed=True)
    fixed = Mesh(points, faces)
    assert not len(fixed.boundary_edges), "se abrió la malla"
    assert total_energy(fixed) < total_energy(damaged), (total_energy(fixed), total_energy(damaged))
    assert (fixed.face_sizes == 4).all(), np.bincount(fixed.face_sizes)


@test
def test_dominant_retopo():
    """Retopo con Instant Meshes en modo quad-dominante: casi todo quads, cerrado y fiel."""
    if not os.path.isfile(INSTANT_MESHES):
        print("    (sin Instant Meshes: se omite)")
        return
    sphere = uv_sphere(2.0, 40, 48)
    points, faces, report = retopo.retopo(sphere.points, sphere.faces(), 600, "instant_meshes",
                                           INSTANT_MESHES, options={"dominant": True})
    result = Mesh(points, faces)
    stats = metrics.topology_stats(result)
    assert not len(result.boundary_edges)
    assert stats["quad_percent"] > 85, stats["quad_percent"]
    assert metrics.surface_error(sphere, result)["mean_percent"] < 0.2



@test
def test_quadriflow_density():
    """Nuestra versión de QuadriFlow (-density): densidad 1 da lo mismo que sin ella, y 2 da más caras."""
    if not os.path.isfile(QUADRIFLOW):
        print("    (sin QuadriFlow: se omite)")
        return
    import subprocess
    import tempfile
    sphere = uv_sphere(2.0, 40, 48)
    points, faces = retopo.compact(sphere.points, sphere.faces())
    tris = retopo.triangulated(points, faces)
    work = tempfile.mkdtemp()
    source = os.path.join(work, "in.obj")
    repair.write_obj(source, points, tris, keep_order=True)

    def run(label, value):
        command = [QUADRIFLOW, "-i", source, "-o", os.path.join(work, label + ".obj"), "-f", "800"]
        if value is not None:
            density = os.path.join(work, label + ".txt")
            np.savetxt(density, np.full(len(points), value), fmt="%.4f")
            command += ["-density", density]
        assert subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT).returncode == 0
        return len(repair.read_obj(os.path.join(work, label + ".obj"))[1])
    plain, same, double = run("plain", None), run("one", 1.0), run("two", 2.0)
    assert plain == same, (plain, same)
    assert 1.6 * plain < double < 2.8 * plain, (plain, double)


@test
def test_quadriflow_retopo_with_density():
    """Retopo del núcleo con QuadriFlow y densidad por piezas finas: quads puros, cerrado, sin polos de 6+,
    y el total de caras cerca del objetivo aunque haya densidad."""
    if not os.path.isfile(QUADRIFLOW):
        print("    (sin QuadriFlow: se omite)")
        return
    sphere = uv_sphere(2.0, 40, 48)
    points, faces, report = retopo.retopo(sphere.points, sphere.faces(), 800, "quadriflow", QUADRIFLOW,
                                          symmetric=False, options={"thin_features": True, "thin_epr": 0.3})
    result = Mesh(points, faces)
    stats = metrics.topology_stats(result)
    assert stats["quad_percent"] == 100.0 and not len(result.boundary_edges)
    assert not stats["poles"][6]
    assert 0.6 * 800 < stats["faces"] < 1.4 * 800, stats["faces"]



@test
def test_decimate():
    """La reducción por colapso de aristas conserva la topología: una esfera reducida sigue cerrada,
    con casi los triángulos pedidos y la misma forma; con bordes abiertos no crea agujeros nuevos."""
    executable = decimate.find_decimator()
    if not executable:
        print("    (sin mbl_decimate: se omite)")
        return
    sphere = uv_sphere(2.0, 80, 100)
    tris = sphere.triangles()[0]
    points, reduced = decimate.qem_decimate(sphere.points, tris, 3000, executable)
    result = Mesh(points, reduced)
    assert 2500 <= len(reduced) <= 3100, len(reduced)
    assert not len(result.boundary_edges) and not (result.edge_face_count > 2).any()
    assert metrics.surface_error(sphere, result)["mean_percent"] < 0.2
    # una rejilla con un agujero: el agujero se queda como estaba, sin abrir otros
    plate = grid(40, 0.1, hole=(15, 24, 15, 24))
    plate_tris = plate.triangles()[0]
    points, reduced = decimate.qem_decimate(plate.points, plate_tris, 400, executable)
    assert len(Mesh(points, reduced).boundary_loops()) == 2
    # si ya tiene menos de los pedidos, la devuelve igual
    same_points, same = decimate.qem_decimate(sphere.points, tris, len(tris) + 10, executable)
    assert len(same) == len(tris)


@test
def test_safe_workers():
    """Cuántas variantes a la vez: respeta núcleos, tope fijo y memoria libre, y nunca baja de 1."""
    gb = 1024 ** 3
    big = 64 * gb        # equipo grande: así la prueba no depende de la RAM de quien la lance
    assert resources.safe_workers(14000, memory=64 * gb, cpus=16, total=big) == resources.MAX_WORKERS
    assert resources.safe_workers(14000, memory=64 * gb, cpus=2, total=big) == 1
    assert resources.safe_workers(14000, memory=64 * gb, cpus=16, requested=2, total=big) == 2
    # águila de 528.000 triángulos: 1,8 GB cada una; con 4 GB libres y 2 GB de reserva cabe una
    assert resources.safe_workers(528000, memory=4 * gb, cpus=16, total=big) == 1
    assert resources.safe_workers(528000, memory=8 * gb, cpus=16, total=big) == 3
    assert resources.safe_workers(528000, memory=0, cpus=16, total=big) == 1
    # si no se puede saber la memoria libre, no limita (se prueba sin depender de la memoria real del equipo)
    real, resources.available_memory = resources.available_memory, lambda: None
    try:
        assert resources.safe_workers(14000, memory=None, cpus=8, total=big) == resources.MAX_WORKERS
    finally:
        resources.available_memory = real


@test
def test_best_retopo_cancel():
    """Cancelar desde tick() detiene las variantes que están en marcha y no deja hilos vivos."""
    if not os.path.isfile(QUADRIFLOW):
        print("    (sin QuadriFlow: se omite)")
        return
    import threading
    sphere = uv_sphere(2.0, 60, 80)
    before = threading.active_count()
    calls = []

    def tick():
        calls.append(1)
        if len(calls) > 3:
            raise KeyboardInterrupt("cancelado por la prueba")
    try:
        retopo.best_retopo(sphere.points, sphere.faces(), 800, "quadriflow", QUADRIFLOW, variants=6, tick=tick)
        raise AssertionError("tick debería haber cancelado")
    except KeyboardInterrupt:
        pass
    assert threading.active_count() <= before, (before, threading.active_count())


def flat_disc(radius=2.0, half_thickness=0.1, rings=24, segments=36):
    """Elipsoide aplastado (una moneda): grosor 2 * half_thickness en el centro, canto redondeado."""
    sphere = uv_sphere(1.0, rings, segments)
    return Mesh(sphere.points * [radius, half_thickness, radius], sphere.faces())


@test
def test_sizefield_thickness():
    """El grosor sale bien en una moneda y en una esfera."""
    # esfera: el grosor es el diámetro y por fuera no hay nada; moneda: el grosor del centro
    sphere = uv_sphere(1.0, 24, 36)
    tris = sphere.triangles()[0]
    normals = sizefield.outward_normals(sphere.points, tris)
    outside, inside = sizefield.thickness_sides(sphere.points, tris, normals, 0.5)
    assert np.isinf(outside).all() and np.isinf(inside).all()  # el diámetro (2) no cabe en la búsqueda (0,6)
    disc = flat_disc()
    dtris = disc.triangles()[0]
    dn = sizefield.outward_normals(disc.points, dtris)
    outside, inside = sizefield.thickness_sides(disc.points, dtris, dn, 0.5)
    top = int(np.argmax(disc.points[:, 1]))
    assert abs(inside[top] - 0.2) < 0.06, inside[top]
    # las normales hacia fuera aunque la malla venga al revés
    flipped = sizefield.outward_normals(disc.points, dtris[:, ::-1])
    assert np.einsum("ij,ij->i", flipped, disc.points - disc.points.mean(axis=0)).mean() > 0


@test
def test_sizefield_inflate():
    """Engordar lleva las piezas finas al grosor pedido sin tocar lo gordo."""
    sphere = uv_sphere(1.0, 24, 36)
    stris = sphere.triangles()[0]
    edge = sizefield.base_edge(sphere.points, stris, 200)
    kept, info = sizefield.inflate(sphere.points, stris, edge)
    assert info["inflated_percent"] == 0 and np.allclose(kept, sphere.points)
    disc = flat_disc()
    dtris = disc.triangles()[0]
    edge = sizefield.base_edge(disc.points, dtris, 200)
    grown, info = sizefield.inflate(disc.points, dtris, edge)
    assert info["inflated_percent"] > 10, info
    top = int(np.argmax(disc.points[:, 1]))
    bottom = int(np.argmin(disc.points[:, 1]))
    assert grown[top, 1] - grown[bottom, 1] > 0.8 * sizefield.INFLATE_RATIO * edge, (grown[top], grown[bottom], edge)


@test
def test_fit_pulls_vertices_to_uncovered_tips():
    """El ajuste lleva a la superficie un polo hundido (zona del original que ninguna cara cubre) sin
    mover los vértices bloqueados ni dar la vuelta a ninguna cara."""
    fine = uv_sphere(1.0, 48, 64)
    source = Mesh(fine.points, fine.faces())
    target = uv_sphere(1.0, 12, 16, triangulate=False)
    sunk = target.points.copy()
    sunk[0, 1] = 0.8                                  # polo norte hundido
    reference = metrics.SurfaceReference(source, 6000)
    project = lambda points: reference.grid.closest(points)[0]  # noqa: E731
    before = reference.error(Mesh(sunk, target.faces()))["max_percent"]
    fitted, report = fit.fit_to_source(sunk, target.faces(), reference, project)
    after = reference.error(Mesh(fitted, target.faces()))["max_percent"]
    assert report["rounds"] >= 1 and after < 0.5 * before, (before, after, report)
    # cada cara sigue mirando hacia donde miraba
    old = Mesh(sunk, target.faces()).face_normals
    new = Mesh(fitted, target.faces()).face_normals
    assert (np.einsum("ij,ij->i", old, new) > 0).all()
    locked = np.zeros(len(sunk), dtype=bool)
    locked[0] = True
    same, _ = fit.fit_to_source(sunk, target.faces(), reference, project, locked=locked)
    assert np.allclose(same[0], sunk[0])


@test
def test_native_grid_matches_python_grid():
    """El buscador nativo (mbl_geom) da las mismas distancias y puntos que la rejilla de Python, también
    con consultas lejos de la malla, y make_grid vuelve a Python si no está."""
    executable = closest.find_native()
    if not executable:
        print("    (sin mbl_geom: se omite)")
        return
    sphere = uv_sphere(2.0, 40, 48)
    tris = sphere.triangles()[0]
    rng = np.random.RandomState(3)
    queries = np.concatenate([sphere.points[rng.randint(0, len(sphere.points), 3000)] + rng.normal(0, 0.1, (3000, 3)),
                              rng.uniform(-6, 6, (500, 3))])
    native = closest.NativeGrid(executable, sphere.points, tris)
    python = closest.TriangleGrid(sphere.points, tris)
    pn, dn, on = native.closest(queries)
    pp, dp, op = python.closest(queries)
    assert np.abs(dn - dp).max() < 1e-9 and np.abs(pn - pp).max() < 1e-9
    assert (on >= 0).all() and (on < len(tris)).all()
    assert len(native.closest(np.zeros((0, 3)))[1]) == 0
    native.close()
    assert isinstance(closest.make_grid(sphere.points, tris), closest.NativeGrid)
    assert isinstance(closest.make_grid(sphere.points, tris, native=False), closest.TriangleGrid)


@test
def test_write_obj_large_meshes():
    """write_obj de muchas caras iguales (camino con numpy) y de caras mezcladas escriben la misma malla:
    al leerla de vuelta cada cara tiene los mismos puntos, en orden de primera aparición o en el original."""
    import tempfile
    sphere = uv_sphere(1.0, 30, 40)
    tris = sphere.triangles()[0]
    assert len(tris) >= 1000
    mixed = [list(map(int, t)) for t in tris] + [[0, 1, 2, 3]]
    folder = tempfile.mkdtemp()
    for label, faces, keep in (("array", tris, False), ("lista", [list(map(int, t)) for t in tris], False),
                               ("orden", tris, True), ("mezcla", mixed, False)):
        path = os.path.join(folder, label + ".obj")
        repair.write_obj(path, sphere.points, faces, keep_order=keep)
        points, back = repair.read_obj(path)
        assert len(back) == len(faces)
        for original, written in zip(list(faces)[::97], back[::97]):
            assert np.allclose(np.asarray(points)[written], sphere.points[list(original)], atol=1e-5), label
        if keep:
            assert len(points) == len(sphere.points)
        else:
            assert len(points) == len(np.unique(np.concatenate([np.asarray(f).ravel() for f in faces])))


@test
def test_quadriflow_symmetric_half():
    """QuadriFlow simétrico: se le da la mitad abierta (-boundary: el borde queda alineado con el plano) y se
    refleja. El resultado queda cerrado, es de solo quads, idéntico a su reflejo y tiene aristas sobre el plano
    central (el bucle central)."""
    if not os.path.isfile(QUADRIFLOW):
        print("    (sin QuadriFlow: se omite)")
        return
    sphere = uv_sphere(2.0, 40, 48)
    points, faces, report = retopo.retopo(sphere.points, sphere.faces(), 1200, "quadriflow", QUADRIFLOW, symmetric=True)
    result = Mesh(points, faces)
    stats = metrics.topology_stats(result)
    assert report["symmetric"] and not len(result.boundary_edges)
    assert stats["quad_percent"] > 99.0, stats["quad_percent"]
    assert symmetry.mirror_error(result)[0] < 0.01
    center = symmetry.plane_x(result.points[result.used_vertices])
    on_plane = np.abs(result.points[:, 0] - center) < 1e-6
    assert (on_plane[result.edges[:, 0]] & on_plane[result.edges[:, 1]]).sum() > 10     # bucle central
    assert metrics.surface_error(sphere, result)["mean_percent"] < 0.3


@test
def test_choose_variant_symmetry_fallback():
    """Con simetría pedida hay variantes simétricas y asimétricas: gana la simétrica solo si deja igual o menos fallos
    visibles que la mejor asimétrica (sin tolerancia); si no hay de las dos clases no hay nada que comparar."""
    base = {"faces_count": 5000, "poles": 200, "error": 0.10, "quad_percent": 100.0}
    sym_ok, sym_bad = dict(base, symmetric=True, defects=3.0), dict(base, symmetric=True, defects=9.0)
    flat = dict(base, symmetric=False, defects=4.0)
    assert retopo.choose_variant([sym_ok, flat], 5000) == 0
    assert retopo.choose_variant([dict(sym_ok, defects=4.0), flat], 5000) == 0         # empate: gana la simétrica
    assert retopo.choose_variant([dict(sym_ok, defects=4.01), flat], 5000) == 1        # sin tolerancia
    assert retopo.choose_variant([sym_bad, flat], 5000) == 1
    # entre las simétricas manda el criterio de siempre (forma, polos...) aunque haya una asimétrica
    better = dict(sym_ok, error=0.05)
    assert retopo.choose_variant([sym_ok, better, flat], 5000) == 1
    # sin asimétricas, o sin simétricas, vale la elección normal
    assert retopo.choose_variant([sym_bad, dict(sym_bad, poles=150)], 5000) == 1
    assert retopo.choose_variant([flat, dict(flat, poles=150)], 5000) == 1


@test
def test_variant_time_gate():
    """Una variante solo empieza si, con lo que tardaron las ya terminadas, acabará dentro del presupuesto; la primera
    ronda (sin ninguna terminada) siempre empieza."""
    assert retopo.can_start_variant(0.0, [], 270.0)
    assert retopo.can_start_variant(500.0, [], 270.0)                     # nada medido: no se puede saber, empieza
    assert retopo.can_start_variant(100.0, [90.0], 270.0)                 # 100 + 90 x margen cabe
    assert not retopo.can_start_variant(200.0, [90.0, 150.0], 270.0)      # la más lenta (150) ya no cabe
    assert retopo.can_start_variant(100.0, [90.0, 150.0], 270.0)


@test
def test_with_plain_variants():
    """Con simetría se añaden variantes sin ella (una por cada 3; con calidad alta, la mitad), repiten ajustes de las
    simétricas (la segunda primero) y van alternadas con ellas para que haya de las dos aunque se acabe el tiempo."""
    for count, expected_plain in ((1, 1), (3, 1), (5, 1), (6, 3), (8, 4)):
        plan = retopo.variant_plan(count, "quadriflow")
        full, plain = retopo.with_plain_variants(plan)
        assert len(full) == count + expected_plain and len(plain) == expected_plain, (count, len(full), plain)
        assert full[min(plain)] == plan[1 % count] and all(full[i] in plan for i in plain)   # primero el ajuste que mejor sale
        assert min(plain) == 1 and (len(plain) < 2 or sorted(plain)[1] == 3)             # alternadas: simétrica, sin, simétrica, sin...
        sym_options = [full[i] for i in range(len(full)) if i not in plain]
        assert sym_options == plan                                                       # las simétricas, tal cual el plan


@test
def test_quadriflow_first_attempt_hint():
    """QuadriFlow con un motor simulado que da 0,98 caras por cara pedida: la primera pasada con el factor de siempre se sale
    de la tolerancia y avisa de lo que habría acertado; con esa petición como "factor_hint" basta una ejecución."""
    sphere = uv_sphere(1.0, 12, 16)
    points, faces = sphere.points, sphere.faces()
    asked, ratio = [], [0.98]
    original = (retopo._run_once, retopo.read_obj)

    def fake_run(command, destination, *args, **kwargs):
        asked.append(int(command[command.index("-f") + 1]))

    def fake_read(path):
        count = int(ratio[0] * asked[-1])
        return [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)], [[0, 1, 2, 3]] * count
    retopo._run_once, retopo.read_obj = fake_run, fake_read
    try:
        learned = []
        retopo._quadriflow("x", points, faces, 5000, tempfile.mkdtemp(), 10, None, {"first_attempt": learned.append})
        assert asked[0] == int(5000 * retopo.QUADRIFLOW_FACTOR) and len(asked) == 2, asked
        assert len(learned) == 1 and abs(learned[0] - asked[1] / 5000.0) < 1e-3, (learned, asked)
        del asked[:]
        retopo._quadriflow("x", points, faces, 5000, tempfile.mkdtemp(), 10, None, {"factor_hint": learned[0]})
        assert len(asked) == 1 and abs(asked[0] - 5000 * learned[0]) < 1, asked
        # una primera pasada que ya acierta no cambia nada para las demás variantes
        del asked[:]
        learned2, ratio[0] = [], 0.77
        retopo._quadriflow("x", points, faces, 5000, tempfile.mkdtemp(), 10, None, {"first_attempt": learned2.append})
        assert learned2 == [None] and len(asked) == 1, (learned2, asked)
    finally:
        retopo._run_once, retopo.read_obj = original


@test
def test_best_retopo_shares_the_first_request_with_the_other_variants():
    """La primera variante cuenta lo que pidió QuadriFlow para acertar y las demás lo reciben (también con varias a la vez);
    si no hizo falta corregir, no reciben nada."""
    sphere = uv_sphere(2.0, 30, 40, triangulate=False)
    quads = uv_sphere(2.0, 30, 40, triangulate=False)
    for workers, factor in ((1, 1.05), (3, 1.05), (3, None)):
        seen = {}
        original = retopo.ENGINES["quadriflow"]

        def stub(executable, points, faces, target, work, timeout, tick, options):
            key = options["seed"]
            seen[key] = (options.get("factor_hint"), "first_attempt" in options)
            if "first_attempt" in options:
                options["first_attempt"](factor)
            return quads.points, quads.faces()
        retopo.ENGINES["quadriflow"] = stub
        try:
            retopo.best_retopo(sphere.points, sphere.faces(), 500, "quadriflow", "x", variants=3, workers=workers,
                               symmetric=False)
        finally:
            retopo.ENGINES["quadriflow"] = original
        assert seen[0] == (None, True), seen
        assert seen[1] == (factor, False) and seen[2] == (factor, False), (workers, factor, seen)


@test
def test_best_retopo_symmetry_keeps_a_fallback():
    """Con simetría, best_retopo prueba también variantes sin ella y lo cuenta en el informe; sin simetría no."""
    if not os.path.isfile(QUADRIFLOW):
        print("    (sin QuadriFlow: se omite)")
        return
    sphere = uv_sphere(2.0, 40, 48)
    points, faces, report = retopo.best_retopo(sphere.points, sphere.faces(), 800, "quadriflow", QUADRIFLOW, variants=3,
                                               symmetric=True)
    flags = [v["symmetric"] for v in report["variants"]]
    assert True in flags and False in flags, flags
    assert report["symmetry"]["asked"] and report["symmetry"]["applied"] == report["variants"][report["chosen"]]["symmetric"]
    assert report["symmetric"] == report["symmetry"]["applied"]
    assert not len(Mesh(points, faces).boundary_edges)
    points, faces, report = retopo.best_retopo(sphere.points, sphere.faces(), 800, "quadriflow", QUADRIFLOW, variants=2,
                                               symmetric=False)
    assert not any(v["symmetric"] for v in report["variants"]) and not report["symmetry"]["asked"]


@test
def test_quality_report():
    """Calidad de topología: una rejilla regular sale limpia; con un quad aplastado, uno enorme y una cara dada la
    vuelta, las cifras lo detectan y la nota baja."""
    flat = grid(10, 1.0)
    clean = quality.report(flat, flat)
    assert clean["shape"]["bad_percent"] == 0 and clean["uniformity"]["jump_percent"] == 0
    assert clean["defects"]["flipped"] == 0 and clean["defects"]["folded"] == 0
    assert quality.grade(clean)[0] == 0
    points = flat.points.copy()
    faces = flat.faces()
    # un vértice llevado casi encima de su vecino: quads aplastados; otro muy lejos: caras enormes
    points[60] = points[61] + [0.05, 0.0, 0.0]
    points[27] = points[27] + [3.0, 3.0, 0.0]
    faces[40] = faces[40][::-1]                               # cara dada la vuelta
    bad = Mesh(points, faces)
    rep = quality.report(bad, flat)
    assert rep["shape"]["bad_percent"] > 5, rep["shape"]
    assert rep["uniformity"]["jump_percent"] > 0
    assert rep["defects"]["flipped"] >= 1
    level, reasons = quality.grade(rep)
    assert level == 2 and reasons, (level, reasons)
    assert quality.defect_score(rep["shape"], rep["uniformity"], rep["defects"], rep["faces"]) >         quality.defect_score(clean["shape"], clean["uniformity"], clean["defects"], clean["faces"])


@test
def test_hole_patterns():
    """Relleno de agujeros de 6 a 16 lados solo con quads, con el patrón de menos polos entre estrella, rayas,
    rejilla y cuerdas: cada uno cierra el agujero (variedad, el borde es el del agujero) y nunca es peor que la
    estrella. Con los vértices del borde de valencia 4 la rejilla gana en agujeros grandes; con valencias bajas
    se aprovechan para recibir las cuerdas."""
    from mbl_engine import holefill
    for n in (6, 8, 10, 12, 14, 16):
        regular = [4] * n
        pattern = holefill.best_pattern(regular)
        report = holefill.check_pattern(pattern, n)
        assert report["all_quads"] and report["manifold"] and report["charge_ok"], (n, report)
        star = holefill.pattern_cost(holefill.star_pattern(n), regular)
        assert holefill.pattern_cost(pattern, regular) <= star, (n, pattern.name)
    # un agujero de 12 con vértices de valencia 4: la estrella deja un centro de valencia 6; la rejilla, ninguno
    twelve = holefill.best_pattern([4] * 12)
    valence = holefill.interior_valences(twelve)
    assert max(valence or [4]) <= 4 and twelve.name != "estrella", (twelve.name, valence)
    # si unas esquinas del borde tienen valencia 3, la rejilla las usa como esquinas (no reciben arista de dentro)
    mixed = [3, 4, 4, 3, 4, 4, 3, 4, 4, 3, 4, 4]
    pattern = holefill.best_pattern(mixed)
    assert holefill.pattern_cost(pattern, mixed) <= holefill.pattern_cost(holefill.best_pattern([4] * 12), mixed)
    # la posición de los vértices nuevos queda dentro del agujero (media de su borde para la estrella)
    ring = np.array([(np.cos(2 * np.pi * i / 8), np.sin(2 * np.pi * i / 8), 0.0) for i in range(8)])
    for pattern in (holefill.grid_pattern(8, 2, 2), holefill.star_pattern(8)):
        assert holefill.check_pattern(pattern, 8)["manifold"]
        points = holefill.interior_points(pattern, ring)
        assert points.shape == (1, 3) and np.abs(points[0]).max() < 1e-9, (pattern.name, points)   # el centro
    assert len(holefill.interior_points(holefill.ladder_pattern(8), ring)) == 0
    for odd in (5, 7):
        try:
            holefill.best_pattern([4] * odd)
            raise AssertionError("un agujero de {} lados no se cierra solo con quads".format(odd))
        except ValueError:
            pass


def corner_deviation(points, faces):
    """Mayor desviación de un ángulo interior de quad respecto a 90 grados, en toda la malla."""
    p = np.asarray(points)[np.array([f for f in faces if len(f) == 4])]
    u, w = np.roll(p, 1, axis=1) - p, np.roll(p, -1, axis=1) - p
    cos = np.einsum("fkj,fkj->fk", u, w) / (np.linalg.norm(u, axis=2) * np.linalg.norm(w, axis=2))
    return float(np.abs(np.degrees(np.arccos(np.clip(cos, -1, 1))) - 90.0).max())


@test
def test_regularize_plane_grid():
    """Una rejilla con los vértices interiores movidos vuelve a quads cuadrados; el borde no se mueve."""
    rng = np.random.RandomState(3)
    g = grid(10)
    points = g.points.copy()
    interior = ~g.boundary_vertices
    points[interior] += rng.uniform(-0.3, 0.3, (int(interior.sum()), 3)) * np.array([0.3, 0.3, 0.0]) * 3.0
    before = corner_deviation(points, g.faces())
    out = quadopt.regularize(points, g.faces(), iterations=40)
    assert corner_deviation(out, g.faces()) < 0.25 * before and corner_deviation(out, g.faces()) < 6.0, (before, corner_deviation(out, g.faces()))
    assert np.allclose(out[g.boundary_vertices], points[g.boundary_vertices])
    assert np.abs(out[:, 2]).max() < 1e-9, "salió del plano"
    # los lados quedan parejos: el cociente entre el mayor y el menor
    edges = np.linalg.norm(out[g.edges[:, 0]] - out[g.edges[:, 1]], axis=1)
    assert edges.max() / edges.min() < 1.25, edges.max() / edges.min()


@test
def test_regularize_on_surface_with_poles():
    """En una esfera de quads (con 8 polos de 3 aristas) el relajado deja los vértices sobre la superficie,
    no da la vuelta a ninguna cara y no empeora los quads deformados."""
    rng = np.random.RandomState(5)
    cube_mesh = subdivided_cube(6)
    sphere = cube_mesh.points / np.linalg.norm(cube_mesh.points, axis=1, keepdims=True)
    project = lambda p: p / np.linalg.norm(p, axis=1, keepdims=True)    # noqa: E731
    shaken = project(sphere + rng.normal(scale=0.03, size=sphere.shape))
    faces = cube_mesh.faces()
    out = quadopt.regularize(shaken, faces, iterations=30, project=project)
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0)
    before, after = Mesh(shaken, faces), Mesh(out, faces)
    assert (np.einsum("ij,ij->i", after.face_normals, after.face_centers()) > 0).all(), "una cara dada la vuelta"
    assert quality.shape_stats(after)["bad_percent"] <= quality.shape_stats(before)["bad_percent"]
    assert corner_deviation(out, faces) < corner_deviation(shaken, faces)
    # tamaño parejo: los polos de un cubo-esfera tienen quads algo mayores en las esquinas, pero no saltos
    assert quality.uniformity_stats(after)["jump_percent"] == 0.0


@test
def test_fix_deformed_on_sharp_edge():
    """Un vértice de una arista viva del cubo se desliza a lo largo de ella y deforma dos quads: el relajado
    normal no lo toca (las aristas vivas se respetan) pero el arreglo de deformados sí, sin salirse del cubo."""
    cube_mesh = subdivided_cube(4)
    faces = cube_mesh.faces()
    project = lambda p: p * (0.5 / np.abs(p).max(axis=1, keepdims=True))      # noqa: E731
    points = cube_mesh.points.copy()
    on_edge = [v for v in range(len(points)) if np.isclose(np.abs(points[v]), 0.5).sum() == 2]
    target = on_edge[0]
    along = int(np.argmin(np.abs(points[target])))
    points[target, along] += 0.2
    before = quality.deformed_quads(points, np.array(faces))
    assert before.sum() >= 2, before.sum()
    kept = quadopt.regularize(points, faces, iterations=10, project=project)
    assert np.allclose(kept[target], points[target]), "una arista viva no se mueve en el relajado normal"
    fixed, count_before, count_after = quadopt.fix_deformed(points, faces, project)
    assert count_before == before.sum() and count_after == 0, (count_before, count_after)
    assert np.allclose(np.abs(fixed).max(axis=1), 0.5), "salió del cubo"
    after = Mesh(fixed, faces)
    assert (np.einsum("ij,ij->i", after.face_normals, after.face_centers()) > 0).all(), "una cara dada la vuelta"


# ------------------------------------------------------------------ analizador de caras

@test
def test_analyze_warns_about_thin_pieces_even_above_the_minimum():
    """Se pide más que el mínimo, pero casi toda la superficie es más fina que 3 aristas: avisa; con más caras, no."""
    v = {"area": np.ones(100), "kappa": np.zeros(100), "thick": np.full(100, 0.5), "sheet": np.zeros(100, dtype=bool),
         "diagonal": 1.0, "total": 100.0, "min_width": 3.0}
    fake = {"minimum": 800, "detail": 50000, "sheet_percent": 0.0, "_vertices": v}
    points, tris = np.zeros((3, 3)), [[0, 1, 2]]
    saved = analyze.analyze
    analyze.analyze = lambda *args, **kwargs: fake
    try:
        report, warning = analyze.recommend(points, tris, 1000)
        assert warning and "menos de 3 aristas" in warning and "Pintar detalle" in warning, warning
        assert "100 %" in warning and "2.000" in warning, warning
        assert analyze.recommend(points, tris, 100000)[1] is None
        # por debajo del mínimo sigue mandando el aviso de siempre
        assert "se recomiendan al menos" in analyze.recommend(points, tris, 400)[1]
    finally:
        analyze.analyze = saved
    # una esfera maciza con caras de sobra no avisa
    ball = uv_sphere(rings=16, segments=24)
    assert analyze.recommend(ball.points, ball.triangles()[0], 3000)[1] is None


if __name__ == "__main__":
    only = sys.argv[1:]
    for func in TESTS:
        if only and not any(word in func.__name__ for word in only):
            continue
        start = time.time()
        try:
            func()
        except Exception:
            RESULTS.append((func.__name__, False))
            print("FALLO  {}\n{}".format(func.__name__, traceback.format_exc()))
        else:
            RESULTS.append((func.__name__, True))
            print("OK     {} ({:.1f} s)".format(func.__name__, time.time() - start))
    failed = [name for name, ok in RESULTS if not ok]
    print("\n{} pruebas, {} correctas, {} fallidas".format(len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)
