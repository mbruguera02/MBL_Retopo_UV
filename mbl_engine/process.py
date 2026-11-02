# -*- coding: utf-8 -*-
"""Ejecutar los motores externos (Instant Meshes, QuadWild, QuadriFlow) sin colgar al usuario:
límite de tiempo, detección de bloqueos (sin consumir CPU) y cancelación desde la interfaz.
"""

import os
import subprocess
import sys
import time


class EngineError(Exception):
    """Error con un mensaje pensado para el usuario (los adaptadores lo muestran tal cual)."""


class _Stalled(Exception):
    pass


# Instant Meshes a veces se bloquea (sin usar CPU) tras el paso "Coloring", de forma intermitente:
# con la misma malla unas veces termina y otras no. Si lleva este tiempo sin consumir CPU se reinicia.
STALL_SECONDS = 20.0


def _cpu_seconds(process):
    """Tiempo de CPU consumido por el proceso hijo (solo Windows; None en otros sistemas)."""
    if not sys.platform.startswith("win"):
        return None
    import ctypes
    from ctypes import wintypes
    creation, exit_, kernel, user = (wintypes.FILETIME() for _ in range(4))
    handle = wintypes.HANDLE(int(process._handle))
    if not ctypes.windll.kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_),
                                                  ctypes.byref(kernel), ctypes.byref(user)):
        return None
    to_int = lambda ft: (ft.dwHighDateTime << 32) + ft.dwLowDateTime
    return (to_int(kernel) + to_int(user)) / 1e7


def run_instant_meshes(command, destination, timeout, log_path, tick=None):
    """Ejecuta Instant Meshes; si se queda bloqueado lo reinicia una vez. tick() refresca la UI o cancela."""
    for attempt in range(2):
        try:
            return _run_once(command, destination, timeout, log_path, tick)
        except _Stalled:
            if attempt:
                raise EngineError("Instant Meshes se ha quedado bloqueado dos veces y se ha detenido. "
                                  "Prueba con otro nº de caras o desmarca 'Determinista'.")


def _run_once(command, destination, timeout, log_path, tick=None, label="Instant Meshes", cwd=None,
              stall_seconds=STALL_SECONDS, ignore_exit_code=False):
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
        try:
            # la salida va a un archivo: con PIPE + espera activa el proceso se podría bloquear
            process = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, cwd=cwd, **kwargs)
        except FileNotFoundError:
            raise EngineError("No se encuentra {} en:\n{}".format(label, command[0]))
        except PermissionError:
            raise EngineError(
                "El sistema no deja ejecutar {}:\n{}\n"
                "En macOS ábrelo una vez con clic derecho > Abrir para quitar la cuarentena.".format(label, command[0]))
        except OSError as exc:
            raise EngineError("No se pudo lanzar {}: {}".format(label, exc))

        start = time.time()
        last_cpu, last_change = None, start
        try:
            while process.poll() is None:
                if tick:
                    tick()
                now = time.time()
                if now - start > timeout:
                    raise EngineError("{} tardó más de {} s y se ha detenido. "
                                      "Prueba con menos caras o sube el tiempo máximo.".format(label, timeout))
                cpu = _cpu_seconds(process)
                if cpu is not None:
                    if last_cpu is None or cpu - last_cpu > 0.05:
                        last_cpu, last_change = cpu, now
                    elif now - last_change > stall_seconds:
                        raise _Stalled()
                time.sleep(0.1)
        except BaseException:
            # cancelado, timeout o error: no dejamos el proceso vivo
            process.kill()
            process.wait()
            raise

    written = os.path.isfile(destination) and os.path.getsize(destination) > 0
    # quad_from_patches de QuadWild 0.0.2 en Windows termina con 0x80000003 al cerrarse, ya con la malla
    # guardada: si se pide, vale con que el archivo exista
    if (process.returncode != 0 and not (ignore_exit_code and written)) or not written:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                tail = "".join(handle.readlines()[-12:]).strip()
        except OSError:
            tail = ""
        raise EngineError("{} falló (código {}).\n{}".format(label, process.returncode, tail or "Sin salida."))
