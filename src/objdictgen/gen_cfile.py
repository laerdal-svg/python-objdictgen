# -*- coding: utf-8 -*-
#
#    This file is based on objdictgen from CanFestival
#
#    Copyright (C) 2022-2023  Svein Seldal, Laerdal Medical AS
#    Copyright (C): Edouard TISSERANT, Francis DUPIN
#
#    This library is free software; you can redistribute it and/or
#    modify it under the terms of the GNU Lesser General Public
#    License as published by the Free Software Foundation; either
#    version 2.1 of the License, or (at your option) any later version.
#
#    This library is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#    Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public
#    License along with this library; if not, write to the Free Software
#    Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301
#    USA

from __future__ import absolute_import
from builtins import range

import re
import os

from objdictgen.maps import OD

RE_WORD = re.compile(r'([a-zA-Z_0-9]*)')
RE_TYPE = re.compile(r'([\_A-Z]*)([0-9]*)')
RE_RANGE = re.compile(r'([\_A-Z]*)([0-9]*)\[([\-0-9]*)-([\-0-9]*)\]')
RE_STARTS_WITH_DIGIT = re.compile(r'^(\d.*)')
RE_NOTW = re.compile(r"[^\w]")

CATEGORIES = [("SDO_SVR", 0x1200, 0x127F), ("SDO_CLT", 0x1280, 0x12FF),
              ("PDO_RCV", 0x1400, 0x15FF), ("PDO_RCV_MAP", 0x1600, 0x17FF),
              ("PDO_TRS", 0x1800, 0x19FF), ("PDO_TRS_MAP", 0x1A00, 0x1BFF)]
INDEX_CATEGORIES = ["firstIndex", "lastIndex"]

FILE_HEADER = """\n/* File generated by gen_cfile.py. Should not be modified. */\n"""


class CFileContext(object):
    def __init__(self):
        self.internal_types = {}
        self.default_string_size = 10


# Format a string for making a C++ variable
def FormatName(name):
    wordlist = [word for word in RE_WORD.findall(name) if word]
    return "_".join(wordlist)


# Extract the informations from a given type name
def GetValidTypeInfos(context, typename, items=None):
    items = items or []
    if typename in context.internal_types:
        return context.internal_types[typename]
    result = RE_TYPE.match(typename)
    if result:
        values = result.groups()
        if values[0] == "UNSIGNED" and int(values[1]) in [i * 8 for i in range(1, 9)]:
            typeinfos = ("UNS%s" % values[1], None, "uint%s" % values[1], True)
        elif values[0] == "INTEGER" and int(values[1]) in [i * 8 for i in range(1, 9)]:
            typeinfos = ("INTEGER%s" % values[1], None, "int%s" % values[1], False)
        elif values[0] == "REAL" and int(values[1]) in (32, 64):
            typeinfos = ("%s%s" % (values[0], values[1]), None, "real%s" % values[1], False)
        elif values[0] in ["VISIBLE_STRING", "OCTET_STRING"]:
            size = context.default_string_size
            for item in items:
                size = max(size, len(item))
            if values[1]:
                size = max(size, int(values[1]))
            typeinfos = ("UNS8", size, "visible_string", False)
        elif values[0] == "DOMAIN":
            size = 0
            for item in items:
                size = max(size, len(item))
            typeinfos = ("UNS8", size, "domain", False)
        elif values[0] == "BOOLEAN":
            typeinfos = ("UNS8", None, "boolean", False)
        else:
            # FIXME: The !!! is for special UI handling
            raise ValueError("!!! '%s' isn't a valid type for CanFestival." % typename)
        if typeinfos[2] not in ["visible_string", "domain"]:
            context.internal_types[typename] = typeinfos
    else:
        # FIXME: The !!! is for special UI handling
        raise ValueError("!!! '%s' isn't a valid type for CanFestival." % typename)
    return typeinfos


def ComputeValue(type_, value):
    if type_ == "visible_string":
        return '"%s"' % value, ""
    if type_ == "domain":
        return '"%s"' % ''.join(["\\x%2.2x" % ord(char) for char in value]), ""
    if type_.startswith("real"):
        return "%f" % value, ""
    # value is integer; make sure to handle negative numbers correctly
    if value < 0:
        return "-0x%X" % (-value), "\t/* %s */" % str(value)
    return "0x%X" % value, "\t/* %s */" % str(value)


