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

from mbl_engine import analyze, cleanup, closest, decimate, fit, metrics, orient, quality, quadopt, repair, resources, retopo, sizefield, symmetry, uv_quality, uv_straighten  # noqa: E402
from mbl_engine import scale  # noqa: E402
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


def unfold_defects(points, faces):
    """(aristas con más de 2 caras, quads con una diagonal que ya es arista): lo que Unfold3D no acepta."""
    mesh = Mesh(points, faces)
    edges = {tuple(e) for e in mesh.edges.tolist()}
    chords = 0
    for face in faces:
        n = len(face)
        chords += sum(1 for i in range(n) for j in range(i + 2, n) if not (i == 0 and j == n - 1)
                      and (min(face[i], face[j]), max(face[i], face[j])) in edges)
    return int((mesh.edge_face_count > 2).sum()), chords


@test
def test_make_manifold_removes_fins():
    """Una aleta (dos triángulos con los mismos vértices y la orientación contraria) pegada a una arista de la
    superficie cae entera y la superficie queda como estaba. Quitar solo "las caras de después" arrancaba un quad de
    la esfera y dejaba la aleta (pasaba tras colapsar una cara diminuta de polyRetopo: criatura y midnight)."""
    sphere = uv_sphere(1.0, 8, 12, triangulate=False)
    faces = sphere.faces()
    quad = next(f for f in faces if len(f) == 4)
    a, b = quad[0], quad[1]
    tip = 1.1 * (sphere.points[a] + sphere.points[b]) / 2
    points = np.vstack([sphere.points, tip])
    bad = faces + [[a, b, len(points) - 1], [b, a, len(points) - 1]]
    assert unfold_defects(points, bad) == (1, 0)
    new_points, new_faces, report = repair.make_manifold(points, bad)
    assert report["fins"] == 2 and not report["bowties"] and not report["chords"], report
    assert sorted(map(sorted, new_faces)) == sorted(map(sorted, faces))
    assert unfold_defects(new_points, new_faces) == (0, 0)


# los cuatro quads donde el Motor MBL dejó la figura de dedos juntos (midnight) con un quad cuyas esquinas opuestas 0 y
# 5 ya estaban unidas por una arista de otro quad: Unfold3D falló con "Mesh has non-manifold UVs"
CHORD_POINTS = [(-6.852, -3.066, -5.872), (-7.089, -2.727, -4.985), (-5.174, -1.223, -5.69), (-5.343, -2.697, -7.536),
                (-5.872, -2.266, -8.828), (-6.634, -3.769, -7.311), (-7.906, -4.691, -6.327), (-8.574, -3.001, -6.942)]
CHORD_FACES = [[0, 1, 2, 3], [3, 5, 6, 0], [0, 5, 7, 1], [3, 4, 7, 5]]


@test
def test_make_manifold_breaks_chords():
    """Un quad cuya diagonal ya es arista de otra cara se parte en dos triángulos por la otra diagonal."""
    assert unfold_defects(CHORD_POINTS, CHORD_FACES) == (0, 1)
    points, faces, report = repair.make_manifold(CHORD_POINTS, CHORD_FACES)
    assert report == {"fins": 0, "doublets": 0, "bowties": 0, "chords": 1}, report
    assert sorted(len(f) for f in faces) == [3, 3, 4, 4, 4], faces
    assert len(points) == len(CHORD_POINTS) and unfold_defects(points, faces) == (0, 0)
    # el resto de caras no se toca y la orientación se conserva (los triángulos recorren la arista como el quad)
    assert [f for f in faces if len(f) == 4] == [f for f in CHORD_FACES if f != [3, 5, 6, 0]]
    area = Mesh(points, faces).face_areas.sum() / Mesh(CHORD_POINTS, CHORD_FACES).face_areas.sum()
    assert 0.95 < area < 1.05, area


@test
def test_make_manifold_center_vertex_when_both_diagonals_are_edges():
    """Si las dos diagonales del quad son aristas, o la cara tiene más de 4 lados, se abre en triángulos hacia un
    vértice nuevo en su centro (partirlo por una diagonal repetiría una arista)."""
    # (la malla de prueba no es una superficie: se llama al paso de las diagonales solo)
    points = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0.5, -1, 0.2), (0.5, 2, 0.2)]
    faces = [[0, 1, 2, 3], [0, 2, 4], [1, 3, 5]]
    assert unfold_defects(points, faces) == (0, 2)
    new_points, new_faces, fixed = repair._break_chords(points, faces)
    assert fixed == 1 and len(new_points) == 7 and len(new_faces) == 6, (fixed, new_faces)
    assert unfold_defects(new_points, new_faces) == (0, 0)
    assert np.allclose(new_points[-1], (0.5, 0.5, 0))
    # un pentágono con una diagonal repetida también
    points = [(0, 0, 0), (2, 0, 0), (3, 1.5, 0), (1, 3, 0), (-1, 1.5, 0), (1.5, 1.5, 1)]
    faces = [[0, 1, 2, 3, 4], [0, 2, 5]]
    new_points, new_faces, fixed = repair._break_chords(points, faces)
    assert fixed == 1 and sum(1 for f in new_faces if len(f) == 3) == 6, (fixed, new_faces)


