# -*- coding: utf-8 -*-
"""Motor MBL: núcleo de retopología y UVs independiente del programa 3D.

Solo usa Python y numpy (y ejecutables propios en C++). Nada de Maya, Blender ni Cinema 4D: cada
programa tiene su adaptador, que lee la malla, llama al núcleo y crea el resultado. El adaptador de
Maya es el paquete mbl_retopo_uv.

Entrada y salida siempre como arrays: puntos (N x 3) y caras (listas de índices, de 3 o más lados).
"""

__version__ = "0.1.0"
