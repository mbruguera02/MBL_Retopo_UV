# -*- coding: utf-8 -*-
"""Parámetros por defecto, guardado en JSON y búsqueda del ejecutable de Instant Meshes.

Este módulo no importa maya a nivel de módulo para poder probarlo fuera de Maya.
"""

import json
import os
import shutil
import sys

SETTINGS_FILE_NAME = "mbl_retopo_uv.json"
DOWNLOAD_URL = "https://github.com/wjakob/instant-meshes"

# Tamaños de textura que ofrece la interfaz
MAP_SIZES = (1024, 2048, 4096, 8192)

DEFAULTS = {
    # Instant Meshes
    "instant_meshes_path": "",
    # Varias mallas a la vez
    # mallas idénticas (mismos vértices y caras, misma forma y tamaño salvo giro y movimiento, como 20 tornillos copiados):
    # limpieza, retopo y UVs de una sola y el resultado se copia a las demás, cada una sobre su transformación, su grupo
    # y sus materiales. Solo cuando el retopo está entre los pasos
    "pipeline_reuse_duplicates": True,
    # Limpieza
    "cleanup_delete_history": True,
    "cleanup_freeze": True,
    "cleanup_merge": True,
    "cleanup_merge_distance": 0.001,
    "cleanup_conform_normals": True,
    "cleanup_fix_nonmanifold": True,
    # Retopo
    # "maya" (polyRetopo), "auto", "mbl" o "instant_meshes". Maya frente a Instant Meshes (águila):
    # 100% quads, 5 veces menos polos de 6+ aristas y 2,7 veces menos error medio. En el banco (humano,
    # prop, hardsurface, criatura y Midnight) Maya rinde mejor que el Motor MBL (fealdad del humano 1,70
    # frente a 5,76), así que es el motor por defecto. "auto" hace Maya y el Motor MBL y se queda el de
    # menor coste (forma, polos y caras que no son quads); "mbl" queda como experimental
    "retopo_engine": "maya",
    "retopo_target_faces": 5000,
    # Motor MBL: variantes que prueba (3 = una ronda; 6 = calidad alta, el doble de tiempo) y segundos máximos
    "mbl_variants": 3,
    "mbl_time_budget": 270,
    # "off" (por defecto): el modelo entero. "auto": si el modelo es simétrico en X se hace la mitad y se refleja;
    # "x": siempre. Con el Motor MBL, si la simetría deja más fallos que sin ella (la costura central de la
    # criatura metía 3 veces más caras dobladas y volteadas) se entrega el resultado sin simetría y se avisa.
    "retopo_symmetry": "off",
    # caras por unidad de área en las zonas pintadas con el pincel de detalle, respecto al resto
    "retopo_detail_density": 4.0,
    "retopo_orient": 0.0,         # Motor MBL: fuerza de las direcciones de curvatura (0 = campo libre)
    "retopo_crease_angle": 0.0,
    "retopo_smooth": 2,
    "retopo_pure_quads": True,
    "retopo_deterministic": True,
    "retopo_align_boundaries": True,
    "retopo_reproject": True,
    "retopo_timeout": 600,
    # densidad por curvatura (solo con la versión MBL de Instant Meshes). 0 = uniforme.
    # Con 0.4 el error medio bajó ~10-25% en la mayoría de pruebas, pero con un 10-40% más de polos.
    "retopo_adaptive": 0.0,
    "retopo_adaptive_ratio": 3.0,
    "retopo_min_piece": 1.0,
    "retopo_fill_holes": 60,
    # limpieza de polos (giros de arista y colapsos diagonales) y relajación sobre el original.
    # Águila: 447 -> 372 polos (6+ aristas: 14 -> 4) y error medio 0.145% -> 0.123%, en ~3 s
    "retopo_pole_cleanup": True,
    "retopo_relax": 10,
    # UVs
    "uv_respect_existing": True,
    # 180 = sin cortes por ángulo: en orgánicos funciona mejor dejar las costuras a Unfold3D
    # (águila de prueba: 80° -> 290 islas y 84 caras solapadas; 180° -> 1 isla)
    "uv_seam_angle": 180.0,
    "uv_auto_seams": True,
    "uv_cut_pipes": True,
    # 0.75: islas por partes (humano 9 -> 39 islas). Textura aprovechada 29% -> 61% (humano) y
    # 38% -> 53% (águila), con menos estiramiento y sin solapes. Por encima de 0.75 no cambia nada.
    "uv_split_shells": 0.75,
    # "resolution": costuras de Unfold3D (más islas, máxima textura aprovechada)
    # "hidden": costuras llevadas a zonas poco visibles y menos islas (humano: 39 -> 14 islas,
    # costuras de brazos y piernas por detrás, ~10% menos textura aprovechada)
    "uv_seam_style": "resolution",
    # en mallas simétricas en X, costuras simétricas: cada isla tiene su gemela (brazo izq./der.)
    "uv_symmetric": True,
    # costuras "de sastre": cortes de tubo atrás o bajo los brazos, tramos rectos, sin islas diminutas
    "uv_tailor_seams": True,
    # islas rectas (alineadas y solo giros de 90°) y nunca boca abajo: más fáciles de pintar
    "uv_straight_islands": True,
    # tubos cortados a lo largo (brazos, piernas, dedos, cables): las dos orillas del corte pasan a ser paralelas para que
    # el dibujo de la textura (vetas, rayones) siga la misma dirección al cruzar la costura. Solo se toca una isla si no
    # estira más que antes
    "uv_straighten_strips": True,
    # islas con más del 10 % de caras estiradas más de 1,5x (o estiramiento medio > 1,3): se prueba Optimize más fuerte solo en
    # esa isla y, si no basta, un corte extra hacia la costura más cercana; solo se acepta si la isla mejora sin solapes ni espejos
    "uv_fix_stretch": False,
    # en "resolution", las islas con más de esta fracción del área se vuelven a cortar (0 = nunca).
    # Águila: tres islas con el 90% del área -> textura aprovechada 42% -> 68%, estiramiento 8% -> 4%
    "uv_split_big_shells": 0.17,
    # 5 en vez de 10: -15% de tiempo y la misma calidad práctica (águila: 13.6% vs 12.5% de caras >1.5x)
    "uv_optimize_iterations": 5,
    # costuras en los bordes entre materiales distintos (cada material tiene sus propias islas y texturas).
    # Solo actúa si la malla tiene 2 o más materiales
    "uv_material_seams": True,
    "uv_map_size": 4096,
    # separación entre islas en píxeles de la textura. 0 = automática: 1/256 del lado (8 px a 2048, 16 a 4096,
    # 32 a 8192), así sigue habiendo margen al bajar la textura a mipmaps (a 1/8 de la resolución, 1 px)
    "uv_padding_px": 0,
    # False: las islas se quedan de pie y el empaquetador no las tumba 90°, así el dibujo de la textura (vetas, rayones
    # en proyección UV) sigue la misma dirección a los dos lados de un corte. Casi no cuesta textura: humano 62.6% -> 65.8%,
    # águila 64.0% -> 60.7%; el largo de corte entre islas giradas 90° baja del 17% al 9% (humano) y del 21% al 16% (águila)
    "uv_allow_rotation": False,
    "uv_layout_quality": 512,
    # pasadas del empaquetado final (u3dLayout -mut). 5: águila 53% -> 63% de textura aprovechada
    "uv_pack_iterations": 5,
    "uv_rotate_step": 90.0,
    "uv_pre_rotate": 0,
    # combinaciones de empaquetado que se prueban (sobre una copia) para quedarse con la que más
    # textura aprovecha. 1 = solo los ajustes de arriba
    "uv_pack_tries": 3,
    "uv_udim": False,
    "uv_texel_density": 10.24,
    # nº de UDIMs a llenar; el plugin calcula la densidad. 0 = usar uv_texel_density
    "uv_udim_tiles": 4,
    # misma densidad de texel (píxeles por unidad de escena) en todas las mallas que se procesan juntas: la de la malla
    # más grande, que es la que menos textura da por unidad, y las demás se escalan a ella (cada una en su 0-1 o en sus
    # UDIMs, usando solo la parte que les toca). Apagado: cada malla llena su textura por separado. Con una sola malla no cambia nada
    "uv_common_texel": False,
    # por encima de este nº de caras Unfold3D puede agotar la memoria (528k caras llegaron a 31 GB)
    "uv_max_faces": 150000,
}