@test
def test_make_manifold_leaves_a_clean_mesh_alone():
    """Sin nada que arreglar devuelve las mismas listas: los resultados que ya funcionaban no cambian ni un vértice."""
    for mesh in (uv_sphere(1.0, 8, 12, triangulate=False), uv_sphere(1.0, 8, 12), cube(), tube(), grid(5, hole=(1, 2, 1, 2))):
        points, faces = mesh.points, mesh.faces()
        out = repair.make_manifold(points, faces)
        assert out[0] is points and out[1] is faces and not any(out[2].values()), out[2]


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
        assert unfold_defects(points, faces) == (0, 0), (engine, "topología que Unfold3D rechaza")
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
def test_drop_small_pieces_matches_the_reference_implementation():
    """drop_small_pieces (numpy) quita lo mismo que repair._remove_small_pieces, en el mismo orden y contando igual."""
    rng = np.random.RandomState(7)
    for trial in range(30):
        pieces, points, faces = rng.randint(1, 6), [], []
        for _ in range(pieces):
            n = rng.randint(1, 14)
            base = len(points)
            points += [tuple(rng.rand(3)) for _ in range(n + 2)]
            for _ in range(rng.randint(1, 60)):
                k = rng.randint(3, 6)
                faces.append([base + int(v) for v in rng.randint(0, n + 2, size=k)])
        order = rng.permutation(len(faces))
        faces = [faces[i] for i in order]
        for ratio in (0.0, 0.05, 0.2, 0.5):
            kept, removed = repair._remove_small_pieces([list(f) for f in faces], ratio)
            mesh, dropped = retopo.drop_small_pieces(Mesh(points, faces), ratio)
            assert mesh.faces() == kept and dropped == removed, (trial, ratio, dropped, removed)
    empty, none = retopo.drop_small_pieces(Mesh(np.zeros((0, 3)), []), 0.01)
    assert none == 0 and empty.face_count == 0


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
                               symmetric=False, options={"learn_request": True})
        finally:
            retopo.ENGINES["quadriflow"] = original
        assert seen[0] == (None, True), seen
        assert seen[1] == (factor, False) and seen[2] == (factor, False), (workers, factor, seen)
    # apagado (por defecto) nadie recibe nada ni avisa
    seen.clear()
    retopo.ENGINES["quadriflow"] = stub
    try:
        retopo.best_retopo(sphere.points, sphere.faces(), 500, "quadriflow", "x", variants=3, workers=1, symmetric=False)
    finally:
        retopo.ENGINES["quadriflow"] = original
    assert all(value == (None, False) for value in seen.values()) and not retopo.LEARN_QUADRIFLOW_REQUEST, seen


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


# ------------------------------------------------------------------ calidad de UVs (uv_quality)

def uv_plane(n=10):
    """Plano n x n bien desplegado: (puntos, caras, UV, caras de UV). La UV es la propia posición, de 0 a 1."""
    mesh = grid(n)
    faces = [list(map(int, f)) for f in mesh.faces()]
    return mesh.points, faces, mesh.points[:, :2] / n, [list(f) for f in faces]


def split_columns(n, gap=0.0, rotate=False, scale=1.0):
    """El plano cortado por la mitad en dos islas con UVs propias. La derecha se separa `gap` de la izquierda,
    se gira 90 grados (`rotate`) o se escala (`scale`)."""
    points, faces, uv, _ = uv_plane(n)
    right = uv - [0.5, 0.0]
    if rotate:
        right = np.column_stack([-right[:, 1], right[:, 0]])
    right = right * scale
    right = right + [0.5 + gap, 0.0] - right[points[:, 0] >= n / 2].min(axis=0)
    uv_faces = [[v + (len(uv) if points[face][:, 0].mean() > n / 2 else 0) for v in face] for face in faces]
    return points, faces, np.vstack([uv, right]), uv_faces


def grade_of(points, faces, uv, uv_faces):
    rep = uv_quality.report(points, faces, uv, uv_faces)
    return rep, uv_quality.grade(rep)


@test
def test_uv_quality_flat_plane_is_perfect():
    """Un plano desplegado tal cual (una isla, sin estirar) saca nota 0 y las cifras de un despliegue perfecto."""
    points, faces, uv, uv_faces = uv_plane(10)
    rep, (level, reasons) = grade_of(points, faces, uv, uv_faces)
    assert (level, reasons) == (0, []), (level, reasons)
    assert rep["overlapping_faces"] == 0 and rep["flipped_faces"] == 0 and rep["islands"] == 1, rep
    assert abs(rep["stretch_mean"] - 1.0) < 1e-9 and rep["over_1_5_percent"] == 0.0, rep
    assert abs(rep["used_percent"] - 100.0) < 1e-9 and rep["margin_min"] is None, rep
    assert rep["seam_percent"] == 0.0 and rep["racord_percent"] == 100.0, rep
    # el mismo plano a la mitad de tamaño usa un cuarto de la textura
    small = uv_quality.report(points, faces, uv * 0.5, uv_faces)
    assert abs(small["used_percent"] - 25.0) < 1e-9 and abs(small["stretch_mean"] - 1.0) < 1e-9, small
    # una malla sin UVs es mala
    assert uv_quality.grade(uv_quality.report(points, faces, uv, [[] for _ in faces]))[0] == 2


