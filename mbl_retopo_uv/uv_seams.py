# -*- coding: utf-8 -*-
"""Colocación de costuras: esconderlas, enderezarlas y no dejar islas diminutas.

Unfold3D (u3dAutoSeam) decide bien DÓNDE hace falta cortar para desplegar, pero no le importa que
la costura se vea: en las pruebas cruzaba la cara del personaje, iba en zigzag y dejaba islas de
pocas caras. Aquí se parte de sus costuras y:
  1. Cada tramo de costura entre dos cruces se vuelve a trazar por el camino más barato entre los
     mismos extremos. El coste de una arista es su longitud por lo visible que es: los huecos
     (axilas, entrepierna, detrás de las orejas) y la parte de atrás (-Z, Maya mira a +Z) son
     baratos; las zonas expuestas del frente son caras.
  2. Los tramos que acaban en punta (cortes de alivio) pueden mover también su punta.
  3. Las islas con menos del 0,5% del área se cosen a la vecina con la que más borde comparten.
Un cambio solo se acepta si todas las islas siguen siendo discos (característica de Euler 1):
así se pueden desplegar sin solapes, igual que con los cortes originales.
"""

import heapq
import math

import maya.api.OpenMaya as om
import numpy as np

from .common import dag_path, mesh_shape


class SeamGraph:
    def __init__(self, transform):
        path = dag_path(mesh_shape(transform))
        fn = om.MFnMesh(path)
        self.fn = fn
        self.points = np.array([(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)], dtype=float)
        self.normals = np.array([(n.x, n.y, n.z) for n in fn.getVertexNormals(False, om.MSpace.kWorld)],
                                dtype=float)
        counts, vertices = fn.getVertices()
        self.face_count = len(counts)
        self.edge_v = []
        self.edge_f = []
        it = om.MItMeshEdge(path)
        while not it.isDone():
            self.edge_v.append((it.vertexId(0), it.vertexId(1)))
            self.edge_f.append(tuple(it.getConnectedFaces()))
            it.next()
        self.vertex_edges = [[] for _ in range(len(self.points))]
        for e, (a, b) in enumerate(self.edge_v):
            self.vertex_edges[a].append(e)
            self.vertex_edges[b].append(e)
        self.border = {e for e, faces in enumerate(self.edge_f) if len(faces) == 1}
        self.border_vertices = {v for e in self.border for v in self.edge_v[e]}
        self.length = np.array([np.linalg.norm(self.points[a] - self.points[b]) for a, b in self.edge_v])
        # área de cada cara (normal de Newell / 2)
        counts = np.array(counts, dtype=np.int64)
        flat = np.array(vertices, dtype=np.int64)
        face_id = np.repeat(np.arange(self.face_count), counts)
        ends = np.cumsum(counts) - 1
        following = np.arange(len(flat)) + 1
        following[ends] = ends - counts + 1
        newell = np.zeros((self.face_count, 3))
        np.add.at(newell, face_id, np.cross(self.points[flat], self.points[flat[following]]))
        self.face_area = 0.5 * np.linalg.norm(newell, axis=1)
        self._cost = None
        self.loops = []
        seen = set()
        for v in sorted(self.border_vertices):
            if v not in seen:
                loop = self.border_loop(v)
                seen |= loop
                self.loops.append(loop)

    @property
    def cost(self):
        """Coste de cortar cada arista: longitud x visibilidad. Se calcula al usarlo (lanza rayos)."""
        if self._cost is None:
            vis = self.visibility = self._visibility()
            # coste muy contrastado: una arista expuesta cuesta ~13 veces más que una escondida
            self._cost = np.array([self.length[e] * (0.05 + 2.0 * (0.5 * (vis[a] + vis[b])) ** 2)
                                   for e, (a, b) in enumerate(self.edge_v)])
        return self._cost

    def _visibility(self, ray_count=32):
        """0 = escondido (hueco o detrás), 1 = muy expuesto (frente, convexo)."""
        golden = math.pi * (3.0 - math.sqrt(5.0))
        dirs = []
        for i in range(ray_count * 2):
            y = 1.0 - 2.0 * (i + 0.5) / (ray_count * 2)
            r = math.sqrt(1.0 - y * y)
            dirs.append((math.cos(golden * i) * r, y, math.sin(golden * i) * r))
        dirs = np.array(dirs)
        size = float(np.linalg.norm(self.points.max(axis=0) - self.points.min(axis=0))) or 1.0
        eps = size * 1e-4
        accel = self.fn.autoUniformGridParams()
        openness = np.ones(len(self.points))
        for v, (p, n) in enumerate(zip(self.points, self.normals)):
            facing = dirs @ n
            use = facing > 0.05
            if not use.any():
                continue
            source = om.MFloatPoint(*(p + n * eps))
            free = total = 0.0
            for d, w in zip(dirs[use], facing[use]):
                hit = self.fn.anyIntersection(source, om.MFloatVector(*d), om.MSpace.kWorld, size, False,
                                              accelParams=accel)
                total += w
                if hit is None:
                    free += w
            openness[v] = free / total if total else 1.0
        front = 0.5 * (1.0 + self.normals[:, 2])  # 1 mirando a +Z (frente), 0 mirando atrás
        return openness * (0.3 + 0.7 * front)

    # ---------------------------------------------------------------- topología de las islas

    def charts(self, seams):
        """(isla de cada cara, nº de islas) cortando por `seams` y por los bordes."""
        parent = list(range(self.face_count))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for e, faces in enumerate(self.edge_f):
            if len(faces) == 2 and e not in seams:
                a, b = find(faces[0]), find(faces[1])
                if a != b:
                    parent[a] = b
        roots = {}
        chart = [roots.setdefault(find(f), len(roots)) for f in range(self.face_count)]
        return chart, len(roots)

    # nº de islas que no son disco que all_disks deja pasar (las que ya venían así y son diminutas)
    tolerated = 0
    TINY_CHART_FACES = 8

    def tiny_non_disks(self, seams):
        """Nº de islas que no son disco si todas son diminutas (pocas caras); None si alguna es grande."""
        self.all_disks(seams)
        chart, _ = self.charts(seams)
        sizes = np.bincount(np.asarray(chart), minlength=len(self._euler))
        bad = [c for c, x in enumerate(self._euler) if x != 1]
        if any(sizes[c] > self.TINY_CHART_FACES for c in bad):
            return None
        return len(bad)

    def all_disks(self, seams):
        """True si todas las islas se pueden aplanar: discos, o discos con agujeros propios del modelo
        (ojos, boca) que ninguna costura toca. Euler V - E + F = 1 - agujeros. Devuelve (ok, nº de islas)."""
        chart, count = self.charts(seams)
        euler = [0] * count
        seam_vertices = {v for e in seams for v in self.edge_v[e]}
        for loop in self.loops:
            if loop & seam_vertices:
                continue
            edge = next(e for e in self.vertex_edges[next(iter(loop))] if e in self.border)
            euler[chart[self.edge_f[edge][0]]] += 1
        for f in range(self.face_count):
            euler[chart[f]] += 1
        for e, faces in enumerate(self.edge_f):
            if len(faces) == 2 and e not in seams:
                euler[chart[faces[0]]] -= 1
            else:
                for f in faces:
                    euler[chart[f]] -= 1
        # vértices: cuántos "abanicos" de caras (unidos por aristas no cortadas) tiene cada vértice
        for v, edges in enumerate(self.vertex_edges):
            faces = set()
            for e in edges:
                faces.update(self.edge_f[e])
            parent = {f: f for f in faces}

            def find(x):
                while parent[x] != x:
                    x = parent[x]
                return x
            for e in edges:
                ef = self.edge_f[e]
                if len(ef) == 2 and e not in seams:
                    a, b = find(ef[0]), find(ef[1])
                    if a != b:
                        parent[a] = b
            for root in {find(f) for f in faces}:
                euler[chart[root]] += 1
        self._euler = euler
        return sum(1 for x in euler if x != 1) <= self.tolerated, count

    def make_disks(self, seams):
        """Añade los cortes que faltan para que todas las islas se puedan aplanar.

        Unfold3D a veces deja una isla con forma de toro o de tubo abierto por un solo tajo (con un
        toro de prueba: 31-88% de caras estiradas más de 1,5x). Se usa el grafo de corte clásico:
        árbol de caras que cruza primero las aristas más visibles (así no se cortan) y, de las aristas
        que quedan fuera del árbol, se podan las ramas sueltas. Lo que sobrevive son los cortes
        imprescindibles, por las zonas menos visibles.
        """
        seams = set(seams) - self.border
        parent = list(range(self.face_count))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        candidates = set()
        interior = [e for e, faces in enumerate(self.edge_f) if len(faces) == 2 and e not in seams]
        for e in sorted(interior, key=lambda e: -self.cost[e]):
            a, b = find(self.edge_f[e][0]), find(self.edge_f[e][1])
            if a != b:
                parent[a] = b
            else:
                candidates.add(e)
        # los agujeros propios del modelo que ninguna costura toca no anclan cortes
        seam_vertices = {v for e in seams for v in self.edge_v[e]}
        free_holes = set()
        for loop in self.loops:
            if not loop & seam_vertices:
                free_holes |= {e for v in loop for e in self.vertex_edges[v] if e in self.border}
        anchors = seams | (self.border - free_holes)
        # una pieza cerrada sin ningún corte (esfera) necesita al menos uno: la arista más escondida
        chart, count = self.charts(seams)
        anchored = {chart[f] for e in anchors for f in self.edge_f[e]}
        cheapest = {}
        for e in candidates:
            c = chart[self.edge_f[e][0]]
            if c not in anchored and (c not in cheapest or self.cost[e] < self.cost[cheapest[c]]):
                cheapest[c] = e
        forced = set(cheapest.values())
        candidates -= forced
        seams = seams | forced
        anchors |= forced
        degree = {}
        for e in candidates | anchors:
            for v in self.edge_v[e]:
                degree[v] = degree.get(v, 0) + 1
        stack = [v for v, d in degree.items() if d == 1]
        while stack:
            v = stack.pop()
            if degree.get(v) != 1:
                continue
            edge = next((e for e in self.vertex_edges[v] if e in candidates), None)
            if edge is None:
                continue
            candidates.discard(edge)
            for w in self.edge_v[edge]:
                degree[w] -= 1
                if degree[w] == 1:
                    stack.append(w)
        return seams | candidates

    # ---------------------------------------------------------------- tramos de costura

    def chains(self, seams):
        """Tramos de costura entre extremos (cruces, puntas o bordes): [(lista de aristas, v0, v1)]."""
        degree = {}
        for e in seams:
            for v in self.edge_v[e]:
                degree[v] = degree.get(v, 0) + 1
        ends = {v for v, d in degree.items() if d != 2 or v in self.border_vertices}
        seen, result = set(), []
        for start in ends:
            for e in self.vertex_edges[start]:
                if e not in seams or e in seen:
                    continue
                chain, v = [], start
                while True:
                    seen.add(e)
                    chain.append(e)
                    a, b = self.edge_v[e]
                    v = b if a == v else a
                    if v in ends:
                        break
                    nxt = [x for x in self.vertex_edges[v] if x in seams and x not in seen]
                    if not nxt:
                        break
                    e = nxt[0]
                result.append((chain, start, v))
        return result, degree

    def ring(self, vertices, steps):
        """Vértices a `steps` aristas o menos de `vertices`."""
        result = set(vertices)
        frontier = set(vertices)
        for _ in range(steps):
            nxt = set()
            for v in frontier:
                for e in self.vertex_edges[v]:
                    for w in self.edge_v[e]:
                        if w not in result:
                            nxt.add(w)
            result |= nxt
            frontier = nxt
        return result

    def border_loop(self, vertex):
        """Vértices del agujero (borde abierto) al que pertenece `vertex`."""
        loop, stack = {vertex}, [vertex]
        while stack:
            v = stack.pop()
            for e in self.vertex_edges[v]:
                if e in self.border:
                    for w in self.edge_v[e]:
                        if w not in loop:
                            loop.add(w)
                            stack.append(w)
        return loop

    TURN_PENALTY = 1.0

    def _paths(self, start, blocked_vertices, blocked_edges):
        """Dijkstra desde start (un vértice o un conjunto). Devuelve (distancia por vértice, rastro).

        Girar (pasar a una arista que comparte cara con la anterior) cuesta un extra: así la costura
        sigue los loops en línea recta en vez de hacer escalera en diagonal.
        """
        starts = set(start) if isinstance(start, (set, frozenset, list, tuple)) else {start}
        # estado = (vértice, arista por la que se llegó)
        best = {(v, -1): 0.0 for v in starts}
        prev = {}
        dist = {v: 0.0 for v in starts}
        arrival = {v: (v, -1) for v in starts}
        heap = [(0.0, v, -1) for v in starts]
        heapq.heapify(heap)
        while heap:
            d, v, came = heapq.heappop(heap)
            if d > best.get((v, came), math.inf):
                continue
            if v in blocked_vertices and v not in starts:
                continue  # se puede llegar pero no pasar a través
            came_faces = set(self.edge_f[came]) if came >= 0 else ()
            for e in self.vertex_edges[v]:
                if e in blocked_edges or e == came:
                    continue
                a, b = self.edge_v[e]
                w = b if a == v else a
                step = self.cost[e]
                if came_faces and came_faces.intersection(self.edge_f[e]):
                    step += self.TURN_PENALTY * self.cost[e]
                nd = d + step
                if nd < best.get((w, e), math.inf):
                    best[(w, e)] = nd
                    prev[(w, e)] = (v, came)
                    heapq.heappush(heap, (nd, w, e))
                    if nd < dist.get(w, math.inf):
                        dist[w] = nd
                        arrival[w] = (w, e)
        return dist, (prev, arrival)

    def _trace(self, trail, start, end):
        prev, arrival = trail
        path, state = [], arrival[end]
        while state[1] >= 0:
            path.append(state[1])
            state = prev[state]
        return path


