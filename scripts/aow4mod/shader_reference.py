"""Pinned DXC disassembly and a bounded scalar DXIL skin-position evaluator.

Game shader text/binaries are extracted from the user's installation, never
distributed by the Skill. No shader or GPU program is executed on the GPU.
"""
import functools
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import urllib.request
import zipfile

from . import clb_streams,content_libraries,effect_programs
from .core import FormatError,sha
from .filesystem import file_hash,new_directory,save_record

URL='https://github.com/microsoft/DirectXShaderCompiler/releases/download/v1.9.2609/dxc_2026_09_29.zip'
ARCHIVE_SHA='ad31b1fc8443175d204f77a611fdb3ef2ec42759bdc2f1167368de24a4a7e7f1'
FILES={'dxc.exe':'aa7965b3763d9c710baaa5a28cedcc633449962a75d1391d161a85304a41b3e2',
    'dxcompiler.dll':'0689e2fee0a1c9df8662a59680469f24e2a3ad7ce6bbbcc9284f7a83172bfc59',
    'dxil.dll':'64be2368211d257a038a7d4ea6bb3e24c878eb8d588c7b8414fccf759b69c2ac'}
PROFILES={
    3:{'program_sha256':'cf61f21cb12052e2f01f519c7d8bbd2330641fbb84b9171061c097bf8c554c26',
       'text_sha256':'f1e0da0b7d853e6db31bbf1c6203e015540b43ba73cc2373f512c26364a7c9dc','outputs':[243,244,245],
       'normal_outputs':[312,313,314],'tangent_outputs':[381,382,383]},
    4:{'program_sha256':'3ca153eff80083a4cb532309e165ad39480d611e422023db9199a2be300b4a20',
       'text_sha256':'a0e3138a923a9d4876786b32031bafdd5d898a88b5c30ddb28173b63f4ab5c20','outputs':[319,320,321],
       'normal_outputs':[412,413,414],'tangent_outputs':[505,506,507]},
}


def f32(v):return struct.unpack('<f',struct.pack('<f',v))[0]


def inspect_tool(path):
    path=Path(path).resolve()
    if path.name.casefold()!='dxc.exe' or any(not (path.parent/name).is_file() or file_hash(path.parent/name)!=digest for name,digest in FILES.items()):
        raise FormatError('DXC and its two DLLs must match the pinned Microsoft v1.9.2609 x64 hashes')
    return {'file':str(path),'files':FILES,'source':URL}


def download(output,proxy=None):
    with new_directory(output) as stage:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({'http':proxy,'https':proxy}) if proxy else urllib.request.ProxyHandler())
        with opener.open(urllib.request.Request(URL,headers={'User-Agent':'AoW4ModToolkit'}),timeout=60) as r:raw=r.read(80*1024*1024+1)
        if len(raw)>80*1024*1024 or sha(raw)!=ARCHIVE_SHA:raise FormatError('DXC archive size/hash differs')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name,digest in FILES.items():
                data=archive.read('bin/x64/'+name)
                if sha(data)!=digest:raise FormatError('DXC extracted file hash differs')
                (stage/name).write_bytes(data)
        save_record(stage/'toolchain.json',{'source':URL,'archive_sha256':ARCHIVE_SHA,'files':FILES})
    return inspect_tool(Path(output)/'dxc.exe')