@test
def test_uv_quality_overlaps_are_worse():
    """Una cara puesta encima de otra (y la de debajo) cuentan como solapadas; tocarse no cuenta."""
    for n, expected in ((10, 2), (40, 1)):
        points, faces, uv, uv_faces = uv_plane(n)
        base = len(uv)
        # la cara 0 con UVs propias, puestas justo donde está la cara de su derecha
        uv = np.vstack([uv, uv[faces[1]]])
        uv_faces[0] = [base, base + 1, base + 2, base + 3]
        rep, (level, reasons) = grade_of(points, faces, uv, uv_faces)
        assert rep["overlapping_faces"] == 2, rep
        assert level == expected and any("solapadas" in r for r in reasons), (n, level, reasons)
    # dos islas pegadas por el corte no se solapan
    points, faces, uv, uv_faces = split_columns(10)
    assert uv_quality.report(points, faces, uv, uv_faces)["overlapping_faces"] == 0
    # un solape parcial (media cara dentro de otra) también se ve
    points, faces, uv, uv_faces = uv_plane(10)
    uv = np.vstack([uv, uv[faces[0]] + [0.05, 0.0]])
    uv_faces[0] = [len(uv) - 4, len(uv) - 3, len(uv) - 2, len(uv) - 1]
    assert uv_quality.report(points, faces, uv, uv_faces)["overlapping_faces"] == 2


@test
def test_uv_quality_flipped_face_is_worse():
    """Una cara con las UVs en espejo cuenta como volteada; una isla entera en espejo, nota 2."""
    points, faces, uv, uv_faces = uv_plane(40)
    base = len(uv)
    square = uv[faces[5]]
    mirrored = square.copy()
    mirrored[:, 0] = square[:, 0].min() + square[:, 0].max() - square[:, 0]
    uv = np.vstack([uv, mirrored])
    uv_faces[5] = [base, base + 1, base + 2, base + 3]
    rep, (level, reasons) = grade_of(points, faces, uv, uv_faces)
    assert rep["flipped_faces"] == 1 and rep["overlapping_faces"] == 0, rep
    assert level == 1 and any("espejo" in r for r in reasons), (level, reasons)
    # todo el plano en espejo
    points, faces, uv, uv_faces = uv_plane(10)
    rep, (level, reasons) = grade_of(points, faces, uv * [-1.0, 1.0] + [1.0, 0.0], uv_faces)
    assert rep["flipped_faces"] == len(faces) and level == 2, (rep, level)
    # darle la vuelta al orden de las esquinas de una cara también la voltea
    points, faces, uv, uv_faces = uv_plane(10)
    uv_faces[3] = uv_faces[3][::-1]
    assert uv_quality.report(points, faces, uv, uv_faces)["flipped_faces"] == 1


@test
def test_uv_quality_stretch_is_worse():
    """Media ponderada y % de caras con más de 1,5x: la mitad derecha del plano en UV a un cuarto de ancho."""
    points, faces, uv, uv_faces = uv_plane(10)
    right = points[:, 0] > 5
    squeezed = uv.copy()
    squeezed[right, 0] = 0.5 + (uv[right, 0] - 0.5) / 4.0
    rep, (level, reasons) = grade_of(points, faces, squeezed, uv_faces)
    # densidad media = (0,5 + 0,125) / 1: la izquierda sale a 1/0,625 = 1,6x y la derecha a 0,25/0,625 -> 2,5x
    assert abs(rep["stretch_mean"] - 0.5 * (1.6 + 2.5)) < 1e-9, rep["stretch_mean"]
    assert abs(rep["over_1_5_percent"] - 100.0) < 1e-9, rep["over_1_5_percent"]
    assert level == 2 and any("estiramiento" in r for r in reasons), (level, reasons)
    # un estiramiento suave no quita la nota
    gentle = uv.copy()
    gentle[right, 0] = 0.5 + (uv[right, 0] - 0.5) * 0.9
    rep, (level, reasons) = grade_of(points, faces, gentle, uv_faces)
    assert 1.0 < rep["stretch_mean"] < 1.1 and rep["over_1_5_percent"] == 0.0 and level == 0, (rep, reasons)
    # una cara que se queda sin área cuenta como mala, aunque en la media pese poco (tope de 4x)
    flat = uv.copy()
    flat[faces[0]] = flat[faces[0]][0]
    assert uv_quality.report(points, faces, flat, uv_faces)["over_1_5_percent"] >= 1.0


@test
def test_uv_quality_zero_margin_between_islands_warns():
    """Dos islas pegadas (margen 0) avisan; con hueco entre ellas, no. El hueco se mide hasta MARGIN_CAP."""
    points, faces, uv, uv_faces = split_columns(10)
    rep, (level, reasons) = grade_of(points, faces, uv, uv_faces)
    assert rep["islands"] == 2 and rep["margin_min"] < 1e-9, rep
    assert level == 1 and any("pegadas" in r for r in reasons), (level, reasons)
    assert abs(rep["texel_variation_percent"]) < 1e-9 and rep["overlapping_faces"] == 0, rep
    points, faces, uv, uv_faces = split_columns(10, gap=0.1)
    rep, (level, reasons) = grade_of(points, faces, uv, uv_faces)
    assert rep["margin_min"] == uv_quality.MARGIN_CAP and (level, reasons) == (0, []), (rep, reasons)
    # un hueco de 0,004 se mide tal cual
    points, faces, uv, uv_faces = split_columns(10, gap=0.004)
    assert abs(uv_quality.report(points, faces, uv, uv_faces)["margin_min"] - 0.004) < 1e-9


