# -*- coding: utf-8 -*-
"""Pruebas de seguridad del Motor MBL: que no pida caras de más ni deje al ordenador sin memoria.

No necesitan Maya ni QuadriFlow ni modelos: se simulan los motores. Se lanzan con
    "C:\\Program Files\\Autodesk\\Maya2026\\bin\\mayapy.exe" tests\\test_safety.py
"""

import os
import sys
import tempfile
import threading
import time
import traceback

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbl_engine import process, resources  # noqa: E402
from mbl_engine import retopo as core  # noqa: E402
from mbl_engine.process import EngineError, MemoryLow  # noqa: E402

GB = 1024 ** 3
TESTS = []


def test(func):
    TESTS.append(func)
    return func


def sphere(rings=20, segments=40):
    """Esfera de quads (puntos, caras)."""
    points = [(0.0, 0.0, 1.0)]
    for i in range(1, rings):
        phi = np.pi * i / rings
        for j in range(segments):
            theta = 2 * np.pi * j / segments
            points.append((np.sin(phi) * np.cos(theta), np.sin(phi) * np.sin(theta), np.cos(phi)))
    points.append((0.0, 0.0, -1.0))
    faces = []
    for j in range(segments):
        faces.append([0, 1 + j, 1 + (j + 1) % segments])
    for i in range(rings - 2):
        for j in range(segments):
            a, b = 1 + i * segments + j, 1 + i * segments + (j + 1) % segments
            faces.append([a, a + segments, b + segments, b])
    last = len(points) - 1
    for j in range(segments):
        faces.append([last, last - segments + (j + 1) % segments, last - segments + j])
    return np.array(points), faces


