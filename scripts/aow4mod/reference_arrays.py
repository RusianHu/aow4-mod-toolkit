"""Append a verified link-shaped template element and retarget it atomically."""
from pathlib import Path

from .core import FormatError, Node, decode_link
from .filesystem import file_hash
from . import editing, linking, structured

FORMAT = 'aow4mod-reference-array-plan/1'


def make_plan(source,rid,array_path,template_file,template_id,template_path,target_id,index,
              target_file=None,old_target_file=None,element_link_path=(),pack=None,game=None,
              source_root=None,allow_type_change=False):
    source,source_pack,resource=linking.resource_file(source,rid,pack)
    template_source,template_pack,template=linking.resource_file(template_file,template_id)
    keys=structured.path_keys(template_path)
    if keys[0] not in template.fields:raise FormatError('Template class is absent')
    item=structured.find_node(template.fields[keys[0]],keys[1:])
    if not isinstance(element_link_path,(list,tuple)) or len(element_link_path)>64:
        raise FormatError('Element link path must be a bounded array of hexadecimal keys')
    relative=[int(x,16) if isinstance(x,str) else x for x in element_link_path]
    if any(type(x) is not int or not 0<=x<=0xffffffff for x in relative):
        raise FormatError('Invalid element link path key')
    selected=structured.find_node(item,relative) if relative else item
    previous=decode_link(selected)
    if previous is None:raise FormatError('Template element/link path is not a recognized reference')
    target,target_path,target_hash=linking.target_resource(index,target_id,target_file)
    old,old_path,old_hash=linking.target_resource(index,previous['id'],old_target_file)
    changed_type=old.type_id!=target.type_id
    if changed_type and not allow_type_change:raise FormatError('New target type differs from the verified template reference')
    updated,_=linking.replace_link(item,relative,target)
    edit=structured.make_array_plan(source,rid,array_path,'append',codec='hex',value=updated.hex(),
        pack=source_pack,game=game,allow_opaque=True)
    edit['required_inputs']={str(template_source):file_hash(template_source),str(target_path):target_hash,str(old_path):old_hash}
    root=Path(source_root).resolve() if source_root else source.parent
    if not source.is_relative_to(root):raise FormatError('Reference source is outside its project root')
    return {'format':FORMAT,'edit':edit,'source_root':str(root),'array_path':array_path,
        'template':{'file':str(template_source),'sha256':file_hash(template_source),'id':template_id,'property':template_path},
        'element_link_path':[f'0x{x:X}' for x in relative],
        'target':{'id':f'{target.rid:016X}','file':str(target_path),'sha256':target_hash,'type_id':target.type_id},
        'previous_target':{'id':f'{old.rid:016X}','file':str(old_path),'sha256':old_hash,'type_id':old.type_id},
        'allow_type_change':bool(allow_type_change),'review_required':changed_type,
        'imports':linking.import_update(source,target_path,source_pack),
        'coverage':'Copied element bytes, reference encoding/identity and default same-type constraint. Field-specific legality and gameplay are not certified.'}


def apply_plan(plan,output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT:raise FormatError('Unsupported reference-array plan')
    edit=plan['edit'];editing.validate_plan(edit)
    if len(edit['operations'])!=1:raise FormatError('Reference-array plan must contain one append')
    op=edit['operations'][0]
    for evidence in [plan['template'],plan['target'],plan['previous_target']]:
        if file_hash(evidence['file'])!=evidence['sha256']:raise FormatError('Reference template or target changed')
    expected=make_plan(edit['source'],op['id'],plan['array_path'],plan['template']['file'],plan['template']['id'],
        plan['template']['property'],plan['target']['id'],None,plan['target']['file'],plan['previous_target']['file'],
        plan['element_link_path'],op['pack'],edit.get('game'),plan['source_root'],plan['allow_type_change'])
    if expected!=plan:raise FormatError('Reference-array plan differs from its reviewed inputs')
    imports=None
    if plan['imports']:
        relative=Path(edit['source']).relative_to(plan['source_root']).as_posix()
        imports={relative:plan['imports']}
    result=editing.apply_project_plans([edit],plan['source_root'],output,imports)
    result.update(target=plan['target'],imports=plan['imports'],review_required=plan['review_required'],coverage=plan['coverage'])
    return result
