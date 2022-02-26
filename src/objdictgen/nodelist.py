# -*- coding: utf-8 -*-
#
# This file is part of CanFestival, a library implementing CanOpen Stack.
#
# Copyright (C): Edouard TISSERANT, Francis DUPIN and Laurent BESSARD
#
# See COPYING file for copyrights details.
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

from __future__ import print_function
from __future__ import absolute_import
from builtins import object

import os
import shutil
import errno
from future.utils import raise_from

from . import eds_utils


# ------------------------------------------------------------------------------
#                          Definition of NodeList Object
# ------------------------------------------------------------------------------


class NodeList(object):
    """
    Class recording a node list for a CANOpen network.
    """

    def __init__(self, manager, netname=""):
        self.Root = ""
        self.Manager = manager
        self.NetworkName = netname
        self.SlaveNodes = {}
        self.EDSNodes = {}
        self.CurrentSelected = None
        self.Changed = False

    def HasChanged(self):
        return self.Changed or not self.Manager.CurrentIsSaved()

    def GetEDSFolder(self, root_path=None):
        if root_path is None:
            root_path = self.Root
        return os.path.join(root_path, "eds")

    def SetRoot(self, newrootpath):
        if os.path.isdir(newrootpath):
            self.Root = newrootpath
            self.Manager.SetCurrentFilePath(os.path.join(self.Root, "master.od"))
            return True
        return False

    def GetMasterNodeID(self):
        return self.Manager.GetCurrentNodeID()

    def GetSlaveNumber(self):
        return len(self.SlaveNodes)

    def GetSlaveName(self, idx):
        return self.SlaveNodes[idx]["Name"]

    def GetSlaveNames(self):
        return [
            "0x%2.2X %s" % (idx, self.SlaveNodes[idx]["Name"])
            for idx in sorted(self.SlaveNodes)
        ]

    def GetSlaveIDs(self):
        return list(sorted(self.SlaveNodes))

    def LoadProject(self, root, netname=None):
        self.SlaveNodes = {}
        self.EDSNodes = {}

        self.Root = root
        if not os.path.exists(self.Root):
            raise OSError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), self.Root)

        eds_folder = self.GetEDSFolder()
        if not os.path.exists(eds_folder):
            os.mkdir(eds_folder)
            # raise ValueError("'%s' folder doesn't contain a 'eds' folder" % self.Root)

        files = os.listdir(eds_folder)
        for file in files:
            filepath = os.path.join(eds_folder, file)
            if os.path.isfile(filepath) and os.path.splitext(filepath)[-1] == ".eds":
                self.LoadEDS(file)

        self.LoadMasterNode(netname)
        self.LoadSlaveNodes(netname)
        self.NetworkName = netname

    def SaveProject(self, netname=None):
        self.SaveMasterNode(netname)
        self.SaveNodeList(netname)

    def GetEDSFilePath(self, edspath):
        _, file = os.path.split(edspath)
        eds_folder = self.GetEDSFolder()
        return os.path.join(eds_folder, file)

    def ImportEDSFile(self, edspath):
        _, file = os.path.split(edspath)
        shutil.copy(edspath, self.GetEDSFolder())
        self.LoadEDS(file)

    def LoadEDS(self, eds):
        edspath = os.path.join(self.GetEDSFolder(), eds)
        node = eds_utils.GenerateNode(edspath)
        self.EDSNodes[eds] = node

    def AddSlaveNode(self, nodename, nodeid, eds):
        if eds not in self.EDSNodes:
            raise ValueError("'%s' EDS file is not available" % eds)
        slave = {"Name": nodename, "EDS": eds, "Node": self.EDSNodes[eds]}
        self.SlaveNodes[nodeid] = slave
        self.Changed = True

    def RemoveSlaveNode(self, index):
        if index not in self.SlaveNodes:
            raise ValueError("Node with '0x%2.2X' ID doesn't exist" % (index))
        self.SlaveNodes.pop(index)
        self.Changed = True

    def LoadMasterNode(self, netname=None):
        if netname:
            masterpath = os.path.join(self.Root, "%s_master.od" % netname)
        else:
            masterpath = os.path.join(self.Root, "master.od")
        if os.path.isfile(masterpath):
            index = self.Manager.OpenFileInCurrent(masterpath)
        else:
            index = self.Manager.CreateNewNode("MasterNode", 0x00, "master", "", "None", "", "Heartbeat", ["DS302"])
        return index

    def SaveMasterNode(self, netname=None):
        if netname:
            masterpath = os.path.join(self.Root, "%s_master.od" % netname)
        else:
            masterpath = os.path.join(self.Root, "master.od")
        try:
            self.Manager.SaveCurrentInFile(masterpath)
        except Exception as exc:  # pylint: disable=broad-except
            raise_from(ValueError("Fail to save master node in '%s'" % (masterpath, )), exc)

    def LoadSlaveNodes(self, netname=None):
        cpjpath = os.path.join(self.Root, "nodelist.cpj")
        if os.path.isfile(cpjpath):
            try:
                networks = eds_utils.ParseCPJFile(cpjpath)
                network = None
                if netname:
                    for net in networks:
                        if net["Name"] == netname:
                            network = net
                    self.NetworkName = netname
                elif len(networks) > 0:
                    network = networks[0]
                    self.NetworkName = network["Name"]
                if network:
                    for nodeid, node in network["Nodes"].items():
                        if node["Present"] == 1:
                            self.AddSlaveNode(node["Name"], nodeid, node["DCFName"])
                self.Changed = False
            except Exception as exc:  # pylint: disable=broad-except
                raise_from(ValueError("Unable to load CPJ file '%s'" % (cpjpath, )), exc)
        return None

    def SaveNodeList(self, netname=None):
        try:
            cpjpath = os.path.join(self.Root, "nodelist.cpj")
            content = eds_utils.GenerateCPJContent(self)
            if netname:
                mode = "a"
            else:
                mode = "w"
            with open(cpjpath, mode=mode) as f:
                f.write(content)
            self.Changed = False
            return None
        except Exception as exc:  # pylint: disable=broad-except
            raise_from(ValueError("Fail to save node list in '%s'" % (cpjpath)), exc)

    def GetSlaveNodeEntry(self, nodeid, index, subindex=None):
        if nodeid not in self.SlaveNodes:
            raise ValueError("Node 0x%2.2X doesn't exist" % nodeid)
        self.SlaveNodes[nodeid]["Node"].ID = nodeid
        return self.SlaveNodes[nodeid]["Node"].GetEntry(index, subindex)

    def GetMasterNodeEntry(self, index, subindex=None):
        return self.Manager.GetCurrentEntry(index, subindex)

    def SetMasterNodeEntry(self, index, subindex=None, value=None):
        self.Manager.SetCurrentEntry(index, subindex, value)

    def GetOrderNumber(self, nodeid):
        nodeindexes = list(sorted(self.SlaveNodes))
        return nodeindexes.index(nodeid) + 1

    def GetNodeByOrder(self, order):
        if order > 0:
            nodeindexes = list(sorted(self.SlaveNodes))
            if order <= len(nodeindexes):
                return self.SlaveNodes[nodeindexes[order - 1]]["Node"]
        return None

    def IsCurrentEntry(self, index):
        if self.CurrentSelected is not None:
            if self.CurrentSelected == 0:
                return self.Manager.IsCurrentEntry(index)
            node = self.SlaveNodes[self.CurrentSelected]["Node"]
            if node:
                node.ID = self.CurrentSelected
                return node.IsEntry(index)
        return False

    def GetEntryInfos(self, index):
        if self.CurrentSelected is not None:
            if self.CurrentSelected == 0:
                return self.Manager.GetEntryInfos(index)
            node = self.SlaveNodes[self.CurrentSelected]["Node"]
            if node:
                node.ID = self.CurrentSelected
                return node.GetEntryInfos(index)
        return None

    def GetSubentryInfos(self, index, subindex):
        if self.CurrentSelected is not None:
            if self.CurrentSelected == 0:
                return self.Manager.GetSubentryInfos(index, subindex)
            node = self.SlaveNodes[self.CurrentSelected]["Node"]
            if node:
                node.ID = self.CurrentSelected
                return node.GetSubentryInfos(index, subindex)
        return None

    def GetCurrentValidIndexes(self, min_, max_):
        if self.CurrentSelected is not None:
            if self.CurrentSelected == 0:
                return self.Manager.GetCurrentValidIndexes(min_, max_)
            node = self.SlaveNodes[self.CurrentSelected]["Node"]
            if node:
                node.ID = self.CurrentSelected
                return [
                    (node.GetEntryName(index), index)
                    for index in node.GetIndexes()
                    if min_ <= index <= max_
                ]
            raise ValueError("Can't find node")
        return []

    def GetCurrentEntryValues(self, index):
        if self.CurrentSelected is not None:
            node = self.SlaveNodes[self.CurrentSelected]["Node"]
            if node:
                node.ID = self.CurrentSelected
                return self.Manager.GetNodeEntryValues(node, index)
            raise ValueError("Can't find node")
        return [], []

    def AddToMasterDCF(self, node_id, index, subindex, size, value):
        # Adding DCF entry into Master node
        if not self.Manager.IsCurrentEntry(0x1F22):
            self.Manager.ManageEntriesOfCurrent([0x1F22], [])
        self.Manager.AddSubentriesToCurrent(0x1F22, 127)

        self.Manager.AddToDCF(node_id, index, subindex, size, value)


def main(projectdir):
    # pylint: disable=import-outside-toplevel
    from .nodemanager import NodeManager

    manager = NodeManager()

    nodelist = NodeList(manager)

    nodelist.LoadProject(projectdir)
    print("MasterNode :")
    manager.CurrentNode.Print()
    print()
    for nodeid, node in nodelist.SlaveNodes.items():
        print("SlaveNode name=%s id=0x%2.2X :" % (node["Name"], nodeid))
        node["Node"].Print()
        print()
