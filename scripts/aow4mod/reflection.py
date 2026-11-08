"""Offline, build-bound extraction of generated serializer metadata.

No game code is executed. An intentionally small x64 constant/alias tracker
records names and supported storage calls; conditional or conflicting evidence
remains a candidate. Corpus observations independently check stored widths.
"""
from bisect import bisect_right
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import struct
import time
import tempfile

from .core import FormatError,read_rpk,sha

FORMAT='aow4mod-reflection/1'
PROFILES={'60d53fd3852ae26fcb9822d78692d788b0334971582e85a776090e4af44efe88':
          {'scope_constructor':0x427ab0,'class_register':0x231fd0,'enum_register':0x40ea70},
          '2d4a1bcacc99a25fd6e71efb94de3daa99c766348897c8424618a363ae903015':
          {'scope_constructor':0x42b010,'class_register':0x234230,'enum_register':0x412030},
          'd9e2530a18a8665b67c3d0a941dffd071343880b7430617acb0262afa1f47e0e':
          {'scope_constructor':0x42bd60,'class_register':0x234250,'enum_register':0x412d80},
          '16e19fa50154c243f92333e795ae2f789a4c952588aa42cafc911d48af82f896':
          {'scope_constructor':0x42bd60,'class_register':0x234250,'enum_register':0x412d80}}


def dependencies():
    try:
        import capstone,pefile
        return capstone,pefile
    except ImportError as exc:raise FormatError('Schema extraction requires the project [analysis] extra') from exc


def family(name):
    groups={'rax':('eax','ax','al','ah'),'rbx':('ebx','bx','bl','bh'),
            'rcx':('ecx','cx','cl','ch'),'rdx':('edx','dx','dl','dh'),
            'rsi':('esi','si','sil'),'rdi':('edi','di','dil'),
            'rbp':('ebp','bp','bpl'),'rsp':('esp','sp','spl')}
    for root,aliases in groups.items():
        if name==root or name in aliases:return root
    match=re.fullmatch(r'(r(?:[89]|1[0-5]))[dwb]?',name)
    return match[1] if match else name


def integer(value,bits=0):
    return value[1] if value and value[0]=='int' and value[2]>=bits else None


