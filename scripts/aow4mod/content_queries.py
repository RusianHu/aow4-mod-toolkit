"""Bounded author-facing discovery over an installed-file snapshot."""
from collections import deque
from pathlib import Path

from . import catalog, content_index, core, semantics
from .core import FormatError

FIELDS = ('any', 'name', 'id', 'key', 'tag', 'text')
SELECT = ('SELECT r.row_id,r.id,r.type_id,r.name,r.package,r.origin,r.kind,r.file,r.operation,f.sha256 '
          'FROM resources r JOIN files f ON f.path=r.file ')
ORDER = ' ORDER BY r.name COLLATE NOCASE,r.id,r.file,r.row_id '


def condition(column, query, exact=False):
    if exact:
        return f'CASEFOLD({column}) = ?', [query.casefold()]
    value = query.casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f"CASEFOLD({column}) LIKE ? ESCAPE '\\'", ['%' + value + '%']


def public_row(row):
    row = dict(row)
    row['type_id'] = f"0x{row['type_id']:08X}"
    return row


def coverage(db):
    return {'scope': 'indexed_file_snapshot',
            'locales': [r[0] for r in db.execute('SELECT DISTINCT locale FROM text_files ORDER BY locale')],
            'failed_resource_files': db.execute('SELECT count(*) FROM files WHERE error IS NOT NULL').fetchone()[0],
            'failed_text_files': db.execute('SELECT count(*) FROM text_files WHERE error IS NOT NULL').fetchone()[0],
            'text_search_status': 'indexed' if db.execute('SELECT 1 FROM text_files LIMIT 1').fetchone() else 'not_indexed',
            'text_scan_issues': db.execute('SELECT count(*) FROM text_scan_issues').fetchone()[0],
            'runtime_dictionary_selected': False, 'complete_reference_graph': False,
            'note': 'Candidate text matches and recognized links; not effective playset precedence, reachability, or gameplay. '
                    'Returned evidence files are hash checked; counts and absent matches describe the index snapshot.'}


def require_locale(db, locale):
    if locale is None:
        return None
    locale = content_index.locale_name(locale)
    if not db.execute('SELECT 1 FROM text_files WHERE locale=? LIMIT 1', (locale,)).fetchone():
        raise FormatError(f'Language {locale} is not indexed; run index --locale {locale} or --catalog {locale}=path.mo')
    return locale


def translations(db, key, locale=None, limit=20):
    sql = ('SELECT e.key,e.context,e.plural_index,e.text,t.path AS file,t.sha256,t.locale,t.origin '
           'FROM text_entries e JOIN text_files t ON t.path=e.file WHERE e.key=?')
    args = [key]
    if locale:
        sql += ' AND t.locale=?'
        args.append(locale)
    total = db.execute('SELECT count(*) FROM (' + sql + ')', args).fetchone()[0]
    rows = [dict(r) for r in db.execute(sql + ' ORDER BY t.locale,t.path,e.ordinal,e.plural_index LIMIT ?', args + [limit])]
    catalog.verify_files({r['file']: r['sha256'] for r in rows})
    return {'items': rows, 'total': total, 'truncated': total > limit, 'runtime_selection': 'not_evaluated'}


def match_evidence(db, row, query, field, locale, exact):
    matches = []
    def matches_value(value):
        return value is not None and (query.casefold() == value.casefold() if exact else query.casefold() in value.casefold())
    for name in ('name', 'id'):
        if field in ('any', name) and matches_value(row[name]):
            matches.append({'kind': name, 'value': row[name]})
    if field in ('any', 'key', 'tag'):
        pred, values = condition('value', query, exact)
        sql = 'SELECT kind,property,value FROM resource_terms WHERE source=? AND ' + pred
        args = [row['row_id']] + values
        if field != 'any':
            sql += ' AND kind=?'
            args.append(field)
        for term in db.execute(sql + ' ORDER BY kind,property LIMIT 21', args):
            matches.append({'kind': term['kind'], 'property': term['property'], 'value': term['value']})
    if field in ('any', 'text'):
        pred, args = condition('e.text', query, exact)
        sql = ('SELECT u.property,e.key,e.text,e.context,e.plural_index,t.locale,t.path AS file,t.sha256 '
               'FROM resource_terms u JOIN text_entries e ON e.key=u.value JOIN text_files t ON t.path=e.file '
               "WHERE u.source=? AND u.kind='key' AND " + pred)
        values = [row['row_id']] + args
        if locale:
            sql += ' AND t.locale=?'
            values.append(locale)
        for entry in db.execute(sql + ' ORDER BY u.property,t.locale,t.path,e.ordinal,e.plural_index LIMIT 21', values):
            item = dict(entry)
            item['text_length'] = len(item['text'])
            item['text'] = item['text'][:500]
            matches.append({'kind': 'text', **item})
    catalog.verify_files({m['file']: m['sha256'] for m in matches if 'file' in m})
    return {'items': matches[:20], 'truncated': len(matches) > 20}


