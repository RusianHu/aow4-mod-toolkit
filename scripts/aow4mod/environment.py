"""Bounded dependency graph for an explicit Mod set, installed content and assets."""
from collections import Counter,defaultdict,deque
from pathlib import Path

from . import assets,catalog,core,dlc,projects,import_graph,overlay_imports
from .core import FormatError,Node,Resource,read_rpk,references,sha
from .filesystem import file_hash


def actionable(issue):
    return not issue.get('advisory_only') and issue.get('confidence') != 'path_string_candidate'


def explain(report, primary=None):
    """Attribute captured findings without dismissing them or inferring runtime use."""
    projects = report['projects']; game = Path(report['game']).resolve()
    selected = [p for p in projects if primary is not None and Path(p['path']).resolve() == Path(primary).resolve()]
    if primary is not None and len(selected) != 1:
        raise FormatError('Finding attribution requires one captured primary project')
    groups = {}; rows = []
    for index, issue in enumerate(report['issues']):
        source = issue.get('source', issue.get('project'))
        source = source.get('file') if isinstance(source, dict) else source
        paths = issue.get('declared_by') or ([source] if isinstance(source, str) else [])
        owners = []
        for value in paths:
            path = Path(value).resolve()
            matches = [p for p in projects if path == Path(p['path']).resolve() or Path(p['path']).resolve() in path.parents]
            if len(matches) == 1:
                p = matches[0]
                owners.append({'kind': 'selected_mod', 'path': p['path'], 'name': p['name'], 'package_id': p.get('package_id')})
            elif len(matches) > 1:
                owners.append({'kind': 'ambiguous_selected_roots'})
            elif path == game or game in path.parents:
                # Older captures can name the base game pack for a Mod-added
                # declaration without retaining its declaring ACP. Do not
                # misattribute that requirement to the original game.
                missing_provenance = issue['issue'].startswith('declared_import_') and issue.get('inherited_from_original') is False
                owners.append({'kind': 'mod_declaration_owner_unavailable' if missing_provenance else 'installed_game'})
            else:
                owners.append({'kind': 'unknown_source'})
        distinct = {tuple(sorted(o.items())): o for o in owners}
        owner = next(iter(distinct.values())) if len(distinct) == 1 else {'kind': 'multiple_or_unknown_sources'}
        inherited = issue.get('inherited_from_original', issue.get('inherited_reference'))
        if inherited is None and owner['kind'] == 'installed_game' and issue['issue'].startswith('resource_'):
            inherited = True
        row = {'issue_index': index, 'issue': issue['issue'], 'owner': owner,
               'actionable': actionable(issue), 'inherited_reference': inherited,
               'runtime_harmlessness_verified': False}
        rows.append(row)
        key = tuple(sorted(owner.items()))
        if key not in groups:
            groups[key] = {'owner': owner, 'issues': 0, 'actionable_issues': 0,
                                  'inherited_reference_issues': 0, 'issue_types': {}}
        group = groups[key]; group['issues'] += 1; group['actionable_issues'] += int(row['actionable'])
        group['inherited_reference_issues'] += int(inherited is True)
        group['issue_types'][issue['issue']] = group['issue_types'].get(issue['issue'], 0) + 1
    primary_rows = [r for r in rows if selected and r['owner'].get('path') == selected[0]['path']]
    return {'format': 'aow4mod-environment-attribution/1', 'groups': list(groups.values()), 'rows': rows,
            'primary': selected[0] if selected else None,
            'primary_summary': {'issues': len(primary_rows), 'actionable_issues': sum(r['actionable'] for r in primary_rows),
                                'inherited_reference_issues': sum(r['inherited_reference'] is True for r in primary_rows)} if selected else None,
            'review_required': report['summary']['review_required'],
            'scope': 'Captured source/declaration ownership and exact original-reference equality. No finding is cleared; reachability, causal impact and runtime use are not inferred.'}


