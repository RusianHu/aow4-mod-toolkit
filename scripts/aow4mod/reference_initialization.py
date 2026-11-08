"""Initialize an empty direct reference from the same field of the same class."""
from pathlib import Path

from . import editing,linking
from .core import FormatError,Node,decode_link
from .filesystem import file_hash

FORMAT='aow4mod-reference-initialization-plan/1'


def empty_status(node):
    if node is None:return 'omitted'
    if node.raw()==b'\0':return 'empty_map'
    link=decode_link(node)
    if link and int(link['id'],16)==0:return 'zero_id_reference'
    try:
        fields=node.mapping()
        if (set(fields)=={0x14,0x16,0x17} and fields[0x14].string()==''
                and fields[0x16].uint(8)==0 and fields[0x17].string()==''):
            return 'empty_locator_reference'
    except FormatError:pass
    raise FormatError('Reference initializer only fills an omitted or recognized empty reference; use link-plan to retarget existing links')


def make_plan(source,rid,property_path,template_file,template_id,target_id,index,
              target_file=None,old_target_file=None,pack=None,game=None,source_root=None):
    source,source_pack,resource=linking.resource_file(source,rid,pack)
    template_path,template_pack,template=linking.resource_file(template_file,template_id)
    class_id,field_id,normalized=editing.property_parts(property_path)
    if len(property_path.split('/'))!=2:
        raise FormatError('Reference initialization currently requires a direct class/property field')
    if resource.type_id!=template.type_id:
        raise FormatError('Reference initializer requires a template of the same resource class')
    if class_id not in resource.fields or class_id not in template.fields:
        raise FormatError('Reference initializer class scope is absent')
    fields=resource.fields[class_id].mapping();template_fields=template.fields[class_id].mapping()
    if field_id not in template_fields:raise FormatError('Template does not serialize the selected reference field')
    selected=template_fields[field_id];previous=decode_link(selected)
    if previous is None or int(previous['id'],16)==0:
        raise FormatError('Template field must contain a recognized nonempty resource reference')
    before=empty_status(fields.get(field_id))
    target,target_path,target_hash=linking.target_resource(index,target_id,target_file)
    old,old_path,old_hash=linking.target_resource(index,previous['id'],old_target_file)
    if target.type_id!=old.type_id:raise FormatError('Reference initializer target type differs from the same-field template')
    updated,_=linking.replace_link(selected,[],target)
    edit=editing.make_plan(source,rid,normalized,updated.hex(),'hex',source_pack,game,True)
    edit['required_inputs']={str(template_path):file_hash(template_path),str(target_path):target_hash,str(old_path):old_hash}
    root=Path(source_root).resolve() if source_root else source.parent
    if not source.is_relative_to(root):raise FormatError('Reference initializer source is outside its Mod root')
    return {'format':FORMAT,'edit':edit,'source_root':str(root),'property':normalized,'before':before,
        'template':{'file':str(template_path),'sha256':file_hash(template_path),'id':f'{template.rid:016X}'},
        'target':{'file':str(target_path),'sha256':target_hash,'id':f'{target.rid:016X}','type_id':target.type_id},
        'previous_target':{'file':str(old_path),'sha256':old_hash,'id':f'{old.rid:016X}','type_id':old.type_id},
        'imports':linking.import_update(source,target_path,source_pack),
        'validation_level':'same_resource_class_and_direct_field_template_same_target_type',
        'coverage':'Copies the same direct reference field from the same resource class. Native effective loading and gameplay remain unverified.'}


def apply_plan(plan,output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT:raise FormatError('Unsupported reference initialization plan')
    edit=plan['edit'];editing.validate_plan(edit)
    if len(edit['operations'])!=1:raise FormatError('Reference initializer must contain one field operation')
    for evidence in (plan['template'],plan['target'],plan['previous_target']):
        if file_hash(evidence['file'])!=evidence['sha256']:raise FormatError('Reference initializer evidence changed')
    op=edit['operations'][0]
    expected=make_plan(edit['source'],op['id'],plan['property'],plan['template']['file'],plan['template']['id'],
        plan['target']['id'],None,plan['target']['file'],plan['previous_target']['file'],op['pack'],edit.get('game'),plan['source_root'])
    if expected!=plan:raise FormatError('Reference initializer plan differs from its source/template/target evidence')
    imports=None
    if plan['imports']:
        relative=Path(edit['source']).relative_to(plan['source_root']).as_posix();imports={relative:plan['imports']}
    result=editing.apply_project_plans([edit],plan['source_root'],output,imports)
    result.update(property=plan['property'],target=plan['target'],before=plan['before'],imports=plan['imports'],
                  validation_level=plan['validation_level'],coverage=plan['coverage'])
    return result
