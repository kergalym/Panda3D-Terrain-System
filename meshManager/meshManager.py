from panda3d.core import NodePath, Geom, GeomNode, GeomVertexWriter, GeomVertexData, GeomVertexFormat
import math

import toroidalCache

"""
This module provides a MeshManager class and its assoiated classes.

Together this creates a system for paging and LODing meshes that streams them into
minimal ammounts of Geom and NodePaths. This allows for properly chunked, paged and LODed
meshes, in a finifed system that does not create any temparart/intermeadary meshes or data,
requires no flattening works with fully procedural meshes.

Support for static meshes should be straight forward, just impliment a MeshFactory
that caches the vertex data
and geom data in a optimized manner for writing out.

for performance, most or all of this, including the factories should be ported to C++
The main performance bottleneck should be the auctual ganeration and writing on meshes
into the geoms. This is greatly slowed by both python, and the very high ammount of calls across
Panda3D's python wrapper.

However, even in pure python, reasonable performance can be achieved with this system!
"""


class LODLevel(toroidalCache.ToroidalCache):
    def __init__(self,meshManager,LOD,blockSize,blockCount):
        self.meshManager=meshManager
        self.blockSize=blockSize
        self.LOD=LOD
        #self.blockCount=blockCount
        
        
        self.geomRequirementsCollection=None
        
        
        toroidalCache.ToroidalCache.__init__(self,blockCount,hysteresis=.6)
    
    def addBlock(self,x,y,x2,y2):
        if self.geomRequirementsCollection is None:
            self.geomRequirementsCollection=GeomRequirementsCollection()
            for c in self.meshManager.factories:
                c.regesterGeomRequirements(self.LOD,self.geomRequirementsCollection)
        
        drawResourcesFactory=self.geomRequirementsCollection.getDrawResourcesFactory()
        if drawResourcesFactory is None: return None
        
        for c in self.meshManager.factories:
            c.draw(self.LOD,x,y,x2,y2,drawResourcesFactory)
        
        nodePath=drawResourcesFactory.getNodePath()
        
        if nodePath is None: return
        block=_MeshBlock(self.LOD,x,y,x2,y2)
        nodePath.reparentTo(self.meshManager)
        nodePath.setPythonTag("_MeshBlock",block)
        return nodePath

    
    def replaceValue(self,x,y,old):
        if old is not None:
            old.removeNode()
            #old.setPythonTag("_MeshBlock",None)
        s=self.blockSize
        return self.addBlock(x*s,y*s,(x+1)*s,(y+1)*s)
    def update(self,pos):
        p=pos*(1.0/self.blockSize)
        self.updateCenter(p.getX(),p.getY())

    
class MeshManager(NodePath):
    """
    A NodePath that will fill it self with meshes, with proper blocking and LOD
    
    meshes come from passed in factories
    """
    def __init__(self,factories):
        self.factories=factories
        NodePath.__init__(self,"MeshManager")
        self.LODLevels=[LODLevel(self,1,20*.0002,7)]
    
    def update(self,focuseNode):
        pos=focuseNode.getPos(self)
        for l in self.LODLevels:
            l.update(pos)
        

    
class _MeshBlock(object):
    """
    for storing info about mesh block node paths.
    """
    def __init__(self,LOD,x,y,x2,y2):
        self.LOD=LOD
        self.x=x
        self.y=y
        self.x2=x2
        self.y2=y2
        self.center=NodePath("Center")
        self.center.setPos((x+x2)/2.0,(y+y2)/2.0,0)
        self.maxR=math.sqrt((x-x2)**2+(y-y2)**2)/2
    

class MeshFactory(object):
    def regesterGeomRequirements(self,LOD,collection):
        """
        collection is a GeomRequirementsCollection
        
        example:
        self.trunkData=collection.add(GeomRequirements(...))
        """
        pass
    
    def getLodThresholds(self):
        # perhaps this should also have some approximate cost stats for efficent graceful degradation
        return [] # list of values at which rendering changes somewhat
    
    def draw(self,LOD,x,y,x1,y1,drawResourcesFactory):
        pass # gets called with all entries in getGeomRequirements(LOD)
    
    