class GameIndex:
    def __init__(self,index,game):
        self.db=catalog.open_existing(index);self.game=Path(game).resolve();self.data={};self.inputs={};self.packs={}
        try:
            metadata=dict(self.db.execute('SELECT key,value FROM meta'))
            if metadata.get('game')!=str(self.game):raise FormatError('Index belongs to a different game directory')
            catalog.check_parser(self.db)
            self.pack_files=defaultdict(list)
            for row in self.db.execute("SELECT * FROM files WHERE origin='game' AND kind='rpk' AND error IS NULL"):
                self.pack_files[Path(row['package'].replace('\\','/')).name.casefold()].append(dict(row))
            self.failures=[dict(r) for r in self.db.execute("SELECT path,error FROM files WHERE origin='game' AND error IS NOT NULL")]
        except BaseException:self.db.close();raise

    def rows(self,rid,origin='game'):
        return [dict(r) for r in self.db.execute('''SELECT r.*,f.sha256 FROM resources r
                JOIN files f ON r.file=f.path WHERE r.id=? AND r.origin=?''',(rid,origin))]

    def bytes(self,path,expected):
        if path not in self.data:
            raw=Path(path).read_bytes()
            if sha(raw)!=expected:raise FormatError('Index is stale for '+path)
            self.data[path]=raw;self.inputs[path]=expected
        elif self.inputs[path]!=expected:raise FormatError('Inconsistent indexed file hashes')
        return self.data[path]

    def definition(self,row):
        raw=self.bytes(row['file'],row['sha256']);r=Resource.parse(Node(raw,row['start'],row['end']))
        if f'{r.rid:016X}'!=row['id'] or r.type_id!=row['type_id']:raise FormatError('Indexed identity mismatch')
        return {'resource':r,'file':row['file'],'pack':row['package'],'origin':'game','kind':'rpk','operation':None}

    def pack(self,row):
        path=row['path'];self.bytes(path,row['sha256'])
        if path not in self.packs:
            info,_=read_rpk(Path(path))
            info['import_declarations']=overlay_imports.rpk_declarations(self.data[path])
            if info['sha256']!=row['sha256']:raise FormatError('Pack changed during dependency inspection')
            self.packs[path]=info
        return self.packs[path]


def analyze(mods,game,index,disabled_dlc=(),steam=None,depth=2,max_resources=2000):
    if type(depth) is not int or not 0<=depth<=12:raise FormatError('Dependency depth must be between 0 and 12')
    if type(max_resources) is not int or not 1<=max_resources<=100000:raise FormatError('Resource limit must be 1..100000')
    game=Path(game).resolve();installed=GameIndex(index,game)
    try:return _analyze(mods,game,installed,disabled_dlc,steam,depth,max_resources)
    finally:installed.db.close()


