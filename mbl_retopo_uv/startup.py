# -*- coding: utf-8 -*-
"""Crea el menú "MBL" y el botón de la shelf "MBL" al arrancar Maya (lo llama userSetup.py)."""

import os

import maya.cmds as cmds
import maya.mel as mel

LABEL = "MBL Retopo UV"
MENU_NAME = "MBLMainMenu"
SHELF_NAME = "MBL"
COMMAND = "import mbl_retopo_uv; mbl_retopo_uv.show()"
ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "mbl.png").replace("\\", "/")


def add_menu():
    main_window = mel.eval("$_mbl_tmp = $gMainWindow")
    if not cmds.menu(MENU_NAME, exists=True):
        cmds.menu(MENU_NAME, label="MBL", parent=main_window, tearOff=True)
    items = cmds.menu(MENU_NAME, query=True, itemArray=True) or []
    if not any(cmds.menuItem(item, query=True, label=True) == LABEL for item in items):
        cmds.menuItem(label=LABEL, parent=MENU_NAME, command=lambda *_: _show(),
                      annotation="Limpieza, retopología e UVs automáticas", image=ICON)


def add_shelf_button():
    top = mel.eval("$_mbl_tmp = $gShelfTopLevel")
    if not top or not cmds.shelfTabLayout(top, exists=True):
        return
    if not cmds.shelfLayout(SHELF_NAME, exists=True):
        cmds.shelfLayout(SHELF_NAME, parent=top)
    for button in cmds.shelfLayout(SHELF_NAME, query=True, childArray=True) or []:
        if cmds.objectTypeUI(button) == "shelfButton" and cmds.shelfButton(button, query=True, label=True) == LABEL:
            return
    cmds.shelfButton(parent=SHELF_NAME, label=LABEL, image=ICON, imageOverlayLabel="Retopo",
                     command=COMMAND, sourceType="python",
                     annotation="MBL Retopo UV: limpieza, retopología e UVs automáticas")


def _show():
    import mbl_retopo_uv
    mbl_retopo_uv.show()


def setup():
    if cmds.about(batch=True):
        return
    add_menu()
    add_shelf_button()


def remove():
    """Quita el menú y nuestro botón de la shelf (lo usa uninstall.py). Si la pestaña MBL se queda sin botones, se
    borra también su archivo de las preferencias, para que no reaparezca al abrir Maya."""
    if cmds.about(batch=True):
        return
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME, menu=True)
    if not cmds.shelfLayout(SHELF_NAME, exists=True):
        return
    for button in cmds.shelfLayout(SHELF_NAME, query=True, childArray=True) or []:
        if cmds.objectTypeUI(button) == "shelfButton" and cmds.shelfButton(button, query=True, label=True) == LABEL:
            cmds.deleteUI(button)
    if not cmds.shelfLayout(SHELF_NAME, query=True, childArray=True):
        cmds.deleteUI(SHELF_NAME, layout=True)
        shelf_file = os.path.join(cmds.internalVar(userShelfDir=True), "shelf_{}.mel".format(SHELF_NAME))
        if os.path.isfile(shelf_file):
            os.remove(shelf_file)
