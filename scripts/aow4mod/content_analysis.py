"""Build-bound gameplay branches, value provenance and shared-definition impact.

Rules describe observed class fields, never resource names or guessed defaults.
Unknown references remain visible and do not become execution edges by inference.
"""
from collections import Counter, deque
from pathlib import Path
import json
import math
import struct

from . import catalog, content_index, content_queries, core, native_inspection, semantics, starting_traits
from .core import FormatError, Node, Resource, decode_link, sha
from .filesystem import file_hash

FORMAT = 'aow4mod-content-explanation/1'
RULESET = 'witch-1.1-gameplay/2'
# Class/field, editor getter name, storage codec, author-facing meaning.
SCALARS = {
    0x473702: [(8, 'Tier', 'i32', 'research_level')],
    0x471EF8: [(2, 'Operation Point Cost', 'i32', 'base_casting_points')],
    0x470276: [(1, 'Duration', 'i32', 'sustained_duration'), (3, 'Is Unit Enchantment', 'bool', 'unit_enchantment')],
    0x47154B: [(1, 'Friendly Fire Type', 'i32', 'target_side')],
    0x47355A: [(3, 'Num Turns', 'i32', 'status_turns'), (6, 'Allow Stacking', 'bool', 'allow_stacking'), (7, 'Max Stacks', 'i32', 'max_stacks')],
    0x471DF6: [(1, 'Disable All', 'bool', 'disable_all_matching_upgrades'), (3, 'Duration', 'i32', 'disable_turns')],
    0x47190D: [(15, 'Strength', 'i32', 'resistance_strength'), (12, 'Ignore Target Resistances', 'bool', 'ignore_target_resistances')],
    0x4715C8: [(1, 'Positive Filter', 'i32', 'dispel_positive_filter'), (6, 'Num Things To Dispel', 'i32', 'dispel_count')],
    0x471C23: [(1, 'Multiplier', 'f32', 'damage_multiplier')],
    0x471CA4: [(1, 'Accuracy Modifier', 'f32', 'accuracy_against_target_bonus')],
    0x471DB0: [(1, 'Tactical Move Points', 'i32', 'tactical_move_points'), (2, 'Strategic Move Points', 'i32', 'strategic_move_points')],
    0x471DB2: [(3, 'Only If Faster', 'bool', 'only_if_faster')],
    0x4714EB: [(8, 'Block Enemy Units', 'bool', 'blocks_enemy_units')],
}
ROLES = {
    (0x473708, 9): 'initial_property', (0x473708, 0x1D): 'tome_tier',
    (0x473702, 0xD): 'subskill', (0x471DDA, 4): 'research_grant',
    (0x471CE4, 3): 'unlock_requirement', (0x471CE4, 0x20): 'cost_template',
    (0x4714CD, 3): 'effect', (0x4715BA, 0x19): 'status',
    (0x47355A, 1): 'status_property', (0x47143C, 2): 'property_group_member',
    (0x47222B, 4): 'player_property', (0x471CD0, 5): 'unit_property',
    (0x471CD0, 0x26): 'unit_upkeep', (0x471C26, 1): 'ability_modifier',
    (0x471DB2, 1): 'movement_settings', (0x47190D, 0x15): 'success_effect',
    (0x47190D, 0x16): 'failure_effect', (0x47190D, 0x17): 'success_caster_effect',
    (0x471DF6, 5): 'disable_tooltip_property',
    (0x47512B, 3): 'empire_skill_side_property',
}
SPELL_TYPES = (0x47024A, 0x470276, 0x471E36)
COST_FIELDS = {0x471EF8: [(6, 'base_cost'), (7, 'spell_upkeep')], 0x476E8F: [(1, 'per_unit_upkeep')]}


def fingerprint():
    return sha(b''.join(Path(__file__).with_name(name).read_bytes() for name in
                        ('content_analysis.py','starting_traits.py','content_snapshots.py','semantics.py','reflection.py',
                         'core.py','content_index.py','native_inspection.py','native.py','native_worker.py')))


def path_parts(path):
    return [int(k, 16) for k in path.split('/') if k != 'meta']


def role(resource, reference):
    starting = starting_traits.reference_role(resource, reference)
    if starting:
        return starting
    path = reference['property']; keys = path_parts(path)
    if path.startswith('meta/'):
        return 'unit_upgrade' if resource.type_id == 0x476E3E and keys == [2] else None
    if len(keys) >= 4 and keys[:4] == [0x471CE4, 0xD, 1, 3]:
        return 'targeter'
    return ROLES.get(tuple(keys[:2]))