def mirror_edges(graph, tolerance=1e-4):
    """Arista espejo (reflejo en X por el centro del modelo) de cada arista, o None si la malla no es
    simétrica. Exige que casi todos los vértices tengan pareja (las mallas del retopo simétrico la
    tienen exacta)."""
    points = graph.points
    if not len(points):
        return None, None
    low, high = points.min(axis=0), points.max(axis=0)
    size = float(np.linalg.norm(high - low)) or 1.0
    center = 0.5 * (low[0] + high[0])
    step = tolerance * size
    keys = {}
    for i, p in enumerate(np.round(points / step).astype(np.int64)):
        keys.setdefault(tuple(p), i)
    mirrored = points.copy()
    mirrored[:, 0] = 2.0 * center - mirrored[:, 0]
    partner = np.full(len(points), -1, dtype=np.int64)
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    for i, q in enumerate(np.round(mirrored / step).astype(np.int64)):
        for dx, dy, dz in offsets:
            j = keys.get((q[0] + dx, q[1] + dy, q[2] + dz))
            if j is not None and np.linalg.norm(points[j] - mirrored[i]) <= 2 * step:
                partner[i] = j
                break
    if np.mean(partner >= 0) < MIRROR_MIN_MATCH:
        return None, center
    by_vertices = {frozenset(v): e for e, v in enumerate(graph.edge_v)}
    edge_mirror = []
    for a, b in graph.edge_v:
        if partner[a] < 0 or partner[b] < 0:
            edge_mirror.append(None)
        else:
            edge_mirror.append(by_vertices.get(frozenset((int(partner[a]), int(partner[b])))))
    if np.mean([m is not None for m in edge_mirror]) < MIRROR_MIN_MATCH:
        return None, center
    return edge_mirror, center


