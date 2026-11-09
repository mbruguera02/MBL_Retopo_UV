# -*- coding: utf-8 -*-
"""Imagen rápida de una malla con un color por parche (o por valor), para revisar el motor nativo a ojo. Sin Maya.

    python tests/core_view.py malla.obj etiquetas.txt salida.png [polos.txt]

Vista de frente, de espaldas y de lado. Cada triángulo se pinta como un punto en su centro (la malla de entrada es densa),
con el más cercano delante. Los polos, si se dan, en blanco (+) y negro (-).
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
                ids = [int(w.split("/")[0]) - 1 for w in line.split()[1:]]
                for k in range(1, len(ids) - 1):
                    faces.append([ids[0], ids[k], ids[k + 1]])
    return np.array(points), np.array(faces)


def save_png(path, img):
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def main():
    points, faces = read_obj(sys.argv[1])
    labels = np.loadtxt(sys.argv[2], dtype=np.int64)
    poles = np.loadtxt(sys.argv[4]).reshape(-1, 4) if len(sys.argv) > 4 else np.zeros((0, 4))
    colors = np.random.default_rng(3).integers(60, 255, (labels.max() + 1, 3)).astype(np.uint8)
    centers = points[faces].mean(axis=1)
    normals = np.cross(points[faces[:, 1]] - points[faces[:, 0]], points[faces[:, 2]] - points[faces[:, 0]])
    normals /= np.maximum(np.linalg.norm(normals, axis=1), 1e-30)[:, None]
    size, panels = 700, []
    middle = points.mean(axis=0)
    span = np.ptp(points, axis=0).max()
    for yaw in (0.0, 180.0, 90.0):
        a = np.radians(yaw)
        rot = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
        q = (centers - middle) @ rot.T
        n = normals @ rot.T
        x = (q[:, 0] / span * 0.9 + 0.5) * size
        y = (0.5 - q[:, 1] / span * 0.9) * size
        img = np.full((size, size, 3), 35, np.uint8)
        order = np.argsort(q[:, 2])  # de atrás hacia delante: lo de delante pinta encima
        shade = (0.45 + 0.55 * np.abs(n[:, 2]))[:, None]
        rgb = (colors[labels] * shade).astype(np.uint8)
        for dx in (0, 1):
            for dy in (0, 1):
                xi, yi = (x[order] + dx).astype(int), (y[order] + dy).astype(int)
                ok = (xi >= 0) & (yi >= 0) & (xi < size) & (yi < size)
                img[yi[ok], xi[ok]] = rgb[order][ok]
        if len(poles):
            pq = (poles[:, :3] - middle) @ rot.T
            px, py = ((pq[:, 0] / span * 0.9 + 0.5) * size).astype(int), ((0.5 - pq[:, 1] / span * 0.9) * size).astype(int)
            for (cx, cy, depth, k) in zip(px, py, pq[:, 2], poles[:, 3]):
                if 2 <= cx < size - 2 and 2 <= cy < size - 2:
                    img[cy - 2:cy + 3, cx - 2:cx + 3] = 255 if k > 0 else 0
        panels.append(img)
    save_png(sys.argv[3], np.concatenate(panels, axis=1))


if __name__ == "__main__":
    main()