# Versión de los ajustes. Cuando cambia un valor por defecto porque las pruebas demostraron que el
# nuevo es mejor, se sube la versión y se añade aquí la clave: los JSON guardados con una versión
# anterior vuelven a usar el valor nuevo en esa clave (el resto de lo que eligió el usuario se respeta).
SETTINGS_VERSION = 9
UPGRADED_KEYS = {
    2: ("uv_seam_angle", "uv_optimize_iterations", "uv_split_shells"),
    3: ("retopo_engine",),
    4: ("retopo_symmetry",),
    5: ("mbl_time_budget",),
    6: ("uv_allow_rotation",),
    9: ("uv_split_big_shells",),
}
# Valores que ya no existen porque se quitó un motor del plugin (7) o que eran el valor por defecto de antes (8): los
# JSON guardados con una versión anterior pasan al valor que lo sustituye. A diferencia de UPGRADED_KEYS, el resto de lo
# que eligió el usuario en esa clave se respeta (quien tenía Maya, Instant Meshes o el Motor MBL se queda con el suyo).
# Se aplican en orden: QuadWild (6 o menos) pasa a Automático y de ahí a Maya. El 8 no distingue a quien eligió
# «Automático» a mano antes de la 8 de quien lo tenía por ser el valor por defecto: los dos pasan a Maya y pueden
# volver a elegirlo en el desplegable (a partir de la 8 se respeta).
REPLACED_VALUES = {
    7: {"retopo_engine": {"quadwild": "auto"}},
    # el relleno de 16 px (el valor por defecto antiguo) pasa a automático (16 px a 4096); otro número se conserva
    8: {"retopo_engine": {"auto": "maya"}, "uv_padding_px": {16: 0}},
}