@test
def test_uv_quality_islands_and_texel_density():
    """Islas por UVs compartidas y variación de la densidad de texel entre ellas."""
    box = cube()
    faces = [list(map(int, f)) for f in box.faces()]
    # seis cuadrados sueltos, cada uno con sus UVs
    uv, uv_faces = [], []
    for k in range(len(faces)):
        uv.extend([(k * 1.2 + x, y) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))])
        uv_faces.append(list(range(len(uv) - 4, len(uv))))
    uv = np.array(uv) / 8.0
    rep = uv_quality.report(box.points, faces, uv, uv_faces)
    assert rep["islands"] == 6 and rep["texel_variation_percent"] < 1e-6, rep
    # una isla al doble de escala: la densidad ya no es la misma
    big = uv.copy()
    big[:4] = (big[:4] - big[0]) * 2.0 + big[0]
    rep = uv_quality.report(box.points, faces, big, uv_faces)
    assert rep["texel_variation_percent"] > 20.0 and rep["islands"] == 6, rep
    # compartir UVs une las islas; una cara sin UVs no tiene isla
    island, count = uv_quality.islands([[0, 1, 2, 3], [1, 4, 5, 2], [9, 8, 7, 6], []])
    assert count == 2 and island[0] == island[1] != island[2] and island[3] == -1, (island, count)


def reference_seam_continuity(points, faces, uv_points, uv_faces):
    """El recorrido por aristas de `seam_continuity` de tests/bench.py, sobre arrays."""
    import math
    edges = {}
    for face, uv_face in zip(faces, uv_faces):
        c = len(face)
        for k in range(c):
            a, b = face[k], face[(k + 1) % c]
            ua, ub = uv_face[k], uv_face[(k + 1) % c]
            if a > b:
                a, b, ua, ub = b, a, ub, ua
            edges.setdefault((a, b), []).append((ua, ub))
    total = seam = 0.0
    rows = []
    for (a, b), sides in edges.items():
        length = float(np.linalg.norm(points[a] - points[b]))
        total += length
        if len(sides) != 2 or (sides[0][0] == sides[1][0] and sides[0][1] == sides[1][1]):
            continue
        seam += length
        s1 = uv_points[sides[0][1]] - uv_points[sides[0][0]]
        s2 = uv_points[sides[1][1]] - uv_points[sides[1][0]]
        n1, n2 = np.linalg.norm(s1), np.linalg.norm(s2)
        if n1 < 1e-9 or n2 < 1e-9:
            continue
        rows.append((length, math.degrees(math.atan2(s1[0] * s2[1] - s1[1] * s2[0], float(s1 @ s2))), math.log2(n1 / n2)))
    rows = np.array(rows)
    w = rows[:, 0] / rows[:, 0].sum()
    ang, scl = np.abs(rows[:, 1]), np.abs(rows[:, 2])
    turn, same = ang < 15, scl < math.log2(1.1)
    return (100 * seam / total, 100.0 * float(w[turn].sum()), 100.0 * float(w[same].sum()), 100.0 * float(w[turn & same].sum()))


@test
def test_uv_quality_seam_continuity_matches_reference():
    """La continuidad en los cortes da lo mismo que el recorrido por aristas del banco, y baja si una isla se gira o se escala."""
    ball = uv_sphere(rings=8, segments=12, triangulate=False)
    faces = [list(map(int, f)) for f in ball.faces()]
    rng = np.random.RandomState(4)
    # cada cara con sus UVs propias (todas las aristas son cortes), giradas y escaladas al azar
    uv, uv_faces = [], []
    for face in faces:
        angle, scale = rng.choice([0.0, 0.05, 0.3, 1.5, 3.1]), rng.choice([1.0, 1.05, 1.5, 2.0])
        rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        flat = np.array([(0, 0), (1, 0), (1, 1), (0, 1)][:len(face)], dtype=float)
        uv.extend((flat @ rot.T * scale + rng.rand(2)).tolist())
        uv_faces.append(list(range(len(uv) - len(face), len(uv))))
    uv = np.array(uv)
    got = uv_quality.seam_continuity(ball.points, faces, uv, uv_faces)
    expected = reference_seam_continuity(ball.points, faces, uv, uv_faces)
    values = (got["seam_percent"], got["racord_rot_lt_15"], got["racord_scale_lt_10"], got["racord_percent"])
    assert np.allclose(values, expected, atol=1e-9), (values, expected)
    assert got["racord_percent"] < got["racord_rot_lt_15"] < 100.0, got
    # el plano cortado por la mitad: la derecha igual, girada 90 grados o al doble de escala
    for kwargs, rot, scl in (({}, 100.0, 100.0), ({"rotate": True}, 0.0, 100.0), ({"scale": 2.0}, 100.0, 0.0)):
        points, faces, uv, uv_faces = split_columns(10, **kwargs)
        got = uv_quality.seam_continuity(points, faces, uv, uv_faces)
        assert abs(got["seam_percent"] - 100.0 / 22) < 1e-9, got
        assert abs(got["racord_rot_lt_15"] - rot) < 1e-9 and abs(got["racord_scale_lt_10"] - scl) < 1e-9, (kwargs, got)
        assert abs(got["racord_percent"] - min(rot, scl)) < 1e-9, (kwargs, got)


