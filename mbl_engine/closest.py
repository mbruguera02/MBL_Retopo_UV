# -*- coding: utf-8 -*-
"""Punto más cercano sobre una malla de triángulos, sin Maya (sustituye a MMeshIntersector).

Los triángulos se reparten en una rejilla de celdas cúbicas. Para cada punto se miran las celdas
de alrededor en anillos crecientes; en cuanto el mejor candidato está más cerca que el borde del
anillo mirado, la respuesta es exacta (nada de fuera puede estar más cerca). La distancia a cada
triángulo candidato es exacta (Christer Ericson, "Real-Time Collision Detection", 2005, 5.1.5).
"""

import os
import struct
import subprocess
import threading

import numpy as np

# triángulos por celda que se buscan de media: más celdas gastan memoria, menos dan más candidatos
TRIANGLES_PER_CELL = 4.0
# un triángulo que ocupe más celdas que esto por eje hace la rejilla más gruesa
MAX_CELLS_PER_AXIS = 4
# anillos de celdas que se miran vectorizados (todas las consultas a la vez); lo que siga sin
# resolver (muy lejos de la malla) se termina consulta a consulta
VECTOR_RINGS = (0, 1, 2, 4)


def closest_on_triangles(p, a, b, c):
    """Punto más cercano a p (N x 3) en cada triángulo (a, b, c: N x 3). Vectorizado."""
    ab, ac, ap = b - a, c - a, p - a
    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)
    bp = p - b
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)
    cp = p - c
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2

    # dentro del triángulo (caso general)
    denom = va + vb + vc
    safe = np.where(np.abs(denom) > 1e-300, denom, 1.0)
    v = vb / safe
    w = vc / safe
    result = a + ab * v[:, None] + ac * w[:, None]

    def put(mask, value):
        result[mask] = value[mask] if value.ndim == 2 else value

    # aristas
    edge_bc = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
    t = (d4 - d3) / np.where(np.abs((d4 - d3) + (d5 - d6)) > 1e-300, (d4 - d3) + (d5 - d6), 1.0)
    put(edge_bc, b + (c - b) * t[:, None])
    edge_ac = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
    t = d2 / np.where(np.abs(d2 - d6) > 1e-300, d2 - d6, 1.0)
    put(edge_ac, a + ac * t[:, None])
    edge_ab = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
    t = d1 / np.where(np.abs(d1 - d3) > 1e-300, d1 - d3, 1.0)
    put(edge_ab, a + ab * t[:, None])
    # vértices (se aplican los últimos: tienen prioridad)
    put((d6 >= 0) & (d5 <= d6), c)
    put((d3 >= 0) & (d4 <= d3), b)
    put((d1 <= 0) & (d2 <= 0), a)
    return result


