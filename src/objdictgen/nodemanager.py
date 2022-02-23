#!/usr/bin/env python
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

from __future__ import absolute_import
# from builtins import str
from builtins import object
from builtins import range

import os
import re
import codecs
import errno
from future.utils import raise_from

from .nosis import pickle as nosis
from . import node as nod
from . import eds_utils, gen_cfile
from . import dbg

UndoBufferLength = 20

type_model = re.compile(r'([\_A-Z]*)([0-9]*)')
range_model = re.compile(r'([\_A-Z]*)([0-9]*)\[([\-0-9]*)-([\-0-9]*)\]')

# ID for the file viewed
CurrentID = 0


# Returns a new id
def GetNewId():
    global CurrentID
    CurrentID += 1
    return CurrentID


class UndoBuffer(object):
    """
    Class implementing a buffer of changes made on the current editing Object Dictionary
    """

    def __init__(self, currentstate, issaved=False):
        """
        Constructor initialising buffer
        """
        self.Buffer = []
        self.CurrentIndex = -1
        self.MinIndex = -1
        self.MaxIndex = -1
        # if current state is defined
        if currentstate:
            self.CurrentIndex = 0
            self.MinIndex = 0
            self.MaxIndex = 0
        # Initialising buffer with currentstate at the first place
        for i in range(UndoBufferLength):
            self.Buffer.append(currentstate if not i else None)
        # Initialising index of state saved
        if issaved:
            self.LastSave = 0
        else:
            self.LastSave = -1

    def Buffering(self, currentstate):
        """
        Add a new state in buffer
        """
        self.CurrentIndex = (self.CurrentIndex + 1) % UndoBufferLength
        self.Buffer[self.CurrentIndex] = currentstate
        # Actualising buffer limits
        self.MaxIndex = self.CurrentIndex
        if self.MinIndex == self.CurrentIndex:
            # If the removed state was the state saved, there is no state saved in the buffer
            if self.LastSave == self.MinIndex:
                self.LastSave = -1
            self.MinIndex = (self.MinIndex + 1) % UndoBufferLength
        self.MinIndex = max(self.MinIndex, 0)

    def Current(self):
        """
        Return current state of buffer
        """
        return self.Buffer[self.CurrentIndex]

    def Previous(self):
        """
        Change current state to previous in buffer and return new current state
        """
        if self.CurrentIndex != self.MinIndex:
            self.CurrentIndex = (self.CurrentIndex - 1) % UndoBufferLength
            return self.Buffer[self.CurrentIndex]
        return None

    def Next(self):
        """
        Change current state to next in buffer and return new current state
        """
        if self.CurrentIndex != self.MaxIndex:
            self.CurrentIndex = (self.CurrentIndex + 1) % UndoBufferLength
            return self.Buffer[self.CurrentIndex]
        return None

    def IsFirst(self):
        """
        Return True if current state is the first in buffer
        """
        return self.CurrentIndex == self.MinIndex

    def IsLast(self):
        """
        Return True if current state is the last in buffer
        """
        return self.CurrentIndex == self.MaxIndex

    def CurrentSaved(self):
        """
        Save current state
        """
        self.LastSave = self.CurrentIndex

    def IsCurrentSaved(self):
        """
        Return True if current state is saved
        """
        return self.LastSave == self.CurrentIndex


class NodeManager(object):
    """
    Class which control the operations made on the node and answer to view requests
    """

    def __init__(self):
        """
        Constructor
        """
        self.LastNewIndex = 0
        self.FilePaths = {}
        self.FileNames = {}
        self.NodeIndex = None
        self.CurrentNode = None
        self.UndoBuffers = {}

# ------------------------------------------------------------------------------
#                         Type and Map Variable Lists
# ------------------------------------------------------------------------------

    def GetCurrentTypeList(self):
        """
        Return the list of types defined for the current node
        """
        if self.CurrentNode:
            return self.CurrentNode.GetTypeList()
        return ""

    def GetCurrentMapList(self):
        """
        Return the list of variables that can be mapped for the current node
        """
        if self.CurrentNode:
            return self.CurrentNode.GetMapList()
        return ""