def search(path, query='', rid=None, type_id=None, origin=None, limit=20, offset=0,
           field='any', locale=None, exact=False):
    catalog.check_page(limit, offset)
    if field not in FIELDS:
        raise FormatError('Unsupported resource search field')
    db = catalog.open_existing(path)
    try:
        catalog.check_parser(db)
        locale = require_locale(db, locale)
        where, args = [], []
        if query:
            choices = []
            for name in ('name', 'id'):
                if field in ('any', name):
                    pred, values = condition('r.' + name, query, exact)
                    choices.append(pred)
                    args.extend(values)
            for kind in ('key', 'tag'):
                if field in ('any', kind):
                    pred, values = condition('u.value', query, exact)
                    choices.append("EXISTS(SELECT 1 FROM resource_terms u WHERE u.source=r.row_id AND u.kind=? AND " + pred + ')')
                    args.extend([kind] + values)
            if field in ('any', 'text'):
                pred, values = condition('e.text', query, exact)
                clause = ("EXISTS(SELECT 1 FROM resource_terms u JOIN text_entries e ON e.key=u.value "
                          "JOIN text_files t ON t.path=e.file WHERE u.source=r.row_id AND u.kind='key' AND " + pred)
                args.extend(values)
                if locale:
                    clause += ' AND t.locale=?'
                    args.append(locale)
                choices.append(clause + ')')
            where.append('(' + ' OR '.join(choices) + ')')
        if rid is not None:
            where.append('r.id=?')
            args.append(f'{int(rid, 16):016X}')
        if type_id is not None:
            where.append('r.type_id=?')
            args.append(int(type_id, 16))
        if origin:
            where.append('r.origin=?')
            args.append(origin)
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        total = db.execute('SELECT count(*) FROM resources r' + clause, args).fetchone()[0]
        rows = [public_row(r) for r in db.execute(SELECT + clause + ORDER + ' LIMIT ? OFFSET ?', args + [limit, offset])]
        catalog.verify_files({r['file']: r['sha256'] for r in rows})
        for row in rows:
            row['matches'] = match_evidence(db, row, query, field, locale, exact) if query else {'items': [], 'truncated': False}
        return {**catalog.page(rows, total, limit, offset), 'coverage': coverage(db)}
    finally:
        db.close()


def text_search(path, query='', locale=None, field='any', exact=False, limit=20, offset=0):
    catalog.check_page(limit, offset)
    if field not in ('any', 'key', 'text'):
        raise FormatError('Text search field must be any, key or text')
    db = catalog.open_existing(path)
    try:
        catalog.check_parser(db)
        locale = require_locale(db, locale)
        if not db.execute('SELECT 1 FROM text_files LIMIT 1').fetchone():
            raise FormatError('No text catalogs indexed; run index --locale ZH or --catalog LOCALE=path.mo')
        where, args = [], []
        if query:
            parts = []
            for name in ('key', 'text'):
                if field in ('any', name):
                    pred, values = condition('e.' + name, query, exact)
                    parts.append(pred)
                    args.extend(values)
            where.append('(' + ' OR '.join(parts) + ')')
        if locale:
            where.append('t.locale=?')
            args.append(locale)
        sql = ' FROM text_entries e JOIN text_files t ON t.path=e.file'
        sql += ' WHERE ' + ' AND '.join(where) if where else ''
        total = db.execute('SELECT count(*)' + sql, args).fetchone()[0]
        rows = [dict(r) for r in db.execute('SELECT e.key,e.context,e.plural_index,e.text,t.path AS file,t.sha256,t.locale,t.origin' + sql +
                ' ORDER BY e.key,t.locale,t.path,e.ordinal,e.plural_index LIMIT ? OFFSET ?', args + [limit, offset])]
        catalog.verify_files({r['file']: r['sha256'] for r in rows})
        for row in rows:
            row['recognized_use_count'] = db.execute("SELECT count(*) FROM resource_terms WHERE kind='key' AND value=?", (row['key'],)).fetchone()[0]
            row['placeholders'] = content_index.placeholders(row['text'])
        return {**catalog.page(rows, total, limit, offset), 'coverage': coverage(db)}
    finally:
        db.close()