class TriangleGrid(object):
    """Rejilla de triángulos para consultas de punto más cercano."""

    def __init__(self, points, triangles):
        self.points = np.asarray(points, dtype=np.float64)
        self.tris = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
        corners = self.points[self.tris]                     # T x 3 x 3
        low, high = corners.min(axis=1), corners.max(axis=1)
        self.low = low.min(axis=0) if len(low) else np.zeros(3)
        extent = (high.max(axis=0) - self.low) if len(low) else np.ones(3)
        # celda: tal que haya ~TRIANGLES_PER_CELL triángulos por celda ocupada (superficie ~ área)
        area = 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1)
        cell = np.sqrt(max(float(area.sum()), 1e-30) * TRIANGLES_PER_CELL / max(len(self.tris), 1))
        size = (high - low).max(axis=1) if len(low) else np.zeros(1)
        cell = max(cell, float(np.percentile(size, 99)) / MAX_CELLS_PER_AXIS if len(size) else 0.0,
                   float(extent.max()) / 512.0, 1e-12)
        self.cell = cell
        self.dims = np.maximum(np.ceil(extent / cell).astype(np.int64) + 1, 1)

        lo = np.floor((low - self.low) / cell).astype(np.int64)
        hi = np.floor((high - self.low) / cell).astype(np.int64)
        span = hi - lo + 1
        keys, owners = [], []
        for ox in range(int(span[:, 0].max()) if len(span) else 0):
            for oy in range(int(span[:, 1].max())):
                for oz in range(int(span[:, 2].max())):
                    sel = np.nonzero((span[:, 0] > ox) & (span[:, 1] > oy) & (span[:, 2] > oz))[0]
                    if not len(sel):
                        continue
                    idx = lo[sel] + (ox, oy, oz)
                    keys.append(self._key(idx))
                    owners.append(sel)
        keys = np.concatenate(keys) if keys else np.zeros(0, dtype=np.int64)
        owners = np.concatenate(owners) if owners else np.zeros(0, dtype=np.int64)
        order = np.argsort(keys, kind="stable")
        keys, owners = keys[order], owners[order]
        self.cell_keys, starts = np.unique(keys, return_index=True)
        self.cell_starts = np.append(starts, len(keys))
        self.cell_tris = owners
        self._cell_index = {int(k): i for i, k in enumerate(self.cell_keys)}

    def _key(self, idx):
        idx = np.asarray(idx, dtype=np.int64)
        return (idx[..., 0] * self.dims[1] + idx[..., 1]) * self.dims[2] + idx[..., 2]

    def _cell_triangles(self, cells):
        found = []
        for key in self._key(cells).tolist():
            i = self._cell_index.get(key)
            if i is not None:
                found.append(self.cell_tris[self.cell_starts[i]:self.cell_starts[i + 1]])
        return np.unique(np.concatenate(found)) if found else np.zeros(0, dtype=np.int64)

    def closest(self, queries, chunk=4000):
        """(puntos más cercanos N x 3, distancias N, triángulo de cada uno N) para cada consulta.

        Primero, todas a la vez contra las 27 celdas de alrededor (vectorizado, por bloques para no
        disparar la memoria). Las que no quedan resueltas así (lejos de la malla, o con un triángulo
        más cerca que el borde de esas celdas) se terminan una a una con anillos más grandes.
        """
        queries = np.asarray(queries, dtype=np.float64).reshape(-1, 3)
        result = np.zeros_like(queries)
        dist = np.full(len(queries), np.inf)
        owner = np.full(len(queries), -1, dtype=np.int64)
        if not len(self.tris):
            return result, dist, owner
        home = np.floor((queries - self.low) / self.cell).astype(np.int64)
        a_all, b_all, c_all = (self.points[self.tris[:, k]] for k in range(3))
        pending = np.arange(len(queries))
        # margen de cada consulta hasta el borde de su celda: con el anillo r mirado, nada de fuera
        # puede estar a menos de r celdas + ese margen
        local = (queries - self.low) / self.cell - home
        margin = np.minimum(local, 1.0 - local).min(axis=1) * self.cell
        # anillos 0 (su celda), 1, 2 y 4, todas las consultas pendientes a la vez
        for ring in VECTOR_RINGS:
            if not len(pending):
                break
            self._vector_pass(queries, home, pending, ring, max(64, chunk * 27 // (2 * ring + 1) ** 3),
                              a_all, b_all, c_all, result, dist, owner)
            pending = pending[~(dist[pending] <= ring * self.cell + margin[pending])]
        if len(pending):
            r, d, o = self._closest_rings(queries[pending], home[pending], a_all, b_all, c_all)
            better = d < dist[pending]
            dist[pending[better]], result[pending[better]], owner[pending[better]] = d[better], r[better], o[better]
        return result, dist, owner

    def _vector_pass(self, queries, home, which, ring, chunk, a_all, b_all, c_all, result, dist, owner):
        """Mejor triángulo de las celdas a `ring` o menos de cada consulta de `which` (actualiza en sitio)."""
        r = np.arange(-ring, ring + 1)
        offsets = np.stack(np.meshgrid(r, r, r, indexing="ij"), axis=-1).reshape(-1, 3)
        counts_all = np.diff(self.cell_starts)
        for start in range(0, len(which), chunk):
            idx = which[start:start + chunk]
            cells = (home[idx][:, None, :] + offsets[None]).reshape(-1, 3)
            query_of_cell = np.repeat(idx, len(offsets))
            inside = np.all((cells >= 0) & (cells < self.dims), axis=1)
            cells, query_of_cell = cells[inside], query_of_cell[inside]
            # poda: celdas cuya caja ya está más lejos que el mejor triángulo encontrado
            box_low = self.low + cells * self.cell
            gap = np.maximum(np.maximum(box_low - queries[query_of_cell], 0.0),
                             queries[query_of_cell] - (box_low + self.cell))
            near = np.einsum("ij,ij->i", gap, gap) < dist[query_of_cell] ** 2
            cells, query_of_cell = cells[near], query_of_cell[near]
            keys = self._key(cells)
            pos = np.searchsorted(self.cell_keys, keys)
            pos = np.minimum(pos, len(self.cell_keys) - 1)
            hit = self.cell_keys[pos] == keys
            pos, query_of_cell = pos[hit], query_of_cell[hit]
            counts = counts_all[pos]
            if not counts.sum():
                continue
            # pares (consulta, triángulo)
            pair_query = np.repeat(query_of_cell, counts)
            first = np.repeat(self.cell_starts[pos], counts)
            within = np.arange(len(pair_query)) - np.repeat(np.cumsum(counts) - counts, counts)
            pair_tri = self.cell_tris[first + within]
            pts = closest_on_triangles(queries[pair_query], a_all[pair_tri], b_all[pair_tri], c_all[pair_tri])
            d = np.linalg.norm(pts - queries[pair_query], axis=1)
            # mínimo por consulta
            order = np.lexsort((d, pair_query))
            pair_query, d, pts, pair_tri = pair_query[order], d[order], pts[order], pair_tri[order]
            firsts = np.concatenate([[True], pair_query[1:] != pair_query[:-1]])
            q, d, pts, pair_tri = pair_query[firsts], d[firsts], pts[firsts], pair_tri[firsts]
            better = d < dist[q]
            q = q[better]
            dist[q], result[q], owner[q] = d[better], pts[better], pair_tri[better]

    def _closest_rings(self, queries, home, a_all, b_all, c_all):
        """Consulta a consulta, con anillos de celdas crecientes (para las que quedan lejos)."""
        result = np.zeros_like(queries)
        dist = np.full(len(queries), np.inf)
        owner = np.full(len(queries), -1, dtype=np.int64)
        # distancia (en celdas) de cada consulta a la rejilla: los puntos de fuera empiezan ahí
        outside = np.maximum(np.maximum(-home, home - (self.dims - 1)), 0).max(axis=1)
        for i, (q, h) in enumerate(zip(queries, home)):
            seen = np.zeros(0, dtype=np.int64)
            ring = max(1, int(outside[i]))
            max_ring = int(outside[i]) + int(self.dims.max()) + 1
            while True:
                # cubo de celdas alrededor, recortado a la rejilla (un punto lejano no genera celdas vacías)
                lo = np.maximum(h - ring, 0)
                hi = np.minimum(h + ring, self.dims - 1)
                if (lo <= hi).all():
                    axes = [np.arange(lo[k], hi[k] + 1) for k in range(3)]
                    cube = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
                else:
                    cube = np.zeros((0, 3), dtype=np.int64)
                cand = self._cell_triangles(cube) if len(cube) else np.zeros(0, dtype=np.int64)
                cand = np.setdiff1d(cand, seen, assume_unique=True)
                if len(cand):
                    qq = np.broadcast_to(q, (len(cand), 3))
                    pts = closest_on_triangles(qq, a_all[cand], b_all[cand], c_all[cand])
                    d = np.linalg.norm(pts - q, axis=1)
                    j = int(np.argmin(d))
                    if d[j] < dist[i]:
                        dist[i], result[i], owner[i] = d[j], pts[j], cand[j]
                    seen = np.concatenate([seen, cand])
                # todo lo que queda fuera del cubo mirado está al menos a ring celdas
                if dist[i] <= ring * self.cell or ring > max_ring:
                    break
                ring = ring * 2 if not np.isfinite(dist[i]) else ring + 1
        return result, dist, owner


# ------------------------------------------------------------------ versión nativa (engine/mbl_geom)

_NATIVE_NAMES = ("mbl_geom.exe", "mbl_geom")


def find_native(extra=None):
    """Ruta de mbl_geom o None: la variable MBL_GEOM, `extra`, o las carpetas del proyecto."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folders = [os.path.join(root, "mbl_retopo_uv", "bin"), os.path.join(root, "bin"),
               os.path.join(root, "engine", "build-geom", "Release"), os.path.join(root, "engine", "build-geom")]
    candidates = [os.environ.get("MBL_GEOM"), extra]
    candidates += [os.path.join(folder, name) for folder in folders for name in _NATIVE_NAMES]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class NativeGrid(object):
    """Lo mismo que TriangleGrid, pero la búsqueda la hace engine/mbl_geom (C++, jerarquía de cajas y
    varios hilos): unas 100 veces más rápido. Un proceso por malla; se cierra con close() o al soltarlo."""

    def __init__(self, executable, points, triangles):
        self.points = np.ascontiguousarray(points, dtype=np.float64).reshape(-1, 3)
        self.tris = np.ascontiguousarray(triangles, dtype=np.int32).reshape(-1, 3)
        self._lock = threading.Lock()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen([executable], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         creationflags=flags)
        header = struct.pack("<cii", b"M", len(self.points), len(self.tris))
        self._write(header + self.points.tobytes() + self.tris.tobytes())
        if self._read(1) != b"\x01":
            raise RuntimeError("mbl_geom no cargó la malla")

    def _write(self, data):
        self._process.stdin.write(data)
        self._process.stdin.flush()

    def _read(self, size):
        data = self._process.stdout.read(size)
        if len(data) != size:
            raise RuntimeError("mbl_geom se cerró")
        return data

    def closest(self, queries, chunk=None):
        """(puntos más cercanos N x 3, distancias N, triángulo de cada uno N), como TriangleGrid.closest."""
        queries = np.ascontiguousarray(queries, dtype=np.float64).reshape(-1, 3)
        n = len(queries)
        if not n:
            return np.zeros((0, 3)), np.zeros(0), np.zeros(0, dtype=np.int64)
        with self._lock:
            self._write(struct.pack("<ci", b"Q", n) + queries.tobytes())
            raw = self._read(n * 24 + n * 8 + n * 4)
        points = np.frombuffer(raw, dtype=np.float64, count=n * 3).reshape(n, 3).copy()
        dist = np.frombuffer(raw, dtype=np.float64, count=n, offset=n * 24).copy()
        owner = np.frombuffer(raw, dtype=np.int32, count=n, offset=n * 32).astype(np.int64)
        return points, dist, owner

    def close(self):
        process, self._process = getattr(self, "_process", None), None
        if process is None:
            return
        try:
            process.stdin.write(b"X")
            process.stdin.flush()
            process.stdin.close()
            process.stdout.close()
            process.wait(timeout=5)
        except Exception:
            process.kill()

    def __del__(self):
        self.close()


def make_grid(points, triangles, native=True):
    """NativeGrid si mbl_geom está y funciona; si no, TriangleGrid (Python). Misma interfaz."""
    if native:
        executable = find_native()
        if executable:
            try:
                return NativeGrid(executable, points, triangles)
            except Exception:
                pass
    return TriangleGrid(points, triangles)