def GetTypeName(node, typenumber):
    typename = node.GetTypeName(typenumber)
    if typename is None:
        # FIXME: The !!! is for special UI handling
        raise ValueError("!!! Datatype with value '0x%4.4X' isn't defined in CanFestival." % typenumber)
    return typename


def GenerateFileContent(node, headerfilepath, pointers_dict=None):
    """
    pointers_dict = {(Idx,Sidx):"VariableName",...}
    """

    # FIXME: Too many camelCase vars in here
    # pylint: disable=invalid-name

    context = CFileContext()
    pointers_dict = pointers_dict or {}
    texts = {}
    texts["maxPDOtransmit"] = 0
    texts["NodeName"] = node.Name
    texts["NodeID"] = node.ID
    texts["NodeType"] = node.Type
    texts["Description"] = node.Description or ""
    texts["iam_a_slave"] = 0
    if texts["NodeType"] == "slave":
        texts["iam_a_slave"] = 1

    context.default_string_size = node.DefaultStringSize

    # Compiling lists of indexes
    rangelist = [idx for idx in node.GetIndexes() if 0 <= idx <= 0x260]
    listindex = [idx for idx in node.GetIndexes() if 0x1000 <= idx <= 0xFFFF]
    communicationlist = [idx for idx in node.GetIndexes() if 0x1000 <= idx <= 0x11FF]
    # sdolist = [idx for idx in node.GetIndexes() if 0x1200 <= idx <= 0x12FF]
    # pdolist = [idx for idx in node.GetIndexes() if 0x1400 <= idx <= 0x1BFF]
    variablelist = [idx for idx in node.GetIndexes() if 0x2000 <= idx <= 0xBFFF]

# ------------------------------------------------------------------------------
#                       Declaration of the value range types
# ------------------------------------------------------------------------------

    valueRangeContent = ""
    strDefine = "\n#define valueRange_EMC 0x9F /* Type for index 0x1003 subindex 0x00 (only set of value 0 is possible) */"
    strSwitch = """    case valueRange_EMC:
      if (*(UNS8*)value != (UNS8)0) return OD_VALUE_RANGE_EXCEEDED;
      break;\n"""
    context.internal_types["valueRange_EMC"] = ("UNS8", "", "valueRange_EMC", True)
    num = 0
    for index in rangelist:
        rangename = node.GetEntryName(index)
        result = RE_RANGE.match(rangename)
        if result:
            num += 1
            typeindex = node.GetEntry(index, 1)
            typename = node.GetTypeName(typeindex)
            typeinfos = GetValidTypeInfos(context, typename)
            context.internal_types[rangename] = (typeinfos[0], typeinfos[1], "valueRange_%d" % num)
            minvalue = node.GetEntry(index, 2)
            maxvalue = node.GetEntry(index, 3)
            strDefine += "\n#define valueRange_%d 0x%02X /* Type %s, %s < value < %s */" % (num, index, typeinfos[0], str(minvalue), str(maxvalue))
            strSwitch += "    case valueRange_%d:\n" % (num)
            if typeinfos[3] and minvalue <= 0:
                strSwitch += "      /* Negative or null low limit ignored because of unsigned type */;\n"
            else:
                strSwitch += "      if (*(%s*)value < (%s)%s) return OD_VALUE_TOO_LOW;\n" % (typeinfos[0], typeinfos[0], str(minvalue))
            strSwitch += "      if (*(%s*)value > (%s)%s) return OD_VALUE_TOO_HIGH;\n" % (typeinfos[0], typeinfos[0], str(maxvalue))
            strSwitch += "    break;\n"

    valueRangeContent += strDefine
    valueRangeContent += "\nUNS32 %(NodeName)s_valueRangeTest (UNS8 typeValue, void * value)\n{" % texts
    valueRangeContent += "\n  switch (typeValue) {\n"
    valueRangeContent += strSwitch
    valueRangeContent += "  }\n  return 0;\n}\n"

