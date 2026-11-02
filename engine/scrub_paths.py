# -*- coding: utf-8 -*-
"""Quita de los ejecutables compilados la ruta de la carpeta donde se compilaron.

Visual Studio deja en cada .exe la ruta completa de los archivos fuente (en los mensajes de error de
las bibliotecas) y la de su archivo .pdb. Si la compilación se hizo en tu carpeta de usuario, esa ruta
lleva tu nombre. No afecta al funcionamiento, pero no hace falta repartirla.

Cada ruta se sustituye por otra de la misma longitud (no se desplaza nada dentro del archivo):

    C:\\Users\\usuario\\proyecto\\engine\\...   ->   C:\\mbl_compilado_______________\\engine\\...

Uso, después de compilar y antes de copiar los .exe a mbl_retopo_uv/bin:

    python engine/scrub_paths.py                 (todos los .exe de mbl_retopo_uv/bin)
    python engine/scrub_paths.py ruta/a/uno.exe  (solo esos)
"""

import glob
import os
import re
import sys

REPLACEMENT = b"mbl_compilado"
SEPARATORS = rb"[\\/]"

# C:\Users\<nombre>\...\ hasta la carpeta "engine" (raíz de la compilación): se cambia entera
BUILD_ROOT = re.compile(rb"[A-Za-z]:" + SEPARATORS + rb"+Users" + SEPARATORS + rb"+[^\x00\"<>|?*]*?(?=engine" + SEPARATORS + rb")",
                        re.IGNORECASE)
# cualquier otra ruta dentro de una carpeta de usuario: solo se cambia el nombre de usuario
USER_ONLY = re.compile(rb"([A-Za-z]:" + SEPARATORS + rb"+Users" + SEPARATORS + rb"+)([^\\/\x00\"<>|?*:]+)", re.IGNORECASE)


def neutral(length, separator):
    """Ruta falsa de exactamente `length` caracteres: C:\\mbl_compilado____\\ (con el separador del original)."""
    if length < 6:
        return b"x" * length
    name = (REPLACEMENT + b"_" * length)[:length - 4]
    return b"C:" + separator + name + separator


def scrub_bytes(data, wide=False):
    """Devuelve (datos nuevos, nº de cambios). `wide`: texto UTF-16 (dos bytes por carácter)."""
    changes = 0
    if wide:
        out = bytearray(data)
        # dos pasadas (primero la ruta de compilación, luego el nombre de usuario que quede suelto), cada una
        # sobre una proyección ASCII: un byte por carácter de 2 bytes (los que no son ASCII pasan a "?")
        for pattern in (BUILD_ROOT, USER_ONLY):
            count = len(out) // 2
            low, high = out[0:count * 2:2], out[1:count * 2:2]
            text = bytes(c if h == 0 else 63 for c, h in zip(low, high))
            for match in pattern.finditer(text):
                original = match.group(0)
                if pattern is BUILD_ROOT:
                    new = neutral(len(original), original[2:3])
                else:
                    new = match.group(1) + (b"usuario" + b"_" * len(match.group(2)))[:len(match.group(2))]
                for i, char in enumerate(new):
                    out[(match.start() + i) * 2:(match.start() + i) * 2 + 2] = bytes([char, 0])
                changes += 1
        return bytes(out), changes

    def build_root(match):
        nonlocal changes
        changes += 1
        original = match.group(0)
        return neutral(len(original), original[2:3])

    def user_only(match):
        nonlocal changes
        changes += 1
        name = match.group(2)
        return match.group(1) + (b"usuario" + b"_" * len(name))[:len(name)]

    data = BUILD_ROOT.sub(build_root, data)
    data = USER_ONLY.sub(user_only, data)
    return data, changes


def scrub_file(path):
    with open(path, "rb") as handle:
        data = handle.read()
    new, changes = scrub_bytes(data)
    new, changes_wide = scrub_bytes(new, wide=True)
    total = changes + changes_wide
    if total:
        assert len(new) == len(data)
        with open(path, "wb") as handle:
            handle.write(new)
    return total


def main(argv):
    if argv:
        paths = argv
    else:
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mbl_retopo_uv", "bin")
        paths = [p for p in glob.glob(os.path.join(root, "**", "*.exe"), recursive=True)]
    for path in sorted(paths):
        print("{}: {} rutas cambiadas".format(os.path.relpath(path), scrub_file(path)))


if __name__ == "__main__":
    main(sys.argv[1:])