def _analyze(mods,game,installed,disabled,steam,depth,max_resources):
    state=dlc.inventory(game,disabled,steam);issues=list(state['issues']);project_list=[]
    mod_by_id=defaultdict(list);mod_packs=defaultdict(list);roots=[];inputs={};identities=defaultdict(list);overlay_declarations=[]
    for ordinal,mod in enumerate(mods):
        try:project=projects.inspect_project(mod)
        except (OSError,FormatError) as exc:
            issues.append({'issue':'selected_project_unreadable','project':str(mod),'error':str(exc)});continue
        root=Path(project['meta']['path']).parent
        roots.append(root);issues.extend({'project':str(root),**i} for i in project['issues'])
        project_list.append({'path':str(root),'name':project['meta']['name'],'position':ordinal,
                             'package_id':project['meta'].get('package_id'),'definitions':len(project['definitions'])})
        if project['meta'].get('package_id'):identities[project['meta']['package_id']].append(str(root))
        inputs[project['meta']['path']]=project['meta']['sha256']
        overlay_declarations.extend({'source':project['meta']['path'],**record} for record in project['overlay_imports'])
        for item in project['definitions']:
            d={**item,'origin':'mod','project':str(root),'position':ordinal}
            mod_by_id[f'{d["resource"].rid:016X}'].append(d)
        for pack in project['packs']:
            mod_packs[Path(pack['name'].replace('\\','/')).name.casefold()].append(pack)
            inputs[pack['path']]=pack['sha256']
    for key,owners in identities.items():
        if len(owners)>1:issues.append({'issue':'duplicate_package_identity','package_id':key,'projects':owners})
    if installed.failures:issues.append({'issue':'incomplete_game_index','files':installed.failures})
    asset_index=assets.Inventory(game,roots);used_groups={};asset_hashes={}

    def location(file,reason='resource'):
        s=dlc.file_state(file,game,state)
        if 'group' in s:
            if s['group'] not in used_groups:used_groups[s['group']]={**s,'reasons':[]}
            if reason not in used_groups[s['group']]['reasons']:used_groups[s['group']]['reasons'].append(reason)
        return {'file':str(file),**{k:s[k] for k in ('group','state','steam_app_id') if k in s}}

    def check_availability(locations,kind,source,target,inherited=False):
        if locations and all(t['state'] in dlc.UNAVAILABLE for t in locations):
            issues.append({'issue':kind+'_unavailable_in_configuration','source':source,'target':target,
                           'states':sorted({x['state'] for x in locations}),'inherited_reference':inherited})

    pack_queue=deque();overlay_sources=defaultdict(set)
    for owners in mod_by_id.values():
        for d in owners:
            if d['kind']=='override':pack_queue.append((d['file'],d['pack'],'override_target'))
    for packs in mod_packs.values():
        for p in packs:
            for item in p['import_declarations']:
                pack_queue.append((p['path'],item['name'],'optional_import' if item['optional'] else 'declared_import'))
    for record in overlay_declarations:
        if not record['required'] and not record['optional']:continue
        key=Path(record['pack'].replace('\\','/')).name.casefold()
        candidates=mod_packs.get(key,[]) or [installed.pack(row) for row in installed.pack_files.get(key,[])]
        if len({row['sha256'] for row in candidates})!=1:
            issues.append({'issue':'overlay_import_source_missing_or_ambiguous','source':record['source'],'pack':record['pack']})
            continue
        for candidate in candidates:
            for optional in (False,True):
                for name in record['optional' if optional else 'required']:
                    pack_queue.append((candidate['path'],name,'mod_optional_import' if optional else 'mod_required_import'))
                    overlay_sources[(candidate['path'],Path(name.replace('\\','/')).name.casefold(),optional)].add(record['source'])
    pack_edges=[];pack_seen=set();expanded=set()
    while pack_queue:
        source,name,reason=pack_queue.popleft();key=Path(name.replace('\\','/')).name.casefold()
        optional=reason in ('optional_import','mod_optional_import');mod_added=reason.startswith('mod_')
        if (source,key,optional,mod_added) in pack_seen:continue
        pack_seen.add((source,key,optional,mod_added))
        is_pack=key.endswith('.rpk')
        candidates=mod_packs.get(key,[]) if is_pack else []
        if is_pack and not candidates:candidates=[installed.pack(row) for row in installed.pack_files.get(key,[])]
        if is_pack:locs=[location(p['path'],reason) for p in candidates]
        else:
            hits,_=asset_index.resolve({'kind':'file','value':name})
            locs=[location(h['file'],'declared_import') for h in hits]
        original_import=Path(source).is_relative_to(game) and not mod_added
        edge={'source':source,'pack':name,'kind':'rpk' if is_pack else 'asset_library','inherited_from_original':original_import,
              'optional':optional,'mod_added':mod_added,
              'targets':locs,'status':assets.classify_matches(locs,asset_hashes)}
        if mod_added:edge['declared_by']=sorted(overlay_sources[(source,key,optional)])
        pack_edges.append(edge)
        if edge['status'] in ('missing','ambiguous'):issues.append({'issue':'declared_import_'+edge['status'],'source':source,'pack':name,
                                      'candidates':len(locs),'inherited_from_original':original_import,
                                      **({'declared_by':edge['declared_by']} if mod_added else {}),
                                      'advisory_only':(optional and edge['status']=='missing') or original_import})
        if not original_import and not optional:check_availability(locs,'pack',source,name)
        for candidate in candidates:
            if candidate['path'] in expanded:continue
            expanded.add(candidate['path'])
            if len(expanded)>2000:raise FormatError('Pack dependency traversal exceeds 2000 packs')
            for item in candidate['import_declarations']:
                pack_queue.append((candidate['path'],item['name'],'optional_import' if item['optional'] else 'declared_import'))

    import_state=import_graph.analyze(pack_edges,{**inputs,**installed.inputs,**asset_hashes})
    issues.extend({'issue':'pack_import_cycle',**cycle} for cycle in import_state['cycles'])

    def resolve(rid):
        if rid in mod_by_id:return mod_by_id[rid],'selected_mod_definitions'
        found=[installed.definition(row) for row in installed.rows(rid)]
        return found,'original_definitions'

    def original_definition(d):
        if d['origin']=='game':return d['resource']
        if d['kind']!='override':return None
        rows=[r for r in installed.rows(f'{d["resource"].rid:016X}') if r['package'].casefold()==d['pack'].casefold()]
        if len(rows)!=1:return None
        original=installed.definition(rows[0])['resource']
        return original if original.type_id==d['resource'].type_id else None

    queue=deque((d,0) for owners in mod_by_id.values() for d in owners)
    visited=set();resource_edges=[];asset_edges=[];visited_items=[];depth_boundary=0;limit_reached=False
    while queue:
        d,level=queue.popleft();r=d['resource'];identity=(d['file'],r.rid)
        if identity in visited:continue
        if len(visited)>=max_resources:limit_reached=True;break
        visited.add(identity);source={'id':f'{r.rid:016X}','name':r.name,'file':d['file']}
        visited_items.append({**source,'depth':level,'origin':d['origin']})
        original=original_definition(d)
        original_refs={(ref['property'],ref['id']) for ref in references(original)} if original else set()
        for ref in references(r):
            candidates,resolution=resolve(ref['id']);locs=[{**location(c['file']),'type_id':f'0x{c["resource"].type_id:08X}'} for c in candidates]
            status='found' if len(candidates)==1 else 'ambiguous' if candidates else 'missing'
            if len(candidates)>1 and len({sha(c['resource'].node.raw()) for c in candidates})==1:status='equivalent_definitions'
            edge={'source':source,'property':ref['property'],'id':ref['id'],'name':ref['name'],
                  'depth':level,'resolution':resolution,'status':status,'targets':locs,
                  'inherited_from_original':(ref['property'],ref['id']) in original_refs}
            if not candidates:
                inactive=[row for row in installed.rows(ref['id'],'mod') if row['file'] not in inputs]
                if inactive:edge.update(status='only_in_unselected_mods',unselected_files=sorted({x['file'] for x in inactive}))
            resource_edges.append(edge)
            if status in ('ambiguous','missing'):issues.append({'issue':'resource_'+edge['status'],'source':source,'id':ref['id'],'property':ref['property'],
                                                              'inherited_from_original':edge['inherited_from_original']})
            check_availability(locs,'resource',source,ref['id'],d['origin']=='game')
            if level<depth:queue.extend((candidate,level+1) for candidate in candidates)
            elif any((c['file'],c['resource'].rid) not in visited for c in candidates):depth_boundary+=1
        inherited={(ref['property'],ref['value']) for ref in assets.references(original)[0]} if original else set()
        asset_refs,truncated=assets.references(r)
        if truncated:issues.append({'issue':'asset_scan_depth_limit','source':source})
        for ref in asset_refs:
            hits,method=asset_index.resolve(ref);locs=[location(h['file'],'asset_reference') for h in hits]
            is_inherited=(ref['property'],ref['value']) in inherited
            status=assets.classify_matches(locs,asset_hashes)
            asset_edges.append({'source':source,**ref,'depth':level,'resolution':method,'status':status,
                                'inherited_from_original':is_inherited,'availability_scope':'loose_file_inventory','targets':locs})
            if status in ('missing','ambiguous'):issues.append({'issue':'asset_'+status,'source':source,'value':ref['value'],
                'property':ref['property'],'inherited_from_original':is_inherited,'confidence':ref['confidence'],
                'availability_scope':'loose_file_inventory','runtime_use_verified':False})
            check_availability(locs,'asset',source,ref['value'],is_inherited)
    if limit_reached:issues.append({'issue':'resource_traversal_limit','limit':max_resources})
    inputs.update(installed.inputs);inputs.update(state['metadata_inputs']);inputs.update(asset_hashes)
    for path,expected in inputs.items():
        if file_hash(path)!=expected:raise FormatError('Input changed during environment analysis: '+path)
    # Inheriting a broken asset path does not prove that the engine ignores it.
    # Only broad original import declarations and untyped filename candidates are advisory.
    actionable_issues=[i for i in issues if actionable(i)]
    report={'format':'aow4mod-environment/1','game':str(game),'game_build':core.game_build(game),'projects':project_list,
            'summary':{'projects':len(project_list),'resources_visited':len(visited),'resource_references':len(resource_edges),
                       'pack_dependencies':len(pack_edges),'asset_references':len(asset_edges),'issues':len(issues),
                       'pack_import_cycles':len(import_state['cycles']),
                       'unresolved_pack_imports':import_state['unresolved_declarations'],
                       'review_required':bool(actionable_issues),'actionable_issues':len(actionable_issues),
                       'issue_types':dict(sorted(Counter(i['issue'] for i in issues).items())),
                       'conclusion':'not_evaluated' if not visited else 'review_required' if actionable_issues else 'no_actionable_issue_in_captured_scope'},
            'dlc':{'checked_utc':state['checked_utc'],'steam_checked':state['steam_checked'],
                   'disabled_dlc':state['disabled_dlc'],'referenced_groups':list(used_groups.values())},
            'capture':{'depth':depth,'max_resources':max_resources,'limit_reached':limit_reached,'depth_boundary_references':depth_boundary},
            'resources':visited_items,'resource_references':resource_edges,'pack_dependencies':pack_edges,
            'pack_import_graph':import_state,
            'asset_references':asset_edges,'issues':issues,'input_hashes':inputs,
            'coverage':'Explicit selected Mod definitions and bounded recognized-reference graph. No last-Mod-wins assumption. Loose asset files only; archive/virtual assets, CLB members, runtime mount order, opaque links and gameplay remain unverified.'}
    report['attribution']=explain(report)
    return report