# ------------------------------------------------------------------------------
#                        Create Load and Save Functions
# ------------------------------------------------------------------------------

    def CreateNewNode(self, name, id, type, description, profile, filepath, nmt, options):
        """
        Create a new node and add a new buffer for storing it
        """
        # Create a new node
        node = nod.Node()
        # Load profile given
        self.LoadProfile(profile, filepath, node)
        # Initialising node
        self.CurrentNode = node
        self.CurrentNode.SetNodeName(name)
        self.CurrentNode.SetNodeID(id)
        self.CurrentNode.SetNodeType(type)
        self.CurrentNode.SetNodeDescription(description)
        addindexlist = self.GetMandatoryIndexes()
        addsubindexlist = []
        if nmt == "NodeGuarding":
            addindexlist.extend([0x100C, 0x100D])
        elif nmt == "Heartbeat":
            addindexlist.append(0x1017)
        for option in options:
            if option == "DS302":
                ds302path = os.path.join(os.path.split(__file__)[0], "config/DS-302.prf")
                # Charging DS-302 profile if choosen by user
                if not os.path.isfile(ds302path):
                    raise OSError(errno.ENOENT, os.strerror(errno.ENOENT), ds302path)
                # Import profile
                mapping, menuentries = nod.ImportProfile(ds302path)
                self.CurrentNode.SetDS302Profile(mapping)
                self.CurrentNode.ExtendSpecificMenu(menuentries)
            elif option == "GenSYNC":
                addindexlist.extend([0x1005, 0x1006])
            elif option == "Emergency":
                addindexlist.append(0x1014)
            elif option == "SaveConfig":
                addindexlist.extend([0x1010, 0x1011, 0x1020])
            elif option == "StoreEDS":
                addindexlist.extend([0x1021, 0x1022])
        if type == "slave":
            # add default SDO server
            addindexlist.append(0x1200)
            # add default 4 receive and 4 transmit PDO
            for comm, mapping in [(0x1400, 0x1600), (0x1800, 0x1A00)]:
                firstparamindex = self.GetLineFromIndex(comm)
                firstmappingindex = self.GetLineFromIndex(mapping)
                addindexlist.extend(list(range(firstparamindex, firstparamindex + 4)))
                for idx in range(firstmappingindex, firstmappingindex + 4):
                    addindexlist.append(idx)
                    addsubindexlist.append((idx, 8))
        # Add a new buffer
        index = self.AddNodeBuffer(self.CurrentNode.Copy(), False)
        self.SetCurrentFilePath("")
        # Add Mandatory indexes
        self.ManageEntriesOfCurrent(addindexlist, [])
        for idx, num in addsubindexlist:
            self.AddSubentriesToCurrent(idx, num)
        return index

    def LoadProfile(self, profile, filepath, node):
        """
        Load a profile in node
        """
        if profile != "None":
            # Import profile
            mapping, menuentries = nod.ImportProfile(filepath)
            node.SetProfileName(profile)
            node.SetProfile(mapping)
            node.SetSpecificMenu(menuentries)
        else:
            # Default profile
            node.SetProfileName("None")
            node.SetProfile({})
            node.SetSpecificMenu([])

    def OpenFileInCurrent(self, filepath):
        """
        Open a file and store it in a new buffer
        """
        with open(filepath, "r") as f:
            node = nosis.xmlload(f)
        self.CurrentNode = node
        self.CurrentNode.SetNodeID(0)
        # Add a new buffer and defining current state
        index = self.AddNodeBuffer(self.CurrentNode.Copy(), True)
        self.SetCurrentFilePath(filepath)
        return index

    def SaveCurrentInFile(self, filepath=None):
        """
        Save current node in  a file
        """
        # if no filepath given, verify if current node has a filepath defined
        if not filepath:
            filepath = self.GetCurrentFilePath()
            if filepath == "":
                return False
        # Save node in file
        with open(filepath, "w") as f:
            nosis.xmldump(f, self.CurrentNode)
        self.SetCurrentFilePath(filepath)
        # Update saved state in buffer
        self.UndoBuffers[self.NodeIndex].CurrentSaved()
        return True

    def CloseCurrent(self, ignore=False):
        """
        Close current state
        """
        # Verify if it's not forced that the current node is saved before closing it
        if self.NodeIndex in self.UndoBuffers and (self.UndoBuffers[self.NodeIndex].IsCurrentSaved() or ignore):
            self.RemoveNodeBuffer(self.NodeIndex)
            if len(self.UndoBuffers) > 0:
                previousindexes = [idx for idx in self.UndoBuffers if idx < self.NodeIndex]
                nextindexes = [idx for idx in self.UndoBuffers if idx > self.NodeIndex]
                if len(previousindexes) > 0:
                    previousindexes.sort()
                    self.NodeIndex = previousindexes[-1]
                elif len(nextindexes) > 0:
                    nextindexes.sort()
                    self.NodeIndex = nextindexes[0]
                else:
                    self.NodeIndex = None
            else:
                self.NodeIndex = None
            return True
        return False

    def ImportCurrentFromEDSFile(self, filepath):
        """
        Import an eds file and store it in a new buffer if no node edited
        """
        # Generate node from definition in a xml file
        node = eds_utils.GenerateNode(filepath)
        self.CurrentNode = node
        index = self.AddNodeBuffer(self.CurrentNode.Copy(), False)
        self.SetCurrentFilePath("")
        return index

    def ExportCurrentToEDSFile(self, filepath):
        """
        Export to an eds file and store it in a new buffer if no node edited
        """
        eds_utils.GenerateEDSFile(filepath, self.CurrentNode)

    def ExportCurrentToCFile(self, filepath):
        """
        Build the C definition of Object Dictionary for current node
        """
        if self.CurrentNode:
            gen_cfile.GenerateFile(filepath, self.CurrentNode)

