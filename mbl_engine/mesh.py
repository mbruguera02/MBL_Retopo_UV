# -*- coding: utf-8 -*-
"""Malla poligonal en numpy: puntos y caras de cualquier número de lados.

Las caras se guardan en formato compacto (todos los índices seguidos + dónde empieza cada cara),
que sirve igual para triángulos, quads y n-gons y no gasta memoria de más con mallas grandes
(el águila de prueba tiene 528.000 triángulos). Lo que se deriva (aristas, bordes, normales...)
se calcula la primera vez que se pide y se guarda.
"""

import numpy as np


class Mesh(object):
    def __init__(self, points, faces):
        """points: N x 3. faces: lista de listas de índices, o (índices seguidos, nº de lados por cara)."""
        self.points = np.ascontiguousarray(points, dtype=np.float64).reshape(-1, 3)
        if isinstance(faces, tuple) and len(faces) == 2:
            flat, sizes = faces
            self.face_vertices = np.asarray(flat, dtype=np.int64)
            self.face_sizes = np.asarray(sizes, dtype=np.int64)
        else:
            faces = list(faces)
            self.face_sizes = np.array([len(f) for f in faces], dtype=np.int64)
            self.face_vertices = (np.fromiter((v for f in faces for v in f), dtype=np.int64,
                                              count=int(self.face_sizes.sum())))
        self.face_starts = np.concatenate([[0], np.cumsum(self.face_sizes)[:-1]]).astype(np.int64)
        self._cache = {}

    # ------------------------------------------------------------------ básicos

    @property
    def vertex_count(self):
        return len(self.points)

    @property
    def face_count(self):
        return len(self.face_sizes)

    def faces(self):
        """Caras como lista de listas (cómodo, pero lento con millones de caras)."""
        return [self.face_vertices[s:s + n].tolist() for s, n in zip(self.face_starts, self.face_sizes)]

    def face(self, index):
        start = self.face_starts[index]
        return self.face_vertices[start:start + self.face_sizes[index]]

    def _cached(self, key, build):
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    # ------------------------------------------------------------------ aristas de cara

    @property
    def corner_face(self):
        """Cara a la que pertenece cada esquina (cada entrada de face_vertices)."""
        return self._cached("corner_face", lambda: np.repeat(np.arange(self.face_count), self.face_sizes))

    @property
    def corner_next(self):
        """Vértice siguiente de cada esquina dentro de su cara (la arista dirigida esquina -> siguiente)."""
        def build():
            following = np.arange(len(self.face_vertices)) + 1
            ends = self.face_starts + self.face_sizes - 1
            following[ends] = self.face_starts
            return self.face_vertices[following]
        return self._cached("corner_next", build)

    # ------------------------------------------------------------------ aristas

    def _build_edges(self):
        a, b = self.face_vertices, self.corner_next
        pairs = np.sort(np.stack([a, b], axis=1), axis=1)
        edges, corner_edge, counts = np.unique(pairs, axis=0, return_inverse=True, return_counts=True)
        self._cache["edges"] = edges
        self._cache["corner_edge"] = corner_edge.ravel()
        self._cache["edge_face_count"] = counts

    @property
    def edges(self):
        """Aristas únicas (E x 2, índices ordenados de menor a mayor)."""
        if "edges" not in self._cache:
            self._build_edges()
        return self._cache["edges"]

    @property
    def corner_edge(self):
        """Arista (índice en edges) de cada esquina: la que va de la esquina a la siguiente."""
        if "corner_edge" not in self._cache:
            self._build_edges()
        return self._cache["corner_edge"]

    @property
    def edge_face_count(self):
        """Nº de caras de cada arista: 1 = borde, 2 = interior, más = no-manifold."""
        if "edge_face_count" not in self._cache:
            self._build_edges()
        return self._cache["edge_face_count"]

    @property
    def boundary_edges(self):
        return np.nonzero(self.edge_face_count == 1)[0]

    @property
    def used_vertices(self):
        """Máscara de los vértices que aparecen en alguna cara."""
        def build():
            used = np.zeros(self.vertex_count, dtype=bool)
            used[self.face_vertices] = True
            return used
        return self._cached("used", build)

    @property
    def valence(self):
        """Nº de aristas de cada vértice."""
        return self._cached("valence", lambda: np.bincount(self.edges.ravel(), minlength=self.vertex_count))

    @property
    def boundary_vertices(self):
        """Máscara de los vértices que tocan una arista de borde."""
        def build():
            mask = np.zeros(self.vertex_count, dtype=bool)
            mask[self.edges[self.boundary_edges].ravel()] = True
            return mask
        return self._cached("boundary_vertices", build)

    def boundary_loops(self):
        """Bordes abiertos agrupados en lazos (lista de arrays de índices de arista), como los
        agujeros y aberturas del modelo. Un borde que se toca a sí mismo cuenta como un solo lazo."""
        border = self.boundary_edges
        if not len(border):
            return []
        # componentes conexas de las aristas de borde (unión de vértices)
        parent = {}

        def find(x):
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x
        for a, b in self.edges[border]:
            ra, rb = find(int(a)), find(int(b))
            if ra != rb:
                parent[ra] = rb
        groups = {}
        for e, (a, _) in zip(border, self.edges[border]):
            groups.setdefault(find(int(a)), []).append(int(e))
        return [np.array(g, dtype=np.int64) for g in groups.values()]

    # ------------------------------------------------------------------ geometría

    @property
    def face_normals_raw(self):
        """Normal de cada cara sin normalizar (su longitud es el doble del área): fórmula de Newell."""
        def build():
            p, q = self.points[self.face_vertices], self.points[self.corner_next]
            cross = np.cross(p, q)
            normals = np.zeros((self.face_count, 3))
            np.add.at(normals, self.corner_face, cross)
            return normals
        return self._cached("face_normals_raw", build)

    @property
    def face_areas(self):
        return self._cached("face_areas", lambda: 0.5 * np.linalg.norm(self.face_normals_raw, axis=1))

    @property
    def face_normals(self):
        def build():
            raw = self.face_normals_raw
            length = np.linalg.norm(raw, axis=1, keepdims=True)
            return raw / np.maximum(length, 1e-30)
        return self._cached("face_normals", build)

    @property
    def vertex_normals(self):
        """Normales por vértice: media de las de sus caras, ponderada por área."""
        def build():
            normals = np.zeros_like(self.points)
            np.add.at(normals, self.face_vertices, self.face_normals_raw[self.corner_face])
            length = np.linalg.norm(normals, axis=1, keepdims=True)
            return normals / np.maximum(length, 1e-30)
        return self._cached("vertex_normals", build)

    def face_centers(self):
        centers = np.zeros((self.face_count, 3))
        np.add.at(centers, self.corner_face, self.points[self.face_vertices])
        return centers / self.face_sizes[:, None]

    def bounding_box(self):
        used = self.points[self.used_vertices] if self.used_vertices.any() else self.points
        return used.min(axis=0), used.max(axis=0)

    def diagonal(self):
        low, high = self.bounding_box()
        return float(np.linalg.norm(high - low)) or 1.0

    def triangles(self):
        """Triángulos en abanico de cada cara (T x 3) y la cara de la que sale cada uno."""
        def build():
            tri_count = self.face_sizes - 2
            owner = np.repeat(np.arange(self.face_count), np.maximum(tri_count, 0))
            first = self.face_starts[owner]
            # posición del triángulo dentro de su cara: 0, 1, 2...
            offsets = np.arange(len(owner)) - np.repeat(np.cumsum(np.maximum(tri_count, 0)) - np.maximum(tri_count, 0),
                                                        np.maximum(tri_count, 0))
            tris = np.stack([self.face_vertices[first],
                             self.face_vertices[first + offsets + 1],
                             self.face_vertices[first + offsets + 2]], axis=1)
            return tris, owner
        return self._cached("triangles", build)