# ------------------------------------------------------------------------------
#            Creation of the mapped variables and object dictionary
# ------------------------------------------------------------------------------

    mappedVariableContent = ""
    pointedVariableContent = ""
    strDeclareHeader = ""
    indexContents = {}
    headerObjectDefinitionContent = ""
    for index in listindex:
        texts["index"] = index
        strindex = ""
        entry_infos = node.GetEntryInfos(index)
        params_infos = node.GetParamsEntry(index)
        texts["EntryName"] = entry_infos["name"]
        values = node.GetEntry(index)
        if index in variablelist:
            strindex += "\n/* index 0x%(index)04X :   Mapped variable %(EntryName)s */\n" % texts
        else:
            strindex += "\n/* index 0x%(index)04X :   %(EntryName)s. */\n" % texts

        # Entry type is VAR
        if not isinstance(values, list):
            subentry_infos = node.GetSubentryInfos(index, 0)
            typename = GetTypeName(node, subentry_infos["type"])
            typeinfos = GetValidTypeInfos(context, typename, [values])
            if typename == "DOMAIN" and index in variablelist:
                if not typeinfos[1]:
                    raise ValueError("Domain variable not initialized, index: 0x%04X, subindex: 0x00" % index)
            texts["subIndexType"] = typeinfos[0]
            if typeinfos[1] is not None:
                if params_infos["buffer_size"]:
                    texts["suffixe"] = "[%s]" % params_infos["buffer_size"]
                else:
                    texts["suffixe"] = "[%d]" % typeinfos[1]
            else:
                texts["suffixe"] = ""
            texts["value"], texts["comment"] = ComputeValue(typeinfos[2], values)
            if index in variablelist:
                texts["name"] = RE_STARTS_WITH_DIGIT.sub(r'_\1', FormatName(subentry_infos["name"]))
                strDeclareHeader += "extern %(subIndexType)s %(name)s%(suffixe)s;\t\t/* Mapped at index 0x%(index)04X, subindex 0x00*/\n" % texts
                mappedVariableContent += "%(subIndexType)s %(name)s%(suffixe)s = %(value)s;\t\t/* Mapped at index 0x%(index)04X, subindex 0x00 */\n" % texts
            else:
                strindex += "                    %(subIndexType)s %(NodeName)s_obj%(index)04X%(suffixe)s = %(value)s;%(comment)s\n" % texts
            values = [values]
        else:
            subentry_infos = node.GetSubentryInfos(index, 0)
            typename = GetTypeName(node, subentry_infos["type"])
            typeinfos = GetValidTypeInfos(context, typename)
            if index == 0x1003:
                texts["value"] = 0
            else:
                texts["value"] = values[0]
            texts["subIndexType"] = typeinfos[0]
            strindex += "                    %(subIndexType)s %(NodeName)s_highestSubIndex_obj%(index)04X = %(value)d; /* number of subindex - 1*/\n" % texts

            # Entry type is ARRAY
            if entry_infos["struct"] & OD.IdenticalSubindexes:
                subentry_infos = node.GetSubentryInfos(index, 1)
                typename = node.GetTypeName(subentry_infos["type"])
                typeinfos = GetValidTypeInfos(context, typename, values[1:])
                texts["subIndexType"] = typeinfos[0]
                if typeinfos[1] is not None:
                    texts["suffixe"] = "[%d]" % typeinfos[1]
                    texts["type_suffixe"] = "*"
                else:
                    texts["suffixe"] = ""
                    texts["type_suffixe"] = ""
                texts["length"] = values[0]
                if index in variablelist:
                    texts["name"] = RE_STARTS_WITH_DIGIT.sub(r'_\1', FormatName(entry_infos["name"]))
                    texts["values_count"] = str(len(values) - 1)
                    strDeclareHeader += "extern %(subIndexType)s %(name)s[%(values_count)s]%(suffixe)s;\t\t/* Mapped at index 0x%(index)04X, subindex 0x01 - 0x%(length)02X */\n" % texts
                    mappedVariableContent += "%(subIndexType)s %(name)s[]%(suffixe)s =\t\t/* Mapped at index 0x%(index)04X, subindex 0x01 - 0x%(length)02X */\n  {\n" % texts
                    for subindex, value in enumerate(values):
                        sep = ","
                        if subindex > 0:
                            if subindex == len(values) - 1:
                                sep = ""
                            value, comment = ComputeValue(typeinfos[2], value)
                            if len(value) == 2 and typename == "DOMAIN":
                                raise ValueError("Domain variable not initialized, index : 0x%04X, subindex : 0x%02X" % (index, subindex))
                            mappedVariableContent += "    %s%s%s\n" % (value, sep, comment)
                    mappedVariableContent += "  };\n"
                else:
                    strindex += "                    %(subIndexType)s%(type_suffixe)s %(NodeName)s_obj%(index)04X[] = \n                    {\n" % texts
                    for subindex, value in enumerate(values):
                        sep = ","
                        if subindex > 0:
                            if subindex == len(values) - 1:
                                sep = ""
                            value, comment = ComputeValue(typeinfos[2], value)
                            strindex += "                      %s%s%s\n" % (value, sep, comment)
                    strindex += "                    };\n"
            else:

                texts["parent"] = RE_STARTS_WITH_DIGIT.sub(r'_\1', FormatName(entry_infos["name"]))
                # Entry type is RECORD
                for subindex, value in enumerate(values):
                    texts["subindex"] = subindex
                    params_infos = node.GetParamsEntry(index, subindex)
                    if subindex > 0:
                        subentry_infos = node.GetSubentryInfos(index, subindex)
                        typename = GetTypeName(node, subentry_infos["type"])
                        typeinfos = GetValidTypeInfos(context, typename, [values[subindex]])
                        texts["subIndexType"] = typeinfos[0]
                        if typeinfos[1] is not None:
                            if params_infos["buffer_size"]:
                                texts["suffixe"] = "[%s]" % params_infos["buffer_size"]
                            else:
                                texts["suffixe"] = "[%d]" % typeinfos[1]
                        else:
                            texts["suffixe"] = ""
                        texts["value"], texts["comment"] = ComputeValue(typeinfos[2], value)
                        texts["name"] = FormatName(subentry_infos["name"])
                        if index in variablelist:
                            strDeclareHeader += "extern %(subIndexType)s %(parent)s_%(name)s%(suffixe)s;\t\t/* Mapped at index 0x%(index)04X, subindex 0x%(subindex)02X */\n" % texts
                            mappedVariableContent += "%(subIndexType)s %(parent)s_%(name)s%(suffixe)s = %(value)s;\t\t/* Mapped at index 0x%(index)04X, subindex 0x%(subindex)02X */\n" % texts
                        else:
                            strindex += "                    %(subIndexType)s %(NodeName)s_obj%(index)04X_%(name)s%(suffixe)s = %(value)s;%(comment)s\n" % texts
        headerObjectDefinitionContent += (
            "\n#define "
            + RE_NOTW.sub("_", texts["NodeName"])
            + "_"
            + RE_NOTW.sub("_", texts["EntryName"])
            + "_Idx "
            + str(format(texts["index"], "#04x"))
            + "\n")

        # Generating Dictionary C++ entry
        strindex += "                    subindex %(NodeName)s_Index%(index)04X[] = \n                     {\n" % texts
        generateSubIndexArrayComment = True
        for subindex, _ in enumerate(values):
            subentry_infos = node.GetSubentryInfos(index, subindex)
            params_infos = node.GetParamsEntry(index, subindex)
            if subindex < len(values) - 1:
                sep = ","
            else:
                sep = ""
            typename = node.GetTypeName(subentry_infos["type"])
            if entry_infos["struct"] & OD.IdenticalSubindexes:
                typeinfos = GetValidTypeInfos(context, typename, values[1:])
            else:
                typeinfos = GetValidTypeInfos(context, typename, [values[subindex]])
            if subindex == 0:
                if index == 0x1003:
                    typeinfos = GetValidTypeInfos(context, "valueRange_EMC")
                if entry_infos["struct"] & OD.MultipleSubindexes:
                    name = "%(NodeName)s_highestSubIndex_obj%(index)04X" % texts
                elif index in variablelist:
                    name = FormatName(subentry_infos["name"])
                else:
                    name = FormatName("%s_obj%04X" % (texts["NodeName"], texts["index"]))
            elif entry_infos["struct"] & OD.IdenticalSubindexes:
                if index in variablelist:
                    name = "%s[%d]" % (FormatName(entry_infos["name"]), subindex - 1)
                else:
                    name = "%s_obj%04X[%d]" % (texts["NodeName"], texts["index"], subindex - 1)
            else:
                if index in variablelist:
                    name = FormatName("%s_%s" % (entry_infos["name"], subentry_infos["name"]))
                else:
                    name = "%s_obj%04X_%s" % (texts["NodeName"], texts["index"], FormatName(subentry_infos["name"]))
            if typeinfos[2] == "visible_string":
                if params_infos["buffer_size"]:
                    sizeof = params_infos["buffer_size"]
                else:
                    sizeof = str(max(len(values[subindex]), context.default_string_size))
            elif typeinfos[2] == "domain":
                sizeof = str(len(values[subindex]))
            else:
                sizeof = "sizeof (%s)" % typeinfos[0]
            params = node.GetParamsEntry(index, subindex)
            if params["save"]:
                save = "|TO_BE_SAVE"
            else:
                save = ""
            strindex += "                       { %s%s, %s, %s, (void*)&%s, NULL }%s\n" % (subentry_infos["access"].upper(), save, typeinfos[2], sizeof, RE_STARTS_WITH_DIGIT.sub(r'_\1', name), sep)
            pointer_name = pointers_dict.get((index, subindex), None)
            if pointer_name is not None:
                pointedVariableContent += "%s* %s = &%s;\n" % (typeinfos[0], pointer_name, name)
            if not entry_infos["struct"] & OD.IdenticalSubindexes:
                generateSubIndexArrayComment = True
                headerObjectDefinitionContent += (
                    "#define "
                    + RE_NOTW.sub("_", texts["NodeName"])
                    + "_"
                    + RE_NOTW.sub("_", texts["EntryName"])
                    + "_"
                    + RE_NOTW.sub("_", subentry_infos["name"])
                    + "_sIdx "
                    + str(format(subindex, "#04x")))
                if params_infos["comment"]:
                    headerObjectDefinitionContent += "    /* " + params_infos["comment"] + " */\n"
                else:
                    headerObjectDefinitionContent += "\n"
            elif generateSubIndexArrayComment:
                generateSubIndexArrayComment = False
                # Generate Number_of_Entries_sIdx define and write comment about not generating defines for the rest of the array objects
                headerObjectDefinitionContent += (
                    "#define "
                    + RE_NOTW.sub("_", texts["NodeName"])
                    + "_"
                    + RE_NOTW.sub("_", texts["EntryName"])
                    + "_"
                    + RE_NOTW.sub("_", subentry_infos["name"])
                    + "_sIdx " + str(format(subindex, "#04x"))
                    + "\n")
                headerObjectDefinitionContent += "/* subindex define not generated for array objects */\n"
        strindex += "                     };\n"
        indexContents[index] = strindex

