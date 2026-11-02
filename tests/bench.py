# -*- coding: utf-8 -*-
"""Banco de pruebas del retopo y las UVs: mismas cifras para comparar el motor de hoy con el de mañana.

Para cada modelo y motor hace Limpiar + Retopo + UVs con los ajustes por defecto en un mayapy aparte (así cada
medición parte de cero y se mide su memoria) y guarda las cifras en JSON. Los modelos grandes no van en el
repositorio: se dan en un manifiesto (ver tests/bench_models.example.json).

    python tests/bench.py                                  mide todo y escribe tests/baseline.json
    python tests/bench.py --only human,prop --engines maya
    python tests/bench.py --out otra.json --compare tests/baseline.json     lista las cifras que cambian
    python tests/bench.py --list                           enseña los modelos del manifiesto

Solo corre una medición pesada a la vez en todo el equipo (archivo bench.lock en la carpeta temporal).
El dueño de este archivo, de baseline.json y de los criterios es el probador: los programadores no lo tocan.
"""

import argparse
import ctypes
import json
import math
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_MANIFEST = os.path.join(HERE, "bench_models.json")
DEFAULT_OUT = os.path.join(HERE, "baseline.json")
DEFAULT_MAYAPY = r"C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe"
LOCK = os.path.join(tempfile.gettempdir(), "bench.lock")
LOCK_STALE_SECONDS = 6 * 3600
# el Motor MBL no arranca si tras su trabajo no quedan 2 GB libres (resources.MEMORY_RESERVE_BYTES), y el mayapy del
# banco ya gasta ~1 GB: por debajo de esto la cifra sería un rechazo por memoria, no una medida
MAYA_MIN_FREE_GB = 2.5
ENGINES = ("maya", "mbl", "auto")
DEFAULT_ENGINES = ("maya", "mbl")
# cifras que cambian de una ejecución a otra aunque el resultado sea el mismo: no cuentan al comparar
VOLATILE = ("seconds", "memory_peak_mb", "timings", "wall_seconds")


# ------------------------------------------------------------------ orquestador (sin Maya)

class JobMemory(object):
    """Memoria máxima de un proceso y de todos los que lance (objeto trabajo de Windows). None si no se puede."""

    def __init__(self):
        self.job = None
        try:
            from ctypes import wintypes

            class Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class Counters(ctypes.Structure):
                _fields_ = [(name, ctypes.c_uint64) for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class Extended(ctypes.Structure):
                _fields_ = [("Basic", Basic), ("Io", Counters), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]
            self._extended = Extended
            self._kernel = ctypes.windll.kernel32
            self._kernel.CreateJobObjectW.restype = ctypes.c_void_p
            self.job = self._kernel.CreateJobObjectW(None, None)
        except Exception:
            self.job = None

    def assign(self, process):
        if not self.job:
            return
        self._kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if not self._kernel.AssignProcessToJobObject(self.job, int(process._handle)):
            self.job = None

    def peak_mb(self):
        if not self.job:
            return None
        info = self._extended()
        self._kernel.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                                           ctypes.c_uint32, ctypes.c_void_p]
        if not self._kernel.QueryInformationJobObject(self.job, 9, ctypes.byref(info), ctypes.sizeof(info), None):
            return None
        return round(info.PeakJobMemoryUsed / 1048576.0)


def free_memory_gb():
    """Memoria física libre en GB (None si no se puede saber)."""
    try:
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32), ("total", ctypes.c_uint64),
                        ("free", ctypes.c_uint64), ("page_total", ctypes.c_uint64), ("page_free", ctypes.c_uint64),
                        ("virtual_total", ctypes.c_uint64), ("virtual_free", ctypes.c_uint64),
                        ("extended_free", ctypes.c_uint64)]
        status = Status()
        status.length = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.free / 1073741824.0
    except Exception:
        return None


def wait_for_memory(minimum_gb, patience_minutes):
    """Espera a que haya `minimum_gb` libres (el Motor MBL se niega a arrancar sin memoria y eso no es una cifra).
    Devuelve (hay_memoria, libre_gb)."""
    began = time.time()
    while True:
        free = free_memory_gb()
        if free is None or free >= minimum_gb:
            return True, free
        if time.time() - began > patience_minutes * 60:
            return False, free
        time.sleep(20)


class HeavyLock(object):
    """Una sola medición pesada a la vez en todo el equipo."""

    def __enter__(self):
        while True:
            try:
                self.fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(LOCK) > LOCK_STALE_SECONDS:
                        os.remove(LOCK)
                        continue
                except OSError:
                    pass
                print("bench.lock: hay otra medición en marcha; espero...", flush=True)
                time.sleep(30)

    def __exit__(self, *exc):
        os.close(self.fd)
        try:
            os.remove(LOCK)
        except OSError:
            pass