def linked_rows(db, rid, limit=501):
    return [public_row(r) for r in db.execute(SELECT + ' WHERE r.id=?' + ORDER + ' LIMIT ?', (rid, limit))]


def describe(path, rid, filename=None, locale=None, section='summary', limit=20, offset=0):
    catalog.check_page(limit, offset)
    if section not in ('summary', 'texts', 'members', 'references', 'fields'):
        raise FormatError('Unsupported describe section')
    resource, row, game = catalog.load_resource(path, rid, filename)
    db = catalog.open_existing(path)
    try:
        catalog.check_parser(db)
        locale = require_locale(db, locale)
        result = {'resource': resource.identity(), 'source': {k: row[k] for k in ('file', 'sha256', 'package', 'origin')},
                  'coverage': coverage(db)}
        tag = resource.fields[0x12C].string() if 0x12C in resource.fields else None
        result['data_store_tag'] = tag
        issues = [dict(r) for r in db.execute('SELECT property,issue FROM text_scan_issues WHERE source=?', (row['row_id'],))]
        result['text_scan_issues'] = catalog.page(issues[offset:offset+limit], len(issues), limit, offset)
        if tag:
            predicate = " WHERE EXISTS(SELECT 1 FROM resource_terms u WHERE u.source=r.row_id AND u.kind='tag' AND u.value=?)"
            total = db.execute('SELECT count(*) FROM resources r' + predicate, (tag,)).fetchone()[0]
            definitions = [public_row(r) for r in db.execute(SELECT + predicate + ORDER + ' LIMIT ? OFFSET ?', (tag, limit, offset))]
            catalog.verify_files({r['file']: r['sha256'] for r in definitions})
            result['tag_definitions'] = catalog.page(definitions, total, limit, offset)
            result['tag_ambiguous_in_index'] = total > 1
        if section in ('summary', 'texts'):
            uses = content_index.text_links(resource, [])
            texts = []
            for use in uses[offset:offset+limit]:
                found = translations(db, use['key'], locale)
                texts.append({**use, 'catalog_candidates': found,
                              'unresolved_expressions': list(dict.fromkeys(expression for entry in found['items']
                                  for expression in content_index.placeholders(entry['text']))),
                              'rendered_tooltip': 'not_evaluated'})
            result['texts'] = catalog.page(texts, len(uses), limit, offset)
        if section in ('summary', 'members'):
            if resource.type_id == 0x473708:
                missing = db.execute('SELECT count(*) FROM files WHERE links_indexed=0 AND error IS NULL').fetchone()[0]
                if missing:
                    raise FormatError('Tome membership references not indexed; rebuild with --links')
                sql = (" WHERE r.type_id=0x473702 AND EXISTS(SELECT 1 FROM links l WHERE l.source=r.row_id "
                       "AND l.target=? AND l.property='0x00473702/0x6')")
                total = db.execute('SELECT count(*) FROM resources r' + sql, (row['id'],)).fetchone()[0]
                members = [public_row(r) for r in db.execute(SELECT + sql + ORDER + ' LIMIT ? OFFSET ?', (row['id'], limit, offset))]
                catalog.verify_files({r['file']: r['sha256'] for r in members})
                for member in members:
                    keys = db.execute("SELECT value,property FROM resource_terms WHERE source=? AND kind='key' ORDER BY property", (member['row_id'],)).fetchall()
                    member['text_keys'] = list(dict.fromkeys(k[0] for k in keys))
                    name_keys = [k[0] for k in keys if k[1] == '0x00473702/0x1' or k[1].startswith('0x00473702/0x1/')]
                    member['display_name_candidates'] = [{'key': key, **translations(db, key, locale)} for key in dict.fromkeys(name_keys)]
                result['members'] = {**catalog.page(members, total, limit, offset),
                                     'rule': 'TGridSkill.GroupLink -> selected TGridSkillGroup ID; installed candidates, not runtime activation'}
                other_sql = (" WHERE r.file=? AND r.type_id=0x473702 AND NOT EXISTS(SELECT 1 FROM links l "
                             "WHERE l.source=r.row_id AND l.target=? AND l.property='0x00473702/0x6')")
                other_count = db.execute('SELECT count(*) FROM resources r' + other_sql, (row['file'], row['id'])).fetchone()[0]
                others = [public_row(r) for r in db.execute(SELECT + other_sql + ORDER + ' LIMIT ?', (row['file'], row['id'], limit))]
                result['same_file_other_research'] = {**catalog.page(others, other_count, limit, 0),
                                                     'meaning': 'Not linked to this tome; not proof of global unreachability or deprecated status'}
            else:
                result['members'] = {'status': 'not_evaluated', 'reason': 'No membership rule for this resource class'}
        if section in ('summary', 'references'):
            refs = core.references(resource)
            result['references'] = catalog.page(refs[offset:offset+limit], len(refs), limit, offset)
        if section == 'fields':
            inspected = semantics.inspect_resource(resource, game)
            properties = inspected.pop('properties')
            result['fields'] = catalog.page(properties[offset:offset+limit], len(properties), limit, offset)
            result['schema_status'] = inspected['schema_status']
        catalog.verify_files({row['file']: row['sha256']})
        return result
    finally:
        db.close()


