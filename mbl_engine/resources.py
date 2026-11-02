# -*- coding: utf-8 -*-
"""Cuántos procesos se pueden lanzar a la vez sin ahogar el ordenador del usuario.

Los motores externos (QuadriFlow, Instant Meshes) son procesos aparte y tardan lo suyo; probar
varias variantes a la vez ahorra tiempo, pero cada una gasta memoria y un núcleo. Se limita con
tres topes: la mitad de los núcleos, un máximo fijo y la memoria libre (con margen).
"""

import os
import sys

# máximo de procesos a la vez, tenga el ordenador los núcleos que tenga
MAX_WORKERS = 3
# memoria que nunca se toca: se deja libre al menos esto además de lo que gaste cada proceso
MEMORY_RESERVE_BYTES = 2 * 1024 ** 3
# memoria que gasta un motor por triángulo de entrada (QuadriFlow: águila de 528.000 triángulos 1,5 GB)
BYTES_PER_INPUT_FACE = 3000
BYTES_BASE = 300 * 1024 ** 2


def available_memory():
    """Memoria libre en bytes, o None si no se puede saber."""
    try:
        if sys.platform.startswith("win"):
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                            ("total_page", ctypes.c_ulonglong), ("free_page", ctypes.c_ulonglong),
                            ("total_virtual", ctypes.c_ulonglong), ("free_virtual", ctypes.c_ulonglong),
                            ("free_extended", ctypes.c_ulonglong)]
            status = Status()
            status.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.free)
            return None
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo") as handle:
                for line in handle:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) * 1024
        return None
    except (OSError, ValueError, AttributeError):
        return None


def job_memory(input_faces):
    """Memoria aproximada de un motor con una malla de `input_faces` triángulos."""
    return BYTES_BASE + BYTES_PER_INPUT_FACE * int(input_faces)


def safe_workers(input_faces, requested=None, memory=None, cpus=None):
    """Nº de procesos a la vez: el menor de lo pedido, la mitad de los núcleos, MAX_WORKERS y los que
    caben en la memoria libre. Siempre al menos 1. memory y cpus se pasan en las pruebas."""
    cpus = cpus if cpus is not None else (os.cpu_count() or 1)
    limit = min(MAX_WORKERS, max(1, cpus // 2))
    if requested:
        limit = min(limit, int(requested))
    free = memory if memory is not None else available_memory()
    if free is not None:
        fits = int(max(free - MEMORY_RESERVE_BYTES, 0) // job_memory(input_faces))
        limit = min(limit, fits)
    return max(1, limit)
