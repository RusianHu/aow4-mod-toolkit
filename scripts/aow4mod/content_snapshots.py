"""Frozen, bounded gameplay interpretation snapshots with source body witnesses.

Comparison never treats an unchanged interpreted subset as unchanged gameplay.
Native process artifacts bind getter values; no native code runs during replay.
"""
from pathlib import Path
import json

from . import content_analysis, editing, native, native_objects, starting_traits, content_index
from .core import FormatError,Node,Resource,sha,references
from .filesystem import file_hash

FORMAT='aow4mod-content-snapshot/1'


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')


def native_evidence(report):
    run=Path(report['run'])
    material={name:(run/name).read_bytes().hex() for name in ('request.json','result.json','process.json')}
    material['output_body']=Path(report['roundtrip_artifact']['file']).read_bytes().hex()
    material['nested_bodies']=[Path(row['file']).read_bytes().hex() for row in report.get('object_path',[])]
    return material


def verify_native(report,material,resource):
    request_bytes=bytes.fromhex(material['request.json'])
    request=json.loads(request_bytes);result=json.loads(bytes.fromhex(material['result.json']));process=json.loads(bytes.fromhex(material['process.json']))
    if request['operation']!='object-roundtrip' or result.get('error') or process.get('exit_code')!=0 or process.get('timed_out'):
        raise FormatError('Native snapshot witness did not complete successfully')
    if result['request_sha256']!=sha(request_bytes) or process['request_sha256']!=sha(request_bytes) or result['request_id']!=request['request_id']:
        raise FormatError('Native snapshot request/receipt binding differs')
    params=request['params'];payload=native.validate_payload('object-roundtrip',result['payload'],params)
    body=resource.node.raw()[4:]
    if params['body_hex']!=body.hex() or params['body_sha256']!=sha(body) or params['cid']!=f'{resource.type_id:08X}':
        raise FormatError('Native snapshot getter input differs from the frozen resource')
    saved=bytes.fromhex(material['output_body'])
    if sha(saved)!=report['roundtrip_artifact']['sha256'] or sha(saved)!=payload['output_sha256']:
        raise FormatError('Native snapshot serialized output differs')
    if native_objects.compare_bodies(body,saved)!=report['comparison']:
        raise FormatError('Native snapshot comparison differs')
    if native.public_binding(request['binding'])!=report['binding']:
        raise FormatError('Native snapshot profile binding differs')
    expected=[]
    for field in payload['properties']:
        row=dict(field)
        for old,new in [('default','value'),('default_label','value_label'),('default_status','value_status')]:
            if old in row:row[new]=row.pop(old)
        expected.append(row)
    if expected!=report['properties']:
        raise FormatError('Native snapshot getter values disagree with worker evidence')
    nested=payload.get('nested_objects',[])
    if nested!=report.get('object_path',[]) or len(nested)!=len(material['nested_bodies']):
        raise FormatError('Native snapshot nested coverage differs')
    if any(sha(bytes.fromhex(body))!=row['sha256'] for row,body in zip(nested,material['nested_bodies'])):
        raise FormatError('Native snapshot nested body differs')


def definition_key(node,game):
    path=Path(node['file'])
    try:identity='game:'+path.relative_to(Path(game)).as_posix()
    except ValueError:identity='external:'+path.as_posix()
    return identity.casefold()+'#'+node['id']


def projection(report,keys):
    nodes={}
    for item in report['nodes']:
        texts=[]
        for text in item['text_candidates']:
            texts.append({'key':text['key'],'property':text['property'],'parameters':text['parameters'],
                          'translations':[{'locale':t['locale'],'text':t['text'],'context':t.get('context'),'plural_index':t.get('plural_index')} for t in text['translations']['items']]})
        area=item.get('area')
        if area:
            area={k:v for k,v in area.items() if k!='witness'}
        nodes[keys[item['node']]]={'id':item['id'],'name':item['name'],'type_id':item['type_id'],
            'facts':item['facts'],'conditions':item['conditions'],'costs':item['costs'],'cost_issues':item['cost_issues'],
            'native_error':item.get('native_error'),'native_roundtrip_review_required':item.get('native_roundtrip_review_required',False),
            'area':area,'texts':texts,'tag':item.get('data_store_tag'),'tag_resolution':item.get('tag_resolution'),
            'starting_bonus':item.get('starting_bonus'),'text_candidate_coverage':item.get('text_candidate_coverage'),
            'same_file_unattached_research':item.get('same_file_unattached_research',[]),
            'unclassified_references':[{k:v for k,v in r.items() if k!='offset'} for r in item['unclassified_references']]}
    def type_projection(edge):
        check=edge.get('target_type_check')
        if check is None:return None
        return {**check,'mismatches':[{**m,'node':keys[m['node']]} for m in check['mismatches']]}
    edges=[{'from':keys[e['from_node']],'to':[keys[n] for n in e['included_nodes']],
            'target_id':e['target_id'],'property':e['property'],'role':e['role'],'resolution':e['resolution'],
            'candidate_count':e['candidate_count'],'truncated':e['candidates_truncated'],
            'target_type_check':type_projection(e)} for e in report['edges']]
    return {'nodes':nodes,'edges':sorted(edges,key=lambda e:canonical(e)), 'coverage':report['coverage'],
            'traversal_truncated':report['traversal_truncated'],'limits':report['limits']}