def git_head():
    try:
        out = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        return out.stdout.strip()
    except Exception:
        return ""


def measure(name, path, engine, faces, mayapy, logs_dir):
    """Lanza un mayapy aparte con el trabajo y devuelve sus cifras (o {"error": ...})."""
    result_file = os.path.join(tempfile.gettempdir(), "bench_{}_{}_{}.json".format(name, engine, os.getpid()))
    log_file = os.path.join(logs_dir, "{}_{}.log".format(name, engine))
    command = [mayapy, os.path.abspath(__file__), "--worker", "--name", name, "--model", path, "--engine", engine,
               "--faces", str(faces), "--result", result_file, "--log", log_file]
    job = JobMemory()
    began = time.time()
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    job.assign(process)
    process.wait()
    wall = time.time() - began
    peak = job.peak_mb()
    if not os.path.isfile(result_file):
        return {"failure": "el mayapy terminó sin dar cifras (código {}); mira {}".format(process.returncode, log_file)}
    with open(result_file, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    os.remove(result_file)
    data["wall_seconds"] = round(wall, 1)
    data["memory_peak_mb"] = peak
    return data


def load_manifest(path):
    with open(path, "r", encoding="utf-8") as handle:
        models = json.load(handle)
    base = os.path.dirname(os.path.abspath(path))
    return {name: os.path.normpath(os.path.join(base, os.path.expandvars(os.path.expanduser(p))))
            for name, p in models.items() if not name.startswith("_")}


def flatten(value, prefix=""):
    """{ruta: número o texto} de todas las hojas de un JSON, sin las cifras volátiles."""
    out = {}
    if isinstance(value, dict):
        for key, item in value.items():
            if key in VOLATILE:
                continue
            out.update(flatten(item, "{}{}.".format(prefix, key)))
    elif isinstance(value, list):
        out[prefix[:-1]] = json.dumps(value, sort_keys=True)
    else:
        out[prefix[:-1]] = value
    return out


def compare(new, old, tolerance=1e-6):
    """Lista de (ruta, antes, ahora) de lo que cambia entre dos archivos de resultados."""
    a, b = flatten(old.get("results", old)), flatten(new.get("results", new))
    changes = []
    for key in sorted(set(a) | set(b)):
        x, y = a.get(key), b.get(key)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            if abs(x - y) > tolerance * max(1.0, abs(x), abs(y)):
                changes.append((key, x, y))
        elif x != y:
            changes.append((key, x, y))
    return changes


def orchestrate(args):
    manifest = load_manifest(args.models)
    names = [n for n in manifest if not args.only or n in args.only.split(",")]
    engines = [e for e in args.engines.split(",") if e]
    retry = {}
    if args.retry_unmeasured and os.path.isfile(args.out):
        with open(args.out, "r", encoding="utf-8") as handle:
            for name, per_engine in json.load(handle).get("results", {}).items():
                for engine, r in per_engine.items():
                    if r.get("not_measured"):
                        retry.setdefault(name, []).append(engine)
        names = [n for n in names if n in retry]
        args.only = args.only or ",".join(names)
    if args.list:
        for name in manifest:
            print("{:<14}{}  {}".format(name, "OK" if os.path.isfile(manifest[name]) else "NO ENCONTRADO", manifest[name]))
        return 0
    logs_dir = os.path.splitext(args.out)[0] + "_logs"
    os.makedirs(logs_dir, exist_ok=True)
    results = {}
    with HeavyLock():
        for name in names:
            path = manifest[name]
            if not os.path.isfile(path):
                results[name] = {"failure": "no existe " + path}
                print("{}: NO ENCONTRADO".format(name), flush=True)
                continue
            results[name] = {}
            for engine in (retry.get(name) or engines):
                began = time.time()
                needed = MAYA_MIN_FREE_GB if engine == "maya" else args.min_free_gb
                enough, free = wait_for_memory(needed, args.wait_minutes)
                if not enough:
                    results[name][engine] = {"not_measured": "memoria", "failure": "solo {:.1f} GB libres; hacen falta {:.1f}".format(
                        free, needed)}
                else:
                    results[name][engine] = measure(name, path, engine, args.faces, args.mayapy, logs_dir)
                    if "memoria" in str(results[name][engine].get("failure", "")):
                        results[name][engine]["not_measured"] = "memoria"
                r = results[name][engine]
                print("{:<12}{:<6}{}  ({:.0f} s)".format(
                    name, engine, r.get("failure") or summary(r), time.time() - began), flush=True)
    document = {"meta": {"faces": args.faces, "commit": git_head(), "date": time.strftime("%Y-%m-%d %H:%M"),
                         "engines": engines}, "results": results}
    if args.only and os.path.isfile(args.out) and not args.compare:
        # una medición parcial se funde con lo anterior en vez de borrarlo
        with open(args.out, "r", encoding="utf-8") as handle:
            previous = json.load(handle)
        merged = previous.get("results", {})
        for name, per_engine in results.items():
            merged.setdefault(name, {}).update(per_engine)
        document["results"] = merged
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=1, sort_keys=True, ensure_ascii=False)
    print("Escrito", args.out)
    if args.compare:
        with open(args.compare, "r", encoding="utf-8") as handle:
            old = json.load(handle)
        changes = compare(document, old)
        for key, x, y in changes:
            print("  {}: {} -> {}".format(key, x, y))
        print("{} cifras distintas".format(len(changes)))
        return 1 if changes else 0
    return 0