# fracción de vértices y aristas que deben tener pareja para considerar la malla simétrica
MIRROR_MIN_MATCH = 0.98


def symmetrize_seams(transform, seams, graph=None):
    """Costuras simétricas: las del lado +X (y las del centro) se copian al lado -X.

    Así cada isla tiene una gemela con la misma forma (el brazo izquierdo y el derecho), que es lo
    que se espera al texturizar un personaje. Devuelve las costuras nuevas o None si la malla no es
    simétrica o el resultado no se puede aplanar.
    """
    graph = graph or SeamGraph(transform)
    mirror, center = mirror_edges(graph)
    if mirror is None:
        return None
    seams = set(seams) - graph.border
    for _ in range(3):
        result = set()
        for e in seams:
            a, b = graph.edge_v[e]
            mid = 0.5 * (graph.points[a][0] + graph.points[b][0])
            if mid >= center - 1e-6:
                result.add(e)
                if mirror[e] is not None:
                    result.add(mirror[e])
        result -= graph.border
        if graph.all_disks(result)[0]:
            return sorted(result)
        # algún corte del lado -X era imprescindible: se completan los que faltan y se repite
        seams = graph.make_disks(result)
    return None


def fix_charts(transform, seams):
    """Si alguna isla no se puede aplanar, devuelve (costuras con los cortes que faltan, nº de cortes
    añadidos); si todas están bien, None."""
    graph = SeamGraph(transform)
    seams = set(seams) - graph.border
    if graph.all_disks(seams)[0]:
        return None
    fixed = graph.make_disks(seams)
    if fixed == seams:
        return None
    return sorted(fixed), len(fixed - seams)