def validate(snapshot):
    if not isinstance(snapshot,dict) or snapshot.get('format')!=FORMAT:raise FormatError('Unsupported content snapshot')
    if len(snapshot.get('explanation_hex',''))>128*1024*1024:raise FormatError('Frozen explanation exceeds 64 MiB')
    raw=bytes.fromhex(snapshot['explanation_hex'])
    if sha(raw)!=snapshot['explanation_sha256']:raise FormatError('Content explanation hash differs')
    report=json.loads(raw)
    if report.get('format')!=content_analysis.FORMAT or report.get('rules_fingerprint')!=content_analysis.fingerprint():
        raise FormatError('Gameplay analysis rules changed; re-evaluate both snapshots before comparing')
    if len(snapshot['resources'])!=len(report['nodes']):raise FormatError('Frozen resource coverage differs')
    if not 1<=len(snapshot['resources'])<=500:raise FormatError('Frozen resource count is outside bounds')
    keys={};resources={};by_node={};total=0
    for item,witness in zip(report['nodes'],snapshot['resources']):
        total+=len(witness['body_hex'])
        if total>256*1024*1024:raise FormatError('Frozen resource bodies exceed 128 MiB')
        data=bytes.fromhex(witness['body_hex']);r=Resource.parse(Node(data,0,len(data)))
        if (f'{r.rid:016X}',f'0x{r.type_id:08X}')!=(item['id'],item['type_id']) or witness['node']!=item['node'] or sha(data)!=witness['sha256']:
            raise FormatError('Frozen resource identity or hash differs')
        key=definition_key(item,report['game_build']['path'])
        if key in keys.values():raise FormatError('Duplicate frozen definition identity')
        keys[item['node']]=key;resources[(item['file'],item['id'])]=r;by_node[item['node']]=r
    if len(report['native_witnesses'])!=len(snapshot['native_evidence']):raise FormatError('Frozen native coverage differs')
    for native_report,material in zip(report['native_witnesses'],snapshot['native_evidence']):
        r=resources[(native_report['source']['file'],native_report['source']['resource_id'])]
        verify_native(native_report,material,r)
    for item in report['nodes']:
        r=resources[(item['file'],item['id'])]
        nr=report['native_witnesses'][item['native_witness']] if 'native_witness' in item else None
        if nr and (nr['source']['file'],nr['source']['resource_id'])!=(item['file'],item['id']):
            raise FormatError('Node native witness names a different resource')
        if content_analysis.scalar_facts(r,nr)!=item['facts'] or content_analysis.filters(r)!=item['conditions']:
            raise FormatError('Gameplay facts disagree with frozen resource/getter evidence')
        if starting_traits.analyze(r)!=item.get('starting_bonus'):
            raise FormatError('Starting-bonus declarations disagree with frozen resource evidence')
        texts=content_index.text_links(r,[])
        if item.get('text_candidate_coverage')!={'total':len(texts),'limit':16,'truncated':len(texts)>16}:
            raise FormatError('Text candidate coverage disagrees with frozen resource evidence')
        selected_texts=[{k:t[k] for k in ('key','property','parameters')} for t in item['text_candidates']]
        if selected_texts!=texts[:16]:
            raise FormatError('Text keys/parameter bindings disagree with frozen resource evidence')
        costs,issues=content_analysis.cost_rows(r)
        if (costs,issues)!=(item['costs'],item['cost_issues']):raise FormatError('Cost facts disagree with frozen resource evidence')
        if item.get('area',{}).get('properties') is not None:
            area=report['native_witnesses'][item['area']['witness']]
            if (area['source']['file'],area['source']['resource_id'])!=(item['file'],item['id']) or area['properties']!=item['area']['properties'] or [p['selector']['name'] for p in area['object_path']]!=['Area']:
                raise FormatError('Nested Area facts disagree with their native witness')
    for edge in report['edges']:
        owner=by_node[edge['from_node']]
        if edge['property'].startswith('reverse:'):
            if edge['role'] not in ('unlocks_spell','tome_research'):raise FormatError('Unrecognized reverse gameplay role')
            prop=edge['property'][8:]
            for target in edge['included_nodes']:
                r=by_node[target]
                if f'{r.rid:016X}'!=edge['target_id'] or not any(x['property']==prop and x['id']==f'{owner.rid:016X}' for x in references(r)):
                    raise FormatError('Reverse gameplay edge disagrees with source references')
        elif edge['role'].endswith('_resource'):
            costs,_=content_analysis.cost_rows(owner)
            if not any(row['resource_id']==edge['target_id'] and row['property']+'/resource'==edge['property'] and row['kind']+'_resource'==edge['role'] for row in costs):
                raise FormatError('Cost edge disagrees with source value objects')
        elif not any(ref['id']==edge['target_id'] and ref['property']==edge['property'] and content_analysis.role(owner,ref)==edge['role'] for ref in references(owner)):
            raise FormatError('Gameplay edge disagrees with source references')
        if any(f'{by_node[n].rid:016X}'!=edge['target_id'] for n in edge['included_nodes']):
            raise FormatError('Gameplay edge resolves to a different resource identity')
        expected_check=starting_traits.target_type_check(edge,{n['node']:n for n in report['nodes']})
        if edge.get('target_type_check')!=expected_check:
            raise FormatError('Starting-bonus target type check disagrees with frozen evidence')
    if report.get('starting_bonus_summary')!=starting_traits.summarize(report['nodes'],report['edges']):
        raise FormatError('Starting-bonus summary disagrees with frozen declarations')
    result=projection(report,keys)
    if result!=snapshot['projection']:raise FormatError('Content snapshot projection differs from evidence')
    return report,keys