# ------------------------------------------------------------------------------
#                     Declaration of Particular Parameters
# ------------------------------------------------------------------------------

    if 0x1003 not in communicationlist:
        entry_infos = node.GetEntryInfos(0x1003)
        texts["EntryName"] = entry_infos["name"]
        indexContents[0x1003] = """\n/* index 0x1003 :   %(EntryName)s */
                    UNS8 %(NodeName)s_highestSubIndex_obj1003 = 0; /* number of subindex - 1*/
                    UNS32 %(NodeName)s_obj1003[] =
                    {
                      0x0	/* 0 */
                    };
                    subindex %(NodeName)s_Index1003[] =
                     {
                       { RW, valueRange_EMC, sizeof (UNS8), (void*)&%(NodeName)s_highestSubIndex_obj1003, NULL },
                       { RO, uint32, sizeof (UNS32), (void*)&%(NodeName)s_obj1003[0], NULL }
                     };
""" % texts

    if 0x1005 not in communicationlist:
        entry_infos = node.GetEntryInfos(0x1005)
        texts["EntryName"] = entry_infos["name"]
        indexContents[0x1005] = """\n/* index 0x1005 :   %(EntryName)s */
                    UNS32 %(NodeName)s_obj1005 = 0x0;   /* 0 */
""" % texts

    if 0x1006 not in communicationlist:
        entry_infos = node.GetEntryInfos(0x1006)
        texts["EntryName"] = entry_infos["name"]
        indexContents[0x1006] = """\n/* index 0x1006 :   %(EntryName)s */
                    UNS32 %(NodeName)s_obj1006 = 0x0;   /* 0 */
""" % texts

    if 0x1014 not in communicationlist:
        entry_infos = node.GetEntryInfos(0x1014)
        texts["EntryName"] = entry_infos["name"]
        indexContents[0x1014] = """\n/* index 0x1014 :   %(EntryName)s */
                    UNS32 %(NodeName)s_obj1014 = 0x80 + 0x%(NodeID)02X;   /* 128 + NodeID */
""" % texts

    if 0x1016 in communicationlist:
        texts["heartBeatTimers_number"] = node.GetEntry(0x1016, 0)
    else:
        texts["heartBeatTimers_number"] = 0
        entry_infos = node.GetEntryInfos(0x1016)
        texts["EntryName"] = entry_infos["name"]
        indexContents[0x1016] = """\n/* index 0x1016 :   %(EntryName)s */
                    UNS8 %(NodeName)s_highestSubIndex_obj1016 = 0;
                    UNS32 %(NodeName)s_obj1016[]={0};
""" % texts

    if 0x1017 not in communicationlist:
        entry_infos = node.GetEntryInfos(0x1017)
        texts["EntryName"] = entry_infos["name"]
        indexContents[0x1017] = """\n/* index 0x1017 :   %(EntryName)s */
                    UNS16 %(NodeName)s_obj1017 = 0x0;   /* 0 */
""" % texts

    if 0x100C not in communicationlist:
        entry_infos = node.GetEntryInfos(0x100C)
        texts["EntryName"] = entry_infos["name"]
        indexContents[0x100C] = """\n/* index 0x100C :   %(EntryName)s */
                    UNS16 %(NodeName)s_obj100C = 0x0;   /* 0 */
""" % texts

    if 0x100D not in communicationlist:
        entry_infos = node.GetEntryInfos(0x100D)
        texts["EntryName"] = entry_infos["name"]
        indexContents[0x100D] = """\n/* index 0x100D :   %(EntryName)s */
                    UNS8 %(NodeName)s_obj100D = 0x0;   /* 0 */
""" % texts

