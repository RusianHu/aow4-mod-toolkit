from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import animation_sources,fbx
from aow4mod.core import FormatError


def prop(kind,value):
    if kind=='S':raw=value.encode();return b'S'+struct.pack('<I',len(raw))+raw
    if kind=='L':return b'L'+struct.pack('<q',value)
    fmt={'l':'q','f':'f','i':'i'}[kind];raw=struct.pack('<'+str(len(value))+fmt,*value)
    return kind.encode()+struct.pack('<III',len(value),0,len(raw))+raw


def fixture(times=(0,46186158000),stop=46186158000,extra=(),cycle=False,misaligned=False,attributes=False,values=None,interpolation=4,extra_connections=()):
    record=lambda kind,i,name,sub='':(kind,[prop('L',i),prop('S',name),prop('S',sub)],[])
    objects=[record('Model',1,'Bone','LimbNode'),record('AnimationLayer',2,'Layer'),
        ('AnimationStack',[prop('L',3),prop('S','Walk'),prop('S','')],[('Properties70',[],[
            ('P',[prop('S','LocalStop'),prop('S','KTime'),prop('S','Time'),prop('S',''),prop('L',stop)],[])])]),
        record('AnimationCurveNode',5,'Rotation'),
        ('AnimationCurve',[prop('L',4),prop('S','Curve'),prop('S','')],[
            ('KeyTime',[prop('l',times)],[]),('KeyValueFloat',[prop('f',values if values is not None else [0]*len(times))],[])])]
    curve=objects[-1]
    if attributes:
        curve[2].extend([('KeyAttrFlags',[prop('i',[interpolation])],[]),('KeyAttrDataFloat',[prop('f',[0]*4)],[]),('KeyAttrRefCount',[prop('i',[len(times)])],[])])
    for identity in (6,7):
        objects.append(('AnimationCurve',[prop('L',identity),prop('S','Curve'),prop('S','')],curve[2]))
    if misaligned:
        kind,props,children=objects[-1]
        objects[-1]=(kind,props,[('KeyTime',[prop('l',(0,stop//2,stop))],[]),('KeyValueFloat',[prop('f',(0,1,0))],[])])
    con=[('C',[prop('S','OO'),prop('L',2),prop('L',3)],[]),
        ('C',[prop('S','OP'),prop('L',5),prop('L',1),prop('S','Lcl Rotation')],[])]+[
        ('C',[prop('S','OP'),prop('L',identity),prop('L',5),prop('S',axis)],[]) for identity,axis in [(4,'d|X'),(6,'d|Y'),(7,'d|Z')]]
    for group_id,label,first_id in [(8,'Lcl Translation',10),(9,'Lcl Scaling',13)]:
        objects.append(record('AnimationCurveNode',group_id,label))
        con.append(('C',[prop('S','OP'),prop('L',group_id),prop('L',1),prop('S',label)],[]))
        for identity,axis in [(first_id,'d|X'),(first_id+1,'d|Y'),(first_id+2,'d|Z')]:
            objects.append(('AnimationCurve',[prop('L',identity),prop('S','Curve'),prop('S','')],curve[2]))
            con.append(('C',[prop('S','OP'),prop('L',identity),prop('L',group_id),prop('S',axis)],[]))
    objects+=list(extra)
    con+=list(extra_connections)
    if cycle:con.append(('C',[prop('S','OO'),prop('L',1),prop('L',1)],[]))
    def node(item,start):
        name,props,children=item;name=name.encode();payload=b''.join(props);pos=start+13+len(name)+len(payload);body=b''
        for child in children:
            raw=node(child,pos);body+=raw;pos+=len(raw)
        if children:body+=bytes(13);pos+=13
        return struct.pack('<III',pos,len(props),len(payload))+bytes([len(name)])+name+payload+body
    body=b'';pos=27
    for item in [('Objects',[],objects),('Connections',[],con)]:
        raw=node(item,pos);body+=raw;pos+=len(raw)
    return fbx.MAGIC+struct.pack('<I',7400)+body+bytes(13)


class AnimationSourceTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name).resolve()/'action.fbx'

    def read(self,raw):self.path.write_bytes(raw);return animation_sources.inspect(self.path)

    def test_animation_policy_is_separate_from_static_mesh_policy(self):
        result=self.read(fixture());self.assertEqual(result['seconds'],1);self.assertEqual(result['bone_names'],['Bone'])
        with self.assertRaises(FormatError):fbx.inspect(self.path)

    def test_unsorted_negative_keys_take_bounds_and_cycles_are_refused(self):
        for raw in (fixture(times=(1,0)),fixture(times=(-1,0)),fixture(stop=0),fixture(stop=121*46186158000),fixture(cycle=True)):
            with self.subTest(size=len(raw)),self.assertRaises(FormatError):self.read(raw)

    def test_media_extra_take_and_duplicate_names_are_refused(self):
        for kind,name,sub in [('Texture','Texture',''),('AnimationStack','Second',''),('Model','Bone','Null'),('Model','x'*32,'Null')]:
            extra=[(kind,[prop('L',20),prop('S',name),prop('S',sub)],[])]
            with self.subTest(kind=kind),self.assertRaises(FormatError):self.read(fixture(extra=extra))

    def test_truncated_binary_still_uses_shared_bounded_parser(self):
        for raw in (fixture()[:25],fixture()[:-20]):
            with self.assertRaises(FormatError):self.read(raw)

    def test_misaligned_xyz_keys_are_refused_before_the_native_importer(self):
        with self.assertRaisesRegex(FormatError,'XYZ transform key times must be aligned'):self.read(fixture(misaligned=True))


if __name__=='__main__':unittest.main()