@test
def test_uv_quality_overlap_search_matches_brute_force():
    """La búsqueda por rejilla da las mismas caras solapadas y el mismo margen que comparar todas con todas."""
    rng = np.random.RandomState(1)
    for trial in range(10):
        count = int(rng.randint(5, 50))
        size = rng.choice([0.03, 0.1, 0.3])
        uv, uv_faces = [], []
        for centre in rng.rand(count, 2):
            k = int(rng.choice([3, 4, 4, 5]))
            angle = np.sort(rng.rand(k) * 2 * np.pi)
            radius = size * (0.5 + rng.rand(k))
            uv.extend((centre + radius[:, None] * np.column_stack([np.cos(angle), np.sin(angle)])).tolist())
            uv_faces.append(list(range(len(uv) - k, len(uv))))
        uv = np.array(uv)
        counts, ids = uv_quality._flatten(uv_faces)
        tris, owner = uv_quality._uv_triangles(uv, counts, ids)
        i, j = np.triu_indices(len(tris), 1)
        keep = owner[i] != owner[j]
        i, j = i[keep], j[keep]
        over = uv_quality._triangles_overlap(tris[i], tris[j], uv_quality.OVERLAP_TOL)
        expected = np.zeros(count, dtype=bool)
        expected[owner[i[over]]] = expected[owner[j[over]]] = True
        assert (uv_quality.overlapping_faces(uv, uv_faces) == expected).all(), trial
        # margen: la menor distancia entre dos aristas de islas distintas
        island, number = uv_quality.islands(uv_faces)
        owner_edge = np.array([f for f, fc in enumerate(uv_faces) for _ in fc])
        start = np.array([uv[v] for fc in uv_faces for v in fc])
        stop = np.array([uv[fc[(a + 1) % len(fc)]] for fc in uv_faces for a in range(len(fc))])
        i, j = np.triu_indices(len(start), 1)
        keep = island[owner_edge[i]] != island[owner_edge[j]]
        i, j = i[keep], j[keep]
        best = min(0.05, float(uv_quality._segment_distance(start[i], stop[i], start[j], stop[j]).min()))
        assert abs(uv_quality.island_margin(uv, uv_faces, island, number, cap=0.05) - best) < 1e-12, trial
    # un quad cóncavo: el triángulo que cae en su hueco no se solapa, el que cae dentro sí
    arrow = np.array([[0, 0], [1, 0], [0.3, 0.3], [0, 1.0]])
    inside = np.vstack([arrow, [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]])
    notch = np.vstack([arrow, [[0.6, 0.6], [0.8, 0.6], [0.8, 0.8]]])
    assert uv_quality.overlapping_faces(inside, [[0, 1, 2, 3], [4, 5, 6]]).all()
    assert not uv_quality.overlapping_faces(notch, [[0, 1, 2, 3], [4, 5, 6]]).any()


@test
def test_uv_quality_is_fast_and_grades_the_baseline_as_acceptable():
    """40.000 caras en pocos segundos, y las cifras de la línea base del banco (2 de noviembre) no sacan nota 2."""
    points, faces, uv, uv_faces = uv_plane(200)
    start = time.time()
    rep = uv_quality.report(points, faces, uv, uv_faces)
    assert time.time() - start < 20.0 and rep["overlapping_faces"] == 0 and rep["faces"] == 40000, rep
    # (usado %, > 1,5x %, estiramiento medio, giro < 15 grados %, costuras %) de los 10 resultados de tests/baseline.json
    baseline = [(64.59, 7.41, 1.12, 23.11, 10.84), (60.59, 6.78, 1.173, 21.57, 9.89), (56.03, 9.73, 1.141, 2.91, 3.84),
                (48.93, 9.57, 1.19, 7.35, 3.51), (60.31, 3.51, 1.077, 27.46, 7.28), (61.58, 5.1, 1.114, 22.28, 8.01),
                (56.07, 6.52, 1.14, 15.53, 9.6), (58.85, 6.61, 1.128, 27.78, 10.55), (60.28, 1.23, 1.069, 15.73, 5.95),
                (53.61, 4.26, 1.1, 19.93, 4.68)]
    for used, over, mean, rot, seam in baseline:
        # la continuidad con giro y escala a la vez no pasa de la del giro: se prueba ese tope y su mitad
        for racord in (rot, rot / 2.0):
            case = dict(rep, used_percent=used, over_1_5_percent=over, stretch_mean=mean, racord_percent=racord,
                        seam_percent=seam, islands=30, margin_min=0.004, texel_variation_percent=10.0)
            level, reasons = uv_quality.grade(case)
            assert level < 2, (used, over, mean, rot, reasons)
    # lo que dio uv_quality.report sobre el prop, el humano y el hardsurface (retopo de Maya a 5.000 caras): nota 0
    # (usado, > 1,5x, estiramiento, continuidad, costuras, margen, variación de texel)
    measured = [(60.2777, 1.228, 1.0687, 10.8952, 5.9499, 0.0052, 0.1421), (60.3128, 3.5132, 1.0772, 17.0262, 7.2752, 0.0053, 0.0811),
                (56.0299, 9.7298, 1.1413, 0.2417, 3.8445, 0.0061, 0.0039)]
    for used, over, mean, racord, seam, margin, texel in measured:
        case = dict(rep, used_percent=used, over_1_5_percent=over, stretch_mean=mean, racord_percent=racord,
                    seam_percent=seam, islands=20, margin_min=margin, texel_variation_percent=texel)
        assert uv_quality.grade(case) == (0, []), (uv_quality.grade(case), case)