# ------------------------------------------------------------------------------
#               Declaration of navigation in the Object Dictionary
# ------------------------------------------------------------------------------

    strDeclareIndex = ""
    strDeclareSwitch = ""
    strQuickIndex = ""
    quick_index = {}
    for index_cat in INDEX_CATEGORIES:
        quick_index[index_cat] = {}
        for cat, idx_min, idx_max in CATEGORIES:
            quick_index[index_cat][cat] = 0
    maxPDOtransmit = 0
    for i, index in enumerate(listindex):
        texts["index"] = index
        strDeclareIndex += "  { (subindex*)%(NodeName)s_Index%(index)04X,sizeof(%(NodeName)s_Index%(index)04X)/sizeof(%(NodeName)s_Index%(index)04X[0]), 0x%(index)04X},\n" % texts
        strDeclareSwitch += "       case 0x%04X: i = %d;break;\n" % (index, i)
        for cat, idx_min, idx_max in CATEGORIES:
            if idx_min <= index <= idx_max:
                quick_index["lastIndex"][cat] = i
                if quick_index["firstIndex"][cat] == 0:
                    quick_index["firstIndex"][cat] = i
                if cat == "PDO_TRS":
                    maxPDOtransmit += 1
    texts["maxPDOtransmit"] = max(1, maxPDOtransmit)
    for index_cat in INDEX_CATEGORIES:
        strQuickIndex += "\nconst quick_index %s_%s = {\n" % (texts["NodeName"], index_cat)
        sep = ","
        for i, (cat, idx_min, idx_max) in enumerate(CATEGORIES):
            if i == len(CATEGORIES) - 1:
                sep = ""
            strQuickIndex += "  %d%s /* %s */\n" % (quick_index[index_cat][cat], sep, cat)
        strQuickIndex += "};\n"