@test
def test_next_request_is_capped():
    target, asked = 40000, 52000
    # QuadriFlow devolvió 10 veces menos de lo pedido: antes se pedían 520.000 caras
    request = core._next_request([(asked, asked // 10, None)], target)
    assert request <= core.QUADRIFLOW_MAX_REQUEST * target, request
    assert request <= core.QUADRIFLOW_MAX_STEP * asked, request
    # otra pasada igual de mala no hace crecer el pedido por encima del tope
    request2 = core._next_request([(asked, asked // 10, None), (request, int(request) // 10, None)], target)
    assert request2 <= core.QUADRIFLOW_MAX_REQUEST * target, request2
    # y si devuelve muchas de más, el pedido baja como mucho a la mitad
    assert core._next_request([(asked, asked * 10, None)], target) >= asked / core.QUADRIFLOW_MAX_STEP


@test
def test_quadriflow_never_asks_more_than_the_cap_and_discards_a_bad_variant():
    target = 40000
    asked_values = []

    def fake_run(command, destination, timeout, log_path, tick=None, label="", **kwargs):
        asked_values.append(int(command[command.index("-f") + 1]))
        with open(destination, "w") as handle:
            handle.write("v 0 0 0\n")

    def fake_read(path):
        return np.zeros((4, 3)), [[0, 1, 2, 3]] * (target // 10)    # siempre 10 veces menos de lo pedido

    saved = core._run_once, core.read_obj
    core._run_once, core.read_obj = fake_run, fake_read
    try:
        work = tempfile.mkdtemp(prefix="mbl_seguridad_")
        try:
            core._quadriflow("quadriflow.exe", np.zeros((4, 3)), [[0, 1, 2, 3]], target, work, 60, None, {})
        except EngineError as exc:
            assert "descarta" in str(exc), exc
        else:
            raise AssertionError("una variante que da 10 veces menos caras tenía que descartarse")
    finally:
        core._run_once, core.read_obj = saved
    assert asked_values, "no se lanzó QuadriFlow"
    assert max(asked_values) <= core.QUADRIFLOW_MAX_REQUEST * target, asked_values
    assert len(asked_values) <= core.QUADRIFLOW_ATTEMPTS, asked_values


@test
def test_workers_follow_memory_and_ram():
    # equipo de 16 GB: una variante a la vez aunque haya memoria libre y núcleos de sobra
    assert resources.safe_workers(100000, memory=10 * GB, cpus=16, asked_faces=5000, total=int(15.7 * GB)) == 1
    # equipo grande: varias a la vez
    assert resources.safe_workers(100000, memory=60 * GB, cpus=16, asked_faces=5000, total=64 * GB) == 3
    # pero con caras pedidas enormes caben menos (cada una ~12 GB; solo se usa la mitad de la total)
    assert resources.safe_workers(100000, memory=60 * GB, cpus=16, asked_faces=200000, total=32 * GB) == 1
    # lo que se puede pedir con la memoria que hay
    allowed = resources.max_asked_faces(100000, free=8 * GB, total=int(15.7 * GB))
    assert 50000 < allowed < 120000, allowed
    assert resources.max_asked_faces(100000, free=int(2.2 * GB), total=int(15.7 * GB)) == 0
    # 40.000 caras objetivo (52.000 pedidas) no deben dejar a un equipo de 16 GB con 3 procesos
    assert resources.job_memory(100000, 52000) > 3 * GB


@test
def test_safe_workers_basic_limits():
    """Núcleos, tope fijo y memoria libre (en un equipo grande); nunca baja de 1."""
    big = 64 * GB
    assert resources.safe_workers(14000, memory=64 * GB, cpus=16, total=big) == resources.MAX_WORKERS
    assert resources.safe_workers(14000, memory=64 * GB, cpus=2, total=big) == 1
    assert resources.safe_workers(14000, memory=64 * GB, cpus=16, requested=2, total=big) == 2
    # águila de 528.000 triángulos de entrada: ~1,7 GB cada una; con 4 GB libres y 2 GB de reserva cabe una
    assert resources.safe_workers(528000, memory=4 * GB, cpus=16, total=big) == 1
    assert resources.safe_workers(528000, memory=8 * GB, cpus=16, total=big) == 3
    assert resources.safe_workers(528000, memory=0, cpus=16, total=big) == 1


@test
def test_wait_for_memory():
    free = {"v": 1 * GB}
    state = {"running": 0}
    # nadie corre y no hay memoria: no se espera
    assert not resources.wait_for_memory(1 * GB, lambda: state["running"], free_fn=lambda: free["v"], poll=0.01)
    # otro proceso corre y al terminar libera memoria
    state["running"] = 1

    def release():
        time.sleep(0.3)
        free["v"] = 8 * GB
        state["running"] = 0
    threading.Thread(target=release).start()
    assert resources.wait_for_memory(1 * GB, lambda: state["running"], free_fn=lambda: free["v"], patience=5, poll=0.05)
    # si el otro proceso no termina, se acaba la paciencia
    free["v"], state["running"] = 1 * GB, 1
    began = time.time()
    assert not resources.wait_for_memory(1 * GB, lambda: state["running"], free_fn=lambda: free["v"], patience=0.3, poll=0.05)
    assert time.time() - began < 3


@test
def test_memory_guard_stops_only_the_newest():
    old, new = {"started": 1.0}, {"started": 2.0}
    with process._active_lock:
        process._active[:] = [old, new]
    try:
        assert process._memory_must_stop(new, lambda: int(0.5 * GB))[0] is True
        assert process._memory_must_stop(old, lambda: int(0.5 * GB))[0] is False
        assert process._memory_must_stop(new, lambda: 8 * GB)[0] is False
    finally:
        with process._active_lock:
            process._active[:] = []


@test
def test_memory_guard_stops_a_running_engine_without_hanging():
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    destination = os.path.join(tempfile.mkdtemp(prefix="mbl_seguridad_"), "salida.obj")
    began = time.time()
    try:
        process._run_once(sleeper, destination, 120, destination + ".log", label="Motor de prueba",
                          memory_free_fn=lambda: int(0.5 * GB))
    except MemoryLow as exc:
        assert "memoria" in str(exc), exc
    else:
        raise AssertionError("con poca memoria el motor tenía que detenerse")
    assert time.time() - began < 15, "tardó demasiado en detenerse"
    assert process.active_jobs() == 0, "quedó un proceso registrado como activo"


@test
def test_best_retopo_refuses_when_the_machine_cannot_hold_the_request():
    points, faces = sphere()
    calls = []
    saved = core._run_once, resources._memory_status
    core._run_once = lambda *args, **kwargs: calls.append(args)
    resources._memory_status = lambda: (int(3 * GB), int(15.7 * GB))     # 3 GB libres de 15,7
    try:
        try:
            core.best_retopo(points, faces, 40000, "quadriflow", "no-existe.exe", variants=1, time_budget=30)
        except MemoryLow as exc:
            assert "Baja las caras objetivo" in str(exc), exc
        else:
            raise AssertionError("con 3 GB libres no se podían pedir 52.000 caras")
    finally:
        core._run_once, resources._memory_status = saved
    assert not calls, "se lanzó un motor aunque no cabía en memoria"


def main():
    failed = []
    for func in TESTS:
        began = time.time()
        try:
            func()
            print("OK     {} ({:.1f} s)".format(func.__name__, time.time() - began), flush=True)
        except Exception:
            failed.append(func.__name__)
            print("FALLO  {}".format(func.__name__), flush=True)
            traceback.print_exc()
    print("\n{} pruebas, {} correctas, {} fallidas".format(len(TESTS), len(TESTS) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
