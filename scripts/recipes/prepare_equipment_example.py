"""Build an unpublished Lucky Ring variant connected to the original item reward sets.

This reviewed recipe is deliberately limited to the pinned original build. It
reuses original ring artwork, slots, tier and targeting; it does not generate
arbitrary equipment or prove actual drops, item-forge eligibility or rendering.
"""
import argparse
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aow4mod import authoring, cloning, editing, localized_links, native_authoring
from aow4mod import native, native_edits, native_inspection, localization, pack_imports, structured
from aow4mod.core import FormatError, Node, Resource, read_rpk, references, string_list, sha
from aow4mod.discovery import find_game
from aow4mod.filesystem import copy_directory, file_hash, inventory, save_record

EQUIPMENT = 'Content/Title/Packs/Equipment.rpk'
REWARDS = 'Content/Title/Packs/Strategic/RewardDefinitions_Strategic.rpk'
REWARD_SETS = 'Content/Title/Packs/Strategic/RewardSets_Stategic.rpk'
DLC_EQUIPMENT = 'Content/EmpiresAndAshes/Packs/Equipment_EmpiresAndAshes.rpk'
SOURCES = {
    'DotNetBindings.dll':'58b7210aa024d26cc0c148083d4aca7a322082a75ec3740a26dc2c4b9d4a8eff',
    EQUIPMENT:'8e034c21e29666bf17c1128a5a29305061cd6830cc3a8dd50c9184ec1e609e94',
    REWARDS:'6a48caef896922fe304c16dd079fddf3bf92d307ab1e32bb3437d2fa768318ae',
    REWARD_SETS:'d88dfe4df0d54403cf5cb9974dd87d8c0f1b2b75ce38ce17f554bdcb21e141ad',
    DLC_EQUIPMENT:'1cf140c5a739c9e6f8f4dfeb1f84050f92945cd63121b856ad56c09a05d98fc5',
}
ITEM='00000415000002CD'; ICON='00000415000002CF'; PROPERTY='00000415000002D0'; MODIFIER='00000415000002D1'
MAIN_SET='000003E6000004F2'
POOL='0000045E000006D2'; SMALL='00000413000000E5'; CATEGORY='00000413000000E6'
LOOT='00000413000000E4'; PICKUP='0000042A0000078E'
ARRAY='0x00471D33/0x32/0x1E'


def verified_sources(game):
    for relative, expected in SOURCES.items():
        if file_hash(game/relative)!=expected:
            raise FormatError('Equipment recipe original build changed; review before adapting: '+relative)
    native.profile_for(game)


def rows(path):
    return {f'{r.rid:016X}':r for r in read_rpk(path)[1]}


def official_set_pattern(game,cache):
    result=[]
    for relative,rid in [(EQUIPMENT,MAIN_SET),(DLC_EQUIPMENT,'0000048900000151')]:
        obj=rows(game/relative)[rid];raw=obj.leaves()['0x00471D33/0x33']
        if Node(raw,0,len(raw)).string()!='HIGHLANDER_EQUIPMENT_SET' or not references(obj):
            raise FormatError('Official same-name equipment-set pattern changed')
        proof=native_inspection.inspect(game/relative,game,rid,cache_dir=cache)
        priority=[p for p in proof['properties'] if p['name']=='Override Priority']
        if not proof['comparison']['data_equal'] or len(priority)!=1 or priority[0].get('value')!=0:
            raise FormatError('Official matching-name set priority could not be verified')
        result.append(proof)
    return result