# ------------------------------------------------------------------------------
#                        Add Entries to Current Functions
# ------------------------------------------------------------------------------

    def AddSubentriesToCurrent(self, index, number, node=None):
        """
        Add the specified number of subentry for the given entry. Verify that total
        number of subentry (except 0) doesn't exceed nbmax defined
        """
        disable_buffer = node is not None
        if node is None:
            node = self.CurrentNode
        # Informations about entry
        length = node.GetEntry(index, 0)
        infos = node.GetEntryInfos(index)
        subentry_infos = node.GetSubentryInfos(index, 1)
        # Get default value for subindex
        if "default" in subentry_infos:
            default = subentry_infos["default"]
        else:
            default = self.GetTypeDefaultValue(subentry_infos["type"])
        # First case entry is record
        if infos["struct"] & nod.OD_IdenticalSubindexes:
            for i in range(1, min(number, subentry_infos["nbmax"] - length) + 1):
                node.AddEntry(index, length + i, default)
            if not disable_buffer:
                self.BufferCurrentNode()
            return None
        # Second case entry is array, only possible for manufacturer specific
        elif infos["struct"] & nod.OD_MultipleSubindexes and 0x2000 <= index <= 0x5FFF:
            values = {"name": "Undefined", "type": 5, "access": "rw", "pdo": True}
            for i in range(1, min(number, 0xFE - length) + 1):
                node.AddMappingEntry(index, length + i, values=values.copy())
                node.AddEntry(index, length + i, 0)
            if not disable_buffer:
                self.BufferCurrentNode()
            return None

    def RemoveSubentriesFromCurrent(self, index, number):
        """
        Remove the specified number of subentry for the given entry. Verify that total
        number of subentry (except 0) isn't less than 1
        """
        # Informations about entry
        infos = self.GetEntryInfos(index)
        length = self.CurrentNode.GetEntry(index, 0)
        if "nbmin" in infos:
            nbmin = infos["nbmin"]
        else:
            nbmin = 1
        # Entry is a record, or is an array of manufacturer specific
        if infos["struct"] & nod.OD_IdenticalSubindexes or 0x2000 <= index <= 0x5FFF and infos["struct"] & nod.OD_IdenticalSubindexes:
            for i in range(min(number, length - nbmin)):
                self.RemoveCurrentVariable(index, length - i)
            self.BufferCurrentNode()

    def AddSDOServerToCurrent(self):
        """
        Add a SDO Server to current node
        """
        # An SDO Server is already defined at index 0x1200
        if self.CurrentNode.IsEntry(0x1200):
            indexlist = [self.GetLineFromIndex(0x1201)]
            if None not in indexlist:
                self.ManageEntriesOfCurrent(indexlist, [])
        # Add an SDO Server at index 0x1200
        else:
            self.ManageEntriesOfCurrent([0x1200], [])

    def AddSDOClientToCurrent(self):
        """
        Add a SDO Server to current node
        """
        indexlist = [self.GetLineFromIndex(0x1280)]
        if None not in indexlist:
            self.ManageEntriesOfCurrent(indexlist, [])

    def AddPDOTransmitToCurrent(self):
        """
        Add a Transmit PDO to current node
        """
        indexlist = [self.GetLineFromIndex(0x1800), self.GetLineFromIndex(0x1A00)]
        if None not in indexlist:
            self.ManageEntriesOfCurrent(indexlist, [])

    def AddPDOReceiveToCurrent(self):
        """
        Add a Receive PDO to current node
        """
        indexlist = [self.GetLineFromIndex(0x1400), self.GetLineFromIndex(0x1600)]
        if None not in indexlist:
            self.ManageEntriesOfCurrent(indexlist, [])

    def AddSpecificEntryToCurrent(self, menuitem):
        """
        Add a list of entries defined in profile for menu item selected to current node
        """
        indexlist = []
        for menu, indexes in self.CurrentNode.GetSpecificMenu():
            if menuitem == menu:
                indexlist.extend(
                    self.GetLineFromIndex(index)
                    for index in indexes
                )
        if None not in indexlist:
            self.ManageEntriesOfCurrent(indexlist, [])

    def GetLineFromIndex(self, base_index):
        """
        Search the first index available for a pluri entry from base_index
        """
        found = False
        index = base_index
        infos = self.GetEntryInfos(base_index)
        while index < base_index + infos["incr"] * infos["nbmax"] and not found:
            if not self.CurrentNode.IsEntry(index):
                found = True
            else:
                index += infos["incr"]
        if found:
            return index
        return None

    def ManageEntriesOfCurrent(self, addinglist, removinglist, node=None):
        """
        Add entries specified in addinglist and remove entries specified in removinglist
        """
        disable_buffer = node is not None
        if node is None:
            node = self.CurrentNode
        # Add all the entries in addinglist
        for index in addinglist:
            infos = self.GetEntryInfos(index)
            if infos["struct"] & nod.OD_MultipleSubindexes:
                # First case entry is an array
                if infos["struct"] & nod.OD_IdenticalSubindexes:
                    subentry_infos = self.GetSubentryInfos(index, 1)
                    if "default" in subentry_infos:
                        default = subentry_infos["default"]
                    else:
                        default = self.GetTypeDefaultValue(subentry_infos["type"])
                    node.AddEntry(index, value=[])
                    if "nbmin" in subentry_infos:
                        for i in range(subentry_infos["nbmin"]):
                            node.AddEntry(index, i + 1, default)
                    else:
                        node.AddEntry(index, 1, default)
                # Second case entry is a record
                else:
                    i = 1
                    subentry_infos = self.GetSubentryInfos(index, i)
                    while subentry_infos:
                        if "default" in subentry_infos:
                            default = subentry_infos["default"]
                        else:
                            default = self.GetTypeDefaultValue(subentry_infos["type"])
                        node.AddEntry(index, i, default)
                        i += 1
                        subentry_infos = self.GetSubentryInfos(index, i)
            # Third case entry is a var
            else:
                subentry_infos = self.GetSubentryInfos(index, 0)
                if "default" in subentry_infos:
                    default = subentry_infos["default"]
                else:
                    default = self.GetTypeDefaultValue(subentry_infos["type"])
                node.AddEntry(index, 0, default)
        # Remove all the entries in removinglist
        for index in removinglist:
            self.RemoveCurrentVariable(index)
        if not disable_buffer:
            self.BufferCurrentNode()
        return None

    def SetCurrentEntryToDefault(self, index, subindex, node=None):
        """
        Reset an subentry from current node to its default value
        """
        disable_buffer = node is not None
        if node is None:
            node = self.CurrentNode
        if node.IsEntry(index, subindex):
            subentry_infos = self.GetSubentryInfos(index, subindex)
            if "default" in subentry_infos:
                default = subentry_infos["default"]
            else:
                default = self.GetTypeDefaultValue(subentry_infos["type"])
            node.SetEntry(index, subindex, default)
            if not disable_buffer:
                self.BufferCurrentNode()

    def RemoveCurrentVariable(self, index, subindex=None):
        """
        Remove an entry from current node. Analize the index to perform the correct
        method
        """
        mappings = self.CurrentNode.GetMappings()
        if index < 0x1000 and subindex is None:
            type = self.CurrentNode.GetEntry(index, 1)
            for i in mappings[-1]:
                for value in mappings[-1][i]["values"]:
                    if value["type"] == index:
                        value["type"] = type
            self.CurrentNode.RemoveMappingEntry(index)
            self.CurrentNode.RemoveEntry(index)
        elif index == 0x1200 and subindex is None:
            self.CurrentNode.RemoveEntry(0x1200)
        elif 0x1201 <= index <= 0x127F and subindex is None:
            self.CurrentNode.RemoveLine(index, 0x127F)
        elif 0x1280 <= index <= 0x12FF and subindex is None:
            self.CurrentNode.RemoveLine(index, 0x12FF)
        elif 0x1400 <= index <= 0x15FF or 0x1600 <= index <= 0x17FF and subindex is None:
            if 0x1600 <= index <= 0x17FF and subindex is None:
                index -= 0x200
            self.CurrentNode.RemoveLine(index, 0x15FF)
            self.CurrentNode.RemoveLine(index + 0x200, 0x17FF)
        elif 0x1800 <= index <= 0x19FF or 0x1A00 <= index <= 0x1BFF and subindex is None:
            if 0x1A00 <= index <= 0x1BFF:
                index -= 0x200
            self.CurrentNode.RemoveLine(index, 0x19FF)
            self.CurrentNode.RemoveLine(index + 0x200, 0x1BFF)
        else:
            found = False
            for _, list_ in self.CurrentNode.GetSpecificMenu():
                for i in list_:
                    iinfos = self.GetEntryInfos(i)
                    indexes = [i + incr * iinfos["incr"] for incr in range(iinfos["nbmax"])]
                    if index in indexes:
                        found = True
                        diff = index - i
                        for j in list_:
                            jinfos = self.GetEntryInfos(j)
                            self.CurrentNode.RemoveLine(j + diff, j + jinfos["incr"] * jinfos["nbmax"], jinfos["incr"])
            self.CurrentNode.RemoveMapVariable(index, subindex)
            if not found:
                infos = self.GetEntryInfos(index)
                if not infos["need"]:
                    self.CurrentNode.RemoveEntry(index, subindex)
            if index in mappings[-1]:
                self.CurrentNode.RemoveMappingEntry(index, subindex)

    def AddMapVariableToCurrent(self, index, name, struct, number, node=None):
        if 0x2000 <= index <= 0x5FFF:
            disable_buffer = node is not None
            if node is None:
                node = self.CurrentNode
            if node.IsEntry(index):
                raise ValueError("Index 0x%04X already defined!" % index)
            node.AddMappingEntry(index, name=name, struct=struct)
            if struct == nod.var:
                values = {"name": name, "type": 0x05, "access": "rw", "pdo": True}
                node.AddMappingEntry(index, 0, values=values)
                node.AddEntry(index, 0, 0)
            else:
                values = {"name": "Number of Entries", "type": 0x05, "access": "ro", "pdo": False}
                node.AddMappingEntry(index, 0, values=values)
                if struct == nod.array:
                    values = {"name": name + " %d[(sub)]", "type": 0x05, "access": "rw", "pdo": True, "nbmax": 0xFE}
                    node.AddMappingEntry(index, 1, values=values)
                    for i in range(number):
                        node.AddEntry(index, i + 1, 0)
                else:
                    for i in range(number):
                        values = {"name": "Undefined", "type": 0x05, "access": "rw", "pdo": True}
                        node.AddMappingEntry(index, i + 1, values=values)
                        node.AddEntry(index, i + 1, 0)
            if not disable_buffer:
                self.BufferCurrentNode()
            return None
        else:
            raise ValueError("Index 0x%04X isn't a valid index for Map Variable!" % index)

    def AddUserTypeToCurrent(self, type, min, max, length):
        index = 0xA0
        while index < 0x100 and self.CurrentNode.IsEntry(index):
            index += 1
        if index >= 0x100:
            raise ValueError("Too many User Types have already been defined!")
        customisabletypes = self.GetCustomisableTypes()
        name, valuetype = customisabletypes[type]
        size = self.GetEntryInfos(type)["size"]
        default = self.GetTypeDefaultValue(type)
        if valuetype == 0:
            self.CurrentNode.AddMappingEntry(index, name="%s[%d-%d]" % (name, min, max), struct=nod.record, size=size, default=default)
            self.CurrentNode.AddMappingEntry(index, 0, values={"name": "Number of Entries", "type": 0x05, "access": "ro", "pdo": False})
            self.CurrentNode.AddMappingEntry(index, 1, values={"name": "Type", "type": 0x05, "access": "ro", "pdo": False})
            self.CurrentNode.AddMappingEntry(index, 2, values={"name": "Minimum Value", "type": type, "access": "ro", "pdo": False})
            self.CurrentNode.AddMappingEntry(index, 3, values={"name": "Maximum Value", "type": type, "access": "ro", "pdo": False})
            self.CurrentNode.AddEntry(index, 1, type)
            self.CurrentNode.AddEntry(index, 2, min)
            self.CurrentNode.AddEntry(index, 3, max)
        elif valuetype == 1:
            self.CurrentNode.AddMappingEntry(index, name="%s%d" % (name, length), struct=nod.record, size=length * size, default=default)
            self.CurrentNode.AddMappingEntry(index, 0, values={"name": "Number of Entries", "type": 0x05, "access": "ro", "pdo": False})
            self.CurrentNode.AddMappingEntry(index, 1, values={"name": "Type", "type": 0x05, "access": "ro", "pdo": False})
            self.CurrentNode.AddMappingEntry(index, 2, values={"name": "Length", "type": 0x05, "access": "ro", "pdo": False})
            self.CurrentNode.AddEntry(index, 1, type)
            self.CurrentNode.AddEntry(index, 2, length)
        self.BufferCurrentNode()