def prepare(game,influences,tool,output):
    profile=PROFILES.get(influences)
    if profile is None:raise FormatError('Only reviewed three/four influence skin positions are supported')
    binding=inspect_tool(tool);source=Path(game)/'Content/System/Libraries/System_Effects.clb'
    decoded=effect_programs.decode(source,r'[SYSTEM_EFFECTS]EFFECT\0')
    rows=[r for r in decoded['rows'] if r['program_sha256']==profile['program_sha256'] and r['shader_kind']==1]
    if not rows:raise FormatError('Installed effect lacks the pinned skin-position reference program')
    row=rows[0];lib=content_libraries.read(source);member=next(m for m in lib.members if m['identity']['locator']==r'[SYSTEM_EFFECTS]EFFECT\0');fields=member['fields']
    pool,_=clb_streams.read(lib,fields[73],fields[72].uint(4));data=pool[row['program_offset']:row['program_offset']+row['program_bytes']]
    with new_directory(output,[Path(game)]) as stage:
        binary=stage/'program.dxil';binary.write_bytes(data)
        result=subprocess.run([binding['file'],'-dumpbin',str(binary)],cwd=Path(binding['file']).parent,
            capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or len(result.stdout)>8*1024*1024:raise FormatError('Pinned DXC disassembly failed or exceeds bound')
        text=result.stdout.decode('utf-8-sig').replace('\r\n','\n')
        if sha(text.encode())!=profile['text_sha256'] or sha(data)!=profile['program_sha256']:raise FormatError('Skin-position reference program/text differs from the reviewed profile')
        inspect_tool(tool);(stage/'program.ll').write_text(text,encoding='utf-8',newline='\n')
    root=Path(output).resolve()
    return {'influences':influences,'program':{'file':str(root/'program.dxil'),'sha256':profile['program_sha256']},
        'text':{'file':str(root/'program.ll'),'sha256':profile['text_sha256']},'tool':binding,'selection':row,
        'runtime_variant_verified':False,'gpu_execution_verified':False}


class Evaluator:
    def __init__(self,text,influences,checked=True):
        self.profile=PROFILES[influences];self.influences=influences
        if checked and sha(text.replace('\r\n','\n').encode())!=self.profile['text_sha256']:raise FormatError('Unreviewed skin shader disassembly')
        self.definitions={}
        for line in text.splitlines():
            match=re.match(r'\s*%(\d+) = (.*?)(?:\s+;.*)?$',line)
            if match:
                key=int(match[1])
                if key in self.definitions:raise FormatError('Duplicate shader SSA value')
                self.definitions[key]=match[2]
        if len(self.definitions)>10000 or any(n not in self.definitions for n in self.profile['outputs']):raise FormatError('Skin shader output coverage differs')

    def position(self,position,indices,weights,mapping,bones,inverse):
        return self.evaluate({0:position,5:indices,6:weights},mapping,bones,inverse,self.profile['outputs'])

    def directions(self,normal,tangent,indices,weights,mapping,bones,inverse):
        # The reviewed prefix uses the same two 3x3 matrix products for each
        # direction, without translation or inverse transpose. World transform
        # and normalization occur later and are deliberately outside this API.
        result=self.evaluate({1:normal,4:tangent,5:indices,6:weights},mapping,bones,inverse,
            self.profile['normal_outputs']+self.profile['tangent_outputs'])
        return result[:3],result[3:]

    def evaluate(self,inputs,mapping,bones,inverse,outputs):
        def number(token,integer=False):
            token=token.strip()
            if token.startswith('%'):return value(int(token[1:]))
            if token in ('undef','true','false'):return None if token=='undef' else token=='true'
            return int(token) if integer else float(token)
        def arguments(expr):
            inner=expr[expr.index('(')+1:expr.rindex(')')]
            return [a.strip().rsplit(' ',1)[-1] for a in inner.split(',')]
        active=set()
        @functools.lru_cache(None)
        def value(key):
            if key in active:raise FormatError('Cyclic shader SSA reference')
            if len(active)>512:raise FormatError('Shader expression depth exceeded')
            active.add(key)
            try:
                expr=self.definitions[key]
                if expr.startswith('call '):
                    name=re.search(r'@([^(]+)',expr)[1];args=arguments(expr)
                    if name=='dx.op.createHandle':
                        if [int(x) for x in args[:2]]!=[57,0]:raise FormatError('Only source SRV handles are supported in the skin prefix')
                        return int(args[3])
                    if name.startswith('dx.op.loadInput.'):
                        slot,col=int(args[1]),int(args[3])
                        if int(args[0])!=4 or int(args[2])!=0 or slot not in inputs or not 0<=col<len(inputs[slot]):raise FormatError('Unsupported skin-prefix input')
                        return inputs[slot][col]
                    if name.startswith('dx.op.bufferLoad.'):
                        handle=number(args[1],True);index=number(args[2],True)
                        if handle==3:
                            if index%4 or not 0<=index//4<len(mapping):raise FormatError('Skin shader bone map read exceeds its buffer')
                            return (mapping[index//4]&0xffffffff,0,0,0,1)
                        if handle not in (4,5):raise FormatError('Unexpected skin shader SRV')
                        data=bones if handle==4 else inverse;offset=number(args[3],True)
                        if not 0<=index<len(data) or offset not in (0,16,32,48):raise FormatError('Skin shader matrix read exceeds its buffer')
                        return tuple(data[index][offset//4:offset//4+4])+(1,)
                    if name=='dx.op.tertiary.f32' and args[0]=='46':return f32(number(args[1])*number(args[2])+number(args[3]))
                    raise FormatError('Unsupported operation in the reviewed skin prefix: '+name)
                if expr.startswith('extractvalue '):
                    m=re.fullmatch(r'extractvalue [^ ]+ %(\d+), (\d+)',expr)
                    if not m:raise FormatError('Unsupported shader extractvalue')
                    return value(int(m[1]))[int(m[2])]
                m=re.fullmatch(r'(fadd|fsub|fmul)(?: fast)? float ([^,]+), (.+)',expr)
                if m:
                    a,b=number(m[2]),number(m[3]);return f32(a+b if m[1]=='fadd' else a-b if m[1]=='fsub' else a*b)
                m=re.fullmatch(r'shl i32 ([^,]+), (\d+)',expr)
                if m:return (number(m[1],True)<<int(m[2]))&0xffffffff
                raise FormatError('Unsupported instruction in the reviewed skin prefix')
            except (KeyError,IndexError,ValueError,OverflowError) as exc:raise FormatError('Invalid skin shader SSA/reference data') from exc
            finally:active.discard(key)
        result=[value(n) for n in outputs]
        if not all(math.isfinite(v) for v in result):raise FormatError('Nonfinite skin shader result')
        return result