def summary(r):
    q, u = r.get("quality", {}), r.get("uv", {})
    return "caras {} | quads {:.1f}% | polos {} | error {:.3f}% | feo {} | UV {:.1f}% usado, >1.5x {:.1f}%, racord {}% | {:.0f} MB".format(
        r.get("retopo", {}).get("faces"), r.get("retopo", {}).get("quad_percent", 0), r.get("retopo", {}).get("poles"),
        r.get("shape_error", {}).get("mean_percent", 0), q.get("defect_score"), u.get("used_percent", 0),
        u.get("over_1_5_percent", 0), u.get("racord_rot_lt_15"), r.get("memory_peak_mb") or 0)


# ------------------------------------------------------------------ trabajador (dentro de mayapy)

def worker(args):
    sys.path.insert(0, ROOT)
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.api.OpenMaya as om
    import maya.cmds as cmds
    import numpy as np

    from mbl_engine import quality
    from mbl_engine.mesh import Mesh
    from mbl_retopo_uv import config, diagnostics, pipeline, retopo, uv

    lines = []

    def log(message, level="info"):
        lines.append("[{}] {}".format(level, message))

    def load(path):
        cmds.file(new=True, force=True)
        ext = os.path.splitext(path)[1].lower()
        if ext == ".obj":
            cmds.loadPlugin("objExport", quiet=True)
            cmds.file(path, i=True, type="OBJ", ignoreVersion=True, options="mo=1")
        elif ext == ".fbx":
            cmds.loadPlugin("fbxmaya", quiet=True)
            cmds.file(path, i=True, type="FBX", ignoreVersion=True, options="v=0;")
        else:
            cmds.file(path, i=True, ignoreVersion=True)
        shapes = cmds.ls(type="mesh", noIntermediate=True, long=True) or []
        transforms = sorted({cmds.listRelatives(s, parent=True, fullPath=True)[0] for s in shapes})
        biggest = max(transforms, key=lambda t: cmds.polyEvaluate(t, face=True))
        for t in transforms:
            if t != biggest:
                cmds.delete(t)
        return biggest

    def seam_continuity(transform):
        """Cómo casan las dos orillas de cada costura: giro y escala relativos (ponderado por longitud)."""
        sel = om.MSelectionList()
        sel.add(transform)
        fn = om.MFnMesh(sel.getDagPath(0))
        us, vs = fn.getUVs()
        pts = np.array([(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)])
        counts, verts = fn.getVertices()
        uv_counts, uv_ids = fn.getAssignedUVs()
        uvs = np.column_stack([us, vs])
        edges = {}
        off = uoff = 0
        for c, uc in zip(counts, uv_counts):
            for k in range(c):
                a, b = verts[off + k], verts[off + (k + 1) % c]
                ua, ub = uv_ids[uoff + k], uv_ids[uoff + (k + 1) % c]
                if a > b:
                    a, b, ua, ub = b, a, ub, ua
                edges.setdefault((a, b), []).append((ua, ub))
            off += c
            uoff += uc
        total = seam = 0.0
        rows = []
        for (a, b), sides in edges.items():
            length = float(np.linalg.norm(pts[a] - pts[b]))
            total += length
            if len(sides) != 2 or (sides[0][0] == sides[1][0] and sides[0][1] == sides[1][1]):
                continue
            seam += length
            s1 = uvs[sides[0][1]] - uvs[sides[0][0]]
            s2 = uvs[sides[1][1]] - uvs[sides[1][0]]
            n1, n2 = np.linalg.norm(s1), np.linalg.norm(s2)
            if n1 < 1e-9 or n2 < 1e-9:
                continue
            ang = math.degrees(math.atan2(s1[0] * s2[1] - s1[1] * s2[0], float(s1 @ s2)))
            rows.append((length, ang, math.log2(n1 / n2)))
        if not rows:
            return {"seam_percent": 0.0, "racord_rot_lt_15": 100.0, "racord_scale_lt_10": 100.0}
        rows = np.array(rows)
        w = rows[:, 0] / rows[:, 0].sum()
        ang, scl = np.abs(rows[:, 1]), np.abs(rows[:, 2])
        return {"seam_percent": round(100 * seam / total, 2),
                "racord_rot_lt_15": round(100.0 * float(w[ang < 15].sum()), 2),
                "racord_scale_lt_10": round(100.0 * float(w[scl < math.log2(1.1)].sum()), 2)}

    def arrays_to_mesh(transform):
        points, faces = retopo.mesh_arrays(transform)
        return Mesh(points, faces)

    path = args.model
    source = load(path)
    input_tris = int(cmds.polyEvaluate(source, triangle=True))
    settings = dict(config.DEFAULTS, retopo_engine=args.engine, retopo_target_faces=args.faces)
    outcome = pipeline.run([source], ["cleanup", "retopo", "uv"], settings, log)
    data = {"input_tris": input_tris, "faces_target": args.faces, "engine": args.engine}
    if outcome["errors"] or not outcome["results"]:
        data["failure"] = "; ".join(str(m) for _, m in outcome["errors"]) or "sin resultado"
    else:
        final = outcome["results"][0]
        timings = outcome["timings"].get(final, {})
        data["timings"] = {k: round(v, 1) for k, v in timings.items()}
        data["status"] = {item["step"]: item["level"] for item in (outcome["status"].get(final) or [])}
        origin = diagnostics.find_source(final) or source
        mesh = arrays_to_mesh(final)
        original = arrays_to_mesh(origin)
        report = quality.report(mesh, original)
        level, reasons = quality.grade(report)
        faces = max(report["faces"], 1)
        d = report["defects"]
        data["retopo"] = {"faces": report["faces"], "quad_percent": round(report["quad_percent"], 3),
                          "poles": report["poles"], "tris": d["tris"], "ngons": d["ngons"]}
        data["quality"] = {
            "grade": level, "reasons": reasons,
            "defect_score": round(quality.defect_score(report["shape"], report["uniformity"], d, report["faces"]), 3),
            "bad_shape_percent": round(report["shape"]["bad_percent"], 3),
            "size_jump_percent": round(report["uniformity"]["jump_percent"], 3),
            "flipped_percent": round(100.0 * d["flipped"] / faces, 4), "folded_percent": round(100.0 * d["folded"] / faces, 4),
            "new_holes": d["new_holes"], "loops_closed_percent": round(report["flow"]["closed_percent"], 2),
            "loop_mean": round(report["flow"]["loop_mean"], 2)}
        error = diagnostics.surface_error(origin, final)
        data["shape_error"] = {"mean_percent": round(error["mean_percent"], 4), "max_percent": round(error["max_percent"], 4)}
        shells = len(set(om.MFnMesh(_dag(final, om)).getUvShellsIds()[1]))
        stretch = uv.stretch(final)
        data["uv"] = {"islands": shells, "used_percent": round(100.0 * uv.uv_area(final), 2),
                      "stretch_mean": round(stretch["mean"], 3), "over_1_5_percent": round(stretch["over_1_5_percent"], 2)}
        data["uv"].update(seam_continuity(final))
    with open(args.log, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    with open(args.result, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return 0


def _dag(transform, om):
    sel = om.MSelectionList()
    sel.add(transform)
    return sel.getDagPath(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default=DEFAULT_MANIFEST)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--only", default="")
    parser.add_argument("--engines", default=",".join(DEFAULT_ENGINES))
    parser.add_argument("--faces", type=int, default=5000)
    parser.add_argument("--mayapy", default=os.environ.get("MBL_MAYAPY", DEFAULT_MAYAPY))
    parser.add_argument("--min-free-gb", type=float, default=5.0, help="memoria libre mínima antes de medir con el Motor MBL (con Maya basta MAYA_MIN_FREE_GB)")
    parser.add_argument("--wait-minutes", type=float, default=20.0, help="cuánto esperar a que haya memoria")
    parser.add_argument("--retry-unmeasured", action="store_true", help="repite solo lo que no se midió por falta de memoria")
    parser.add_argument("--compare", default="")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--name")
    parser.add_argument("--model")
    parser.add_argument("--engine")
    parser.add_argument("--result")
    parser.add_argument("--log")
    args = parser.parse_args()
    if args.worker:
        return worker(args)
    return orchestrate(args)


if __name__ == "__main__":
    sys.exit(main())