# ------------------------------------------------------------------ enderezado de islas de UV (uv_straighten)

def uv_tube(rows, columns, radius=1.0, height=4.0, taper=1.0, fan=0.5):
    """Tubo de quads (columns alrededor, rows a lo largo, abierto por los dos extremos) cortado a lo largo por una costura
    y desplegado en abanico: las filas son arcos y las columnas, radios. `fan` = giro total entre las dos orillas del corte
    (radianes). `taper`: radio del extremo de arriba frente al de abajo. Devuelve (puntos, caras, UV, caras UV)."""
    points, uv = [], []
    for i in range(rows + 1):
        t = i / rows
        r = radius * (1.0 + (taper - 1.0) * t)
        for j in range(columns + 1):
            a = 2.0 * np.pi * j / columns
            if j < columns:
                points.append((r * np.cos(a), r * np.sin(a), height * t))
            # abanico: radio de giro grande frente al ancho del tubo, así cada fila mide casi lo que mide en 3D
            rho = 2.0 * np.pi * radius / fan + height * t
            theta = fan * j / columns
            uv.append((rho * np.sin(theta), rho * np.cos(theta)))

    def point_id(i, j):
        return i * columns + j % columns

    def uv_id(i, j):
        return i * (columns + 1) + j
    faces = [[point_id(i, j), point_id(i, j + 1), point_id(i + 1, j + 1), point_id(i + 1, j)]
             for i in range(rows) for j in range(columns)]
    uv_faces = [[uv_id(i, j), uv_id(i, j + 1), uv_id(i + 1, j + 1), uv_id(i + 1, j)]
                for i in range(rows) for j in range(columns)]
    return np.array(points), faces, np.array(uv), uv_faces


def island_stretch(points, faces, uv, uv_faces):
    """(estiramiento medio, % de caras > 1,5x) de unas UV, con la densidad de la propia malla como referencia."""
    world = uv_quality.face_areas(points, faces)
    flat = np.abs(uv_quality.uv_face_areas(uv, uv_faces)[0])
    rep = uv_quality.stretch_stats(world, flat)
    return rep["stretch_mean"], rep["over_1_5_percent"]


@test
def test_straighten_tube_cut_lengthwise():
    """Un tubo de quads cortado a lo largo y desplegado en abanico: las dos orillas del corte no casan (giro de unos 29
    grados); tras enderezarlo casan del todo, sin estirar más y sin caras en espejo ni solapes."""
    points, faces, uv, uv_faces = uv_tube(10, 16, fan=0.5)
    before = uv_quality.seam_continuity(points, faces, uv, uv_faces)
    assert before["racord_rot_lt_15"] < 1.0, before
    new, done = uv_straighten.straighten(points, faces, uv, uv_faces)
    assert len(done) == 1 and done[0]["matched"][0] < 1.0 and done[0]["matched"][1] > 99.0, done
    after = uv_quality.seam_continuity(points, faces, new, uv_faces)
    assert after["racord_rot_lt_15"] > 99.0 and after["racord_scale_lt_10"] > 99.0, after
    old_mean, old_over = island_stretch(points, faces, uv, uv_faces)
    mean, over = island_stretch(points, faces, new, uv_faces)
    assert mean <= old_mean + uv_straighten.STRETCH_TOLERANCE and over <= old_over, (old_mean, mean, old_over, over)
    rep = uv_quality.report(points, faces, new, uv_faces)
    assert rep["overlapping_faces"] == 0 and rep["flipped_faces"] == 0, rep
    # las filas quedan rectas: la primera arista del corte, en las dos orillas, es la misma (misma dirección y longitud)
    first = uv_faces[0]
    left, right = new[first[3]] - new[first[0]], new[uv_faces[15][2]] - new[uv_faces[15][1]]
    assert np.allclose(left, right, atol=5e-2 * np.linalg.norm(left)), (left, right)


@test
def test_straighten_ring_of_quads():
    """Un anillo de una sola fila de quads (un cinturón) cortado por un sitio también se endereza: la arista del corte
    casa en las dos orillas."""
    points, faces, uv, uv_faces = uv_tube(1, 12, radius=1.0, height=0.3, fan=0.6)
    assert uv_quality.seam_continuity(points, faces, uv, uv_faces)["racord_rot_lt_15"] < 1.0
    new, done = uv_straighten.straighten(points, faces, uv, uv_faces)
    assert len(done) == 1, done
    assert uv_quality.seam_continuity(points, faces, new, uv_faces)["racord_rot_lt_15"] > 99.0
    result = uv_straighten.straighten_island(points, faces, uv, uv_faces, range(len(faces)))
    assert result is not None and len(result["uv_ids"]) == len(uv) and result["uv"].shape == (len(uv), 2)