def decode_scalar(raw, codec):
    if codec == 'bool':
        if raw not in (b'\0', b'\1'):raise FormatError('Boolean field has unsupported storage')
        return bool(raw[0])
    if len(raw) != 4:raise FormatError('Scalar field has unsupported storage width')
    value = struct.unpack('<f' if codec == 'f32' else '<i', raw)[0]
    if codec == 'f32' and not math.isfinite(value):raise FormatError('Nonfinite gameplay scalar')
    return value


def scalar_facts(resource, native_report=None):
    values = native_report.get('properties', []) if native_report else []
    facts = []
    for cid, rows in SCALARS.items():
        if cid not in resource.fields:continue
        fields = resource.fields[cid].mapping()
        for field, getter, codec, meaning in rows:
            raw = fields[field].raw() if field in fields else None
            fact = {'kind': meaning, 'property': f'0x{cid:08X}/0x{field:X}', 'codec': codec,
                    'serialized': raw is not None, 'value': None, 'value_source': 'omitted_unread'}
            if raw is not None:
                try:fact.update(value=decode_scalar(raw, codec), value_source='serialized')
                except FormatError as exc:fact.update(value_source='unsupported_storage', error=str(exc))
            matches = [v for v in values if v['name'] == getter and v.get('value_status') == 'read']
            if len(matches) == 1:
                fact['serialized_value'] = fact['value']
                fact.update(value=matches[0]['value'], value_source='loaded_native_getter', getter=getter)
                if raw is not None and fact['serialized_value'] != fact['value']:
                    fact['serialized_getter_disagreement'] = True
            elif len(matches) > 1:fact['native_status'] = 'ambiguous_getter'
            elif native_report:fact['native_status'] = 'getter_unavailable'
            value = fact['value']
            if value is not None and not fact.get('serialized_getter_disagreement'):
                if meaning == 'damage_multiplier':fact['derived'] = {'bonus_percent': (value - 1) * 100, 'formula': '(Multiplier - 1) * 100'}
                elif meaning == 'resistance_strength':fact['derived'] = {'base_chance_percent': value * 10, 'formula': 'Strength * 10', 'final_chance_evaluated': False}
                elif meaning == 'research_level':fact['note'] = 'Internal research level; not tome tier or final knowledge price.'
                elif meaning == 'target_side':fact['label'] = {0: 'enemies', 1: 'friendlies', 2: 'everyone'}.get(value, 'uninterpreted_enum')
            facts.append(fact)
    return facts


def cost_rows(resource):
    rows, issues = [], []
    for cid, fields in COST_FIELDS.items():
        if cid not in resource.fields:continue
        owner = resource.fields[cid].mapping()
        for key, meaning in fields:
            prefix = f'0x{cid:08X}/0x{key:X}'
            if key not in owner:
                issues.append({'property': prefix, 'issue': 'cost_block_omitted_unread'});continue
            try:
                block = owner[key].mapping()
                if set(block) != {0x473EA3}:raise FormatError('Unrecognized resource-amount block')
                fields = block[0x473EA3].mapping()
                if set(fields) != {1}:raise FormatError('Unrecognized resource-amount envelope')
                entries = fields[1].mapping()
                count = entries[0xA].uint(4) if 0xA in entries else 0
                if not 0 <= count <= 128 or set(entries) != ({0xA, *range(0xB, 0xB + count)} if count else set()):
                    raise FormatError('Unrecognized cost row count/layout')
                for i in range(count):
                    body = entries[0xB+i].mapping()
                    if set(body) != {0x473EA2}:raise FormatError('Unrecognized resource-amount value class')
                    value = body[0x473EA2].mapping()
                    if not {1} <= set(value) <= {1, 2} or set(value[1].mapping()) != {2}:
                        raise FormatError('Unrecognized resource-amount value fields')
                    rid = value[1].mapping()[2].uint(8)
                    if not rid:raise FormatError('Null cost resource identity')
                    rows.append({'kind': meaning, 'ordinal': i, 'resource_id': f'{rid:016X}',
                                 'amount': decode_scalar(value[2].raw(), 'i32') if 2 in value else None,
                                 'value_source': 'serialized' if 2 in value else 'omitted_unread',
                                 'property': prefix+f'/0x473EA3/0x1/0x{0xB+i:X}/0x473EA2',
                                 'final_economic_cost_evaluated': False})
            except FormatError as exc:issues.append({'property': prefix, 'issue': str(exc)})
    return rows, issues


