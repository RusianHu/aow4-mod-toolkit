"""Explicit local RPK bindings and bounded import-cycle preflight for native loads."""
from collections import defaultdict,deque
from pathlib import Path

from .core import FormatError,Node,read_rpk,sha
from .filesystem import file_hash
from . import overlay_imports,import_graph


def bind(paths):
    names={};total=0
    for value in sorted({str(Path(path).resolve()) for path in paths}):
        path=Path(value)
        if path.stat().st_size>64*1024*1024:raise FormatError('Native context RPK exceeds 64 MiB')
        info,_=read_rpk(path);name=info['name']
        if Path(name).name!=name or '\\' in name or not name.lower().endswith('.rpk'):
            raise FormatError('Native context requires bare RPK internal filenames')
        key=name.casefold();row={'file':value,'name':name,'sha256':info['sha256']}
        if key in names:
            if names[key]['sha256']!=row['sha256']:raise FormatError('Ambiguous native RPK context internal name: '+name)
            continue
        names[key]=row;total+=info['size']
        if len(names)>256 or total>256*1024*1024:raise FormatError('Native local RPK context exceeds its bound')
    return [names[key] for key in sorted(names)]


def check_frozen_cycles(data,packs):
    """Recheck cycles whose declarations are available in frozen binaries."""
    raw={Node(data,176,len(data)).mapping()[0x16].string().casefold():data}
    for row in packs:
        path=Path(row['file']);content=path.read_bytes()
        if sha(content)!=row['sha256']:raise FormatError('Frozen RPK context changed')
        key=Node(content,176,len(content)).mapping()[0x16].string().casefold()
        if key in raw:raise FormatError('Frozen RPK context duplicates the base or another namespace')
        raw[key]=content
    edges=[]
    for name,content in raw.items():
        for declaration in overlay_imports.rpk_declarations(content):
            target=Path(declaration['name'].replace('\\','/')).name.casefold()
            if target in raw:edges.append({'source':name,'kind':'rpk','status':'found','targets':[{'file':target}]})
    if import_graph.analyze(edges,{})['cycles']:raise FormatError('Frozen local native RPK context contains an import cycle')


def preflight(data,packs,game,base_owned=False):
    base_name=Node(data,176,len(data)).mapping()[0x16].string();local={row['name'].casefold():row for row in packs}
    if base_name.casefold() in local:raise FormatError('Native dependency context shadows the selected base pack')
    available=defaultdict(list)
    for path in (Path(game)/'Content').rglob('*.rpk'):available[path.name.casefold()].append(path)
    pending=deque([(base_name,data,base_owned)]);seen=set();edges=[];originals={};advisory=[];resolved={}
    while pending:
        name,raw,owned=pending.popleft();key=name.casefold()
        if key in seen:continue
        seen.add(key)
        if len(seen)>2000:raise FormatError('Native RPK import traversal exceeds 2000 packs')
        for declaration in overlay_imports.rpk_declarations(raw):
            target=Path(declaration['name'].replace('\\','/')).name.casefold()
            if not target.endswith('.rpk'):continue
            if target==base_name.casefold():
                target_name=base_name;target_raw=data;target_owned=True
            elif target in local:
                row=local[target];target_raw=Path(row['file']).read_bytes()
                if sha(target_raw)!=row['sha256']:raise FormatError('Native RPK context changed during preflight')
                target_name=row['name'];target_owned=True
            elif target in resolved:
                target_name,target_raw,target_owned=resolved[target]
            else:
                candidates=available.get(target,[])
                hashes={file_hash(path) for path in candidates}
                if len(hashes)>1:raise FormatError('Ambiguous original native RPK import: '+declaration['name'])
                if not candidates:
                    if not declaration['optional'] and (owned or declaration['mod_added']):
                        raise FormatError('Missing required native RPK import: '+declaration['name'])
                    advisory.append({'source':name,**declaration});continue
                path=sorted(candidates)[0];info,_=read_rpk(path);target_raw=path.read_bytes()
                if info['sha256']!=sha(target_raw) or info['name'].casefold()!=target:
                    raise FormatError('Original native RPK identity/hash differs from its import')
                if hashes!={info['sha256']}:raise FormatError('Original native RPK import changed during preflight')
                originals.update({str(path.resolve()):info['sha256'] for path in candidates})
                target_name=info['name'];target_owned=False;resolved[target]=(target_name,target_raw,target_owned)
            edges.append({'source':key,'kind':'rpk','status':'found','targets':[{'file':target_name.casefold()}]})
            pending.append((target_name,target_raw,target_owned))
    graph=import_graph.analyze(edges,{})
    if graph['cycles']:raise FormatError('Native RPK context contains an import cycle: '+' -> '.join(graph['cycles'][0]['cycle']))
    return {'graph':{key:value for key,value in graph.items() if key not in ('dependency_first_order','coverage')},
            'original_input_hashes':originals,'missing_advisory_imports':advisory}