# ------------------------------------------------------------------------------
#                      Modify Entry and Mapping Functions
# ------------------------------------------------------------------------------

    def SetCurrentEntryCallbacks(self, index, value):
        if self.CurrentNode and self.CurrentNode.IsEntry(index):
            entry_infos = self.GetEntryInfos(index)
            if "callback" not in entry_infos:
                self.CurrentNode.SetParamsEntry(index, None, callback=value)
                self.BufferCurrentNode()

    def SetCurrentEntry(self, index, subindex, value, name, editor, node=None):
        disable_buffer = node is not None
        if node is None:
            node = self.CurrentNode
        if node and node.IsEntry(index):
            if name == "value":
                if editor == "map":
                    value = node.GetMapValue(value)
                    if value is not None:
                        node.SetEntry(index, subindex, value)
                elif editor == "bool":
                    value = value == "True"
                    node.SetEntry(index, subindex, value)
                elif editor == "time":
                    node.SetEntry(index, subindex, value)
                elif editor == "number":
                    try:
                        node.SetEntry(index, subindex, int(value))
                    except ValueError as exc:
                        dbg("ValueError: '%s': %s" % (value, exc))
                        raise  # FIXME: Originial code swallows exception
                elif editor == "float":
                    try:
                        node.SetEntry(index, subindex, float(value))
                    except ValueError as exc:
                        dbg("ValueError: '%s': %s" % (value, exc))
                        raise  # FIXME: Originial code swallows exception
                elif editor == "domain":
                    try:
                        if len(value) % 2 != 0:
                            value = "0" + value
                        value = codecs.decode(value, 'hex_codec')
                        node.SetEntry(index, subindex, value)
                    except Exception as exc:  # FIXME: Which exception is wanted here?
                        dbg("Exception: '%s': %s" % (value, exc))
                        raise  # FIXME: Originial code swallows exception
                elif editor == "dcf":
                    node.SetEntry(index, subindex, value)
                else:
                    subentry_infos = self.GetSubentryInfos(index, subindex)
                    type = subentry_infos["type"]
                    dic = {
                        typeindex: typevalue
                        for typeindex, typevalue in nod.CustomisableTypes
                    }
                    if type not in dic:
                        type = node.GetEntry(type)[1]
                    if dic[type] == 0:
                        try:
                            if value.startswith("$NODEID"):
                                value = "\"%s\"" % value
                            elif value.startswith("0x"):
                                value = int(value, 16)
                            else:
                                value = int(value)
                            node.SetEntry(index, subindex, value)
                        except Exception as exc:  # Which exception shoulw be used
                            dbg("Exception: '%s': %s" % (value, exc))
                            raise  # FIXME: Originial code swallows exception
                    else:
                        node.SetEntry(index, subindex, value)
            elif name in ["comment", "save", "buffer_size"]:
                if editor == "option":
                    value = value == "Yes"
                if name == "save":
                    node.SetParamsEntry(index, subindex, save=value)
                elif name == "comment":
                    node.SetParamsEntry(index, subindex, comment=value)
                elif name == "buffer_size":
                    node.SetParamsEntry(index, subindex, buffer_size=value)
            else:
                if editor == "type":
                    value = self.GetTypeIndex(value)
                    size = self.GetEntryInfos(value)["size"]
                    node.UpdateMapVariable(index, subindex, size)
                elif editor in ["access", "raccess"]:
                    dic = {
                        access: abbrev
                        for abbrev, access in nod.AccessType.items()
                    }
                    value = dic[value]
                    if editor == "raccess" and not node.IsMappingEntry(index):
                        entry_infos = self.GetEntryInfos(index)
                        subindex0_infos = self.GetSubentryInfos(index, 0, False).copy()
                        subindex1_infos = self.GetSubentryInfos(index, 1, False).copy()
                        node.AddMappingEntry(index, name=entry_infos["name"], struct=nod.array)
                        node.AddMappingEntry(index, 0, values=subindex0_infos)
                        node.AddMappingEntry(index, 1, values=subindex1_infos)
                node.SetMappingEntry(index, subindex, values={name: value})
            if not disable_buffer:
                self.BufferCurrentNode()
            return None

    def SetCurrentEntryName(self, index, name):
        self.CurrentNode.SetMappingEntry(index, name=name)
        self.BufferCurrentNode()

    def SetCurrentUserType(self, index, type, min, max, length):
        customisabletypes = self.GetCustomisableTypes()
        _, valuetype = self.GetCustomisedTypeValues(index)
        name, new_valuetype = customisabletypes[type]
        size = self.GetEntryInfos(type)["size"]
        default = self.GetTypeDefaultValue(type)
        if new_valuetype == 0:
            self.CurrentNode.SetMappingEntry(index, name="%s[%d-%d]" % (name, min, max), struct=nod.record, size=size, default=default)
            if valuetype == 1:
                self.CurrentNode.SetMappingEntry(index, 2, values={"name": "Minimum Value", "type": type, "access": "ro", "pdo": False})
                self.CurrentNode.AddMappingEntry(index, 3, values={"name": "Maximum Value", "type": type, "access": "ro", "pdo": False})
            self.CurrentNode.SetEntry(index, 1, type)
            self.CurrentNode.SetEntry(index, 2, min)
            if valuetype == 1:
                self.CurrentNode.AddEntry(index, 3, max)
            else:
                self.CurrentNode.SetEntry(index, 3, max)
        elif new_valuetype == 1:
            self.CurrentNode.SetMappingEntry(index, name="%s%d" % (name, length), struct=nod.record, size=size, default=default)
            if valuetype == 0:
                self.CurrentNode.SetMappingEntry(index, 2, values={"name": "Length", "type": 0x02, "access": "ro", "pdo": False})
                self.CurrentNode.RemoveMappingEntry(index, 3)
            self.CurrentNode.SetEntry(index, 1, type)
            self.CurrentNode.SetEntry(index, 2, length)
            if valuetype == 0:
                self.CurrentNode.RemoveEntry(index, 3)
        self.BufferCurrentNode()