def reward_entry(rewards, reward_sets, pool, item_id):
    """Prove explicit ID links AND the name-based subset/category transitions."""
    if pool.type_id!=0x471d33:raise FormatError('Reward pool is not a TEquipmentSet')
    raw=pool.leaves()['0x00471D33/0x33'];name=Node(raw,0,len(raw)).string()
    if name!='RING_T1_SET':raise FormatError('Original ring reward set name changed')
    if not any(x['id']==item_id and x['property'].startswith(ARRAY+'/') for x in references(pool)):
        raise FormatError('New equipment is absent from the ring reward array')
    small=rewards[SMALL];raw=small.leaves()['0x00471D33/0x35']
    if name not in string_list(Node(raw,0,len(raw))):raise FormatError('Small rewards no longer include the named ring set')
    raw=small.leaves()['0x00471D33/0x33'];small_name=Node(raw,0,len(raw)).string()
    category=rewards[CATEGORY]
    if category.type_id!=0x471d35 or small_name not in string_list(category.fields[0x34]):
        raise FormatError('Reward category does not select the named small set')
    loot=rewards[LOOT];pickup=reward_sets[PICKUP]
    if not any(x['id']==CATEGORY for x in references(loot)) or not any(x['id']==LOOT for x in references(pickup)):
        raise FormatError('Original loot/pickup entry links changed')
    return {'format':'aow4mod-equipment-entry/1','item_id':item_id,
            'path':[{'id':PICKUP,'name':pickup.name,'transition':'resource link'},
                    {'id':LOOT,'name':loot.name,'transition':'resource link'},
                    {'id':CATEGORY,'name':category.name,'transition':'category name '+small_name},
                    {'id':SMALL,'name':small.name,'transition':'subset name '+name},
                    {'id':f'{pool.rid:016X}','name':pool.name,'transition':'equipment reference'}, {'id':item_id}],
            'coverage':'Serialized original reward entry and matching category/subset names. This new set follows the official DLC same-name-set pattern; actual engine union/drop probability, map use, forge, mounting and gameplay are not verified.'}


def filter_pool(data, pool_id, item_id):
    """Retain only the copied item in the copied original equipment set."""
    matches=[]
    def transform(pack,node):
        resource=Resource.parse(node)
        if resource.rid!=int(pool_id,16):return node.raw()
        matches.append(resource.rid)
        if resource.type_id!=0x471d33:raise FormatError('Equipment set template type changed')
        field=resource.fields[0x471d33].mapping()[0x32]
        array=structured.find_node(field,[0x1e]);items=[]
        from aow4mod.core import decode_link
        for child in array.array().values():
            link=decode_link(child)
            if link and link['id']==item_id:items.append(child.raw())
        if len(items)!=1:raise FormatError('Copied reward set must contain the new item exactly once')
        updated=structured.replace(field,[0x1e],editing.replace_array_items(array,items))
        return editing.rewrite_typed(node,{0x471d33:editing.rewrite_map(resource.fields[0x471d33],{0x32:updated})})
    result=editing.transform_records(data,'.rpk',transform)
    if len(matches)!=1:raise FormatError('Equipment set selection missing or ambiguous')
    return result


def retag(source, output, pack, rid, game, cache):
    """One reviewed metadata string; confirm actual native DataStoreTag getter."""
    before=inventory(source);file=source/pack;data=file.read_bytes();selected=rows(file)[rid]
    if selected.type_id!=0x471d25 or selected.fields[0x12c].string()!='LUCKY_RING':
        raise FormatError('Equipment tag template changed')
    tag='CLI_RING_'+rid
    def transform(name,node):
        obj=Resource.parse(node)
        return editing.rewrite_typed(node,{0x12c:authoring.string(tag)}) if obj.rid==int(rid,16) else node.raw()
    updated=editing.transform_records(data,'.rpk',transform)
    with copy_directory(source,output,[game]) as (stage,files):
        if files!=before:raise FormatError('Equipment source changed before tag staging')
        (stage/pack).write_bytes(updated)
        after=rows(stage/pack)[rid]
        a,b=selected.leaves(),after.leaves()
        if [k for k in a.keys()|b.keys() if a.get(k)!=b.get(k)]!=['meta/0x12C']:
            raise FormatError('Equipment retag changed unrelated fields')
        proof=native_inspection.inspect(stage/pack,game,rid,cache_dir=cache)
        actual=[r for r in proof['properties'] if r['name']=='DataStoreTag']
        if (not proof['comparison']['data_equal'] or len(actual)!=1
                or actual[0].get('value_status')!='read' or actual[0].get('value')!=tag):
            raise FormatError('Fresh native reader did not preserve/read the new equipment tag')
    return proof


