import os
import maya.cmds as cmds
import maya.mel as mel
import math
import sys
import datetime
import os.path
import traceback
import maya.OpenMaya as OpenMaya
import maya.OpenMayaAnim as OpenMayaAnim
import urllib2
import socket
import subprocess
import webbrowser
import Queue
import _winreg as reg
import time
import struct
import shutil
import zipfile
import re
from subprocess import Popen, PIPE, STDOUT
from maya.plugin.timeSliderBookmark.timeSliderBookmark import frameAllBookmark
from maya.plugin.timeSliderBookmark.timeSliderBookmark import getAllBookmarks


def __math_matrixtoquat__(maya_matrix):
    """Converts a Maya matrix array to a quaternion"""
    quat_x, quat_y, quat_z, quat_w = (0, 0, 0, 1)

    trans_remain = maya_matrix[0] + maya_matrix[5] + maya_matrix[10]
    if trans_remain > 0:
        divisor = math.sqrt(trans_remain + 1.0) * 2.0
        quat_w = 0.25 * divisor
        quat_x = (maya_matrix[6] - maya_matrix[9]) / divisor
        quat_y = (maya_matrix[8] - maya_matrix[2]) / divisor
        quat_z = (maya_matrix[1] - maya_matrix[4]) / divisor
    elif (maya_matrix[0] > maya_matrix[5]) and (maya_matrix[0] > maya_matrix[10]):
        divisor = math.sqrt(
            1.0 + maya_matrix[0] - maya_matrix[5] - maya_matrix[10]) * 2.0
        quat_w = (maya_matrix[6] - maya_matrix[9]) / divisor
        quat_x = 0.25 * divisor
        quat_y = (maya_matrix[4] + maya_matrix[1]) / divisor
        quat_z = (maya_matrix[8] + maya_matrix[2]) / divisor
    elif maya_matrix[5] > maya_matrix[10]:
        divisor = math.sqrt(
            1.0 + maya_matrix[5] - maya_matrix[0] - maya_matrix[10]) * 2.0
        quat_w = (maya_matrix[8] - maya_matrix[2]) / divisor
        quat_x = (maya_matrix[4] + maya_matrix[1]) / divisor
        quat_y = 0.25 * divisor
        quat_z = (maya_matrix[9] + maya_matrix[6]) / divisor
    else:
        divisor = math.sqrt(
            1.0 + maya_matrix[10] - maya_matrix[0] - maya_matrix[5]) * 2.0
        quat_w = (maya_matrix[1] - maya_matrix[4]) / divisor
        quat_x = (maya_matrix[8] + maya_matrix[2]) / divisor
        quat_y = (maya_matrix[9] + maya_matrix[6]) / divisor
        quat_z = 0.25 * divisor

    # Return the result

    return OpenMaya.MQuaternion(quat_x, quat_y, quat_z, quat_w)



def GetJointList():
    joints = []

    # Get selected objects
    selectedObjects = OpenMaya.MSelectionList()
    OpenMaya.MGlobal.getActiveSelectionList(selectedObjects)


    for i in range(selectedObjects.length()):
        # Get object path and node
        dagPath = OpenMaya.MDagPath()
        selectedObjects.getDagPath(i, dagPath)
        dagNode = OpenMaya.MFnDagNode(dagPath)

       

        # Ignore nodes that aren't joints or arn't top-level
        if not dagPath.hasFn(OpenMaya.MFn.kJoint) or not RecursiveCheckIsTopNode(selectedObjects, dagNode):
            continue


        # Breadth first search of joint tree
        searchQueue = Queue.Queue(0)
        searchQueue.put((-1, dagNode, True)) # (index = child node's parent index, child node)
        while not searchQueue.empty():
            node = searchQueue.get()
            index = len(joints)


            if node[2]:
                joints.append((node[0], node[1]))
            else:
                index = node[0]


            for i in range(node[1].childCount()):
                dagPath = OpenMaya.MDagPath()
                childNode = OpenMaya.MFnDagNode(node[1].child(i))
                childNode.getPath(dagPath)
                searchQueue.put((index, childNode, selectedObjects.hasItem(dagPath) and dagPath.hasFn(OpenMaya.MFn.kJoint)))

    return joints


def RecursiveCheckIsTopNode(cSelectionList, currentNode): # Checks if the given node has ANY selected parent, grandparent, etc joints
    if currentNode.parentCount() == 0:
        return True

    for i in range(currentNode.parentCount()):
        parentDagPath = OpenMaya.MDagPath()
        parentNode = OpenMaya.MFnDagNode(currentNode.parent(i))
        parentNode.getPath(parentDagPath)

        if not parentDagPath.hasFn(OpenMaya.MFn.kJoint): # Not a joint, but still check parents
            if not RecursiveCheckIsTopNode(cSelectionList, parentNode):
                return False # A parent joint is selected, we're done
            else:
                continue # No parent joints are selected, ignore this node

        if cSelectionList.hasItem(parentDagPath):
            return False
        else:
            if not RecursiveCheckIsTopNode(cSelectionList, parentNode):
                return False

    return True