# ------------------------------------------------------------------------------
#                      Current Buffering Management Functions
# ------------------------------------------------------------------------------

    def BufferCurrentNode(self):
        self.UndoBuffers[self.NodeIndex].Buffering(self.CurrentNode.Copy())

    def CurrentIsSaved(self):
        return self.UndoBuffers[self.NodeIndex].IsCurrentSaved()

    def OneFileHasChanged(self):
        return any(
            not buffer
            for buffer in self.UndoBuffers.values()
        )

    def GetBufferNumber(self):
        return len(self.UndoBuffers)

    def GetBufferIndexes(self):
        return list(self.UndoBuffers)

    def LoadCurrentPrevious(self):
        self.CurrentNode = self.UndoBuffers[self.NodeIndex].Previous().Copy()

    def LoadCurrentNext(self):
        self.CurrentNode = self.UndoBuffers[self.NodeIndex].Next().Copy()

    def AddNodeBuffer(self, currentstate=None, issaved=False):
        self.NodeIndex = GetNewId()
        self.UndoBuffers[self.NodeIndex] = UndoBuffer(currentstate, issaved)
        self.FilePaths[self.NodeIndex] = ""
        self.FileNames[self.NodeIndex] = ""
        return self.NodeIndex

    def ChangeCurrentNode(self, index):
        if index in self.UndoBuffers:
            self.NodeIndex = index
            self.CurrentNode = self.UndoBuffers[self.NodeIndex].Current().Copy()

    def RemoveNodeBuffer(self, index):
        self.UndoBuffers.pop(index)
        self.FilePaths.pop(index)
        self.FileNames.pop(index)

    def GetCurrentNodeIndex(self):
        return self.NodeIndex

    def GetCurrentFilename(self):
        return self.GetFilename(self.NodeIndex)

    def GetAllFilenames(self):
        return [
            self.GetFilename(idx)
            for idx in sorted(self.UndoBuffers)
        ]

    def GetFilename(self, index):
        if self.UndoBuffers[index].IsCurrentSaved():
            return self.FileNames[index]
        else:
            return "~%s~" % self.FileNames[index]

    def SetCurrentFilePath(self, filepath):
        self.FilePaths[self.NodeIndex] = filepath
        if filepath == "":
            self.LastNewIndex += 1
            self.FileNames[self.NodeIndex] = "Unnamed%d" % self.LastNewIndex
        else:
            self.FileNames[self.NodeIndex] = os.path.splitext(os.path.basename(filepath))[0]

    def GetCurrentFilePath(self):
        if len(self.FilePaths) > 0:
            return self.FilePaths[self.NodeIndex]
        else:
            return ""

    def GetCurrentBufferState(self):
        first = self.UndoBuffers[self.NodeIndex].IsFirst()
        last = self.UndoBuffers[self.NodeIndex].IsLast()
        return not first, not last

