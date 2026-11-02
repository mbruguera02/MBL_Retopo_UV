# -*- coding: utf-8 -*-
"""Instalador de MBL Retopo UV.

Para instalar: abre Maya y arrastra este archivo (install.py) a la ventana de Maya, sobre el visor.
Para actualizar: lo mismo con la versión nueva. Para quitarlo: arrastra uninstall.py.

Qué hace:
  1. Copia el plugin a la carpeta de preferencias de Maya (Documentos/maya/MBL_Retopo_UV), así puedes borrar
     la carpeta que descargaste.
  2. Añade un bloque a scripts/userSetup.py de tu versión de Maya (crea el archivo si no existe, y no toca nada
     más de lo que tengas) que prepara el menú "MBL" y la pestaña "MBL" de la shelf al arrancar Maya.

Desde una terminal también vale (python install.py), con estas opciones:
  --maya 2026   versión de Maya a la que instalar (por defecto, 2026)
  --en-sitio    no copia nada: usa esta misma carpeta (para quien desarrolla el plugin)
  --desinstalar quita lo instalado
"""

import os
import shutil
import sys

APP_NAME = "MBL Retopo UV"
INSTALL_FOLDER = "MBL_Retopo_UV"
PACKAGES = ("mbl_retopo_uv", "mbl_engine")
DEFAULT_MAYA_VERSION = "2026"
TESTED_MAYA_VERSION = "2026"
BEGIN = "# >>> MBL Retopo UV >>>"
END = "# <<< MBL Retopo UV <<<"
BACKUP_NAME = "userSetup.py.copia-antes-de-MBL"
SOURCE_ROOT = os.path.dirname(os.path.abspath(__file__))

BLOCK = '''{begin}
# Añadido por el instalador de MBL Retopo UV. Para quitarlo, arrastra uninstall.py a Maya o borra este bloque.
import sys as _mbl_sys
_MBL_PATH = {path!r}
if _MBL_PATH not in _mbl_sys.path:
    _mbl_sys.path.append(_MBL_PATH)


def _mbl_retopo_uv_startup():
    try:
        import mbl_retopo_uv.startup
        mbl_retopo_uv.startup.setup()
    except Exception as _mbl_exc:
        print("[MBL Retopo UV] No se pudo crear el menú: %s" % _mbl_exc)


import maya.utils as _mbl_utils
_mbl_utils.executeDeferred(_mbl_retopo_uv_startup)
{end}
'''


class InstallError(Exception):
    """Un problema que el usuario puede arreglar; el mensaje le dice cómo."""


# ---------------------------------------------------------------------------------------------- carpetas de Maya

