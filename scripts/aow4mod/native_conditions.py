"""Legitimate TEExpression/variable-mapping construction in an isolated worker."""
import ctypes as c
import struct

from . import condition_syntax
from .core import FormatError


def validate_jobs(jobs):
    if not isinstance(jobs,list) or not 1<=len(jobs)<=128:raise FormatError('Native condition evaluation requires 1..128 jobs')
    for job in jobs:
        tree=condition_syntax.parse(job['source'])
        if condition_syntax.normalized(tree)!=job['normalized']:raise FormatError('Native condition normalization differs')
        variables=job['variables']
        if not isinstance(variables,dict) or len(variables)>256:raise FormatError('Native condition variable count exceeds bounds')
        for name,value in variables.items():
            if not isinstance(name,str) or not name.isascii() or not name.replace('_','a').isalnum() or name[:1].isdigit():raise FormatError('Invalid native condition variable name')
            codec=value.get('codec');data=value.get('value')
            if codec=='bool':
                if type(data) is not bool:raise FormatError('Native condition bool requires a boolean')
            elif codec=='f32':
                if type(data) not in (int,float) or condition_syntax.f32(data)!=data:raise FormatError('Native condition float must be exact float32')
            elif codec=='i32':
                if type(data) is not int or not -(2**31)<=data<2**31:raise FormatError('Native condition integer exceeds int32')
            else:raise FormatError('Unsupported native condition scalar codec')
        if condition_syntax.names(tree)-set(variables):raise FormatError('Native condition contains an unresolved name')
    return jobs


def worker(api,params):
    jobs=validate_jobs(params['jobs']);profile=api.profile['conditions']
    method=lambda name,args,ret=None:c.CFUNCTYPE(ret,*args)(api.dll._handle+profile[name])
    ctor=method('ctor',[c.c_void_p],c.c_void_p);dtor=method('dtor',[c.c_void_p]);text_ctor=method('string_ctor',[c.c_void_p]);text_set=method('string_set',[c.c_void_p,c.c_char_p]);text_dtor=method('string_dtor',[c.c_void_p])
    newmap=method('mapping_create',[c.c_void_p]);add=method('mapping_add',[c.c_void_p,c.c_char_p,c.c_int,c.c_void_p],c.c_int);attach=method('mapping_attach',[c.c_void_p,c.c_void_p],c.c_int)
    parse=method('parse',[c.c_void_p,c.c_void_p,c.c_void_p],c.c_ubyte);evaluate=method('evaluate',[c.c_void_p,c.c_void_p,c.c_void_p],c.c_ubyte)
    release=api.private('release_raw_object_rva',[c.c_void_p]);results=[]
    for job in jobs:
        obj=c.create_string_buffer(profile['size']);text=c.create_string_buffer(16);holder=c.create_string_buffer(8)
        ctor(obj);text_ctor(text);newmap(holder);mapping=int.from_bytes(holder.raw,'little')
        if not mapping:raise FormatError('Native expression mapping constructor failed')
        buffers={};codes={};observed=[]
        try:
            for pointer,cid in ((c.addressof(obj),0x323),(mapping,0x326)):
                getter=api.qword(api.qword(pointer)+0x30)
                if api.memory(getter,6)!=b'\xb8'+struct.pack('<I',cid)+b'\xc3':raise FormatError('Native expression/mapping class differs')
            for name,entry in job['variables'].items():
                kind={'bool':(c.c_ubyte,5),'f32':(c.c_float,2),'i32':(c.c_int32,1)}[entry['codec']]
                buffers[name]=kind[0](entry['value']);codes[name]=kind[1]
                add(mapping,name.encode('ascii'),kind[1],c.byref(buffers[name]))
            count=int.from_bytes(api.memory(mapping+0x18,4),'little');array=api.qword(mapping+0x10)
            if count!=len(buffers):raise FormatError('Native expression mapping count differs')
            for i,name in enumerate(buffers):
                row=array+i*32;p=api.qword(row);length=int.from_bytes(api.memory(row+8,4),'little');code=int.from_bytes(api.memory(row+16,4),'little');data=api.qword(row+24)
                if (length!=len(name) or api.memory(p,length+1)!=name.encode()+b'\0' or code!=codes[name]
                        or data!=c.addressof(buffers[name])):raise FormatError('Native variable mapping name/type/address differs')
                observed.append({'name':name,'code':code,'raw_hex':api.memory(data,c.sizeof(buffers[name])).hex()})
            attach(obj,mapping);text_set(text,job['normalized'].encode('ascii'));parsed=parse(obj,text,None)
            if parsed!=1:raise FormatError('Native condition did not compile')
            number=c.c_float();truth=evaluate(obj,None,c.byref(number))
            values={name:v['value'] for name,v in job['variables'].items()};expected=condition_syntax.evaluate(condition_syntax.parse(job['source']),values)
            if number.value!=expected or bool(truth)!=bool(expected):raise FormatError('Native condition disagrees with the independent float32 evaluator')
            results.append({'source':job['source'],'normalized':job['normalized'],'parsed':True,'value':number.value,'truth':bool(truth),'variables':observed})
        finally:text_dtor(text);dtor(obj);release(mapping)
    return {'expression_cid':'00000323','mapping_cid':'00000326','results':results,'renderer_initialized':bool(api.memory(api.dll._handle+profile['renderer_enabled_rva'],1)[0])}


def validate_payload(payload,params):
    jobs=validate_jobs(params['jobs']);rows=payload.get('results')
    if payload.get('expression_cid')!='00000323' or payload.get('mapping_cid')!='00000326' or not isinstance(rows,list) or len(rows)!=len(jobs):raise FormatError('Native condition result identity/coverage differs')
    if type(payload.get('renderer_initialized')) is not bool:raise FormatError('Native renderer observation is missing')
    for row,job in zip(rows,jobs):
        expected=condition_syntax.evaluate(condition_syntax.parse(job['source']),{k:v['value'] for k,v in job['variables'].items()})
        if row.get('source')!=job['source'] or row.get('normalized')!=job['normalized'] or row.get('parsed') is not True or type(row.get('value')) is not float or row.get('value')!=expected or row.get('truth') is not bool(expected):raise FormatError('Native condition result differs')
        wanted=[{'name':name,'code':{'bool':5,'f32':2,'i32':1}[v['codec']],
                 'raw_hex':struct.pack({'bool':'<B','f32':'<f','i32':'<i'}[v['codec']],v['value']).hex()} for name,v in job['variables'].items()]
        if row.get('variables')!=wanted:raise FormatError('Native condition variable readback differs')
    return payload
