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


def _components(count, a, b):
    """Grupo de cada nodo (el menor índice de su grupo) para `count` nodos unidos por los enlaces (a[i], b[i]).

    Con numpy puro: cada vuelta cuelga la raíz de un grupo de la menor que toca y aplana los árboles. Union-find
    en Python tardaba 13 ms por llamada con 5.000 caras y se llamaba cientos de veces."""
    label = np.arange(count)
    if not len(a):
        return label
    while True:
        la, lb = label[a], label[b]
        low = np.minimum(la, lb)
        new = label.copy()
        np.minimum.at(new, la, low)
        np.minimum.at(new, lb, low)
        while True:
            jump = new[new]
            if np.array_equal(jump, new):
                break
            new = jump
        if np.array_equal(new, label):
            return label
        label = new


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
        # int64 explícito: en Windows con numpy 1.x np.arange da int32 y (cara * nº de vértices) se desbordaba con
        # mallas de más de ~46.000 caras (índice fuera de rango en searchsorted y claves de esquina mal calculadas)
        face_id = np.repeat(np.arange(self.face_count, dtype=np.int64), counts)
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

        # tablas de numpy para charts() y all_disks() (la topología no cambia mientras dura el grafo)
        nv, ne = len(self.points), len(self.edge_v)
        self.edge_count = ne
        edge_faces = np.array([len(faces) for faces in self.edge_f], dtype=np.int64)
        first = np.cumsum(edge_faces) - edge_faces
        self.incident_face = np.fromiter((f for faces in self.edge_f for f in faces), dtype=np.int64,
                                         count=int(edge_faces.sum()))
        self.incident_edge = np.repeat(np.arange(ne), edge_faces)
        self.interior_edges = np.nonzero(edge_faces == 2)[0]
        self.interior_first = self.incident_face[first[self.interior_edges]]
        self.interior_second = self.incident_face[first[self.interior_edges] + 1]
        self.edge_ends = np.array(self.edge_v, dtype=np.int64).reshape(-1, 2)
        # nodos del abanico: una (cara, vértice) por esquina. Los abanicos de un vértice son sus caras
        # unidas por aristas sin costura; cada abanico es un vértice en las UVs
        node_keys = np.unique(face_id * nv + flat)
        self.corner_node = np.searchsorted(node_keys, face_id * nv + flat)
        self.corner_count = counts
        self.node_face = node_keys // nv
        ends_of = self.edge_ends[self.interior_edges]
        self.link_a = np.concatenate([np.searchsorted(node_keys, self.interior_first * nv + ends_of[:, k])
                                      for k in (0, 1)])
        self.link_b = np.concatenate([np.searchsorted(node_keys, self.interior_second * nv + ends_of[:, k])
                                      for k in (0, 1)])
        self.link_edge = np.concatenate([self.interior_edges, self.interior_edges])
        # agujeros del modelo: vértices de cada uno y una cara de su borde
        self.loop_vertices = np.fromiter((v for loop in self.loops for v in loop), dtype=np.int64)
        self.loop_index = np.repeat(np.arange(len(self.loops)), [len(loop) for loop in self.loops])
        self.loop_face = np.array([self.edge_f[next(e for e in self.vertex_edges[next(iter(loop))]
                                                    if e in self.border)][0] for loop in self.loops], dtype=np.int64)
        self._charts_cache = {}
        self._disks_cache = {}
        self._mirror = None
        self._neighbors = {}
        self._cost_list = None

    @property
    def cost(self):
        """Coste de cortar cada arista: longitud x visibilidad. Se calcula al usarlo (lanza rayos)."""
        if self._cost is None:
            vis = self.visibility = self._visibility()
            # coste muy contrastado: una arista expuesta cuesta ~13 veces más que una escondida
            self._cost = self.length * (0.05 + 2.0 * (0.5 * (vis[self.edge_ends[:, 0]] + vis[self.edge_ends[:, 1]])) ** 2)
            self._cost_list = self._cost.tolist()
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
        vectors = [om.MFloatVector(*d) for d in dirs]
        size = float(np.linalg.norm(self.points.max(axis=0) - self.points.min(axis=0))) or 1.0
        eps = size * 1e-4
        accel = self.fn.autoUniformGridParams()
        any_hit = self.fn.anyIntersection
        world = om.MSpace.kWorld
        openness = np.ones(len(self.points))
        for v, (p, n) in enumerate(zip(self.points, self.normals)):
            facing = dirs @ n
            use = np.nonzero(facing > 0.05)[0]
            if not len(use):
                continue
            source = om.MFloatPoint(*(p + n * eps))
            free = total = 0.0
            for i, w in zip(use.tolist(), facing[use].tolist()):
                hit = any_hit(source, vectors[i], world, size, False, accelParams=accel)
                total += w
                if hit is None:
                    free += w
            openness[v] = free / total if total else 1.0
        front = 0.5 * (1.0 + self.normals[:, 2])  # 1 mirando a +Z (frente), 0 mirando atrás
        return openness * (0.3 + 0.7 * front)

    # ---------------------------------------------------------------- topología de las islas

    def uv_seams(self):
        """Aristas interiores donde las dos caras usan UVs distintos: las costuras que ya existen en la malla."""
        if self.fn.numUVs() == 0:
            return []
        uv_counts, uv_ids = self.fn.getAssignedUVs()
        uv_counts, uv_ids = np.asarray(uv_counts, dtype=np.int64), np.asarray(uv_ids, dtype=np.int64)
        corners = self.corner_count
        first_uv = np.repeat(np.cumsum(uv_counts) - uv_counts, corners)
        first_corner = np.repeat(np.cumsum(corners) - corners, corners)
        position = np.arange(len(self.corner_node)) - first_corner
        # solo las caras con una UV por esquina; en las demás, ninguna (-1)
        complete = np.repeat(uv_counts == corners, corners)
        corner_uv = np.full(len(self.corner_node), -1, dtype=np.int64)
        corner_uv[complete] = uv_ids[(first_uv + position)[complete]]
        node_uv = np.full(len(self.node_face), -1, dtype=np.int64)
        node_uv[self.corner_node] = corner_uv
        differ = node_uv[self.link_a] != node_uv[self.link_b]
        return np.unique(self.link_edge[differ]).tolist()

    def seam_mask(self, seams):
        """Máscara de aristas (bool) con las que son costura."""
        mask = np.zeros(self.edge_count, dtype=bool)
        if len(seams):
            mask[np.fromiter(seams, dtype=np.int64, count=len(seams))] = True
        return mask

    def charts(self, seams):
        """(isla de cada cara, nº de islas) cortando por `seams` y por los bordes. La isla de cada cara es un
        array de numpy que no hay que modificar: el resultado se guarda para las siguientes llamadas con las
        mismas costuras (reroute_slits y optimize_seams lo pedían cientos de veces seguidas)."""
        return self._charts(self.seam_mask(seams))

    def _charts(self, mask):
        key = mask.tobytes()
        found = self._charts_cache.get(key)
        if found is None:
            free = ~mask[self.interior_edges]
            label = _components(self.face_count, self.interior_first[free], self.interior_second[free])
            # las islas se numeran por su primera cara, como al recorrer las caras en orden
            roots, chart = np.unique(label, return_inverse=True)
            found = (chart, len(roots))
            if len(self._charts_cache) >= 32:
                self._charts_cache.clear()
            self._charts_cache[key] = found
        return found

    # nº de islas que no son disco que all_disks deja pasar (las que ya venían así y son diminutas)
    tolerated = 0
    # costuras fijas (bordes entre materiales): optimize_seams y symmetrize_seams no las mueven ni las quitan
    fixed = frozenset()
    TINY_CHART_FACES = 8

    def tiny_non_disks(self, seams):
        """Nº de islas que no son disco si todas son diminutas (pocas caras); None si alguna es grande."""
        self.all_disks(seams)
        chart, _ = self.charts(seams)
        sizes = np.bincount(np.asarray(chart), minlength=len(self._euler))
        bad = np.nonzero(self._euler != 1)[0]
        if (sizes[bad] > self.TINY_CHART_FACES).any():
            return None
        return len(bad)

    def all_disks(self, seams):
        """True si todas las islas se pueden aplanar: discos, o discos con agujeros propios del modelo
        (ojos, boca) que ninguna costura toca. Euler V - E + F = 1 - agujeros. Devuelve (ok, nº de islas)."""
        mask = self.seam_mask(seams)
        key = mask.tobytes()
        found = self._disks_cache.get(key)
        if found is None:
            chart, count = self._charts(mask)
            euler = np.bincount(chart, minlength=count)  # caras
            # agujeros del modelo que ninguna costura toca
            seam_vertex = np.zeros(len(self.points), dtype=bool)
            seam_vertex[self.edge_ends[mask].ravel()] = True
            touched = np.zeros(len(self.loops), dtype=bool)
            touched[self.loop_index[seam_vertex[self.loop_vertices]]] = True
            euler = euler + np.bincount(chart[self.loop_face[~touched]], minlength=count)
            # aristas: una arista interior sin costura cuenta una vez; las demás, una por cada cara
            joined = np.zeros(self.edge_count, dtype=bool)
            joined[self.interior_edges] = ~mask[self.interior_edges]
            euler = euler - np.bincount(chart[self.interior_first[~mask[self.interior_edges]]], minlength=count)
            cut = ~joined[self.incident_edge]
            euler = euler - np.bincount(chart[self.incident_face[cut]], minlength=count)
            # vértices: cuántos "abanicos" de caras (unidos por aristas no cortadas) tiene cada vértice
            fan = _components(len(self.node_face), self.link_a[~mask[self.link_edge]],
                              self.link_b[~mask[self.link_edge]])
            roots = np.nonzero(fan == np.arange(len(fan)))[0]
            euler = euler + np.bincount(chart[self.node_face[roots]], minlength=count)
            found = (euler, count)
            if len(self._disks_cache) >= 32:
                self._disks_cache.clear()
            self._disks_cache[key] = found
        self._euler, count = found
        return int(np.count_nonzero(self._euler != 1)) <= self.tolerated, count

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
        self.cost  # noqa: B018 (calcula también _cost_list)
        cost = self._cost_list
        interior = [e for e, faces in enumerate(self.edge_f) if len(faces) == 2 and e not in seams]
        for e in sorted(interior, key=lambda e: -cost[e]):
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
            if c not in anchored and (c not in cheapest or cost[e] < cost[cheapest[c]]):
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
        if self.fixed:
            # un tramo es entero fijo o entero libre: donde una costura fija sigue en una libre empieza otro tramo
            kinds = {}
            for e in seams:
                for v in self.edge_v[e]:
                    kinds.setdefault(v, set()).add(e in self.fixed)
            ends |= {v for v, kind in kinds.items() if len(kind) == 2}
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

    def _sharing(self, edge):
        """Aristas que comparten cara con `edge` (para el castigo por girar)."""
        found = self._neighbors.get(edge)
        if found is None:
            if not hasattr(self, "_face_edges"):
                self._face_edges = [[] for _ in range(self.face_count)]
                for e, faces in enumerate(self.edge_f):
                    for f in faces:
                        self._face_edges[f].append(e)
            found = self._neighbors[edge] = frozenset(e for f in self.edge_f[edge] for e in self._face_edges[f])
        return found

    def _paths(self, start, blocked_vertices, blocked_edges, allowed=None, stop=None):
        """Dijkstra desde start (un vértice o un conjunto). Devuelve (distancia por vértice, rastro).

        Girar (pasar a una arista que comparte cara con la anterior) cuesta un extra: así la costura
        sigue los loops en línea recta en vez de hacer escalera en diagonal.

        allowed: lista de bool por arista; las que no lo están se tratan como bloqueadas.
        stop: vértices de destino. Si se da, se para al terminar la capa del primer destino que se alcanza:
        las distancias y el rastro de ese destino (y de los que empaten con él) son ya las definitivas, y los
        que llamaban solo miraban los destinos. Evita recorrer toda la isla para cada tramo.
        """
        starts = set(start) if isinstance(start, (set, frozenset, list, tuple)) else {start}
        self.cost  # noqa: B018 (calcula también _cost_list)
        cost = self._cost_list
        edge_v, vertex_edges = self.edge_v, self.vertex_edges
        turn = self.TURN_PENALTY
        inf = math.inf
        # estado = (vértice, arista por la que se llegó)
        best = {(v, -1): 0.0 for v in starts}
        prev = {}
        dist = {v: 0.0 for v in starts}
        arrival = {v: (v, -1) for v in starts}
        heap = [(0.0, v, -1) for v in starts]
        heapq.heapify(heap)
        pop, push = heapq.heappop, heapq.heappush
        limit = inf
        while heap:
            d, v, came = pop(heap)
            if d > limit:
                break
            if d > best.get((v, came), inf):
                continue
            if stop is not None and limit == inf and v in stop and v not in starts:
                limit = d
            if v in blocked_vertices and v not in starts:
                continue  # se puede llegar pero no pasar a través
            sharing = self._sharing(came) if came >= 0 else ()
            for e in vertex_edges[v]:
                if e in blocked_edges or e == came:
                    continue
                if allowed is not None and not allowed[e]:
                    continue
                a, b = edge_v[e]
                w = b if a == v else a
                step = cost[e]
                if e in sharing:
                    step += turn * step
                nd = d + step
                if nd < best.get((w, e), inf):
                    best[(w, e)] = nd
                    prev[(w, e)] = (v, came)
                    push(heap, (nd, w, e))
                    if nd < dist.get(w, inf):
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
    tienen exacta). Solo depende de la geometría: se calcula una vez por grafo."""
    if tolerance == 1e-4:
        if graph._mirror is None:
            graph._mirror = _mirror_edges(graph, tolerance)
        return graph._mirror
    return _mirror_edges(graph, tolerance)


def _mirror_edges(graph, tolerance):
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
        result = (result | graph.fixed) - graph.border
        if graph.all_disks(result)[0]:
            return sorted(result)
        # algún corte del lado -X era imprescindible: se completan los que faltan y se repite
        seams = graph.make_disks(result)
    return None


def fix_charts(transform, seams, graph=None):
    """Si alguna isla no se puede aplanar, devuelve (costuras con los cortes que faltan, nº de cortes
    añadidos); si todas están bien, None."""
    graph = graph or SeamGraph(transform)
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
        # las costuras no cambian dentro de esta vuelta (se sale al primer cambio): las islas se miden una vez
        chart, count = graph.charts(seams)
        for chain, a, b in chains:
            faces = graph.edge_f[chain[0]]
            if len(faces) != 2 or not set(chain) <= seams or graph.fixed.intersection(chain):
                continue
            if chart[faces[0]] != chart[faces[1]]:
                continue  # separa dos islas: no es un corte de tubo
            c = chart[faces[0]]
            others = seams - set(chain)
            inside = chart == c
            # bordes de la isla sin este corte: aristas de costura o de borde que tocan sus caras
            touching = np.bincount(graph.incident_edge[inside[graph.incident_face]], minlength=graph.edge_count) > 0
            candidates = np.fromiter(others | graph.border, dtype=np.int64, count=len(others | graph.border))
            rim = candidates[touching[candidates]].tolist()
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
            # aristas por las que se puede pasar: interiores, sin costura y con las dos caras dentro de la isla
            allowed = np.zeros(graph.edge_count, dtype=bool)
            inner = graph.interior_edges
            allowed[inner] = (~graph.seam_mask(others)[inner]) & inside[graph.interior_first] & inside[graph.interior_second]
            blocked_vertices = set(parent) - sources - targets
            # tampoco a lo largo de un borde (dejaría tiras): los vértices del borde de origen solo
            # sirven para salir
            dist, trail = graph._paths(sources, blocked_vertices | targets, (), allowed.tolist(), targets)
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
    graph: si se da, se reutiliza (la tolerancia de islas diminutas se devuelve como estaba al terminar).
    """
    graph = graph or SeamGraph(transform)
    tolerated = graph.tolerated
    try:
        return _optimize_seams(graph, seams, merge_area, passes, relocate, remove_exposed, merge_tiny, reroute)
    finally:
        graph.tolerated = tolerated


def _optimize_seams(graph, seams, merge_area, passes, relocate, remove_exposed, merge_tiny, reroute):
    seams = (set(seams) | graph.fixed) - graph.border
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
            if not set(chain) <= seams or graph.fixed.intersection(chain):
                continue  # este tramo ya cambió en esta pasada, o es un borde de material
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
                dist, prev = graph._paths(loop, (blocked_vertices | targets) - loop, others, stop=targets)
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
            dist, prev = graph._paths(a, (blocked_vertices | beside) - {a, b}, others, stop={b})
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
            if on_center(chain) and exposure(chain) >= CENTER_EXPOSURE and not graph.fixed.intersection(chain):
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
            if graph.fixed.intersection(chain):
                continue
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
                if graph.fixed.intersection(edges):
                    continue  # otro material: la isla se queda aparte
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