def documents_folder():
    """Carpeta Documentos del usuario. En Windows se pregunta al sistema porque puede estar en OneDrive."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0 and buffer.value:
                return buffer.value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def default_app_dir():
    """Carpeta de preferencias de Maya (la que contiene las carpetas 2025, 2026...)."""
    base = os.environ.get("MAYA_APP_DIR")
    if base:
        return base
    if sys.platform.startswith("win"):
        return os.path.join(documents_folder(), "maya")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Preferences", "Autodesk", "maya")
    return os.path.join(os.path.expanduser("~"), "maya")


def maya_dirs(version=None):
    """(carpeta de preferencias de Maya, carpeta scripts de la versión). Dentro de Maya se las pregunta a Maya."""
    try:
        import maya.cmds as cmds
        return (os.path.normpath(cmds.internalVar(userAppDir=True)),
                os.path.normpath(cmds.internalVar(userScriptDir=True)))
    except Exception:
        app_dir = default_app_dir()
        return app_dir, os.path.join(app_dir, version or DEFAULT_MAYA_VERSION, "scripts")


# ---------------------------------------------------------------------------------------------- userSetup.py

def _has_block(content):
    return BEGIN in content and END in content and content.index(BEGIN) < content.index(END)


def _split_block(content):
    """(antes del bloque, después del bloque): el bloque y el salto de línea que lo cierra no están en ninguno."""
    start = content.index(BEGIN)
    end = content.index(END) + len(END)
    tail = content[end:]
    if tail.startswith("\r\n"):
        tail = tail[2:]
    elif tail.startswith("\n"):
        tail = tail[1:]
    return content[:start], tail


def with_block(content, block):
    """El contenido de userSetup.py con nuestro bloque: se sustituye el que hubiera o se añade al final."""
    if _has_block(content):
        head, tail = _split_block(content)
        return head + block + tail
    if content and not content.endswith("\n"):
        content += "\n"
    return content + ("\n" if content else "") + block


def without_block(content):
    """El contenido de userSetup.py sin nuestro bloque (y sin la línea en blanco que se dejó al añadirlo)."""
    if not _has_block(content):
        return content
    head, tail = _split_block(content)
    if head.endswith("\n\n"):
        head = head[:-1]
    return head + tail


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except UnicodeDecodeError:
        raise InstallError("No puedo leer {} (no está guardado en UTF-8). Ábrelo con un editor, guárdalo en UTF-8 "
                           "y vuelve a intentarlo.".format(path))


def write_user_setup(scripts_dir, install_path):
    """Pone el bloque en userSetup.py. Devuelve la ruta del archivo."""
    os.makedirs(scripts_dir, exist_ok=True)
    path = os.path.join(scripts_dir, "userSetup.py")
    content = _read_text(path) if os.path.isfile(path) else ""
    if content and not _has_block(content):
        backup = os.path.join(scripts_dir, BACKUP_NAME)
        if not os.path.exists(backup):
            shutil.copyfile(path, backup)
    block = BLOCK.format(begin=BEGIN, end=END, path=install_path)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(with_block(content, block))
    return path


def remove_user_setup_block(scripts_dir):
    """Quita el bloque de userSetup.py. Devuelve True si había algo que quitar."""
    path = os.path.join(scripts_dir, "userSetup.py")
    if not os.path.isfile(path):
        return False
    content = _read_text(path)
    new = without_block(content)
    if new == content:
        return False
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(new)
    return True


# ---------------------------------------------------------------------------------------------- copia del plugin

def check_source():
    missing = [name for name in PACKAGES if not os.path.isdir(os.path.join(SOURCE_ROOT, name))]
    if missing:
        raise InstallError("Junto a install.py faltan las carpetas {}. Descomprime el ZIP entero (clic derecho > "
                           "Extraer todo) y arrastra el install.py de la carpeta descomprimida.".format(
                               ", ".join(missing)))


def _same_folder(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def copy_packages(target):
    """Copia los paquetes a `target`, sustituyendo la versión anterior."""
    os.makedirs(target, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in PACKAGES:
        destination = os.path.join(target, name)
        try:
            if os.path.isdir(destination):
                shutil.rmtree(destination)
            shutil.copytree(os.path.join(SOURCE_ROOT, name), destination, ignore=ignore)
        except OSError as exc:
            raise InstallError("No he podido copiar el plugin a {} ({}). Cierra Maya, ábrelo de nuevo y repite la "
                               "instalación.".format(destination, exc))


def install(version=None, in_place=False):
    """Instala el plugin. Devuelve {"location": carpeta que queda en el path de Python, "user_setup": ruta}."""
    check_source()
    app_dir, scripts_dir = maya_dirs(version)
    location = os.path.join(app_dir, INSTALL_FOLDER)
    if in_place or _same_folder(location, SOURCE_ROOT):
        location = SOURCE_ROOT
    else:
        copy_packages(location)
    user_setup = write_user_setup(scripts_dir, location)
    return {"location": location, "user_setup": user_setup}


def uninstall(version=None):
    """Quita el bloque de userSetup.py y la carpeta instalada. Devuelve lo que ha quitado."""
    app_dir, scripts_dir = maya_dirs(version)
    removed = []
    if remove_user_setup_block(scripts_dir):
        removed.append(os.path.join(scripts_dir, "userSetup.py"))
    location = os.path.join(app_dir, INSTALL_FOLDER)
    if os.path.isdir(location) and not _same_folder(location, SOURCE_ROOT):
        try:
            shutil.rmtree(location)
            removed.append(location)
        except OSError as exc:
            raise InstallError("No he podido borrar {} ({}). Cierra Maya y bórrala a mano.".format(location, exc))
    return removed


# ---------------------------------------------------------------------------------------------- dentro de Maya

def forget_modules(keep=None):
    """Olvida los módulos ya cargados y las rutas de cualquier otra copia del plugin (excepto `keep`), para que una
    actualización use el código nuevo y no el de la carpeta que descargaste."""
    for name in [m for m in sys.modules if m.split(".")[0] in PACKAGES]:
        del sys.modules[name]
    sys.path[:] = [p for p in sys.path
                   if not any(os.path.isdir(os.path.join(p, name)) for name in PACKAGES)
                   or (keep and _same_folder(p, keep))]


def close_window():
    """Cierra la ventana de la herramienta si estaba abierta (antes de sustituir el código)."""
    try:
        import mbl_retopo_uv.ui as ui
        if ui._window is not None:
            ui._window.close()
    except Exception:
        pass


def _tell(message, title=APP_NAME):
    import maya.cmds as cmds
    print("[{}] {}".format(APP_NAME, message))
    try:
        cmds.confirmDialog(title=title, message=message, button=["Vale"], defaultButton="Vale")
    except Exception:
        pass


def _warnings():
    """Avisos sobre la versión de Maya o el sistema (no impiden instalar)."""
    import maya.cmds as cmds
    notes = []
    version = str(cmds.about(version=True))
    if not version.startswith(TESTED_MAYA_VERSION):
        notes.append("Está probado en Maya {}; en Maya {} puede fallar.".format(TESTED_MAYA_VERSION, version))
    if not sys.platform.startswith("win"):
        notes.append("Los programas que lleva dentro son de Windows: sin ellos, solo funcionan la limpieza, las UVs "
                     "y el retopo de Maya.")
    return notes


def onMayaDroppedPythonFile(*_):
    """Maya llama a esta función al arrastrar el archivo al visor."""
    import maya.cmds as cmds
    try:
        close_window()
        result = install()
        forget_modules(keep=result["location"])
        if result["location"] not in sys.path:
            sys.path.append(result["location"])
        import mbl_retopo_uv
        import mbl_retopo_uv.startup
        mbl_retopo_uv.startup.setup()
        mbl_retopo_uv.show()
    except InstallError as exc:
        _tell(str(exc), APP_NAME + ": no se pudo instalar")
        return
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _tell("La instalación ha fallado ({}). El detalle está en el Script Editor.".format(exc),
              APP_NAME + ": no se pudo instalar")
        return
    for note in _warnings():
        cmds.warning("[{}] {}".format(APP_NAME, note))
    cmds.inViewMessage(assistMessage="{} instalado. Lo tienes en el menú MBL.".format(APP_NAME), position="topCenter",
                       fade=True)
    print("[{}] Instalado en {}".format(APP_NAME, result["location"]))


def uninstall_from_maya():
    """Desinstala desde dentro de Maya (la llama uninstall.py)."""
    try:
        close_window()
        removed = uninstall()
        try:
            import mbl_retopo_uv.startup as startup
            startup.remove()
        except Exception:
            pass
        forget_modules()
    except InstallError as exc:
        _tell(str(exc), APP_NAME + ": no se pudo desinstalar")
        return
    _tell("{} desinstalado.\n\nTus ajustes se conservan por si lo vuelves a instalar (mbl_retopo_uv.json, en la "
          "carpeta de preferencias de Maya).".format(APP_NAME), APP_NAME)
    print("[{}] Quitado: {}".format(APP_NAME, ", ".join(removed) or "nada (no estaba instalado)"))


def main(argv):
    version = DEFAULT_MAYA_VERSION
    if "--maya" in argv:
        version = argv[argv.index("--maya") + 1]
    try:
        if "--desinstalar" in argv:
            removed = uninstall(version)
            print("Quitado: " + (", ".join(removed) or "nada (no estaba instalado)"))
        else:
            result = install(version, in_place="--en-sitio" in argv)
            print("{} instalado en: {}\nuserSetup.py: {}".format(APP_NAME, result["location"], result["user_setup"]))
    except InstallError as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
