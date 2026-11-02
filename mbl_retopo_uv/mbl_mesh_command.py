# -*- coding: utf-8 -*-
"""Plugin de Maya con el comando mblCreateMesh: crea una malla a partir de datos y se puede deshacer.

cmds.file(i=True) (importar un OBJ) no se puede deshacer: tras Ctrl+Z quedaba la malla importada en
la escena. Este comando crea la malla con OpenMaya y en el deshacer la borra.
Los datos se pasan por meshbuild.create_mesh(), que es quien lo usa.
"""

import maya.api.OpenMaya as om


def maya_useNewAPI():
    pass


class CreateMeshCommand(om.MPxCommand):
    name = "mblCreateMesh"

    def __init__(self):
        super(CreateMeshCommand, self).__init__()
        self._data = None
        self._node_name = ""
        self._node = None

    @staticmethod
    def creator():
        return CreateMeshCommand()

    @staticmethod
    def syntax():
        syntax = om.MSyntax()
        syntax.addArg(om.MSyntax.kString)
        return syntax

    def isUndoable(self):
        return True

    def doIt(self, args):
        from mbl_retopo_uv import meshbuild
        self._data = meshbuild.take_pending()
        if self._data is None:
            raise RuntimeError("mblCreateMesh: no hay datos; usa meshbuild.create_mesh().")
        self._node_name = om.MArgDatabase(self.syntax(), args).commandArgumentString(0)
        self.redoIt()

    def redoIt(self):
        points, counts, connects = self._data
        transform = om.MFnMesh().create(points, counts, connects)
        dag = om.MFnDagNode(transform)
        dag.setName(self._node_name)
        self._node = om.MObjectHandle(transform)
        self.clearResult()
        self.setResult(dag.fullPathName())

    def undoIt(self):
        if self._node is not None and self._node.isValid():
            modifier = om.MDagModifier()
            modifier.deleteNode(self._node.object())
            modifier.doIt()


def initializePlugin(plugin):
    om.MFnPlugin(plugin, "MBL", "1.0").registerCommand(
        CreateMeshCommand.name, CreateMeshCommand.creator, CreateMeshCommand.syntax)


def uninitializePlugin(plugin):
    om.MFnPlugin(plugin).deregisterCommand(CreateMeshCommand.name)