@test
def test_straighten_leaves_islands_that_are_not_tubes():
    """Sin corte contra sí misma (rejilla plana, o un tubo con la costura ya casada), con un n-gon o demasiado grande, la
    isla se deja como está: None."""
    plane = grid(6)
    points = np.asarray(plane.points, dtype=float)
    faces = [list(f) for f in plane.faces()]
    uv = points[:, :2] * 0.1
    assert uv_straighten.straighten_island(points, faces, uv, faces, range(len(faces))) is None
    new, done = uv_straighten.straighten(points, faces, uv, faces)
    assert not done and np.array_equal(new, uv)
    # tubo desplegado sin abanico (fan casi 0): las dos orillas ya casan y no hay nada que ganar
    points, faces, uv, uv_faces = uv_tube(8, 12, fan=0.01)
    assert uv_quality.seam_continuity(points, faces, uv, uv_faces)["racord_rot_lt_15"] > 99.0
    assert uv_straighten.straighten_island(points, faces, uv, uv_faces, range(len(faces))) is None
    # un pentágono en la isla
    points, faces, uv, uv_faces = uv_tube(6, 12, fan=0.5)
    faces[3], uv_faces[3] = faces[3] + [faces[3][0]], uv_faces[3] + [uv_faces[3][0]]
    assert uv_straighten.straighten_island(points, faces, uv, uv_faces, range(len(faces))) is None
    # más vértices que el límite
    points, faces, uv, uv_faces = uv_tube(6, 12, fan=0.5)
    keep = uv_straighten.MAX_VERTICES
    uv_straighten.MAX_VERTICES = 10
    try:
        assert uv_straighten.straighten_island(points, faces, uv, uv_faces, range(len(faces))) is None
    finally:
        uv_straighten.MAX_VERTICES = keep


@test
def test_straighten_never_raises_the_stretch():
    """El estiramiento de una isla no sube: en un cono muy cónico, donde un tubo recto exigiría estirar el desplegado
    (que es exacto), la isla no se toca; en el resto de tubos el estiramiento medio y las caras > 1,5x no suben."""
    for rows, columns, taper, fan in ((10, 16, 3.0, 0.5), (10, 16, 1.0, 1.2), (14, 10, 0.6, 0.8), (6, 24, 1.0, 0.3),
                                      (10, 16, 0.2, 1.0)):
        points, faces, uv, uv_faces = uv_tube(rows, columns, taper=taper, fan=fan)
        old_mean, old_over = island_stretch(points, faces, uv, uv_faces)
        new, done = uv_straighten.straighten(points, faces, uv, uv_faces)
        mean, over = island_stretch(points, faces, new, uv_faces)
        assert mean <= old_mean + uv_straighten.STRETCH_TOLERANCE and over <= old_over, (taper, fan, old_mean, mean, old_over, over)
        rep = uv_quality.report(points, faces, new, uv_faces)
        assert rep["overlapping_faces"] == 0 and rep["flipped_faces"] == 0, (taper, fan, rep)
        if done:
            assert done[0]["matched"][1] > done[0]["matched"][0]


@test
def test_straighten_only_touches_the_islands_that_improve():
    """Con un tubo y una rejilla plana en la misma malla, las UV de la rejilla no cambian y las del tubo sí."""
    points, faces, uv, uv_faces = uv_tube(8, 12, fan=0.5)
    plane = grid(4)
    count = len(points)
    points = np.vstack([points, np.asarray(plane.points, dtype=float)])
    faces = faces + [[v + count for v in f] for f in plane.faces()]
    flat_uv = np.asarray(plane.points, dtype=float)[:, :2] * 0.1 + 5.0
    uv_count = len(uv)
    uv = np.vstack([uv, flat_uv])
    uv_faces = uv_faces + [[v + uv_count for v in f] for f in plane.faces()]
    new, done = uv_straighten.straighten(points, faces, uv, uv_faces)
    assert len(done) == 1 and done[0]["faces"] == 8 * 12, done
    assert np.array_equal(new[uv_count:], uv[uv_count:]) and not np.allclose(new[:uv_count], uv[:uv_count])


# ------------------------------------------------------------------ escala de trabajo

BIN = os.path.join(ROOT, "mbl_retopo_uv", "bin")


def engines_available():
    """[(motor, ejecutable)] de los que hay en este equipo (el de compilación del proyecto o el de bin/)."""
    found = []
    for engine, names in (("instant_meshes", (INSTANT_MESHES,)),
                          ("quadriflow", (QUADRIFLOW, os.path.join(BIN, "quadriflow.exe")))):
        path = next((name for name in names if os.path.isfile(name)), None)
        if path:
            found.append((engine, path))
    return found


def bumpy_sphere():
    """Esfera deformada con ondas (algo parecido a un escaneo orgánico) de diagonal ~11: (puntos, caras)."""
    sphere = uv_sphere(2.0, 40, 48)
    points = np.array(sphere.points)
    bump = 1.0 + 0.08 * np.sin(points[:, 0] * 2.1) * np.cos(points[:, 1] * 1.7) + 0.05 * np.sin(points[:, 2] * 3.3)
    return points * bump[:, None] * np.array([1.0, 1.3, 1.0]), sphere.faces()


