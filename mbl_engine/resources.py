# -*- coding: utf-8 -*-
"""Cuántos procesos se pueden lanzar a la vez sin ahogar el ordenador del usuario.

Los motores externos (QuadriFlow, Instant Meshes) son procesos aparte y tardan lo suyo; probar
varias variantes a la vez ahorra tiempo, pero cada una gasta memoria y un núcleo. Se limita con
estos topes: la mitad de los núcleos, un máximo fijo, la memoria libre (con margen), la mitad de la
memoria total y un solo proceso a la vez en equipos con 16 GB o menos.
"""

import os
import sys
import time

# máximo de procesos a la vez, tenga el ordenador los núcleos que tenga
MAX_WORKERS = 3
# en equipos con esta memoria o menos (16 GB reales aparecen como ~15,7), un solo proceso a la vez
SMALL_RAM_BYTES = 17 * 1024 ** 3
# memoria que nunca se toca: no se lanza un proceso si queda menos libre que esto además de lo que va a gastar
MEMORY_RESERVE_BYTES = 2 * 1024 ** 3
# si mientras corre un motor la memoria libre baja de esto, se detiene el proceso más nuevo (Maya y el sistema empiezan
# a ir a trompicones mucho antes de quedarse sin memoria)
MEMORY_CRITICAL_BYTES = int(1.2 * 1024 ** 3)
# los motores, todos juntos, no usan más de esta parte de la memoria total
MAX_MEMORY_FRACTION = 0.5
# Memoria que gasta QuadriFlow, medida (malla de entrada de 100.000 triángulos): 5.000 caras pedidas 0,4 GB,
# 25.000 -> 1,5 GB, 60.000 -> 3,3 GB. Sube con las caras PEDIDAS (unos 54 KB cada una), no con las de entrada.
BYTES_PER_ASKED_FACE = 60000
BYTES_PER_INPUT_FACE = 3000
BYTES_BASE = 100 * 1024 ** 2


def _memory_status():
    """(libre, total) en bytes, o (None, None) si no se puede saber."""
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
                return int(status.free), int(status.total)
            return None, None
        if sys.platform.startswith("linux"):
            free = total = None
            with open("/proc/meminfo") as handle:
                for line in handle:
                    if line.startswith("MemAvailable:"):
                        free = int(line.split()[1]) * 1024
                    elif line.startswith("MemTotal:"):
                        total = int(line.split()[1]) * 1024
            return free, total
        return None, None
    except (OSError, ValueError, AttributeError):
        return None, None


def available_memory():
    """Memoria libre en bytes, o None si no se puede saber."""
    return _memory_status()[0]


def total_memory():
    """Memoria total en bytes, o None si no se puede saber."""
    return _memory_status()[1]


def job_memory(input_faces, asked_faces=0):
    """Memoria aproximada de un motor con una malla de `input_faces` triángulos y `asked_faces` caras pedidas."""
    return BYTES_BASE + BYTES_PER_INPUT_FACE * int(input_faces) + BYTES_PER_ASKED_FACE * int(asked_faces)


def memory_budget(free=None, total=None):
    """Memoria que pueden usar todos los motores a la vez: lo libre menos el margen, sin pasar de la mitad de la
    memoria total. None si no se puede saber."""
    free = free if free is not None else available_memory()
    total = total if total is not None else total_memory()
    limits = []
    if free is not None:
        limits.append(max(free - MEMORY_RESERVE_BYTES, 0))
    if total is not None:
        limits.append(int(MAX_MEMORY_FRACTION * total))
    return min(limits) if limits else None


def max_asked_faces(input_faces, free=None, total=None):
    """Caras que se pueden pedir a un motor con la memoria que hay (0 si ni una sola cabe)."""
    budget = memory_budget(free, total)
    if budget is None:
        return None
    return max(int((budget - BYTES_BASE - BYTES_PER_INPUT_FACE * int(input_faces)) // BYTES_PER_ASKED_FACE), 0)


def safe_workers(input_faces, requested=None, memory=None, cpus=None, asked_faces=0, total=None):
    """Nº de procesos a la vez: el menor de lo pedido, la mitad de los núcleos, MAX_WORKERS, los que caben en la
    memoria (libre con margen, y como mucho la mitad de la total) y 1 en equipos con 16 GB o menos. Siempre al menos 1.
    memory, total y cpus se pasan en las pruebas."""
    cpus = cpus if cpus is not None else (os.cpu_count() or 1)
    limit = min(MAX_WORKERS, max(1, cpus // 2))
    if requested:
        limit = min(limit, int(requested))
    total = total if total is not None else total_memory()
    if total is not None and total <= SMALL_RAM_BYTES:
        limit = 1
    budget = memory_budget(memory, total)
    if budget is not None:
        limit = min(limit, int(budget // job_memory(input_faces, asked_faces)))
    return max(1, limit)


def wait_for_memory(needed, others_running, cancel=None, patience=60.0, poll=0.5, free_fn=None):
    """Espera a que haya memoria para lanzar un proceso que gastará `needed` bytes. Mientras haya otros procesos
    corriendo se espera (hasta `patience` s) a que terminen y liberen; si no hay ninguno y sigue sin haber, no se
    espera. `others_running` es una función que dice cuántos hay. Devuelve True si se puede lanzar."""
    free_fn = free_fn or available_memory
    began = time.time()
    while True:
        free = free_fn()
        if free is None or free - needed >= MEMORY_RESERVE_BYTES:
            return True
        if cancel is not None and cancel.is_set():
            return False
        if not others_running() or time.time() - began > patience:
            return False
        time.sleep(poll)