# ------------------------------------------------------------------------------
#                         Profiles Management Functions
# ------------------------------------------------------------------------------

    def GetCurrentCommunicationLists(self):
        list_ = []
        for index in nod.MappingDictionary:
            if 0x1000 <= index < 0x1200:
                list_.append(index)
        return self.GetProfileLists(nod.MappingDictionary, list_)

    def GetCurrentDS302Lists(self):
        return self.GetSpecificProfileLists(self.CurrentNode.GetDS302Profile())

    def GetCurrentProfileLists(self):
        return self.GetSpecificProfileLists(self.CurrentNode.GetProfile())

    def GetSpecificProfileLists(self, mappingdictionary):
        validlist = []
        exclusionlist = []
        for _, list_ in self.CurrentNode.GetSpecificMenu():
            exclusionlist.extend(list_)
        for index in mappingdictionary:
            if index not in exclusionlist:
                validlist.append(index)
        return self.GetProfileLists(mappingdictionary, validlist)

    def GetProfileLists(self, mappingdictionary, list_):
        dictionary = {}
        current = []
        for index in list_:
            dictionary[index] = (mappingdictionary[index]["name"], mappingdictionary[index]["need"])
            if self.CurrentNode.IsEntry(index):
                current.append(index)
        return dictionary, current

    def GetCurrentNextMapIndex(self):
        if self.CurrentNode:
            index = 0x2000
            while self.CurrentNode.IsEntry(index) and index < 0x5FFF:
                index += 1
            if index < 0x6000:
                return index
            else:
                return None

    def CurrentDS302Defined(self):
        if self.CurrentNode:
            return len(self.CurrentNode.GetDS302Profile()) > 0
        return False