# gonna need to add more fields to this, such as texture modes, multitexture, clamp, tile, mipmap etc.
# perhaps even include some scale bounds for textures to allow them to be scaled down when palatizing
class GeomRequirements(object):
    """
    a set of requirements for one part of mesh.
    this will get translated to a single geom, or a nodePath as needed,
    and merged with matching requirements
    """
    def __init__(self,geomVertexFormat,texture=None,transparency=False,shaderSettings=None):
        self.geomVertexFormat=geomVertexFormat
        self.texture=texture
        self.transparency=transparency
        self.shaderSettings=[] if shaderSettings is None else shaderSettings 
    def __eq__(self,other):
         return False # TODO

    
class DrawResources(object):
    """
    this provides the needed objects for outputting meshes.
    the resources provided match the corosponding GeomRequirements this was constructed with
    """
    def __init__(self,geomNode,geomRequirements):
        vdata = GeomVertexData("verts", geomRequirements.geomVertexFormat, Geom.UHStatic) 
        geomNode.addGeom(Geom(vdata))
        self.geom=geomNode.modifyGeom(0)
                
        self.vertexWriter = GeomVertexWriter(vdata, "vertex") 
        self.normalWriter = GeomVertexWriter(vdata, "normal") 
        self.texcoordWriter = GeomVertexWriter(vdata, "texcoord")
        

class _DrawNodeSpec(object):
    """
    spec for what properties are needed on the
    NodePath assoiated with a DrawResources/GeomRequirements
    """
    def __init__(self,parentIndex,texture=None):
        # parentIndex of -1 == root
        self.texture=texture
        self.parentIndex=parentIndex


class GeomRequirementsCollection(object):
    """
    a collection of unique GeomRequirements objects.
    
    identical entries are merged
    """
    def __init__(self):
        self.entries=[]
        self.drawNodeSpecs=None
        self.entryTodrawNodeSpec=None # entries[i]'s drawNode is entryTodrawNodeSpec[i]

    def add(self,entry):
        """
        entry should be a GeomRequirements
        returns index added at, used to get DrawResources from result of getDrawResourcesFactory
        """
        for i,e in enumerate(self.entries):
            if e==entry: return i
        self.entries.append(entry)
        self.drawNodeSpecs=None
        return len(self.entries)-1

    def getDrawResourcesFactory(self):
        if len(self.entries) == 0: return None
        if self.drawNodeSpecs is None:
            
            # this is a temp basic non optimal drawNodeSpecs setup
            # TODO : analize requirements on nodes and design hierarchy to minimize state transitions
            self.drawNodeSpecs=[_DrawNodeSpec(-1)]
            for e in self.entries:
                self.drawNodeSpecs.append(_DrawNodeSpec(0,texture=e.texture))
           
            self.entryTodrawNodeSpec=range(1,len(self.entries)+1)
            
            
        return DrawResourcesFactory(self.entries,self.entryTodrawNodeSpec,self.drawNodeSpecs)


class DrawResourcesFactory(object):
    """
    produced by GeomRequirementsCollection
    
    provides DrawResources objects corresponding to a GeomRequirements
    indexed by return value from GeomRequirementsCollection.add
    """
    def __init__(self,requirements,entryTodrawNodeSpec,drawNodeSpecs):
        self.requirements=requirements
        self.entryTodrawNodeSpec=entryTodrawNodeSpec
        self.drawNodeSpecs=drawNodeSpecs
        self.nodePaths=[None]*len(self.drawNodeSpecs)
        self.resources=[None]*len(self.requirements)
        self.np=None

    def getNodePath(self):
        """
        returns None if nothing drawn, else returns a NodePath
        """
        return self.np

    def _getNodePath(self,nodeIndex):
        np=self.nodePaths[nodeIndex]
        if np is not None: return np
        
        s=self.drawNodeSpecs[nodeIndex]
        
        node=GeomNode("DrawResourcesFactoryGeomNode")
        if s.parentIndex==-1:
            np=NodePath(node)
            self.np=np
        else:
            np=self._getNodePath(s.parentIndex).attachNewNode(node)
        self.nodePaths[nodeIndex]=np
        
        # setup render atributes on np here:
        if s.texture is not None: np.setTexture(s.texture)
        
        return np
        
    def getDrawResources(self,index):
        """
        returns corresponding DrawResources instance
        """
    
        r=self.resources[index]
        if r is not None: return r
        
        nodeIndex=self.entryTodrawNodeSpec[index]
        node=self._getNodePath(nodeIndex).getNode(0)
        r=DrawResources(node,self.requirements[index])
        self.resources[index]=r
        
        return r