class Image:
    def __init__(self,path):
        capstone,pefile=dependencies()
        self.path=Path(path);self.data=self.path.read_bytes();self.sha256=sha(self.data)
        self.pe=pefile.PE(data=self.data,fast_load=True)
        if self.pe.FILE_HEADER.Machine!=0x8664:raise FormatError('Expected an x64 PE image')
        self.base=self.pe.OPTIONAL_HEADER.ImageBase
        self.text=next(s for s in self.pe.sections if s.Name.rstrip(b'\0')==b'.text')
        pdata=next(s for s in self.pe.sections if s.Name.rstrip(b'\0')==b'.pdata').get_data()
        self.functions=sorted((a,b) for a,b,_ in struct.iter_unpack('<III',pdata[:len(pdata)//12*12]) if a and a<b)
        self.starts=[x[0] for x in self.functions]
        self.md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64);self.md.detail=True

    def read(self,rva,size):
        if not 0<=rva<self.pe.OPTIONAL_HEADER.SizeOfImage:raise FormatError('RVA outside image')
        data=self.pe.get_data(rva,size)
        if len(data)!=size:raise FormatError('Truncated image data')
        return data

    def cstring(self,rva,max_length=180):
        try:
            raw=self.pe.get_data(rva,max_length)
            if b'\0' not in raw:return None
            raw=raw.split(b'\0')[0]
            if not all(32<=x<127 for x in raw):return None
            return raw.decode('ascii')
        except Exception:return None

    def containing(self,rva):
        i=bisect_right(self.starts,rva)-1
        if i>=0 and self.functions[i][0]<=rva<self.functions[i][1]:return self.functions[i]
        return None

    def callers(self,targets):
        code=self.text.get_data();found=set()
        for m in re.finditer(b'\xe8',code):
            pos=m.start()
            if pos+5>len(code):continue
            rva=self.text.VirtualAddress+pos
            target=rva+5+struct.unpack_from('<i',code,pos+1)[0]
            if target in targets:
                function=self.containing(rva)
                if function:found.add(function)
        return sorted(found)


def events(image,start,end):
    regs={'rcx':('self',0),'rdx':('archive',0)};stack={};sp=0;conditional=False
    result=[]
    instructions=list(image.md.disasm(image.read(start,end-start),image.base+start))
    # Linear tracking is not a control-flow solver, including unconditional jumps.
    conditional=any(i.mnemonic.startswith(('j','loop')) for i in instructions)
    returns=[i for i,ins in enumerate(instructions) if ins.mnemonic.startswith('ret')]
    if returns and any(ins.mnemonic not in ('nop','int3') for ins in instructions[returns[0]+1:]):conditional=True
    def read_op(ins,op,lea=False):
        if op.type==1:
            name=ins.reg_name(op.reg)
            if name in ('ah','bh','ch','dh'):return None
            value=regs.get(family(name))
            if value and value[0]=='int':return ('int',value[1]&((1<<(op.size*8))-1),min(value[2],op.size*8))
            return value if op.size==8 else None
        if op.type==2:return ('int',op.imm,64)
        if op.type!=3:return None
        mem=op.mem;base=ins.reg_name(mem.base) if mem.base else '';index=ins.reg_name(mem.index) if mem.index else ''
        if base=='rip':
            rva=ins.address+ins.size+mem.disp-image.base
            if lea:return ('rva',rva)
            try:
                section=image.pe.get_section_by_rva(rva)
                if section is not None and not section.Characteristics&0x80000000 and op.size in (1,2,4,8):
                    return ('int',int.from_bytes(image.read(rva,op.size),'little'),op.size*8)
            except Exception:pass
            return None
        if base=='rsp' and not index:
            if lea:return ('stack',sp+mem.disp)
            entry=stack.get(sp+mem.disp)
            return entry[0] if entry and entry[1]==op.size else None
        value=regs.get(family(base))
        if not lea:
            if value==('archive',0) and mem.disp==0 and not index:return ('vtable',0)
            return None
        extra=mem.disp
        if index:
            v=integer(regs.get(family(index)))
            if v is None:return None
            extra+=v*mem.scale
        if value and value[0] in ('self','member'):
            return ('member',value[1]+extra)
        if value and value[0]=='int':return ('int',value[1]+extra,64)
        return None
    def assign(ins,op,value):
        if op.type==1:
            original_name=ins.reg_name(op.reg);name=family(original_name)
            if original_name in ('ah','bh','ch','dh'):regs[name]=None;return
            if value and value[0]=='int':
                known=min(value[2],op.size*8)
                value=('int',value[1]&((1<<(op.size*8))-1),64 if op.size==4 and known==32 else known)
            elif op.size!=8:value=None
            regs[name]=value
        elif op.type==3 and ins.reg_name(op.mem.base)=='rsp' and not op.mem.index:
            address=sp+op.mem.disp
            if value and value[0]=='int':value=('int',value[1]&((1<<(op.size*8))-1),min(value[2],op.size*8))
            elif op.size!=8:value=None
            for offset,(_,size) in list(stack.items()):
                if offset<address+op.size and address<offset+size:del stack[offset]
            stack[address]=(value,op.size)
    for ins in instructions:
        ops=ins.operands;m=ins.mnemonic
        if m=='push':sp-=8;continue
        if m=='pop':
            if ops and ops[0].type==1:regs[family(ins.reg_name(ops[0].reg))]=None
            sp+=8;continue
        if m in ('add','sub') and len(ops)==2 and ops[0].type==1 and ins.reg_name(ops[0].reg)=='rsp' and ops[1].type==2:
            sp+=ops[1].imm if m=='add' else -ops[1].imm;continue
        if m=='call':
            target=ops[0].imm-image.base if ops[0].type==2 else None
            slot=None
            if ops[0].type==3 and regs.get(family(ins.reg_name(ops[0].mem.base)))==('vtable',0) and not ops[0].mem.index:
                slot=ops[0].mem.disp
            result.append({'site':ins.address-image.base,'target':target,'slot':slot,
                'args':{k:regs.get(k) for k in ('rcx','rdx','r8','r9')},
                'stack':{k:stack.get(sp+k,(None,0))[0] for k in (0x20,0x28)},'conditional':conditional})
            for name in ('rax','rcx','rdx','r8','r9','r10','r11'):regs[name]=None
            for offset in range(0,0x30,8):stack.pop(sp+offset,None)
            continue
        if m in ('mov','movabs','lea','movzx','movsx','movsxd') and len(ops)==2:
            value=read_op(ins,ops[1],m=='lea')
            if value and value[0]=='int' and m in ('movsx','movsxd','movzx'):
                width=ops[1].size*8;v=value[1]&((1<<width)-1)
                if value[2]<width:value=None
                else:
                    if m!='movzx' and v&(1<<(width-1)):v-=1<<width
                    value=('int',v,64)
            assign(ins,ops[0],value);continue
        if m=='xor' and len(ops)==2 and ops[0].type==ops[1].type==1 and ops[0].reg==ops[1].reg:
            assign(ins,ops[0],('int',0,64));continue
        if m in ('add','sub','and','or','shl','shr') and len(ops)==2 and ops[0].type==1:
            a,b=integer(read_op(ins,ops[0]),ops[0].size*8),integer(read_op(ins,ops[1]),ops[1].size*8)
            if a is not None and b is not None:
                value={'add':lambda:a+b,'sub':lambda:a-b,'and':lambda:a&b,'or':lambda:a|b,
                       'shl':lambda:a<<(b&(63 if ops[0].size==8 else 31)),
                       'shr':lambda:a>>(b&(63 if ops[0].size==8 else 31))}[m]()
                assign(ins,ops[0],('int',value,64));continue
        for op in ops:
            if op.type==3 and op.access&2 and ins.reg_name(op.mem.base)=='rsp' and not op.mem.index:
                address=sp+op.mem.disp
                for offset,(_,size) in list(stack.items()):
                    if offset<address+op.size and address<offset+size:del stack[offset]
        try:
            _,writes=ins.regs_access()
            for reg in writes:
                name=family(ins.reg_name(reg))
                if name not in ('rsp','rflags'):regs[name]=None
        except Exception:pass
    return result


def extract(path,game=None,profile=None):
    started=time.monotonic();image=Image(path)
    protocol=profile or PROFILES.get(image.sha256)
    if protocol is None:raise FormatError('Unknown ResourceED build; review and supply a matching extraction profile')
    if profile and profile.get('tool_sha256')!=image.sha256:raise FormatError('Custom profile hash mismatch')
    targets={k:int(v,0) if isinstance(v,str) else v for k,v in protocol.items() if k in ('scope_constructor','class_register','enum_register')}
    if set(targets)!= {'scope_constructor','class_register','enum_register'}:raise FormatError('Incomplete extractor profile')
    fields=defaultdict(list);types=defaultdict(list);enums={};unparsed_enums=[]
    functions=image.callers(set(targets.values()))
    for start,end in functions:
        scopes=[]
        for event in events(image,start,end):
            args=event['args'];target=event['target']
            if target==targets['scope_constructor']:
                cls=integer(args['r9'],32)
                if cls is not None and args['r8']==('archive',0) and args['rcx'] and args['rcx'][0]=='stack':
                    scopes.append((cls,args['rcx']))
                continue
            if target==targets['class_register']:
                cls,parent=integer(args['rcx'],32),integer(args['rdx'],32)
                name=image.cstring(args['r8'][1]) if args['r8'] and args['r8'][0]=='rva' else None
                if cls is not None and parent is not None and name:
                    types[f'0x{cls:08X}'].append({'name':name,'parent':f'0x{parent:08X}','site':hex(event['site'])})
            if target==targets['enum_register']:
                cls,count=integer(args['rcx'],32),integer(args['r9'],32)
                name=image.cstring(args['rdx'][1]) if args['rdx'] and args['rdx'][0]=='rva' else None
                pointer=args['r8']
                if cls is not None and count is not None and 0<count<1000 and name and pointer and pointer[0]=='rva':
                    try:
                        values=[]
                        for i in range(count):
                            label,value,description=struct.unpack('<QQQ',image.read(pointer[1]+24*i,24))
                            label=image.cstring(label-image.base)
                            desc=image.cstring(description-image.base,512) if description else ''
                            if label is None:raise FormatError('Invalid enum table label')
                            values.append({'value':value,'label':label,'description':desc})
                        enums[f'0x{cls:08X}']={'name':name,'values':values,'site':hex(event['site']),'table_rva':hex(pointer[1])}
                    except Exception:unparsed_enums.append({'name':name,'site':hex(event['site'])})
            if event['slot'] is not None and scopes:
                member=args['rdx'];slot=event['slot'];label=None;field_id=None;storage=None;default=None;width=None
                if member and member[0]=='member':
                    if slot==0x40:
                        field_id=integer(event['stack'][0x20],32);label=event['stack'][0x28]
                        width=integer(args['r8'],32)
                        value=integer(args['r9'],min((width or 0)*8,64))
                        if width in (1,2,4,8) and value is not None:
                            storage='integer';default=(value&((1<<(width*8))-1)).to_bytes(width,'little').hex()
                    else:
                        field_id=integer(args['r9'],32);label=event['stack'][0x20]
                        if slot==0x50:
                            value=integer(args['r8'],8)
                            if value in (0,1):storage='bool';width=1;default=bytes([value]).hex()
                    name=image.cstring(label[1]) if label and label[0]=='rva' else None
                    if field_id is not None and 0<=field_id<65536 and name and re.fullmatch(r'&?[A-Za-z_][A-Za-z0-9_ .:-]*',name):
                        key=f'0x{scopes[-1][0]:08X}/0x{field_id:02X}'
                        fields[key].append({'member_name':name.lstrip('&'),'member_offset':member[1],
                            'storage':storage,'width':width if storage else None,'default_hex':default,
                            'virtual_slot':hex(slot),'function':hex(start),'site':hex(event['site']),
                            'conditional':event['conditional']})
            if target is not None and scopes and args['rcx']==scopes[-1][1]:scopes.pop()
    result_fields={}
    for key,declarations in fields.items():
        signatures={(d['member_name'],d['storage'],d['width'],d['default_hex']) for d in declarations}
        simple=[d for d in declarations if not d['conditional']]
        chosen=(simple or declarations)[0]
        name=chosen['member_name'];display=name[1:] if len(name)>1 and name[0]=='F' and name[1].isupper() else name
        result_fields[key]={'name':display,'member_name':name,'storage':chosen['storage'],'width':chosen['width'],
            'default_hex':chosen['default_hex'],'consistent':len(signatures)==1,'straight_line':bool(simple),
            'declarations':declarations,'observed':0,'width_mismatches':0,'value_mismatches':0}
    result_types={}
    for key,declarations in types.items():
        distinct={(d['name'],d['parent']) for d in declarations}
        result_types[key]={**declarations[0],'consistent':len(distinct)==1}
    failures=[];resource_count=0
    if game:
        for p in sorted((Path(game)/'Content').rglob('*.rpk')):
            try:
                _,resources=read_rpk(p);resource_count+=len(resources)
                for resource in resources:
                    for key,raw in resource.leaves().items():
                        field=result_fields.get(key)
                        if field is None:continue
                        field['observed']+=1
                        if field['width'] is not None and len(raw)!=field['width']:field['width_mismatches']+=1
                        if field['storage']=='bool' and raw not in (b'\0',b'\1'):field['value_mismatches']+=1
            except FormatError as exc:failures.append({'path':str(p),'error':str(exc)})
    for field in result_fields.values():
        field['storage_validated']=bool(field['consistent'] and field['straight_line'] and field['storage'] and
                                        field['observed'] and not field['width_mismatches'] and not field['value_mismatches'])
        field['confidence']='method_and_corpus' if field['storage_validated'] else 'candidate'
    anchor=result_fields.get('0x00471C6D/0x01')
    if not anchor or anchor['member_name']!='FFriendlyFireType' or anchor['default_hex']!='02000000':
        raise FormatError('Extractor anchor failed; profile/ABI needs review')
    return {'format':FORMAT,'extractor_sha256':sha(Path(__file__).read_bytes()),
            'tool':str(Path(path).resolve()),'tool_sha256':image.sha256,'profile':targets,
            'fields':result_fields,'types':result_types,'enums':enums,
            'summary':{'functions_examined':len(functions),'fields':len(result_fields),'types':len(result_types),'enums':len(enums),
                       'storage_validated':sum(f['storage_validated'] for f in result_fields.values()),
                       'resources_observed':resource_count,'unparsed_enum_tables':len(unparsed_enums),'corpus_failures':len(failures)},
            'unparsed_enums':unparsed_enums,'corpus_failures':failures,'elapsed_seconds':round(time.monotonic()-started,3),
            'coverage':'Generated serializer labels and integer/bool storage; no complete game-domain or reference-target constraints. Conditional/ambiguous declarations remain candidates.'}


def load(path,tool_hash):
    schema=json.loads(Path(path).read_text(encoding='utf8'))
    if schema.get('format')!=FORMAT or schema.get('tool_sha256')!=tool_hash:
        raise FormatError('Schema does not match the current tool binary')
    if schema.get('extractor_sha256')!=sha(Path(__file__).read_bytes()):
        raise FormatError('Schema extractor changed; run schema-extract again')
    return schema


def search(schema,query='',kind='fields',limit=20,offset=0,validated_only=False,details=False):
    from .catalog import check_page,page
    check_page(limit,offset)
    if kind not in ('fields','types','enums'):raise FormatError('Unknown schema entity kind')
    rows=[];needle=query.casefold()
    for key,item in sorted(schema[kind].items()):
        if needle not in key.casefold() and needle not in item['name'].casefold():continue
        if validated_only and not item.get('storage_validated'):continue
        if details:row={'key':key,**item}
        else:
            allowed=('name','parent','consistent','storage','width','default_hex','storage_validated','confidence','observed')
            row={'key':key,**{k:item[k] for k in allowed if k in item}}
            if 'values' in item:row['value_count']=len(item['values'])
        rows.append(row)
    return {'kind':kind,**page(rows[offset:offset+limit],len(rows),limit,offset),
            'tool_sha256':schema['tool_sha256'],'coverage':schema['coverage']}


def save(schema,path,game,refresh=False):
    from .core import write_json
    from .filesystem import outside,file_hash,reject_reparse
    path=outside(path,[game])
    if not refresh or not path.exists():
        write_json(path,schema,[Path(game)]);return
    reject_reparse(path)
    if path.suffix.lower()!='.json':raise FormatError('Schema output must be JSON')
    previous=path.read_bytes();previous_hash=sha(previous)
    if json.loads(previous).get('format')!=FORMAT:raise FormatError('Refresh can replace only an extracted schema')
    history=path.parent/'schema-history'/f'{path.stem}-{previous_hash}.json'
    if not history.exists():write_json(history,json.loads(previous),[Path(game)])
    elif file_hash(history)!=previous_hash:
        # JSON layout may differ in a previous archive, compare its full value.
        if json.loads(history.read_text(encoding='utf8'))!=json.loads(previous):
            raise FormatError('Schema history collision')
    with tempfile.TemporaryDirectory(prefix='aow4mod-schema-',dir=path.parent) as temporary:
        staged=Path(temporary)/'schema.json';write_json(staged,schema,[])
        if file_hash(path)!=previous_hash:raise FormatError('Schema changed during refresh')
        os.replace(staged,path)