def _chain_cost(graph, chain):
    return sum(graph.cost[e] for e in chain) + sum(
        graph.TURN_PENALTY * graph.cost[e2] for e1, e2 in zip(chain, chain[1:])
        if set(graph.edge_f[e1]).intersection(graph.edge_f[e2]))


def reroute_slits(graph, seams):
    """Vuelve a trazar los cortes que abren un tubo por el sitio menos visible.

    Un tubo (torso, brazo, pierna) necesita un corte a lo largo para desplegarse. Unfold3D lo pone
    donde le viene bien (en el humano, por delante del pecho). Ese corte es un tramo con la misma isla
    a los dos lados que une dos bordes distintos de la isla (cuello y cintura). Aquí se busca el camino
    más barato entre esos dos bordes, con los extremos libres de deslizarse por ellos: así acaba en la
    espalda o bajo los brazos. Devuelve (costuras, nº de cortes movidos).
    """
    moved = 0
    for _ in range(20):
        changed = False
        chains, _ = graph.chains(seams)
        for chain, a, b in chains:
            faces = graph.edge_f[chain[0]]
            if len(faces) != 2 or not set(chain) <= seams:
                continue
            chart, count = graph.charts(seams)
            if chart[faces[0]] != chart[faces[1]]:
                continue  # separa dos islas: no es un corte de tubo
            c = chart[faces[0]]
            others = seams - set(chain)
            inside = {f for f in range(graph.face_count) if chart[f] == c}
            # bordes de la isla sin este corte: aristas de costura o de borde que tocan sus caras
            rim = [e for e in (others | graph.border) if any(f in inside for f in graph.edge_f[e])]
            # componentes conexas de esos bordes
            parent = {}

            def find(x):
                while parent.setdefault(x, x) != x:
                    x = parent[x]
                return x
            for e in rim:
                u, v = graph.edge_v[e]
                parent[find(u)] = find(v)
            if a not in parent or b not in parent or find(a) == find(b):
                continue
            sources = {v for v in parent if find(v) == find(a)}
            targets = {v for v in parent if find(v) == find(b)}
            allowed = {e for e in range(len(graph.edge_v))
                       if e not in others and len(graph.edge_f[e]) == 2 and graph.edge_f[e][0] in inside
                       and graph.edge_f[e][1] in inside}
            blocked_edges = set(range(len(graph.edge_v))) - allowed
            blocked_vertices = set(parent) - sources - targets
            # tampoco a lo largo de un borde (dejaría tiras): los vértices del borde de origen solo
            # sirven para salir
            dist, trail = graph._paths(sources, blocked_vertices | targets, blocked_edges)
            reachable = [v for v in targets if v in dist]
            if not reachable:
                continue
            end = min(reachable, key=dist.get)
            if dist[end] >= SLIT_GAIN * _chain_cost(graph, chain):
                continue
            path = graph._trace(trail, None, end)
            trial = others | set(path)
            ok, new_count = graph.all_disks(trial)
            if ok and new_count == count:
                seams = trial
                moved += 1
                changed = True
                break
        if not changed:
            break
    return seams, moved