def filters(resource):
    result = []
    # TUnitPropertyFilter has only required/forbidden lists. Do not invent a
    # third slot or apply another filter class's omission/default rules.
    for path, node in content_index.resource_nodes(resource, []):
        if not path.upper().endswith('/0X4719A7'):continue
        try:
            fields = node.mapping()
            groups = {}
            for key, name in ((1, 'required_all'), (2, 'forbidden_any')):
                if key not in fields:groups[name] = {'status': 'omitted_unread'};continue
                items = fields[key].mapping()[0x1E].array()
                refs = [decode_link(n) for n in items.values()]
                if any(r is None for r in refs):raise FormatError('Unrecognized filter reference')
                groups[name] = {'status': 'serialized', 'references': refs}
            result.append({'property': path, 'logic': 'all(required_all) AND NOT any(forbidden_any)', 'groups': groups,
                           'runtime_eligibility_evaluated': False})
        except (FormatError, KeyError):
            result.append({'property': path, 'status': 'unsupported_filter_layout'})
    # Reviewed flat requisite-filter family: serializer 1486210 maps offsets
    # 18/88/F8 to fields 3/4/5; property controller 14792B0 names their OR/NOT/AND
    # semantics. Restricted to the verified upgrade/ability owner fields.
    for cid,key in ((0x471CD0,0x25),(0x471C26,4)):
        if cid not in resource.fields or key not in resource.fields[cid].mapping():continue
        prefix=f'0x{cid:08X}/0x{key:X}'
        try:
            fields=resource.fields[cid].mapping()[key].mapping()
            if set(fields)!={3,4,5}:raise FormatError('Unrecognized flat requisite filter')
            groups={}
            for field,name in ((3,'required_any'),(4,'forbidden_any'),(5,'required_all')):
                refs=[decode_link(n) for n in fields[field].mapping()[0x1E].array().values()]
                if any(r is None for r in refs):raise FormatError('Unrecognized requisite reference')
                groups[name]={'status':'serialized','references':refs}
            result.append({'property':prefix,'groups':groups,'logic':'all(required_all) AND NOT any(forbidden_any) AND (empty(required_any) OR any(required_any))',
                           'runtime_eligibility_evaluated':False})
        except (FormatError,KeyError):result.append({'property':prefix,'status':'unsupported_filter_layout'})
    # Keep tag filters explicit without guessing their match mode from strings.
    for cid, key in ((0x471DF6, 4), (0x471CD0, 0x2A)):
        if cid in resource.fields and key in resource.fields[cid].mapping():
            n = resource.fields[cid].mapping()[key]
            terms = []
            for _, child in content_index.walk(n, '', issues=[]):
                try:text = child.string()
                except FormatError:continue
                if text and len(text) <= 256:terms.append(text)
            result.append({'property': f'0x{cid:08X}/0x{key:X}', 'serialized_tag_terms': terms,
                           'raw_sha256': sha(n.raw()), 'match_mode': 'not_interpreted'})
    return result


def parameter_value(expression, candidates, nodes):
    """Candidate value only: never simulate dictionary/namespace precedence."""
    if len(candidates)!=1:return {'status':'missing_tag' if not candidates else 'ambiguous_tag'}
    target=nodes.get(candidates[0]['row_id'])
    if target is None:return {'status':'target_outside_selected_graph'}
    parts=expression.strip('{}').split('>')
    if len(parts)==3 and parts[1].casefold()=='area' and parts[2]=='Radius':
        matches=[p for p in target.get('area',{}).get('properties',[]) if p['name']=='Radius' and p.get('value_status')=='read']
        return {'status':'loaded_candidate_not_rendered','value':matches[0]['value'],'source_node':target['node']} if len(matches)==1 else {'status':'nested_value_unread'}
    if len(parts)!=2:return {'status':'namespace_or_expression_not_interpreted'}
    fields={'ResolvedMultiplier':('damage_multiplier','bonus_percent'), 'Multiplier':('damage_multiplier',None),
            'ResolvedStrength':('resistance_strength','base_chance_percent'), 'NumTurns':('status_turns',None),
            'Duration':('disable_turns',None),'NumThingsToDispel':('dispel_count',None),
            'ResolvedAccuracyModifier':('accuracy_against_target_bonus','accuracy_percent')}
    if parts[1] not in fields:return {'status':'unsupported_display_expression'}
    kind,derived=fields[parts[1]]
    facts=[f for f in target['facts'] if f['kind']==kind and f['value'] is not None and not f.get('serialized_getter_disagreement')]
    if len(facts)!=1:return {'status':'value_unread_or_ambiguous'}
    fact=facts[0]
    if derived=='accuracy_percent':value=fact['value']*100
    elif derived:value=fact.get('derived',{}).get(derived)
    else:value=fact['value']
    if value is None:return {'status':'unsupported_display_expression'}
    return {'status':'derived_candidate_not_rendered' if derived else 'value_candidate_not_rendered',
            'value':value,'value_source':fact['value_source'],'source_node':target['node'],'source_property':fact['property']}