def capture(path):
    if Path(path).stat().st_size>64*1024*1024:raise FormatError('Explanation exceeds 64 MiB')
    raw=Path(path).read_bytes();report=json.loads(raw)
    if report.get('format')!=content_analysis.FORMAT:raise FormatError('Expected an explain report')
    for file,digest in report['inputs'].items():
        if file_hash(file)!=digest:raise FormatError('An explanation source changed before snapshot capture')
    cache={};witnesses=[];keys={}
    for item in report['nodes']:
        file=item['file']
        if file not in cache:cache[file]=editing.resources_in_file(Path(file))
        matches=[r for pack,r in cache[file] if f'{r.rid:016X}'==item['id']]
        if len(matches)!=1:raise FormatError('Snapshot resource is missing or ambiguous')
        data=matches[0].node.raw();keys[item['node']]=definition_key(item,report['game_build']['path'])
        witnesses.append({'node':item['node'],'body_hex':data.hex(),'sha256':sha(data)})
    result={'format':FORMAT,'explanation_sha256':sha(raw),'explanation_hex':raw.hex(),
            'resources':witnesses,'native_evidence':[native_evidence(r) for r in report['native_witnesses']],
            'projection':projection(report,keys),'scope':'Bounded interpreted content plus full selected resource bodies. Not a complete gameplay snapshot.'}
    validate(result)
    for file,digest in report['inputs'].items():
        if file_hash(file)!=digest:raise FormatError('An explanation source changed during snapshot capture')
    return result


def compare(before,after):
    a,ak=validate(before);b,bk=validate(after)
    if a['ruleset']!=b['ruleset'] or a['rules_tool_sha256']!=b['rules_tool_sha256']:
        raise FormatError('Snapshots use different reviewed gameplay rules/build profiles')
    if before['projection']['coverage']['native_requested']!=after['projection']['coverage']['native_requested']:
        raise FormatError('Compare snapshots with the same native-value evidence mode')
    if before['projection']['limits']!=after['projection']['limits']:
        raise FormatError('Compare snapshots captured with the same traversal limits')
    av,bv=before['projection']['nodes'],after['projection']['nodes']
    changed=[]
    for key in sorted(av.keys()&bv.keys()):
        fields=[field for field in av[key].keys()|bv[key].keys() if av[key].get(field)!=bv[key].get(field)]
        if fields:changed.append({'definition':key,'changed_sections':sorted(fields),
                                  'before':{f:av[key].get(f) for f in fields},'after':{f:bv[key].get(f) for f in fields}})
    ah={ak[r['node']]:r['sha256'] for r in before['resources']};bh={bk[r['node']]:r['sha256'] for r in after['resources']}
    raw_changes=[k for k in sorted(ah.keys()&bh.keys()) if ah[k]!=bh[k]]
    return {'format':'aow4mod-content-comparison/1','before_build':a['game_build'],'after_build':b['game_build'],
            'added':sorted(bv.keys()-av.keys()),'removed':sorted(av.keys()-bv.keys()),'changed':changed,
            'execution_edges_changed':before['projection']['edges']!=after['projection']['edges'],
            'coverage_changed':before['projection']['coverage']!=after['projection']['coverage'],
            'resource_bytes_changed':raw_changes,
            'changes_outside_interpreted_projection':[k for k in raw_changes if not any(c['definition']==k for c in changed)],
            'partial_scope':True,'before_truncated':a['traversal_truncated'],'after_truncated':b['traversal_truncated'],
            'note':'Unchanged interpreted facts do not prove unchanged gameplay; raw and unknown changes remain visible. No live inputs or native code are needed for this comparison.'}