# ------------------------------------------------------------------------------
#                            Write File Content
# ------------------------------------------------------------------------------

    fileContent = FILE_HEADER + """
#include "%s"
""" % (headerfilepath)

    fileContent += """
/**************************************************************************/
/* Declaration of mapped variables                                        */
/**************************************************************************/
""" + mappedVariableContent

    fileContent += """
/**************************************************************************/
/* Declaration of value range types                                       */
/**************************************************************************/
""" + valueRangeContent

    fileContent += """
/**************************************************************************/
/* The node id                                                            */
/**************************************************************************/
/* node_id default value.*/
UNS8 %(NodeName)s_bDeviceNodeId = 0x%(NodeID)02X;

/**************************************************************************/
/* Array of message processing information */

const UNS8 %(NodeName)s_iam_a_slave = %(iam_a_slave)d;

""" % texts
    if texts["heartBeatTimers_number"] > 0:
        declaration = "TIMER_HANDLE %(NodeName)s_heartBeatTimers[%(heartBeatTimers_number)d]" % texts
        initializer = "{TIMER_NONE" + ",TIMER_NONE" * (texts["heartBeatTimers_number"] - 1) + "}"
        fileContent += declaration + " = " + initializer + ";\n"
    else:
        fileContent += "TIMER_HANDLE %(NodeName)s_heartBeatTimers[1];\n" % texts

    fileContent += """
/*
$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$

                               OBJECT DICTIONARY

$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
*/
"""
    for index in sorted(indexContents):
        fileContent += indexContents[index]

    fileContent += """
/**************************************************************************/
/* Declaration of pointed variables                                       */
/**************************************************************************/
""" + pointedVariableContent

    fileContent += """
const indextable %(NodeName)s_objdict[] =
{
""" % texts
    fileContent += strDeclareIndex
    fileContent += """};

const indextable * %(NodeName)s_scanIndexOD (CO_Data *d, UNS16 wIndex, UNS32 * errorCode)
{
    int i;
    (void)d; /* unused parameter */
    switch(wIndex){
""" % texts
    fileContent += strDeclareSwitch
    fileContent += """       default:
            *errorCode = OD_NO_SUCH_OBJECT;
            return NULL;
    }
    *errorCode = OD_SUCCESSFUL;
    return &%(NodeName)s_objdict[i];
}

/*
 * To count at which received SYNC a PDO must be sent.
 * Even if no pdoTransmit are defined, at least one entry is computed
 * for compilations issues.
 */
s_PDO_status %(NodeName)s_PDO_status[%(maxPDOtransmit)d] = {""" % texts

    fileContent += ",".join(["s_PDO_status_Initializer"] * texts["maxPDOtransmit"]) + """};
"""

    fileContent += strQuickIndex
    fileContent += """
const UNS16 %(NodeName)s_ObjdictSize = sizeof(%(NodeName)s_objdict)/sizeof(%(NodeName)s_objdict[0]);

CO_Data %(NodeName)s_Data = CANOPEN_NODE_DATA_INITIALIZER(%(NodeName)s);

""" % texts

