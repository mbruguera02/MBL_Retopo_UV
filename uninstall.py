# -*- coding: utf-8 -*-
"""Desinstalador de MBL Retopo UV: arrastra este archivo (uninstall.py) a la ventana de Maya.

Quita el bloque de userSetup.py, el menú y la pestaña MBL de la shelf, y la carpeta que copió el instalador.
Tus ajustes (mbl_retopo_uv.json en las preferencias de Maya) se conservan.
"""

import os
import runpy

HERE = os.path.dirname(os.path.abspath(__file__))


def onMayaDroppedPythonFile(*_):
    """Maya llama a esta función al arrastrar el archivo al visor."""
    runpy.run_path(os.path.join(HERE, "install.py"))["uninstall_from_maya"]()


if __name__ == "__main__":
    import sys
    sys.exit(runpy.run_path(os.path.join(HERE, "install.py"))["main"](["--desinstalar"] + sys.argv[1:]))