@test
def test_work_scale_factor():
    """Con un tamaño razonable no se toca nada (identidad exacta); fuera, una potencia de 10 que lleva la diagonal al rango de
    un decenio, así que el mismo modelo en cm, m o mm llega al motor con el mismo tamaño."""
    for size in (38.364, 55.575, 251.8, 257.63, scale.DIAGONAL_MIN, 100.0, 299.9):    # mallas del banco y límites del rango
        assert scale.work_factor(size) == 1.0, size
    for size, factor in ((21.0, 10.0), (0.21, 1e3), (2100.0, 0.1), (300.0, 0.1), (29.99, 10.0), (1e5, 1e-3), (0.001, 1e5)):
        assert abs(scale.work_factor(size) / factor - 1.0) < 1e-12, (size, scale.work_factor(size), factor)
    for size in np.geomspace(1e-6, 1e5, 400):
        factor = scale.work_factor(size)
        assert scale.DIAGONAL_MIN * (1 - 1e-9) <= size * factor < scale.DIAGONAL_MAX * (1 + 1e-9), size
        assert abs(np.log10(factor) - round(np.log10(factor))) < 1e-9, (size, factor)         # potencia de 10
        assert scale.work_factor(size * factor) == 1.0, size                                    # idempotente
        for unit in (0.01, 0.1, 10.0, 100.0, 1000.0):                                          # cm, m, mm...
            other = size * unit
            assert abs(other * scale.work_factor(other) / (size * factor) - 1.0) < 1e-9, (size, unit)
    # sin sentido como tamaño: se deja como está
    for size in (0.0, -3.0, float("nan"), float("inf"), 1e-20, 1e20):
        assert scale.work_factor(size) == 1.0, size
    assert scale.diagonal(np.zeros((0, 3))) == 0.0 and abs(scale.diagonal([[0, 0, 0], [3, 4, 12]]) - 13.0) < 1e-12


@test
def test_work_scale_decorator():
    """at_work_scale: la función ve el modelo a la escala de trabajo, el resultado vuelve a la del modelo y, con un tamaño
    razonable, recibe los mismos puntos tal cual."""
    seen, messages = [], []

    @scale.at_work_scale
    def fake(points, faces, extra=None, log=None):
        seen.append(np.array(points, dtype=float))
        return np.array(points, dtype=float) * 0.5, faces, {"kept": True}

    small = np.array([[0.0, 0.0, 0.0], [0.03, 0.04, 0.12], [0.01, 0.0, 0.0]])        # diagonal 0,13
    new_points, faces, report = fake(small, [[0, 1, 2]], log=messages.append)
    assert abs(scale.diagonal(seen[0]) - 130.0) < 1e-6, scale.diagonal(seen[0])
    assert np.allclose(new_points, small * 0.5, rtol=1e-12, atol=0) and faces == [[0, 1, 2]]
    assert report["kept"] and report["scale"] == 1e3, report
    assert len(messages) == 1 and "escala x1000" in messages[0], messages
    normal = np.array([[0.0, 0.0, 0.0], [30.0, 40.0, 120.0], [10.0, 0.0, 0.0]])      # diagonal 130
    probe = {}

    @scale.at_work_scale
    def same(points, faces, log=None):
        probe["points"] = points
        return points, faces, {}
    assert same(normal, [[0, 1, 2]], log=messages.append)[2] == {} and probe["points"] is normal and len(messages) == 1


@test
def test_core_retopo_does_not_depend_on_units():
    """El mismo modelo en cm, en m (x0,01), x10 y x100 da en el núcleo las mismas caras y polos (1 %) y cada resultado
    queda con el tamaño de su original; best_retopo igual."""
    points, faces = bumpy_sphere()
    engines = engines_available()
    if not engines:
        print("    (sin motores: se omite)")
        return
    for engine, executable in engines:
        outcomes = []
        for factor in (1.0, 0.01, 100.0, 10.0):
            new_points, new_faces, report = retopo.retopo(points * factor, faces, 600, engine, executable, symmetric=False)
            stats = metrics.topology_stats(Mesh(new_points, new_faces))
            ratio = scale.diagonal(new_points) / scale.diagonal(points * factor)
            assert 0.95 < ratio < 1.05, (engine, factor, ratio)
            assert report.get("scale", 1.0) == scale.work_factor(scale.diagonal(points * factor)), (engine, factor, report)
            assert ("scale" in report) == (factor != 10.0), (engine, factor, report)       # x10 ya tiene un tamaño razonable
            outcomes.append((stats["faces"], stats["pole_count"], ratio))
        base = outcomes[0]
        for faces_count, poles, ratio in outcomes[1:]:
            assert abs(faces_count - base[0]) <= 0.01 * base[0] and abs(poles - base[1]) <= 0.01 * base[1], (engine, outcomes)
            assert abs(ratio / base[2] - 1.0) < 0.01, (engine, outcomes)
    engine, executable = engines[-1]
    new_points, new_faces, report = retopo.best_retopo(points * 0.01, faces, 600, engine, executable, variants=1)
    assert report["scale"] == 1e3 and 0.95 < scale.diagonal(new_points) / scale.diagonal(points * 0.01) < 1.05, report["scale"]


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