def GetJointData(jointC):
    jointNode = jointC[1]
    # Get the joint's transform
    path = OpenMaya.MDagPath() 
    jointNode.getPath(path)
    transform = OpenMaya.MFnTransform(path)
    

    # Get joint position
    pos = transform.getTranslation(OpenMaya.MSpace.kTransform)


    # Get scale (almost always 1)
    scaleUtil = OpenMaya.MScriptUtil()
    scaleUtil.createFromList([1,1,1], 3)
    scalePtr = scaleUtil.asDoublePtr()
    transform.getScale(scalePtr)
    scale = [OpenMaya.MScriptUtil.getDoubleArrayItem(scalePtr, 0), OpenMaya.MScriptUtil.getDoubleArrayItem(scalePtr, 1), OpenMaya.MScriptUtil.getDoubleArrayItem(scalePtr, 2)]


    # Get rotation matrix (mat is a 4x4, but the last row and column arn't needed)
    jointRotQuat = __math_matrixtoquat__(cmds.getAttr(path.fullPathName()+".matrix"))


    joint_offset = (pos.x*CM_TO_INCH * scale[0], pos.y*CM_TO_INCH * scale[1], pos.z*CM_TO_INCH * scale[2])


    return ( joint_offset, jointRotQuat )




def ExportSMDAnim(filePath, frameStart, frameEnd):
    currentunit_state = cmds.currentUnit(query=True, linear=True)
    currentangle_state = cmds.currentUnit(query=True, angle=True)
    cmds.autoKeyframe(state=False)
    cmds.currentUnit(linear="cm", angle="deg")

    numSelectedObjects = len(cmds.ls(selection=True))
    if numSelectedObjects == 0:
        return "Error: No objects selected for export"

    # Get data
    joints = GetJointList()
    if len(joints) == 0:
        return "Error: No joints selected for export"
    if len(joints) > 128:
        print "Warning: More than 128 joints have been selected. The animation might not compile."

	
    # Open file
    f = None
    try:
        # Create export directory if it doesn't exist
        directory = os.path.dirname(filePath)
        if not os.path.exists(directory):
            os.makedirs(directory)

        # Create file
        f = open(filePath, 'w')

    except (IOError, OSError) as e:
        typex, value, traceback = sys.exc_info()
        #print("SEE ME!"+str("Unable to create file:\n\n%s" % value.strerror))
        return "Unable to create file:\n\n%s" % value.strerror

    # Write header
    f.write("// Exported with Source Maya Tools\n")
    if cmds.file(query=True, exists=True):
        f.write("// Scene: '%s'\n" % os.path.normpath(os.path.abspath(cmds.file(query=True, sceneName=True))).encode('ascii', 'ignore')) # Ignore Ascii characters using .encode()
    else:
        f.write("// Scene: Unsaved\n\n")
    f.write("version 1\n")

    f.write("nodes\n")
    if len(joints) == 0:
        f.write("0 \"tag_origin\" -1\n")
    else:
        for i, joint in enumerate(joints):
            name = joint[1].partialPathName().split("|")
            name = name[len(name)-1].split(":") # Remove namespace prefixes
            name = name[len(name)-1].replace('_', '.', 1)
            f.write("%i \"%s\" %i\n" % (i, name, joint[0]))
    f.write("end\n")

    f.write("skeleton\n")
    cmds.currentTime(frameStart)
    jointsToSubstract = []
    for i, joint in enumerate(joints):
        jointsToSubstract.append(GetJointData(joint))

    for i in range(int(frameStart), int(frameEnd+1)):
        f.write("time %i\n" % (i - frameStart)) 
        cmds.currentTime(i)
        if len(joints) == 0:
            f.write("0 0 0 0 0 0 0\n")
        else:
            for j, joint in enumerate(joints):
                f.write("%i  " % (j))
                #if(substract == True):
                #    WriteJointDataSubstracted(f, joint, jointsToSubstract[j])
                #else:
                WriteJointData(f, joint)

    f.write("end\n")
    f.close()

    cmds.currentUnit(linear=currentunit_state, angle=currentangle_state)


"""
from maya.plugin.timeSliderBookmark.timeSliderBookmark import frameAllBookmark
from maya.plugin.timeSliderBookmark.timeSliderBookmark import getAllBookmarks
"""


def ExportBookmarksSMD():

    frameAllBookmark()
    bookmarks = getAllBookmarks()
    bookmark_names = []
    bookmark_frames = []

    for i in bookmarks:
        bookmark_names.append(cmds.getAttr(str(i)+".name"))
    for i in bookmarks:
        bookmark_frames.append([cmds.getAttr(str(i)+".timeRangeStart"), cmds.getAttr(str(i)+".timeRangeStop")])
    
    print bookmarks, bookmark_frames
    scenename = cmds.file(q=True, sn=True, shn=True)
    scenepath = cmds.file(q=True, sn=True).replace(scenename, '')
    
    # Save file as export.ma
    cmds.file(rename=(str(scenepath)+"export.ma"))
    cmds.file(save=True, type='mayaAscii')

    
    print("SUCKA"+scenename)
    for i, name in enumerate(bookmark_names):
        print(str(scenepath)+str(name)+".smd", bookmark_frames[i][0], bookmark_frames[i][1])
        ExportSMDAnim(str(scenepath)+"/_SMDexport/"+str(name)+".smd", bookmark_frames[i][0], bookmark_frames[i][1])
        
    cmds.confirmDialog(title="Success!", message="Exported Selected Joints!", icon="information")
    os.startfile(str(scenepath)+"/_SMDexport/")


if cmds.ls(sl=True) == []:
    cmds.confirmDialog(title="Notice!", message="Select Joints to Export!", icon="information")
else:
    ExportBookmarksSMD()
#ExportSMDAnim("C:/CS Source Modding/_Compile/hands/anims/hands_idle.smd", 1, 15)

