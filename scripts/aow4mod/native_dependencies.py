"""Exact offline expectations for the profiled native Mod import collector."""
import struct
from pathlib import Path

from .core import FormatError,Node,sha,string_list,read_rpk
from . import editing,native,native_objects
from .filesystem import file_hash


def additions(record):
    raw=bytes.fromhex(record['body_hex'])
    if sha(raw)!=record['body_sha256']:raise FormatError('Mod import record body hash differs')
    fields=Node(raw,0,len(raw)).mapping()[0x401204].mapping()
    return tuple(string_list(fields[key]) if key in fields else [] for key in (3,4))


def needed(records):
    return any(any(additions(record)) for record in records)


def append(data,record):
    from .authoring import string_vector
    root=Node(data,176,len(data));fields=root.mapping();updates={}
    for key,values in zip((0x25,0x26),additions(record)):
        if values:updates[key]=string_vector((string_list(fields[key]) if key in fields else [])+values)
    if not updates:return data
    payload=editing.rewrite_map(root,updates)
    return data[:12]+struct.pack('<I',len(payload))+data[16:176]+payload


def expected(data,records):
    for record in records:data=append(data,record)
    return data


def verify(base,records,application):
    from . import native_overlays as overlays
    if len(application.get('steps',[]))!=len(records):raise FormatError('Native import preparation step coverage differs')
    _,rows=read_rpk(base);resources={row.rid:row for row in rows};data=base.read_bytes()
    baseline=application['baseline'];path=baseline['output_file']
    if file_hash(path)!=baseline['output_sha256']:raise FormatError('Native import preparation baseline changed')
    baseline_check=overlays.compare_stage(base,path,resources);steps=[]
    for record,step in zip(records,application['steps']):
        if step['file']!=record['file'] or step['sha256']!=record['sha256']:
            raise FormatError('Native import preparation record binding differs')
        raw=bytes.fromhex(record['body_hex']);saved=Path(step['mod_data_file']).read_bytes()
        if sha(saved)!=step['mod_data_sha256']:raise FormatError('Native import preparation Mod-data changed')
        readback=native_objects.compare_mod_data(raw,saved)
        markers=[{'id':row['id'],'cid':row['cid'],'operation':0x40,'applied_before':0,'applied_after':0} for row in record['resources']]
        output=step['output']
        if file_hash(output['output_file'])!=output['output_sha256']:raise FormatError('Native import preparation snapshot changed')
        data=append(data,record)
        comparison=overlays.compare_stage(base,output['output_file'],resources,overlays.metadata_signature_data(data))
        steps.append({'comparison':comparison,'input_record_readback':readback,'resources_not_applied':step['entries']==markers})
    matched=baseline_check['all_expected_data_equal'] and all(s['comparison']['all_expected_data_equal'] and
        s['input_record_readback']['data_equal'] and s['resources_not_applied'] for s in steps)
    return {'baseline':baseline_check,'steps':steps,'dependency_preparation_matched':matched}