def check_parameters(db,nodes,inputs):
    for item in nodes.values():
        for text in item['text_candidates']:
            tokens=list(dict.fromkeys('{'+token+'}' for row in text['translations']['items'] for token in content_index.placeholders(row['text'])))
            declared={p['placeholder'] for p in text['parameters']}
            checks=[]
            for token in tokens:
                bindings=[p for p in text['parameters'] if p['placeholder']==token]
                check={'expression':token,'explicit_binding_count':len(bindings),'rendered':False}
                replacements=[p['replacement'] for p in bindings if p['replacement'] is not None]
                if replacements:
                    check.update(status='explicit_replacement' if len(set(replacements))==1 else 'ambiguous_replacement',replacements=replacements)
                elif '>' in token:
                    tag=token.strip('{}').split('>')[0]
                    rows=[dict(r) for r in db.execute("SELECT r.row_id,r.id,r.file,r.name,f.sha256 FROM resource_terms t JOIN resources r ON r.row_id=t.source JOIN files f ON f.path=r.file WHERE t.kind='tag' AND CASEFOLD(t.value)=? ORDER BY r.file,r.id LIMIT 101",(tag.casefold(),))]
                    inputs.update({r['file']:r['sha256'] for r in rows})
                    check.update(tag=tag,candidates=rows,**parameter_value(token,rows,nodes))
                    if len(rows)==101:check.update(status='candidate_limit',candidates_truncated=True)
                else:check['status']='global_or_uninterpreted_expression'
                checks.append(check)
            text['parameter_checks']={'expressions':checks,'unused_explicit_parameters':sorted(declared-set(tokens)),
                                      'tokens_without_explicit_binding':sorted(set(tokens)-declared),
                                      'dictionary_and_hyperlink_runtime_resolution':'not_evaluated'}