def run(output,game,index,schema,cache):
    output=Path(output).resolve();game=Path(game).resolve();cache=Path(cache).resolve()
    verified_sources(game)
    original=rows(game/EQUIPMENT);rewards=rows(game/REWARDS);reward_sets=rows(game/REWARD_SETS)
    reward_entry(rewards,reward_sets,rewards[POOL],ITEM)
    native_authoring.create_project(game,'CLI Fortune Ring Example','CLI Toolkit',output,cache)
    plans=output/'plans';plans.mkdir();evidence=output/'evidence';evidence.mkdir()
    def record(name,plan):save_record(plans/(name+'.json'),plan);return plan
    status={'format':'aow4mod-equipment-example/1','state':'in_progress','project':str(output),
            'source_hashes':SOURCES,'in_game_tested':False,'workshop_submitted':False}
    save_record(output/'pipeline.json',status)
    try:
        save_record(evidence/'official-set-pattern.json',{'native_objects':official_set_pattern(game,cache),
            'coverage':'Observed official base/DLC sets share a name and default priority. Not an execution test of equipment-set merging.'})
        plan=record('clone-ring',cloning.make_plan(output/'mod',game/EQUIPMENT,'CLI_Fortune_Ring.rpk',index,
            prefix='CLI Fortune - ',game=game,selected_ids=[ITEM,ICON,PROPERTY,MODIFIER,MAIN_SET]))
        ids={key:value['new_id'] for key,value in plan['id_map'].items()};pack=Path('Packs/CLI_Fortune_Ring.rpk')
        copied=output/'stages/copied';cloning.apply_plan(plan,copied)
        tagged=output/'stages/tagged';tag_proof=retag(copied,tagged,pack,ids[ITEM],game,cache)
        save_record(evidence/'equipment-tag.json',tag_proof)
        # Native setter and an independent reader prove +0.10 = +10 percentage points.
        plan=record('critical-chance',native_edits.make_plan(tagged/pack,ids[MODIFIER],
            [{'name':'Critical Chance Bonus','property':'0x00471D73/0x1','value':0.10}],game,
            source_root=tagged,schema_path=schema,cache_dir=cache))
        modified=output/'stages/modified';native_edits.apply_plan(plan,modified)
        filtered=output/'stages/filtered'
        with copy_directory(modified,filtered,[game]) as (stage,files):
            (stage/pack).write_bytes(filter_pool((modified/pack).read_bytes(),ids[MAIN_SET],ids[ITEM]))
        plan=record('reward-set-name',native_edits.make_plan(filtered/pack,ids[MAIN_SET],
            [{'name':'SetName','property':'0x00471D33/0x33','value':'RING_T1_SET'}],game,
            source_root=filtered,schema_path=schema,cache_dir=cache))
        named=output/'stages/named';native_edits.apply_plan(plan,named)
        prefix='CLI@FORTUNE_RING@'+ids[ITEM]
        keys={'name':prefix+'@NAME','description':prefix+'@DESCRIPTION'}
        values=[]
        for old,name_prop,desc_prop in [(ITEM,'0x00471D18/0x1','0x00471D18/0xF'),
                                        (PROPERTY,'0x00471432/0x2','0x00471432/0x6'),
                                        (MODIFIER,'0x00471C21/0x2','0x00471C21/0x4')]:
            values.extend([{'id':ids[old],'property':name_prop,'key':keys['name']},
                           {'id':ids[old],'property':desc_prop,'key':keys['description']}])
        plan=record('localized-links',localized_links.make_plan(named/pack,values,game,named,cache))
        localized=output/'stages/localized';localized_links.apply_plan(plan,localized)
        # Use literal descriptions. Existing template parameter objects remain
        # untouched; their old values are deliberately not interpolated here.
        po=localization.library().POFile();po.metadata={'Content-Type':'text/plain; charset=UTF-8','Language':'en'}
        po.append(localization.library().POEntry(msgid=keys['name'],msgstr='Fortune Ring'))
        po.append(localization.library().POEntry(msgid=keys['description'],msgstr='Increases critical hit chance by 10 percentage points for melee, ranged and magic attacks.'))
        (localized/'Language/EN').mkdir(parents=True);po.save(str(localized/'Language/EN/source.po'))
        zh=localization.library().POFile();zh.metadata={**po.metadata,'Language':'zh'}
        zh.append(localization.library().POEntry(msgid=keys['name'],msgstr='幸运之戒'))
        zh.append(localization.library().POEntry(msgid=keys['description'],msgstr='近战、远程与魔法攻击的暴击率提高 10 个百分点。'))
        (localized/'Language/ZH').mkdir();zh.save(str(localized/'Language/ZH/source.po'))
        plan=record('language-import',pack_imports.make_plan(localized/pack,add=['CLI_FORTUNE_RING.MO'],source_root=localized,game=game))
        language_bound=output/'stages/language-bound';pack_imports.apply_plan(plan,language_bound)
        final=language_bound
        from aow4mod.core import mod_file,read_acp
        if read_acp(mod_file(final))[1]:raise FormatError('Additive equipment recipe unexpectedly overrides original resources')
        pool=rows(final/pack)[ids[MAIN_SET]]
        priority=native_inspection.inspect(final/pack,game,ids[MAIN_SET],cache_dir=cache)
        matches=[p for p in priority['properties'] if p['name']=='Override Priority']
        if not priority['comparison']['data_equal'] or len(matches)!=1 or matches[0].get('value')!=0:
            raise FormatError('New equipment set does not preserve the original default priority')
        save_record(evidence/'set-native.json',priority)
        if [x['id'] for x in references(pool)]!=[ids[ITEM]]:raise FormatError('New equipment set includes unexpected references')
        entry=reward_entry(rewards,reward_sets,pool,ids[ITEM]);save_record(evidence/'reward-entry.json',entry)
        icon_refs=[r for r in references(rows(final/pack)[ids[ITEM]]) if r['property']=='0x00471D18/0x2']
        if len(icon_refs)!=1 or icon_refs[0]['id']!=ids[ICON]:raise FormatError('New item icon connection differs')
        authoring.select_source(output,'stages/language-bound')
        config=json.loads((output/authoring.CONFIG).read_text(encoding='utf8'))
        (output/'description.txt').write_text(
            'Fortune Ring authoring example: a new ring using original Lucky Ring artwork, '
            'with +10 percentage points critical chance and English/Chinese text. '
            'Adds a default-priority RING_T1_SET following the official same-name-set pattern. '
            'Native serialization is checked; actual set union, drops, forge and gameplay remain unverified. '
            'Original asset diagnostics remain open. Unpublished acceptance candidate.\n',encoding='utf8')
        config['description_file']='description.txt'
        config['localization']=[{'source':f'Language/{lang}/source.po','output':f'Language/{lang}/CLI_Fortune_Ring.mo','require_complete':True} for lang in ('EN','ZH')]
        config['validation'].update(max_resources=20000,native_cache=str(cache))
        save_record(output/authoring.CONFIG,config)
        build=authoring.build_project(output,output/'release',game)
        summary=build.get('native_validation_summary',{})
        if not summary or summary.get('errors') or summary.get('mismatches'):
            raise FormatError('Equipment native build evidence missing or failing: '+json.dumps(summary))
        verified_sources(game)
        status.update(state='built_review_required' if build['release_gate']['review_required'] else 'built',
            id_map=ids,datastore_tag='CLI_RING_'+ids[ITEM],critical_chance_bonus=0.10,
            entry=entry,localization_keys=keys,release=build,native_build_summary=summary,
            coverage='Pinned original ring variant with new IDs/tag, native scalar edit, original art, two compiled catalogs, no original overrides, matching reward-set names/default priority and native build. Engine set union/drop/forge, gameplay and publication remain unverified.')
        save_record(output/'pipeline.json',status);return status
    except Exception as exc:
        status.update(state='failed',error=str(exc));save_record(output/'pipeline.json',status);raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--game',type=Path);p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'))
    p.add_argument('--schema',type=Path,default=Path('.aow4mod/schema.json'));p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'))
    a=p.parse_args()
    try:
        result=run(a.output,find_game(a.game),a.index,a.schema,a.cache)
        print(json.dumps({k:result[k] for k in ('state','project','id_map','native_build_summary','coverage')},ensure_ascii=False,indent=2))
        return int(result['release']['release_gate']['review_required'])
    except (FormatError,OSError,ValueError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
