# -*- coding: utf-8 -*-
"""MBL Retopo UV: limpieza, retopología y UVs para Maya 2026.

Uso rápido desde el Script Editor (Python):

    import mbl_retopo_uv
    mbl_retopo_uv.show()
"""

__version__ = "1.0.6"


def show():
    """Abre (o trae al frente) la ventana de la herramienta."""
    from . import ui
    return ui.show()