def trace(path, rid, filename=None, direction='outgoing', depth=2, max_nodes=40, max_edges=100):
    if direction not in ('outgoing', 'incoming') or not 0 <= depth <= 8 or not 1 <= max_nodes <= 500 or not 1 <= max_edges <= 2000:
        raise FormatError('Trace requires depth 0..8, max-nodes 1..500 and max-edges 1..2000')
    _, root, _ = catalog.load_resource(path, rid, filename)
    db = catalog.open_existing(path)
    try:
        catalog.check_parser(db)
        if db.execute('SELECT count(*) FROM files WHERE links_indexed=0 AND error IS NULL').fetchone()[0]:
            raise FormatError('Trace references not indexed; rebuild with --links')
        root_row = public_row(db.execute(SELECT + ' WHERE r.row_id=?', (root['row_id'],)).fetchone())
        nodes = {root_row['row_id']: {**root_row, 'depth': 0}}
        queue = deque([root_row['row_id']])
        edges, boundaries = [], []
        while queue:
            current = nodes[queue.popleft()]
            predicate = 'source=?' if direction == 'outgoing' else 'target=?'
            value = current['row_id'] if direction == 'outgoing' else current['id']
            occurrence_count = db.execute('SELECT count(*) FROM links WHERE ' + predicate, (value,)).fetchone()[0]
            if current['depth'] == depth:
                if occurrence_count:
                    boundaries.append({'node': current['row_id'], 'reason': 'depth_limit', 'unexpanded_occurrences': occurrence_count})
                continue
            rows = db.execute('SELECT rowid AS occurrence,source,target,name,property FROM links WHERE ' + predicate +
                              ' ORDER BY rowid LIMIT ?', (value, max_edges - len(edges) + 1)).fetchall()
            for link in rows:
                if len(edges) >= max_edges:
                    boundaries.append({'node': current['row_id'], 'reason': 'edge_limit'})
                    queue.clear()
                    break
                if direction == 'outgoing':
                    candidate_count = db.execute('SELECT count(*) FROM resources WHERE id=?', (link['target'],)).fetchone()[0]
                    candidates = linked_rows(db, link['target'], max_nodes + 1)
                else:
                    candidates = [public_row(r) for r in db.execute(SELECT + ' WHERE r.row_id=?', (link['source'],))]
                    candidate_count = len(candidates)
                included = []
                for candidate in candidates:
                    key = candidate['row_id']
                    if key not in nodes:
                        if len(nodes) >= max_nodes:
                            boundaries.append({'node': current['row_id'], 'reason': 'node_limit', 'candidate': key})
                            continue
                        nodes[key] = {**candidate, 'depth': current['depth'] + 1}
                        queue.append(key)
                    included.append(key)
                edges.append({'from_node': current['row_id'], 'to_nodes': included, 'direction': direction,
                              'target_id': link['target'], 'property': link['property'], 'name': link['name'],
                              'occurrence': link['occurrence'], 'candidate_count': candidate_count,
                              'resolution': 'missing' if not candidate_count else 'ambiguous' if candidate_count > 1 else 'unique',
                              'candidates_truncated': len(included) < candidate_count})
        catalog.verify_files({n['file']: n['sha256'] for n in nodes.values()})
        return {'root_node': root_row['row_id'], 'nodes': list(nodes.values()), 'edges': edges,
                'boundaries': boundaries, 'traversal_truncated': bool(boundaries),
                'limits': {'depth': depth, 'max_nodes': max_nodes, 'max_edges': max_edges},
                'coverage': coverage(db)}
    finally:
        db.close()
