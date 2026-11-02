# -*- coding: utf-8 -*-
"""Sustituto de Instant Meshes solo para las pruebas: copia la malla de entrada a la salida.

Sirve para probar la parte de exportar / ejecutar / reimportar / reproyectar sin el programa real.
MBL_FAKE_IM_FAIL=1   -> simula un fallo (código 3)
MBL_FAKE_IM_ARGS=... -> guarda los argumentos recibidos en ese JSON
"""
import json
import os
import shutil
import sys

args = sys.argv[1:]
source = args[0]
output = args[args.index("-o") + 1]
if os.environ.get("MBL_FAKE_IM_ARGS"):
    with open(os.environ["MBL_FAKE_IM_ARGS"], "w") as handle:
        json.dump(args, handle)
if os.environ.get("MBL_FAKE_IM_FAIL"):
    print("fake instant meshes: fallo simulado")
    sys.exit(3)
shutil.copyfile(source, output)
print("fake instant meshes: ok")