# ------------------------------------------------------------------------------
#                         Node State and Values Functions
# ------------------------------------------------------------------------------

    def GetCurrentNodeName(self):
        if self.CurrentNode:
            return self.CurrentNode.GetNodeName()
        else:
            return ""

    def GetCurrentNodeCopy(self):
        if self.CurrentNode:
            return self.CurrentNode.Copy()
        else:
            return None

    def GetCurrentNodeID(self, node=None):  # pylint: disable=unused-argument
        if self.CurrentNode:
            return self.CurrentNode.GetNodeID()
        else:
            return None

    def GetCurrentNodeInfos(self):
        name = self.CurrentNode.GetNodeName()
        id_ = self.CurrentNode.GetNodeID()
        type_ = self.CurrentNode.GetNodeType()
        description = self.CurrentNode.GetNodeDescription()
        return name, id_, type_, description

    def SetCurrentNodeInfos(self, name, id, type, description):
        self.CurrentNode.SetNodeName(name)
        self.CurrentNode.SetNodeID(id)
        self.CurrentNode.SetNodeType(type)
        self.CurrentNode.SetNodeDescription(description)
        self.BufferCurrentNode()

    def GetCurrentNodeDefaultStringSize(self):
        if self.CurrentNode:
            return self.CurrentNode.GetDefaultStringSize()
        else:
            return nod.Node.DefaultStringSize

    def SetCurrentNodeDefaultStringSize(self, size):
        if self.CurrentNode:
            self.CurrentNode.SetDefaultStringSize(size)
        else:
            nod.Node.DefaultStringSize = size

    def GetCurrentProfileName(self):
        if self.CurrentNode:
            return self.CurrentNode.GetProfileName()
        return ""

    def IsCurrentEntry(self, index):
        if self.CurrentNode:
            return self.CurrentNode.IsEntry(index)
        return False

    def GetCurrentEntry(self, index, subindex=None, compute=True):
        if self.CurrentNode:
            return self.CurrentNode.GetEntry(index, subindex, compute)
        return None

    def GetCurrentParamsEntry(self, index, subindex=None):
        if self.CurrentNode:
            return self.CurrentNode.GetParamsEntry(index, subindex)
        return None

    def GetCurrentValidIndexes(self, min, max):
        validindexes = []
        for index in self.CurrentNode.GetIndexes():
            if min <= index <= max:
                validindexes.append((self.GetEntryName(index), index))
        return validindexes

    def GetCurrentValidChoices(self, min, max):
        validchoices = []
        exclusionlist = []
        for menu, indexes in self.CurrentNode.GetSpecificMenu():
            exclusionlist.extend(indexes)
            good = True
            for index in indexes:
                good &= min <= index <= max
            if good:
                validchoices.append((menu, None))
        list_ = [index for index in nod.MappingDictionary if index >= 0x1000]
        profiles = self.CurrentNode.GetMappings(False)
        for profile in profiles:
            list_.extend(list(profile))
        for index in sorted(list_):
            if min <= index <= max and not self.CurrentNode.IsEntry(index) and index not in exclusionlist:
                validchoices.append((self.GetEntryName(index), index))
        return validchoices

    def HasCurrentEntryCallbacks(self, index):
        if self.CurrentNode:
            return self.CurrentNode.HasEntryCallbacks(index)
        return False

    def GetCurrentEntryValues(self, index):
        if self.CurrentNode:
            return self.GetNodeEntryValues(self.CurrentNode, index)

    def GetNodeEntryValues(self, node, index):
        if node and node.IsEntry(index):
            entry_infos = node.GetEntryInfos(index)
            data = []
            editors = []
            values = node.GetEntry(index, compute=False)
            params = node.GetParamsEntry(index)
            if isinstance(values, list):
                for i, value in enumerate(values):
                    data.append({"value": value})
                    data[-1].update(params[i])
            else:
                data.append({"value": values})
                data[-1].update(params)
            for i, dic in enumerate(data):
                if dic["buffer_size"] and dic["buffer_size"].isdigit() is not True:
                    dic["buffer_size"] = ""
                infos = node.GetSubentryInfos(index, i)
                if infos["name"] == "Number of Entries":
                    dic["buffer_size"] = ""
                dic["subindex"] = "0x%02X" % i
                dic["name"] = infos["name"]
                dic["type"] = node.GetTypeName(infos["type"])
                if dic["type"] is None:
                    dic["type"] = "Unknown"
                    dic["buffer_size"] = ""
                dic["access"] = nod.AccessType[infos["access"]]
                dic["save"] = nod.OptionType[dic["save"]]
                editor = {"subindex": None, "name": None,
                          "type": None, "value": None,
                          "access": None, "save": "option",
                          "callback": "option", "comment": "string", "buffer_size": "string"}
                if isinstance(values, list) and i == 0:
                    if 0x1600 <= index <= 0x17FF or 0x1A00 <= index <= 0x1C00:
                        editor["access"] = "raccess"
                else:
                    if infos["user_defined"]:
                        if entry_infos["struct"] & nod.OD_IdenticalSubindexes:
                            if i == 1:
                                editor["type"] = "type"
                                editor["access"] = "access"
                        else:
                            if entry_infos["struct"] & nod.OD_MultipleSubindexes:
                                editor["name"] = "string"
                            editor["type"] = "type"
                            editor["access"] = "access"
                    if index < 0x260:
                        if i == 1:
                            dic["value"] = node.GetTypeName(dic["value"])
                    elif 0x1600 <= index <= 0x17FF or 0x1A00 <= index <= 0x1C00:
                        editor["value"] = "map"
                        dic["value"] = node.GetMapName(dic["value"])
                    else:
                        if dic["type"].startswith("VISIBLE_STRING") or dic["type"].startswith("OCTET_STRING"):
                            editor["value"] = "string"
                        elif dic["type"] in ["TIME_OF_DAY", "TIME_DIFFERENCE"]:
                            editor["value"] = "time"
                        elif dic["type"] == "DOMAIN":
                            if index == 0x1F22:
                                editor["value"] = "dcf"
                            else:
                                editor["value"] = "domain"
                            dic["value"] = codecs.encode(dic["value"].encode(), 'hex_codec')
                        elif dic["type"] == "BOOLEAN":
                            editor["value"] = "bool"
                            dic["value"] = nod.BoolType[dic["value"]]
                            dic["buffer_size"] = ""
                        result = type_model.match(dic["type"])
                        if result:
                            values = result.groups()
                            if values[0] == "UNSIGNED":
                                dic["buffer_size"] = ""
                                try:
                                    fmt = "0x%0" + str(int(values[1]) // 4) + "X"
                                    dic["value"] = fmt % dic["value"]
                                except ValueError as exc:
                                    dbg("ValueError: '%s': %s" % (values[1], exc))
                                    raise  # FIXME: Originial code swallows exception
                                editor["value"] = "string"
                            if values[0] == "INTEGER":
                                editor["value"] = "number"
                                dic["buffer_size"] = ""
                            elif values[0] == "REAL":
                                editor["value"] = "float"
                                dic["buffer_size"] = ""
                            elif values[0] in ["VISIBLE_STRING", "OCTET_STRING"]:
                                editor["length"] = values[0]
                        result = range_model.match(dic["type"])
                        if result:
                            values = result.groups()
                            if values[0] in ["UNSIGNED", "INTEGER", "REAL"]:
                                editor["min"] = values[2]
                                editor["max"] = values[3]
                                dic["buffer_size"] = ""
                editors.append(editor)
            return data, editors
        else:
            return None

    def AddToDCF(self, node_id, index, subindex, size, value):
        if self.CurrentNode.IsEntry(0x1F22, node_id):
            dcf_value = self.CurrentNode.GetEntry(0x1F22, node_id)
            if dcf_value != "":
                nbparams = nod.BE_to_LE(dcf_value[:4])
            else:
                nbparams = 0
            new_value = nod.LE_to_BE(nbparams + 1, 4) + dcf_value[4:]
            new_value += nod.LE_to_BE(index, 2) + nod.LE_to_BE(subindex, 1) + nod.LE_to_BE(size, 4) + nod.LE_to_BE(value, size)
            self.CurrentNode.SetEntry(0x1F22, node_id, new_value)

# ------------------------------------------------------------------------------
#                         Node Informations Functions
# ------------------------------------------------------------------------------

    def GetCustomisedTypeValues(self, index):
        if self.CurrentNode:
            values = self.CurrentNode.GetEntry(index)
            customisabletypes = self.GetCustomisableTypes()
            return values, customisabletypes[values[1]][1]
        return None, None

    def GetEntryName(self, index, compute=True):
        if self.CurrentNode:
            return self.CurrentNode.GetEntryName(index, compute)
        return nod.FindEntryName(index, nod.MappingDictionary, compute)

    def GetEntryInfos(self, index, compute=True):
        if self.CurrentNode:
            return self.CurrentNode.GetEntryInfos(index, compute)
        return nod.FindEntryInfos(index, nod.MappingDictionary, compute)

    def GetSubentryInfos(self, index, subindex, compute=True):
        if self.CurrentNode:
            return self.CurrentNode.GetSubentryInfos(index, subindex, compute)
        result = nod.FindSubentryInfos(index, subindex, nod.MappingDictionary, compute)
        if result:
            result["user_defined"] = False
        return result

    def GetTypeIndex(self, typename):
        if self.CurrentNode:
            return self.CurrentNode.GetTypeIndex(typename)
        return nod.FindTypeIndex(typename, nod.MappingDictionary)

    def GetTypeName(self, typeindex):
        if self.CurrentNode:
            return self.CurrentNode.GetTypeName(typeindex)
        return nod.FindTypeName(typeindex, nod.MappingDictionary)

    def GetTypeDefaultValue(self, typeindex):
        if self.CurrentNode:
            return self.CurrentNode.GetTypeDefaultValue(typeindex)
        return nod.FindTypeDefaultValue(typeindex, nod.MappingDictionary)

    def GetMapVariableList(self, compute=True):
        if self.CurrentNode:
            return self.CurrentNode.GetMapVariableList(compute)
        return []

    def GetMandatoryIndexes(self):
        if self.CurrentNode:
            return self.CurrentNode.GetMandatoryIndexes()
        return nod.FindMandatoryIndexes(nod.MappingDictionary)

    def GetCustomisableTypes(self):
        return {
            index: [self.GetTypeName(index), valuetype]
            for index, valuetype in nod.CustomisableTypes
        }

    def GetCurrentSpecificMenu(self):
        if self.CurrentNode:
            return self.CurrentNode.GetSpecificMenu()
        return []