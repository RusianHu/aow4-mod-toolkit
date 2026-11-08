"""Known TGameIconR atlas/sprite links, preserving renderer/environment settings."""
import json
from pathlib import Path
import struct

from . import editing, linking, native, pack_imports, projects, structured
from . import content_libraries
from .core import FormatError, Node, read_rpk
from .filesystem import copy_directory, file_hash, inventory, save_record

FORMAT='aow4mod-image-link-plan/1'
PROPERTY='0x040001F3/0x0'


def sprites(path,locator):
    library=content_libraries.read(path)
    matches=[m for m in library.members if (m['identity']['locator'] or '').casefold()==locator.casefold()]
    if len(matches)!=1 or matches[0]['identity']['type_id']!='0x00413403':
        raise FormatError('Expected exactly one native texture atlas at the selected locator')
    member=matches[0];scope=member['fields'][0x413403].mapping();result=[];seen=set()
    for node in scope[1].array().values():
        cid,fields=node.typed()
        if cid!=0x413404 or 10 not in fields or 11 not in fields:raise FormatError('Unsupported atlas sprite identity layout')
        rid=fields[10].uint(4)
        if rid in seen:raise FormatError('Duplicate atlas sprite IDs are ambiguous')
        seen.add(rid);data=fields[cid].mapping()
        result.append({'id':rid,'name':library.string(fields[11]),'width':data[6].uint(4),'height':data[7].uint(4)})
    return {'file':str(Path(path).resolve()),'sha256':file_hash(path),'library':library.name,
            'locator':member['identity']['locator'],'name':member['identity']['name'],'sprites':result,
            'coverage':'Native CLB v8 atlas/sprite identity metadata only; pixels and rendering not evaluated here.'}


def make_plan(source,rid,library,locator,sprite_id,game,pack=None,source_root=None,import_pack=None):
    source,pack,resource=linking.resource_file(source,rid,pack);root=Path(source_root).resolve() if source_root else source.parent
    if not source.is_relative_to(root):raise FormatError('Image link source is outside the Mod root')
    if resource.type_id!=0x460075:raise FormatError('Image links currently support the reviewed TGameIconR layout only')
    binding=native.public_binding(native.profile_for(game))
    library=Path(library).resolve()
    if not library.is_relative_to(root):raise FormatError('Stage the selected image library in the Mod before linking it')
    target=sprites(library,locator);selected=[s for s in target['sprites'] if s['id']==sprite_id]
    if type(sprite_id) is not int or len(selected)!=1:raise FormatError('Selected sprite ID is missing or ambiguous')
    fields=resource.fields[0x040001f3].mapping();parent=fields[0]
    reference=structured.find_node(parent,[0x040112c2,0]);ref=reference.mapping()
    if set(ref)!={0x14,0x15} or set(ref[0x14].mapping())!={0x14,0x17}:
        raise FormatError('Unsupported original TGameIconR image-reference layout')
    atlas=ref[0x14].mapping()
    before={'locator':atlas[0x14].string(),'name':atlas[0x17].string(),'sprite_id':ref[0x15].uint(4)}
    after={'locator':target['locator'],'name':target['name'],'sprite_id':sprite_id}
    if before==after:raise FormatError('Image link makes no change')
    link=editing.rewrite_map(reference,{0x14:editing.rewrite_map(ref[0x14],{
        0x14:editing.encode_value('string',after['locator']),0x17:editing.encode_value('string',after['name'])}),
        0x15:struct.pack('<I',sprite_id)})
    updated=structured.replace(parent,[0x040112c2,0],link)
    edit=editing.make_plan(source,rid,PROPERTY,updated.hex(),'hex',pack,game,True)
    edit['required_inputs']={str(library):target['sha256']}
    if source.suffix.lower()=='.rpk':anchor=source
    elif import_pack:anchor=Path(import_pack).resolve()
    else:
        candidates=[Path(p['path']) for p in projects.inspect_project(root)['packs'] if p['registered']]
        if len(candidates)!=1:raise FormatError('Select one registered local --import-pack to load the new CLB dependency')
        anchor=candidates[0]
    if not anchor.is_relative_to(root):raise FormatError('Image import anchor must be a local registered RPK')
    declared={Path(p['path']).resolve() for p in projects.inspect_project(root)['packs'] if p['registered']}
    if anchor not in declared:raise FormatError('Image import anchor is not reachable from the ACP registration')
    info,_=read_rpk(anchor);dependency=target['library']+'.CLB';imports=None
    if dependency.casefold() not in {x.casefold() for x in info['dependencies']}:
        imports=pack_imports.make_plan(anchor,[dependency],source_root=root,game=game)
    return {'format':FORMAT,'generator_sha256':file_hash(__file__),'binding':binding,'source_root':str(root),
        'source_files':inventory(root),'edit':edit,'library':target,'sprite':selected[0],'before':before,'after':after,
        'import_pack':str(anchor),'imports':imports,'game':str(Path(game).resolve()),
        'coverage':'Known icon atlas locator/name and sprite ID, native member metadata and a registered RPK import. '
                   'Renderer/environment siblings stay unchanged; effective overlays, click behavior and rendering are not certified.'}


def apply_plan(plan,output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=file_hash(__file__):
        raise FormatError('Unsupported or stale image-link plan')
    root=Path(plan['source_root']);edit=plan['edit'];source,data=editing.validate_plan(edit)
    if inventory(root)!=plan['source_files']:raise FormatError('Image-link Mod changed since planning')
    if len(edit['operations'])!=1:raise FormatError('Image-link plan must edit exactly one resource')
    op=edit['operations'][0]
    expected=make_plan(source,op['id'],plan['library']['file'],plan['after']['locator'],plan['after']['sprite_id'],
        plan['game'],op['pack'],root,plan['import_pack'])
    if expected!=plan:raise FormatError('Image-link plan differs from its source, selected sprite or imports')
    patched=editing.patch_bytes(data,source.suffix.lower(),edit['operations'])
    before=editing.resources_in_file(source)
    with copy_directory(root,output,[plan['game']]) as (staged,original):
        target=staged/source.relative_to(root);target.write_bytes(patched)
        changed=editing.verify_changes(before,editing.resources_in_file(target),edit['operations'])
        if plan['imports']:
            imports=plan['imports'];anchor=Path(plan['import_pack']);destination=staged/anchor.relative_to(root)
            info,rows=read_rpk(destination)
            if info['dependencies']!=imports['before']:raise FormatError('Image RPK imports changed before staging')
            saved=pack_imports.rewrite(destination.read_bytes(),imports['after']);destination.write_bytes(saved)
            info,after=read_rpk(destination)
            if info['dependencies']!=imports['after'] or [r.node.raw() for r in rows]!=[r.node.raw() for r in after]:
                raise FormatError('Image import changed unrelated resource data')
        changed=editing.verify_changes(before,editing.resources_in_file(target),edit['operations'])
        if file_hash(plan['library']['file'])!=plan['library']['sha256']:raise FormatError('Linked image library changed during staging')
        result={'format':'aow4mod-image-link-receipt/1','output':str(Path(output).resolve()),'before':plan['before'],
            'after':plan['after'],'sprite':plan['sprite'],'verified_changed_fields':changed,
            'import_pack':Path(plan['import_pack']).relative_to(root).as_posix(),
            'added_imports':plan['imports']['add'] if plan['imports'] else [],'coverage':plan['coverage'],'gameplay_tested':False}
        save_record(staged/'.aow4mod-image-link.json',result)
    return result