# ------------------------------------------------------------------------------
#                          Write Header File Content
# ------------------------------------------------------------------------------

    texts["file_include_name"] = headerfilepath.replace(".", "_").upper()
    headerFileContent = FILE_HEADER + """
#ifndef %(file_include_name)s
#define %(file_include_name)s

#include "data.h"

/* Prototypes of function provided by object dictionnary */
UNS32 %(NodeName)s_valueRangeTest (UNS8 typeValue, void * value);
const indextable * %(NodeName)s_scanIndexOD (CO_Data *d, UNS16 wIndex, UNS32 * errorCode);

/* Master node data struct */
extern CO_Data %(NodeName)s_Data;
""" % texts
    headerFileContent += strDeclareHeader

    headerFileContent += "\n#endif // %(file_include_name)s\n" % texts

# ------------------------------------------------------------------------------
#                          Write Header Object Defintions Content
# ------------------------------------------------------------------------------
    texts["file_include_objdef_name"] = headerfilepath.replace(".", "_OBJECTDEFINES_").upper()
    headerObjectDefinitionContent = FILE_HEADER + """
#ifndef %(file_include_objdef_name)s
#define %(file_include_objdef_name)s

/*
    Object defines naming convention:
    General:
        * All characters in object names that does not match [a-zA-Z0-9_] will be replaced by '_'.
        * Case of object dictionary names will be kept as is.
    Index : Node object dictionary name +_+ index name +_+ Idx
    SubIndex : Node object dictionary name +_+ index name +_+ subIndex name +_+ sIdx
*/
""" % texts + headerObjectDefinitionContent + """
#endif /* %(file_include_objdef_name)s */
""" % texts

    return fileContent, headerFileContent, headerObjectDefinitionContent


# ------------------------------------------------------------------------------
#                             Main Function
# ------------------------------------------------------------------------------

def GenerateFile(filepath, node, pointers_dict=None):
    pointers_dict = pointers_dict or {}
    filebase = os.path.splitext(filepath)[0]
    headerfilepath = filebase + ".h"
    content, header, header_defs = GenerateFileContent(node, os.path.basename(headerfilepath), pointers_dict)

    # Write main .c contents
    with open(filepath, "wb") as f:
        f.write(content.encode('utf-8'))

    # Write header file
    with open(headerfilepath, "wb") as f:
        f.write(header.encode('utf-8'))

    # Write object definitions header
    with open(filebase + "_objectdefines.h", "wb") as f:
        f.write(header_defs.encode('utf-8'))
