# -*- coding: utf-8 -*-
"""Imagen de una malla de quads (las aristas en oscuro), para revisar el motor nativo a ojo. Con numpy, sin Maya.

    mayapy tests/quad_view.py malla.obj salida.png [tamaño] [giros...]

Vistas de frente, de lado y de espaldas por defecto (giros en grados alrededor del eje Y). Cada quad se pinta como dos
triángulos con sombreado plano; los vértices que no tienen 4 aristas (polos) se marcan en rojo (3) o azul (5 o más).
"""

import struct
import sys
import zlib

import numpy as np


def read_obj(path):
    points, faces = [], []
    with open(path, errors="replace") as handle:
        for line in handle:
            if line.startswith("v "):
                points.append([float(x) for x in line.split()[1:4]])
            elif line.startswith("f "):
                faces.append([int(w.split("/")[0]) - 1 for w in line.split()[1:]])
    return np.array(points), faces


def save_png(path, img):
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def render(points, faces, yaw, size, middle, span):
    a = np.radians(yaw)
    rot = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    q = (points - middle) @ rot.T
    sx = (q[:, 0] / span * 0.92 + 0.5) * size
    sy = (0.5 - q[:, 1] / span * 0.92) * size
    depth = np.full((size, size), -np.inf)
    img = np.full((size, size, 3), 40, np.uint8)
    light = np.array([0.3, 0.4, 1.0])
    light /= np.linalg.norm(light)
    for face in faces:
        n = len(face)
        p3 = q[face]
        normal = np.cross(p3[1] - p3[0], p3[2] - p3[0]) if n == 3 else np.cross(p3[2] - p3[0], p3[3] - p3[1])
        length = np.linalg.norm(normal)
        if length == 0:
            continue
        normal /= length
        shade = 0.25 + 0.75 * abs(normal @ light)
        color = np.array([205, 200, 190]) * shade
        for k in range(1, n - 1):
            tri = [face[0], face[k], face[k + 1]]
            x, y, z = sx[tri], sy[tri], q[tri, 2]
            x0, x1 = int(max(0, np.floor(x.min()))), int(min(size - 1, np.ceil(x.max())))
            y0, y1 = int(max(0, np.floor(y.min()))), int(min(size - 1, np.ceil(y.max())))
            if x1 < x0 or y1 < y0:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            det = (x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0])
            if abs(det) < 1e-12:
                continue
            l1 = ((gx - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (gy - y[0])) / det
            l2 = ((x[1] - x[0]) * (gy - y[0]) - (gx - x[0]) * (y[1] - y[0])) / det
            l0 = 1 - l1 - l2
            inside = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
            zz = l0 * z[0] + l1 * z[1] + l2 * z[2]
            region = depth[y0:y1 + 1, x0:x1 + 1]
            front = inside & (zz > region)
            if not front.any():
                continue
            region[front] = zz[front]
            # distancia en píxeles a los lados que son aristas del polígono (no a la diagonal interna)
            edge_dist = np.full(gx.shape, np.inf)
            sides = [(l2, 0, 1), (l0, 1, 2), (l1, 2, 0)]  # coordenada opuesta al lado (i, j)
            for bary, i, j in sides:
                is_edge = (i, j) == (0, 1) and k == 1 or (i, j) == (1, 2) or (i, j) == (2, 0) and k == n - 2
                if not is_edge:
                    continue
                edge_len = np.hypot(x[j] - x[i], y[j] - y[i])
                height = abs(det) / max(edge_len, 1e-9)
                edge_dist = np.minimum(edge_dist, bary * height)
            pix = np.where((edge_dist < 0.7)[..., None], color * 0.35, color).astype(np.uint8)
            img[y0:y1 + 1, x0:x1 + 1][front] = pix[front]
    return img, sx, sy, q


def main():
    points, faces = read_obj(sys.argv[1])
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 700
    yaws = [float(v) for v in sys.argv[4:]] or [0.0, 90.0, 180.0]
    middle = points.mean(axis=0)
    span = np.ptp(points, axis=0).max()
    valence = np.zeros(len(points), int)
    edges = {}
    for face in faces:
        for k in range(len(face)):
            a, b = face[k], face[(k + 1) % len(face)]
            edges[(min(a, b), max(a, b))] = edges.get((min(a, b), max(a, b)), 0) + 1
    border = np.zeros(len(points), bool)
    for (a, b), uses in edges.items():
        valence[a] += 1
        valence[b] += 1
        if uses == 1:
            border[a] = border[b] = True
    panels = []
    for yaw in yaws:
        img, sx, sy, q = render(points, faces, yaw, size, middle, span)
        for v in np.where((valence != 4) & ~border & (valence > 0))[0]:
            cx, cy = int(sx[v]), int(sy[v])
            if 2 <= cx < size - 2 and 2 <= cy < size - 2:
                img[cy - 2:cy + 3, cx - 2:cx + 3] = (230, 40, 40) if valence[v] == 3 else (40, 90, 240)
        for v in np.where(border)[0]:
            cx, cy = int(sx[v]), int(sy[v])
            if 1 <= cx < size - 1 and 1 <= cy < size - 1:
                img[cy - 1:cy + 2, cx - 1:cx + 2] = (250, 200, 0)
        panels.append(img)
    save_png(sys.argv[2], np.concatenate(panels, axis=1))


if __name__ == "__main__":
    main()
