from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import shaders
from aow4mod.core import FormatError


def signature(semantics):
    data=bytearray(struct.pack('<II',len(semantics),8)+bytes(32*len(semantics)))
    for i,(name,index,system,ctype,mask,reads) in enumerate(semantics):
        offset=len(data);data.extend(name.encode()+b'\0');struct.pack_into('<6IBBHI',data,8+32*i,0,offset,index,system,ctype,i,mask,reads,0,0)
    return bytes(data)


def container(parts):
    size=32+4*len(parts);offsets=[];body=b''
    for kind,data in parts:offsets.append(size+len(body));body+=kind+struct.pack('<I',len(data))+data
    return b'DXBC'+bytes(16)+struct.pack('<III',1,size+len(body),len(parts))+struct.pack('<'+'I'*len(parts),*offsets)+body


def program(semantics,kind=1):
    bytecode=struct.pack('<6I',kind<<16|0x60,7,0x4c495844,0x100,16,4)+b'BC\xc0\xde'
    return container([(b'ISG1',signature(semantics)),(b'DXIL',bytecode)])


class ShaderInspectionTests(unittest.TestCase):
    def test_shader_pool_is_exactly_partitioned_and_stage_model_decoded(self):
        blob=program([('POSITION',0,0,3,7,7)])+program([],0)
        rows=shaders.decode(blob);self.assertEqual(len(rows),2);self.assertEqual(rows[0]['shader_kind'],1)
        self.assertEqual(rows[0]['shader_model'],'6.0');self.assertEqual(rows[1]['offset'],rows[0]['bytes'])
        with self.assertRaisesRegex(FormatError,'non-container'):shaders.decode(blob+b'junk')

    def test_profile_checks_read_components_and_system_generated_ids(self):
        blob=program([('POSITION',0,0,3,7,7),('COLOR',0,0,3,15,15),('TANGENT',0,0,3,15,15),('SV_VertexID',0,6,1,1,1)])
        inputs=shaders.decode(blob)[0]['inputs'];self.assertTrue(shaders.profile_check(inputs,'pnuvct52')['requirements_covered'])
        self.assertEqual(len(shaders.profile_check(inputs,'pnuv32')['issues']),2)
        inputs[1]['reads']=0;self.assertEqual(len(shaders.profile_check(inputs,'pnuv32')['issues']),1)

    def test_extra_uv_bones_wrong_types_and_mask_components_are_not_hidden(self):
        for item in [('TEXCOORD',1,0,3,3,3),('BLENDINDICES',0,0,1,15,15),('NORMAL',0,0,3,15,15),('SV_Unexpected',0,0,3,1,1)]:
            with self.subTest(item=item):
                inputs=shaders.decode(program([item]))[0]['inputs'];self.assertFalse(shaders.profile_check(inputs,'pnuvct52')['requirements_covered'])

    def test_part_overlap_duplicate_and_header_offsets_are_refused(self):
        valid=program([])
        cases=[valid[:20],valid[:-1],container([(b'ISG1',signature([])),(b'ISG1',signature([]))])]
        for value in (0,34,0xffffffff):
            bad=bytearray(valid);struct.pack_into('<I',bad,32,value);cases.append(bytes(bad))
        for bad in cases:
            with self.subTest(size=len(bad)),self.assertRaises(FormatError):shaders.decode(bad)

    def test_signature_names_padding_duplicate_and_read_masks_are_bounded(self):
        good=signature([('POSITION',0,0,3,7,7)])
        for offset,value,codec in [(12,8,'<I'),(32,0,'<B'),(33,15,'<B'),(34,1,'<H')]:
            bad=bytearray(good);struct.pack_into(codec,bad,offset,value)
            with self.subTest(offset=offset),self.assertRaises(FormatError):shaders.signature(bytes(bad))
        with self.assertRaisesRegex(FormatError,'Duplicate'):shaders.signature(signature([('POSITION',0,0,3,7,7)]*2))
        with self.assertRaisesRegex(FormatError,'unterminated'):shaders.signature(good[:-1])

    def test_program_bitcode_bounds_are_independently_checked(self):
        good=program([]);row=shaders.decode(good)[0];bad=bytearray(good)
        dxil_offset=struct.unpack_from('<I',good,36)[0]+8
        struct.pack_into('<I',bad,dxil_offset+20,0xffffffff)
        with self.assertRaisesRegex(FormatError,'bitcode bounds'):shaders.decode(bytes(bad))

    def test_zero_vertex_programs_are_explicitly_not_evaluated(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'pixel.bin';path.write_bytes(program([],0));result=shaders.inspect(path,'pnuvct52')
            self.assertTrue(result['profile_summary']['zero_vertices_not_evaluated']);self.assertFalse(result['runtime_variant_verified'])


if __name__=='__main__':unittest.main()
