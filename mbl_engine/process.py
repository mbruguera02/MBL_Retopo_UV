# -*- coding: utf-8 -*-
"""Ejecutar los motores externos (Instant Meshes, QuadriFlow) sin colgar al usuario:
límite de tiempo, detección de bloqueos (sin consumir CPU) y cancelación desde la interfaz.
"""

import os
import subprocess
import sys
import threading
import time


class EngineError(Exception):
    """Error con un mensaje pensado para el usuario (los adaptadores lo muestran tal cual)."""


class MemoryLow(EngineError):
    """Se ha detenido un motor porque el ordenador se quedaba sin memoria libre."""


class _Stalled(Exception):
    pass


# Los motores corren con prioridad baja (Maya y el sistema siguen fluidos). Freno por memoria libre: solo si quien
# lanza el motor lo pide (memory_free_fn); entonces, si baja de resources.MEMORY_CRITICAL_BYTES se detiene el proceso
# más nuevo. Apagado por defecto desde la 1.0.9: con Maya abierto y poca RAM libre paraba los motores sin dejar ni
# probarlos (Marc, 9 nov); si falta memoria, Windows tira de disco y el motor va más lento, pero termina.
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
MEMORY_CHECK_SECONDS = 0.5

_active = []
_active_lock = threading.Lock()


def active_jobs():
    """Cuántos motores están corriendo ahora mismo."""
    with _active_lock:
        return len(_active)


def _memory_must_stop(token, free_fn=None):
    """True si el motor `token` debe detenerse porque la memoria libre está por debajo del mínimo crítico: solo al más
    nuevo de los que corren (los demás siguen y, al soltarse sus recursos, la memoria se recupera)."""
    from . import resources
    free = (free_fn or resources.available_memory)()
    if free is None or free >= resources.MEMORY_CRITICAL_BYTES:
        return False, free
    with _active_lock:
        newest = max(_active, key=lambda t: t["started"]) if _active else token
    return newest is token, free


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
              stall_seconds=STALL_SECONDS, ignore_exit_code=False, memory_free_fn=None):
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | BELOW_NORMAL_PRIORITY_CLASS
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
        token = {"started": start}
        with _active_lock:
            _active.append(token)
        last_memory_check = 0.0
        try:
            while process.poll() is None:
                if tick:
                    tick()
                now = time.time()
                if memory_free_fn is not None and now - last_memory_check >= MEMORY_CHECK_SECONDS:
                    last_memory_check = now
                    stop, free = _memory_must_stop(token, memory_free_fn)
                    if stop:
                        raise MemoryLow("Quedaban solo {:.1f} GB de memoria libre: se ha detenido {} para que Maya y el "
                                        "sistema sigan funcionando. Cierra otros programas o pide menos caras."
                                        .format(free / 1024 ** 3, label))
                if now - start > timeout:
                    raise EngineError("{} tardó más de {} s y se ha detenido. "
                                      "Prueba con menos caras o sube el tiempo máximo.".format(label, timeout))
                cpu = _cpu_seconds(process)
                if cpu is not None:
                    if last_cpu is None or cpu - last_cpu > 0.05:
                        last_cpu, last_change = cpu, now
                    elif now - last_change > stall_seconds:
                        raise _Stalled()
                try:
                    process.wait(timeout=0.1)       # vuelve en cuanto el motor termina, en vez de dormir hasta 0,1 s de más
                except subprocess.TimeoutExpired:
                    pass
        except BaseException:
            # cancelado, timeout o error: no dejamos el proceso vivo
            process.kill()
            process.wait()
            raise
        finally:
            with _active_lock:
                if token in _active:
                    _active.remove(token)

    written = os.path.isfile(destination) and os.path.getsize(destination) > 0
    # para programas que terminan con un código de error al cerrarse (0x80000003 en Windows), ya con la malla
    # guardada: si se pide, vale con que el archivo exista (ningún motor actual lo necesita)
    if (process.returncode != 0 and not (ignore_exit_code and written)) or not written:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                tail = "".join(handle.readlines()[-12:]).strip()
        except OSError:
            tail = ""
        raise EngineError("{} falló (código {}).\n{}".format(label, process.returncode, tail or "Sin salida."))
