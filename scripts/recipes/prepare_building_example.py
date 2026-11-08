"""Create an unpublished regular city building with an explicit research gate."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aow4mod import authoring,cloning,linking,localized_links,localization,native_authoring,native_income
from aow4mod import pack_imports,reference_arrays,reference_initialization
from aow4mod.core import FormatError,mod_file,read_acp,read_rpk,references
from aow4mod.discovery import find_game
from aow4mod.filesystem import file_hash,save_record

CITY='Content/Title/Packs/Strategic/CityUpgrades_Definitions.rpk'
FAITH='Content/Title/Packs/Research/Tomes/Tome_Faith.rpk'
CRYO='Content/Title/Packs/Research/Tomes/Tome_Cryomancy.rpk'
SOURCES={'DotNetBindings.dll':'58b7210aa024d26cc0c148083d4aca7a322082a75ec3740a26dc2c4b9d4a8eff',
         CITY:'6fcc2f096c729be82d2e483472e218dcefbca441ef62fe3722c4f8c7ae99453d',
         FAITH:'108f8a0d5cd8fb0304d8b3113d5a1cc96e3ee99b7c61eaae4c0c2158fc9a3683',
         CRYO:'c46183e2efe92f69a8f7f9f3d72ac2730327046fae980870840bb74621491272'}
LIBRARY='000003E600000379';INCOME='000003E60000037C';ICON='000003E500001870';MARKER='000003E5000018F8'
ABBEY='0000042A0000083B';UNLOCK='0000042A00000839';GATE='000003E300006083'
RESEARCH='0000041B000006C3';SUBSKILL='0000041B000006C5';KNOWLEDGE='000003D400000363'
PACK=Path('Packs/CLI_Frost_Archive.rpk')


def rows(path):return {f'{r.rid:016X}':r for r in read_rpk(path)[1]}


def verified_sources(game):
    for name,digest in SOURCES.items():
        if file_hash(game/name)!=digest:raise FormatError('Building source build changed: '+name)


def selected_links(resource,prefix):
    return [r['id'] for r in references(resource) if r['property'].startswith(prefix)]


def verify_entry(game,path,building,unlock,income,marker,icon):
    cryo=rows(game/CRYO);candidate=rows(path);root=candidate[building];settings=candidate[unlock]
    if SUBSKILL not in selected_links(cryo[RESEARCH],'0x00473702/0xD/') or GATE not in selected_links(cryo[SUBSKILL],'0x00471DDA/0x4/'):
        raise FormatError('Research no longer grants the reviewed city property')
    if cryo[GATE].type_id!=0x47149e or root.type_id!=0x4714ad or settings.type_id!=0x47780c:
        raise FormatError('Building/research/unlock types differ')
    expected={'0x004714AD/0x6':[icon],'0x004714AD/0x30/':[income,marker],
              '0x004714AD/0x4D/':[GATE],'0x004714AD/0x36':['000003AD00001F52'],
              '0x004714AD/0x2B/':['000003E100001FBB'],'0x004714AD/0x8/':['000003E60000037D']}
    for prefix,ids in expected.items():
        if selected_links(root,prefix)!=ids:raise FormatError('Building entry/effects/costs differ at '+prefix)
    if (selected_links(settings,'0x0047780C/0x1')!=[GATE]
            or selected_links(settings,'0x0047780C/0x2/')!=[building]):raise FormatError('Building unlock record differs')
    data=native_income.flat_rows(candidate[income].node.raw()[4:])
    if len(data)!=1 or native_income.value_fields(data[0])!=(KNOWLEDGE,15):raise FormatError('Building knowledge income differs')
    if read_acp(mod_file(path.parent.parent))[1]:raise FormatError('Building example unexpectedly overrides originals')
    return {'building_id':building,'unlock_id':unlock,'research':RESEARCH,'subskill':SUBSKILL,'city_property':GATE,
            'knowledge_flat':15,'cost_data':'000003AD00001F52','ordinary_city_building':True,
            'declared_entry_verified':True,'engine_production_query_executed':False,'gameplay_tested':False}


def run(output,game,index,schema,cache):
    output=Path(output).resolve();game=Path(game).resolve();cache=Path(cache).resolve();verified_sources(game)
    native_authoring.create_project(game,'CLI Frost Archive Example','CLI Toolkit',output,cache)
    (output/'plans').mkdir();(output/'evidence').mkdir()
    state={'format':'aow4mod-building-example/1','state':'in_progress','project':str(output),'source_hashes':SOURCES,
           'gameplay_tested':False,'workshop_submitted':False}
    def record(name,plan):save_record(output/'plans'/(name+'.json'),plan);return plan
    def stage(name):return output/'stages'/name
    try:
        p=record('copy-support',cloning.make_plan(output/'mod',game/CITY,PACK.name,index,prefix='CLI Frost Archive - ',
            game=game,selected_ids=[INCOME,ICON,MARKER],with_dependencies=True))
        income=p['id_map'][INCOME]['new_id'];icon=p['id_map'][ICON]['new_id'];marker=p['id_map'][MARKER]['new_id']
        cloning.apply_plan(p,stage('copied'));source=stage('copied')
        p=record('knowledge-income',native_income.make_plan(source/PACK,income,KNOWLEDGE,15,game,index,source_root=source,cache_dir=cache))
        native_income.apply_plan(p,stage('income'));source=stage('income')
        tag='CLI_FROST_ARCHIVE_'+marker
        values=[{'name':'DataStoreTag','value':tag}, {'name':'Upgrade Name','value':tag},
                {'name':'Allow Random Starting Upgrade','value':False},{'name':'Allow From Free Building Pickup','value':False}]
        p=record('new-building',native_authoring.make_resource_plan(source,game,'004714AD','CLI Frost Archive',values=values,
            into_pack=PACK,schema_path=schema,index=index,cache_dir=cache,initialize_empty_script=True))
        building=p['identity']['id'];native_authoring.apply_resource_plan(p,stage('building'));source=stage('building')
        p=record('new-unlock',native_authoring.make_resource_plan(source,game,'0047780C','CLI Frost Archive - Unlock Settings',
            into_pack=PACK,schema_path=schema,index=index,cache_dir=cache))
        unlock=p['identity']['id'];native_authoring.apply_resource_plan(p,stage('unlock'));source=stage('unlock')
        def direct(name,rid,prop,template,template_id,target,target_file=None):
            nonlocal source
            p=record(name,reference_initialization.make_plan(source/PACK,rid,prop,game/template,template_id,target,index,
                target_file=target_file,game=game,source_root=source))
            reference_initialization.apply_plan(p,stage(name));source=stage(name)
        def append(name,rid,prop,template,template_id,template_path,target,target_file=None):
            nonlocal source
            p=record(name,reference_arrays.make_plan(source/PACK,rid,prop+'/0x1E',game/template,template_id,template_path,target,index,
                target_file=target_file,game=game,source_root=source))
            reference_arrays.apply_plan(p,stage(name));source=stage(name)
        direct('icon',building,'0x004714AD/0x6',CITY,LIBRARY,icon,source/PACK)
        direct('costs',building,'0x004714AD/0x36',CITY,LIBRARY,'000003AD00001F52')
        direct('ai-priority',building,'0x004714AD/0x32',CITY,LIBRARY,'0000039A0000101A')
        direct('overlay-icon',building,'0x004714AD/0x5D',CITY,LIBRARY,'0000045E0000073B')
        append('knowledge-effect',building,'0x004714AD/0x30',CITY,LIBRARY,'0x004714AD/0x30/0x1E/0x1/0x0',income,source/PACK)
        append('identity-property',building,'0x004714AD/0x30',CITY,LIBRARY,'0x004714AD/0x30/0x1E/0x1/0x1',marker,source/PACK)
        append('research-category',building,'0x004714AD/0x8',CITY,LIBRARY,'0x004714AD/0x8/0x1E/0x1/0x0','000003E60000037D')
        append('city-requisite',building,'0x004714AD/0x2B',CITY,LIBRARY,'0x004714AD/0x2B/0x1E/0x1/0x0','000003E100001FBB')
        append('research-requirement',building,'0x004714AD/0x4D',FAITH,ABBEY,'0x004714AD/0x4D/0x1E/0x1/0x0',GATE)
        direct('unlock-property',unlock,'0x0047780C/0x1',FAITH,UNLOCK,GATE)
        append('unlock-building',unlock,'0x0047780C/0x2',FAITH,UNLOCK,'0x0047780C/0x2/0x1E/0x1/0x0',building,source/PACK)
        keys={key:'CLI@FROST_ARCHIVE@'+building+'@'+key.upper() for key in ('name','description')}
        p=record('localized-text',localized_links.make_plan(source/PACK,
            [{'id':building,'property':prop,'key':keys[key],'initialize':True} for key,prop in [('name','0x004714AD/0x5'),('description','0x004714AD/0x9')]],game,source,cache))
        localized_links.apply_plan(p,stage('localized'));source=stage('localized')
        for lang,texts in [('EN',('Frost Archive','An archive of frozen lore. Provides 15 Knowledge after the White Witch research.')),('ZH',('霜寒档案馆','收藏冰霜知识的档案馆。完成白女巫研究后可建造，提供15知识收入。'))]:
            folder=source/'Language'/lang;folder.mkdir(parents=True)
            po=localization.library().POFile();po.metadata={'Content-Type':'text/plain; charset=UTF-8','Language':lang.lower()}
            for key,text in zip(('name','description'),texts):po.append(localization.library().POEntry(msgid=keys[key],msgstr=text))
            po.save(str(folder/'source.po'))
        p=record('language-import',pack_imports.make_plan(source/PACK,add=['CLI_FROST_ARCHIVE.MO'],source_root=source,game=game))
        pack_imports.apply_plan(p,stage('final'));source=stage('final')
        entry=verify_entry(game,source/PACK,building,unlock,income,marker,icon);save_record(output/'evidence/entry.json',entry)
        authoring.select_source(output,'stages/final')
        (output/'description.txt').write_text('Frost Archive: a new regular city building with 15 flat Knowledge, original tier-one building cost data and an explicit White Witch research gate. '
            'Independent building, icon, property, income and unlock definitions. Empty callbacks initialized by official native loading; no copied Library callbacks, boost or original overrides. '
            'Native data and declared entry checked; production-list execution and gameplay untested. Unpublished candidate.\n',encoding='utf-8')
        config=json.loads((output/authoring.CONFIG).read_text(encoding='utf-8'));config['description_file']='description.txt'
        config['localization']=[{'source':f'Language/{lang}/source.po','output':f'Language/{lang}/CLI_Frost_Archive.mo','require_complete':True} for lang in ('EN','ZH')]
        config['validation'].update(native_cache=str(cache),max_resources=20000);save_record(output/authoring.CONFIG,config)
        build=authoring.build_project(output,output/'release',game);summary=build.get('native_validation_summary',{})
        if not summary or summary.get('errors') or summary.get('mismatches'):raise FormatError('Building native build failed: '+json.dumps(summary))
        verified_sources(game);state.update(state='built_review_required' if build['release_gate']['review_required'] else 'built',
            building_id=building,unlock_id=unlock,income_id=income,icon_id=icon,marker_id=marker,datastore_tag=tag,entry=entry,localization_keys=keys,
            native_build_summary=summary,release=build)
        save_record(output/'pipeline.json',state);return state
    except Exception as exc:
        state.update(state='failed',error=str(exc));save_record(output/'pipeline.json',state);raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--game',type=Path)
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'));p.add_argument('--schema',type=Path,default=Path('.aow4mod/schema.json'))
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));a=p.parse_args()
    try:
        result=run(a.output,find_game(a.game),a.index,a.schema,a.cache)
        print(json.dumps({k:result[k] for k in ('state','building_id','entry','native_build_summary')},ensure_ascii=False,indent=2))
        return int(result['release']['release_gate']['review_required'])
    except (FormatError,OSError,ValueError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
