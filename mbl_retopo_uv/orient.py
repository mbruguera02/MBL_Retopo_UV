# -*- coding: utf-8 -*-
"""Ahora vive en el núcleo del Motor MBL: mbl_engine/orient.py. Este nombre se mantiene para el plugin."""
import sys

from mbl_engine import orient as _core

sys.modules[__name__] = _core