def explain(index, rid, filename=None, locale=None, *, with_native=False, depth=12, max_nodes=150, max_edges=600,
            cache_dir=Path('.aow4mod/native'), timeout=30):
    if not 0 <= depth <= 20 or not 1 <= max_nodes <= 500 or not 1 <= max_edges <= 2000:
        raise FormatError('Explain limits: depth 0..20, nodes 1..500, edges 1..2000')
    _, root, game = catalog.load_resource(index, rid, filename)
    db = catalog.open_existing(index)
    try:
        catalog.check_parser(db)
        locale = content_queries.require_locale(db, locale)
        if db.execute('SELECT 1 FROM files WHERE links_indexed=0 AND error IS NULL LIMIT 1').fetchone():
            raise FormatError('Gameplay analysis requires index --links')
        status = semantics.schema_status(game)
        if not status.get('gameplay_rules_verified',status['verified']):
            raise FormatError('Gameplay field rules require the reviewed ResourceED build; raw describe/trace remain available')
        files, inputs, nodes, edges, boundaries, native_reports = {}, {}, {}, [], [], []
        queue = deque([(root['row_id'], 0, 'root')])
        contexts = {}

        def candidates(target):
            count = db.execute('SELECT count(*) FROM resources WHERE id=?', (target,)).fetchone()[0]
            rows = db.execute('SELECT row_id FROM resources WHERE id=? ORDER BY file,ordinal LIMIT ?', (target, max_nodes+1)).fetchall()
            return count, [r[0] for r in rows]

        def connect(source, target, prop, meaning, level, forced=None):
            if len(edges) >= max_edges:
                boundaries.append({'reason': 'edge_limit', 'node': source});return
            count, rows = (1, [forced]) if forced is not None else candidates(target)
            edges.append({'from_node': source, 'target_id': target, 'to_nodes': rows,
                          'property': prop, 'role': meaning, 'candidate_count': count,
                          'resolution': 'missing' if count == 0 else 'unique' if count == 1 else 'ambiguous'})
            if level >= depth:
                if rows:boundaries.append({'reason': 'depth_limit', 'node': source, 'property': prop})
                return
            for row in rows:queue.append((row, level+1, meaning))

        def unlock_spells(source,target,level):
            spells=db.execute("SELECT r.row_id,r.id,l.property FROM resources r JOIN links l ON l.source=r.row_id WHERE l.target=? AND (l.property LIKE '0x00471CE4/0x3/%') ORDER BY r.id,r.file,l.rowid",(target,)).fetchall()
            for spell in spells:connect(source,spell['id'],'reverse:'+spell['property'],'unlocks_spell',level,spell['row_id'])

        while queue:
            row_id, level, context = queue.popleft()
            if row_id in nodes:
                # A definition can first be reached as an initial property and
                # later as a research grant. Deduplicate its body, not the new
                # role that makes reverse spell-unlock discovery applicable.
                if context=='research_grant' and context not in contexts[row_id]:
                    unlock_spells(row_id,nodes[row_id]['id'],level)
                contexts.setdefault(row_id, set()).add(context)
                continue
            if len(nodes) >= max_nodes:
                boundaries.append({'reason': 'node_limit', 'candidate': row_id});continue
            row = dict(db.execute('SELECT r.*,f.sha256 FROM resources r JOIN files f ON r.file=f.path WHERE r.row_id=?', (row_id,)).fetchone())
            path = row['file']
            if path not in files:
                data = Path(path).read_bytes()
                if sha(data) != row['sha256']:raise FormatError('Indexed gameplay source is stale')
                files[path] = data;inputs[path] = row['sha256']
            r = Resource.parse(Node(files[path], row['start'], row['end']))
            if f'{r.rid:016X}' != row['id'] or r.type_id != row['type_id']:raise FormatError('Gameplay source identity mismatch')
            contexts[row_id] = {context}
            item = {'node': row_id, 'id': row['id'], 'name': r.name, 'type_id': f'0x{r.type_id:08X}',
                    'file': path, 'sha256': row['sha256'], 'depth': level, 'facts': [], 'conditions': filters(r),
                    'unclassified_references': [], 'text_candidates': [], 'text_status': 'candidate_dictionary_only'}
            nodes[row_id] = item
            item['starting_bonus'] = starting_traits.analyze(r)
            texts = content_index.text_links(r, [])
            item['text_candidate_coverage'] = {'total': len(texts), 'limit': 16, 'truncated': len(texts) > 16}
            for text in texts[:16]:
                found = content_queries.translations(db, text['key'], locale, limit=5)
                for translation in found['items']:inputs[translation['file']] = translation['sha256']
                item['text_candidates'].append({'key': text['key'], 'property': text['property'], 'parameters': text['parameters'],
                                               'translations': found, 'rendered_tooltip': False})
            native_report = None
            if with_native and any(cid in r.fields for cid in SCALARS):
                try:
                    native_report = native_inspection.inspect(path, game, row['id'], row['package'], cache_dir=cache_dir, timeout=timeout)
                    item['native_witness'] = len(native_reports);native_reports.append(native_report)
                    if not native_report['comparison']['data_equal']:item['native_roundtrip_review_required'] = True
                    item['native_uninterpreted_properties'] = [p for p in native_report['properties'] if p.get('value_status') != 'not_applicable']
                except FormatError as exc:item['native_error'] = str(exc)
            item['facts'] = scalar_facts(r, native_report)
            if r.type_id == 0x471573:
                if with_native:
                    try:
                        area = native_inspection.inspect(path, game, row['id'], row['package'], cache_dir=cache_dir, timeout=timeout, object_path=[{'name': 'Area'}])
                        item['area'] = {'source': area['source_of_values'], 'properties': area['properties'], 'witness': len(native_reports)}
                        native_reports.append(area)
                    except FormatError as exc:item['area'] = {'status': 'getter_failed', 'error': str(exc)}
                else:item['area'] = {'status': 'not_read', 'note': 'Use --native to read the loaded Area; no constructor default substituted.'}
            costs, cost_issues = cost_rows(r)
            item['costs'] = costs;item['cost_issues'] = cost_issues
            for cost in costs:connect(row_id, cost['resource_id'], cost['property']+'/resource', cost['kind']+'_resource', level)
            refs = core.references(r)
            for ref in refs:
                meaning = role(r, ref)
                if meaning:connect(row_id, ref['id'], ref['property'], meaning, level)
                else:item['unclassified_references'].append(ref)
            if r.type_id == 0x473708:
                members = db.execute("SELECT DISTINCT r.row_id,r.id FROM resources r JOIN links l ON l.source=r.row_id WHERE r.type_id=0x473702 AND l.target=? AND l.property='0x00473702/0x6' ORDER BY r.id,r.file", (row['id'],)).fetchall()
                item['research_member_count'] = len(members)
                for member in members:connect(row_id, member['id'], 'reverse:0x00473702/0x6', 'tome_research', level, member['row_id'])
                others = db.execute("SELECT r.id,r.name FROM resources r WHERE r.file=? AND r.type_id=0x473702 AND NOT EXISTS(SELECT 1 FROM links l WHERE l.source=r.row_id AND l.target=? AND l.property='0x00473702/0x6')", (path, row['id'])).fetchall()
                item['same_file_unattached_research'] = [dict(x) for x in others]
            if context == 'research_grant':
                unlock_spells(row_id,row['id'],level)
            if 0x12C in r.fields:
                tag = r.fields[0x12C].string();item['data_store_tag'] = tag
                rows = db.execute("SELECT r.id,r.file,r.name,f.sha256 FROM resource_terms t JOIN resources r ON t.source=r.row_id JOIN files f ON f.path=r.file WHERE t.kind='tag' AND t.value=? ORDER BY r.file,r.id LIMIT 101", (tag,)).fetchall()
                if len(rows)>1:
                    item['tag_candidates'] = [dict(x) for x in rows]
                    item['tag_resolution'] = 'ambiguous_do_not_choose_first'
                    inputs.update({x['file']:x['sha256'] for x in rows})
        for key,item in nodes.items():item['via_roles'] = sorted(contexts[key])
        check_parameters(db,nodes,inputs)
        for edge in edges:
            edge['included_nodes'] = [n for n in edge['to_nodes'] if n in nodes]
            edge['candidates_truncated'] = len(edge['included_nodes']) < edge['candidate_count']
            edge['target_type_check'] = starting_traits.target_type_check(edge, nodes)
        catalog.verify_files(inputs)
        repeated = Counter((e['from_node'],e['target_id'],e['role']) for e in edges)
        return {'format': FORMAT, 'ruleset': status.get('gameplay_ruleset',RULESET), 'rules_fingerprint':fingerprint(), 'rules_tool_sha256': status['observed_tool_sha256'],
                'game_build': core.game_build(game), 'root_node': root['row_id'], 'nodes': list(nodes.values()), 'edges': edges,
                'repeated_effects': [{'from_node': a, 'target_id': b, 'role': c, 'occurrences': n} for (a,b,c),n in repeated.items() if n>1],
                'boundaries': boundaries, 'traversal_truncated': bool(boundaries), 'inputs': inputs,
                'native_witnesses': native_reports, 'limits': {'depth':depth,'max_nodes':max_nodes,'max_edges':max_edges},
                'starting_bonus_summary':starting_traits.summarize(list(nodes.values()),edges),
                'coverage': {'scope':'reviewed_field_rules_and_indexed_candidates', 'native_requested':with_native,
                             'index':content_queries.coverage(db),'unclassified_links':sum(len(n['unclassified_references']) for n in nodes.values()),
                             'complete_effect_semantics':False, 'runtime_dictionary_selected':False, 'final_costs_evaluated':False, 'gameplay_tested':False}}
    finally:db.close()


def impact(index, rid, filename=None, depth=4, max_nodes=100, max_edges=300):
    graph = content_queries.trace(index, rid, filename, 'incoming', depth, max_nodes, max_edges)
    root = next(n for n in graph['nodes'] if n['row_id']==graph['root_node'])
    uses = []
    by_id = {n['row_id']:n for n in graph['nodes']}
    for edge in graph['edges']:
        if edge['from_node'] != graph['root_node']:continue
        for node_id in edge['to_nodes']:
            n = by_id[node_id]
            uses.append({'source_file':n['file'],'source_id':n['id'],'reference_path':edge['property'],'target_id':root['id'],
                         'definition_sha256':n['sha256'],'name':n['name']})
    return {'format':'aow4mod-shared-impact/1','definition':root,'direct_retarget_sites':uses,'graph':graph,
            'choices':{'edit_shared_definition':'Potentially affects every recognized ID reference in this snapshot; runtime winner is not selected.',
                       'clone_and_retarget':'Create a fresh resource identity, then reconnect explicitly selected sites. External references/localization/dependencies still require review.'},
            'complete_impact_verified':False,'note':'Duplicate definitions, unknown encodings, scripts and traversal limits prevent a claim of exhaustive gameplay impact.'}