def padding_px(settings):
    """Separación entre islas en píxeles. Un valor manual (> 0) se respeta; 0 = automática: 1/256 del lado de la
    textura (8 px a 2048, 16 a 4096, 32 a 8192), con un mínimo de 2 px."""
    manual = int(settings.get("uv_padding_px", 0))
    if manual > 0:
        return manual
    return max(2, int(round(int(settings["uv_map_size"]) / 256.0)))


def settings_path():
    """Ruta del JSON de ajustes: carpeta de prefs de Maya si existe, si no la carpeta de usuario."""
    try:
        import maya.cmds as cmds
        folder = cmds.internalVar(userPrefDir=True)
    except Exception:
        folder = os.path.expanduser("~")
    return os.path.join(folder, SETTINGS_FILE_NAME)


def load(path=None):
    """Devuelve los ajustes guardados mezclados con los valores por defecto."""
    settings = dict(DEFAULTS)
    path = path or settings_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return settings
    if not isinstance(stored, dict):
        return settings
    version = stored.get("_version", 1)
    outdated = {key for v, keys in UPGRADED_KEYS.items() if not isinstance(version, int) or version < v
                for key in keys}
    for key, default in DEFAULTS.items():
        if key not in stored or key in outdated:
            continue
        value = stored[key]
        # descartamos valores con un tipo distinto al esperado (JSON editado a mano, versión antigua...)
        if isinstance(default, bool):
            if isinstance(value, bool):
                settings[key] = value
        elif isinstance(default, (int, float)):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                settings[key] = type(default)(value)
        elif isinstance(value, type(default)):
            settings[key] = value
    for v, changes in REPLACED_VALUES.items():
        if not isinstance(version, int) or version < v:
            for key, replacements in changes.items():
                settings[key] = replacements.get(settings[key], settings[key])
    return settings


def save(settings, path=None):
    """Guarda solo las claves conocidas. Devuelve la ruta escrita."""
    path = path or settings_path()
    data = {key: settings.get(key, default) for key, default in DEFAULTS.items()}
    data["_version"] = SETTINGS_VERSION
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    return path


def executable_names(platform=None):
    """Nombres habituales del ejecutable según el sistema."""
    platform = platform or sys.platform
    if platform.startswith("win"):
        return ["Instant Meshes.exe", "InstantMeshes.exe", "instant-meshes.exe"]
    if platform == "darwin":
        return ["Instant Meshes.app", "Instant Meshes", "InstantMeshes", "instant-meshes"]
    return ["Instant Meshes", "InstantMeshes", "instant-meshes"]


def resolve_executable(path, platform=None):
    """Convierte la ruta elegida por el usuario en el binario que se puede ejecutar.

    Acepta el propio ejecutable, la carpeta que lo contiene o, en macOS, el bundle .app.
    Devuelve None si no encuentra nada.
    """
    platform = platform or sys.platform
    if not path:
        return None
    path = os.path.expanduser(os.path.expandvars(path.strip().strip('"')))

    # macOS: el binario real está dentro del bundle
    if path.endswith(".app") and os.path.isdir(path):
        macos_dir = os.path.join(path, "Contents", "MacOS")
        if os.path.isdir(macos_dir):
            for name in ("Instant Meshes", "InstantMeshes"):
                candidate = os.path.join(macos_dir, name)
                if os.path.isfile(candidate):
                    return candidate
            entries = sorted(os.listdir(macos_dir))
            if entries:
                return os.path.join(macos_dir, entries[0])
        return None

    if os.path.isdir(path):
        for name in executable_names(platform):
            found = resolve_executable(os.path.join(path, name), platform)
            if found:
                return found
        return None

    return path if os.path.isfile(path) else None


def find_quadriflow():
    """QuadriFlow (motor del Motor MBL): bin/ junto al paquete o la carpeta de compilación del proyecto."""
    package = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(package)
    name = "quadriflow.exe" if sys.platform.startswith("win") else "quadriflow"
    for folder in (os.path.join(package, "bin"), os.path.join(root, "bin"),
                   os.path.join(root, "engine", "build-quadriflow", "Release")):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


def find_instant_meshes(settings, platform=None):
    """Busca Instant Meshes: ruta guardada, carpeta bin/ junto al paquete y el PATH."""
    found = resolve_executable(settings.get("instant_meshes_path", ""), platform)
    if found:
        return found
    # el que viene con el plugin está en mbl_retopo_uv/bin; también se mira bin/ junto al paquete
    package = os.path.dirname(os.path.abspath(__file__))
    for package_bin in (os.path.join(package, "bin"), os.path.join(os.path.dirname(package), "bin")):
        found = resolve_executable(package_bin, platform)
        if found:
            return found
    for name in executable_names(platform):
        on_path = shutil.which(name)
        if on_path:
            return on_path
    return None