# un corte de tubo solo se mueve si el camino nuevo cuesta menos de esta fracción del actual
SLIT_GAIN = 0.85
# exposición (coste / longitud) a partir de la cual una costura central se considera visible
CENTER_EXPOSURE = 0.3


def optimize_seams(transform, seams, merge_area=0.005, passes=3, relocate=True, remove_exposed=True,
                   merge_tiny=True, reroute=True, graph=None):
    """Devuelve (costuras nuevas, informe). `seams`: índices de arista con costura (sin bordes).

    remove_exposed: True quita todos los tramos visibles que sobran; "light" solo los que unen un
    agujero (ojo, boca) con el resto y el corte central de delante que parte en dos mitades una isla
    que ya está cortada por detrás (el torso); False no quita ninguno.
    """
    graph = graph or SeamGraph(transform)
    seams = set(seams) - graph.border
    report = {"moved": 0, "merged": 0, "rerouted": 0, "cost_before": float(sum(graph.cost[e] for e in seams))}
    ok, report["charts_before"] = graph.all_disks(seams)
    tiny = graph.tiny_non_disks(seams)
    if not ok and tiny is not None:
        # restos diminutos que no son disco (en el humano, 2 islas de 2 caras alrededor de un agujero
        # de 4 aristas): se toleran tal cual y se optimiza el resto, sin crear islas malas nuevas
        graph.tolerated = tiny
        ok = True
    if not ok:
        # Unfold3D dejó alguna isla que no es disco (fix_charts lo arregla antes): no se toca nada
        report["charts_after"] = report["charts_before"]
        report["cost_after"] = report["cost_before"]
        return sorted(seams), report

    if reroute:
        seams, report["rerouted"] = reroute_slits(graph, seams)

    for _ in range(passes if relocate else 0):
        changed = False
        chains, degree = graph.chains(seams)
        for chain, a, b in chains:
            if not set(chain) <= seams:
                continue  # este tramo ya cambió en esta pasada
            others = seams - set(chain)
            blocked_vertices = {v for e in others for v in graph.edge_v[e]} | graph.border_vertices
            old_cost = sum(graph.cost[e] for e in chain) + sum(
                graph.TURN_PENALTY * graph.cost[e2] for e1, e2 in zip(chain, chain[1:])
                if set(graph.edge_f[e1]).intersection(graph.edge_f[e2]))
            dead_end = degree.get(b, 0) == 1 and b not in graph.border_vertices
            if b in graph.border_vertices and a not in graph.border_vertices:
                a, b = b, a
            if a in graph.border_vertices:
                # tramo que une un agujero (ojo, boca) con el resto: puede salir de cualquier punto
                # del agujero y llegar a cualquier otra costura o agujero por el camino más escondido
                loop = graph.border_loop(a)
                targets = (blocked_vertices - loop) | {v for e in others for v in graph.edge_v[e]}
                dist, prev = graph._paths(loop, (blocked_vertices | targets) - loop, others)
                reachable = [v for v in targets if v in dist and v not in loop]
                if not reachable:
                    continue
                end = min(reachable, key=dist.get)
                if dist[end] >= 0.85 * old_cost:
                    continue
                path = graph._trace(prev, None, end)
                trial = others | set(path)
                ok, count = graph.all_disks(trial)
                if ok and count == graph.charts(seams)[1]:
                    seams = trial
                    report["moved"] += 1
                    changed = True
                continue
            if dead_end:
                # corte de alivio en punta: Unfold3D lo puso ahí para no estirar; moverlo dejaba
                # islas en forma de tira
                continue
            # tampoco se puede ir pegado a otra costura (dejaría una tira de una cara de ancho),
            # salvo cerca de los extremos, donde hay que llegar a ellas
            near_ends = graph.ring({a, b}, 2)
            beside = graph.ring({v for e in others for v in graph.edge_v[e]}, 1) - near_ends
            dist, prev = graph._paths(a, (blocked_vertices | beside) - {a, b}, others)
            end = b
            if end not in dist:
                continue
            if dist[end] >= 0.85 * old_cost:
                continue
            path = graph._trace(prev, a, end)
            trial = others | set(path)
            ok, count = graph.all_disks(trial)
            if ok and count == graph.charts(seams)[1]:
                seams = trial
                report["moved"] += 1
                changed = True
        if not changed:
            break

    # quitar tramos visibles que sobran:
    #  - los que unen un agujero (ojo, boca) con el resto: la isla puede tener agujeros
    #  - los que separan dos islas pequeñas que ya se tocan por otro tramo (la cabeza partida en
    #    mitades izquierda y derecha, con un corte por la cara y otro por la nuca)
    total_area = float(graph.face_area.sum())
    light = remove_exposed == "light"
    low, high = graph.points.min(axis=0), graph.points.max(axis=0)
    center_x = 0.5 * (low[0] + high[0])
    center_tolerance = 1e-4 * (float(np.linalg.norm(high - low)) or 1.0)

    def on_center(chain):
        return all(abs(graph.points[v][0] - center_x) <= center_tolerance
                   for e in chain for v in graph.edge_v[e])

    def exposure(chain):
        return sum(graph.cost[e] for e in chain) / max(sum(graph.length[e] for e in chain), 1e-9)

    if light:
        # costura central visible (por delante del pecho o de la cara): se quita y se dejan añadir los
        # cortes imprescindibles por lo menos visible (espalda, axilas)
        chains, _ = graph.chains(seams)
        drop = set()
        for chain, a, b in chains:
            if on_center(chain) and exposure(chain) >= CENTER_EXPOSURE:
                drop |= set(chain)
        if drop:
            trial = graph.make_disks(seams - drop)
            ok, _ = graph.all_disks(trial)
            if ok and sum(graph.cost[e] for e in trial) < sum(graph.cost[e] for e in seams):
                seams = trial
                report["merged"] += 1

    for _ in range(100 if remove_exposed else 0):
        chart, count = graph.charts(seams)
        area = np.zeros(count)
        np.add.at(area, chart, graph.face_area)
        chains, degree = graph.chains(seams)
        pair_count = {}
        for chain, a, b in chains:
            faces = graph.edge_f[chain[0]]
            if len(faces) == 2 and chart[faces[0]] != chart[faces[1]]:
                pair = tuple(sorted((chart[faces[0]], chart[faces[1]])))
                pair_count[pair] = pair_count.get(pair, 0) + 1
        removed = False
        for chain, a, b in sorted(chains, key=lambda c: -exposure(c[0])):
            if exposure(chain) < 0.3:
                break
            ends_on_hole = a in graph.border_vertices or b in graph.border_vertices
            if not ends_on_hole and (degree.get(a, 0) == 1 or degree.get(b, 0) == 1):
                continue  # corte de alivio en punta: hace falta para desplegar sin estirar
            faces = graph.edge_f[chain[0]]
            if len(faces) == 2 and chart[faces[0]] != chart[faces[1]]:
                pair = tuple(sorted((chart[faces[0]], chart[faces[1]])))
                if pair_count[pair] < 2:
                    continue
                if light:
                    # solo el corte central de delante entre dos mitades gemelas (torso, cabeza)
                    if not on_center(chain):
                        continue
                elif area[pair[0]] + area[pair[1]] > 0.12 * total_area:
                    continue
            elif not ends_on_hole:
                continue
            trial = seams - set(chain)
            if graph.all_disks(trial)[0]:
                seams = trial
                report["merged"] += 1
                removed = True
                break
        if not removed:
            break

    # islas diminutas: se cosen a la vecina con la que comparten más borde
    for _ in range(50 if merge_tiny else 0):
        chart, count = graph.charts(seams)
        area = np.zeros(count)
        np.add.at(area, chart, graph.face_area)
        merged = False
        for c in np.argsort(area):
            if area[c] >= merge_area * total_area:
                break
            shared = {}
            for e in seams:
                f = graph.edge_f[e]
                if len(f) == 2 and (chart[f[0]] == c) != (chart[f[1]] == c):
                    other = chart[f[1]] if chart[f[0]] == c else chart[f[0]]
                    shared.setdefault(other, []).append(e)
            for other, edges in sorted(shared.items(), key=lambda item: -len(item[1])):
                trial = seams - set(edges)
                if graph.all_disks(trial)[0]:
                    seams = trial
                    report["merged"] += 1
                    merged = True
                    break
            if merged:
                break
        if not merged:
            break

    report["charts_after"] = graph.charts(seams)[1]
    report["cost_after"] = float(sum(graph.cost[e] for e in seams))
    return sorted(seams), report
