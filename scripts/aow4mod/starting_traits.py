"""Reviewed starting-bonus declarations, distinct from actual game execution."""
import struct
from collections import Counter

from .core import FormatError, decode_link, sha

TRAIT = 0x473533
RESEARCH = 0x477C0D
EMPIRE = 0x477C10
OPERATIONS = 0x477C0F
SETTINGS = 0x477C08
EXPECTED_TYPES = {'starting_research': 0x473702, 'starting_empire_skill': 0x47512B}
TRAIT_LISTS = {5: 'trait_side_property', 7: 'trait_race_property',
               0xA: 'trait_unit_property', 0xB: 'trait_colony_property',
               0x27: 'starting_property'}


def link_list(node, path, role):
    outer = node.mapping()
    if set(outer) != {0x1E}:
        raise FormatError('Unsupported starting-bonus link-list envelope')
    entries = outer[0x1E].array()
    if len(entries) > 256:
        raise FormatError('Starting-bonus list exceeds 256 entries')
    result = []
    for ordinal, value in entries.items():
        link = decode_link(value)
        if link is None or int(link['id'], 16) == 0:
            raise FormatError('Starting-bonus list contains an unreadable or null link')
        result.append({'id': link['id'], 'name': link.get('name'),
                       'property': path + f'/0x1E/0x1/0x{ordinal:X}',
                       'role': role, 'ordinal': ordinal})
    return result


def description(resource):
    path = '0x00477C0A/0x1'
    result = {'property': path, 'rendered_tooltip': False,
              'generated_description_evaluated': False}
    if 0x477C0A not in resource.fields:
        return dict(result, status='omitted_unread')
    try:
        owner = resource.fields[0x477C0A].mapping()
        if 1 not in owner:
            return dict(result, status='omitted_unread')
        fields = owner[1].mapping()
        if not {2} <= set(fields) <= {1, 2} or 0x401604 not in fields[2].mapping():
            raise FormatError('Unsupported starting-description envelope')
        key = fields[1].string() if 1 in fields else ''
        return dict(result, key=key, status='custom_text_key' if key else 'empty_key_runtime_text_unverified',
                    raw_sha256=sha(owner[1].raw()))
    except FormatError as exc:
        return dict(result, status='unsupported_description_layout', error=str(exc))


def analyze(resource):
    """Only exact typed lists become grants; selectors stay unevaluated."""
    cid = resource.type_id
    if cid not in (TRAIT, RESEARCH, EMPIRE, OPERATIONS):
        return None
    result = {'scope': 'serialized_starting_declarations', 'references': [],
              'lists': [], 'selectors': [], 'issues': [],
              'actual_grants_evaluated': False, 'old_save_backfill_evaluated': False}
    if cid != TRAIT:
        result['description'] = description(resource)
    try:
        if cid not in resource.fields:
            raise FormatError('Starting-bonus class section is omitted')
        fields = resource.fields[cid].mapping()
        def add_list(owner, field, prefix, role):
            path = prefix + f'/0x{field:X}'
            if field not in owner:
                result['lists'].append({'property': path, 'role': role, 'status': 'omitted_unread'})
                return
            try:
                refs = link_list(owner[field], path, role)
                result['lists'].append({'property': path, 'role': role,
                                        'status': 'serialized', 'count': len(refs)})
                result['references'].extend(refs)
            except FormatError as exc:
                result['issues'].append({'property': path, 'issue': str(exc)})
        if cid == TRAIT:
            for field, role in TRAIT_LISTS.items():
                add_list(fields, field, '0x00473533', role)
        elif cid == EMPIRE:
            add_list(fields, 1, '0x00477C10', 'starting_empire_skill')
        elif cid == OPERATIONS:
            result['selectors'].append({'property': '0x00477C0F/0x1',
                'kind': 'active_operations_tag_filter',
                'status': 'selector_not_evaluated' if 1 in fields else 'omitted_unread',
                'raw_sha256': sha(fields[1].raw()) if 1 in fields else None,
                'note': 'Requires tag identity, spell TagSet and final overlay context; no spell grant inferred.'})
        else:
            if 1 not in fields:
                raise FormatError('Research selection settings are omitted')
            envelope = fields[1].mapping()
            if set(envelope) != {0xA, 1}:
                raise FormatError('Unsupported research settings envelope')
            count = envelope[0xA].uint(4)
            entries = envelope[1].mapping()
            if count > 256 or sorted(entries) != list(range(count)):
                raise FormatError('Research settings count or indices differ')
            for ordinal in sorted(entries):
                prefix = f'0x00477C0D/0x1/0x1/0x{ordinal:X}'
                try:
                    kind, wrapper = entries[ordinal].typed()
                    if kind != SETTINGS or SETTINGS not in wrapper or not set(wrapper) <= {1, 2, SETTINGS}:
                        raise FormatError('Unsupported research selection settings class/layout')
                    settings = wrapper[SETTINGS].mapping()
                    if not set(settings) <= {1, 2, 3, 4}:
                        raise FormatError('Unreviewed research selection setting field')
                    path = prefix + '/0x477C08'
                    add_list(settings, 1, path, 'starting_research')
                    count_value = None
                    if 2 in settings:
                        if len(settings[2].raw()) != 4:
                            raise FormatError('Unsupported random research count storage')
                        count_value = struct.unpack('<i', settings[2].raw())[0]
                    result['selectors'].append({'property': path, 'kind': 'random_research',
                        'count': count_value, 'count_source': 'serialized' if 2 in settings else 'omitted_unread',
                        'status': 'selector_not_evaluated',
                        'tag_filter_sha256': sha(settings[3].raw()) if 3 in settings else None,
                        'requisite_filter_sha256': sha(settings[4].raw()) if 4 in settings else None})
                except FormatError as exc:
                    result['issues'].append({'property': prefix, 'issue': str(exc)})
    except FormatError as exc:
        result['issues'].append({'property': f'0x{cid:08X}', 'issue': str(exc)})
    return result


def reference_role(resource, reference):
    info = analyze(resource)
    if info is None:
        return None
    return next((r['role'] for r in info['references']
                 if r['property'] == reference['property'] and r['id'] == reference['id']), None)


def target_type_check(edge, nodes):
    expected = EXPECTED_TYPES.get(edge['role'])
    if expected is None:
        return None
    mismatch = [{'node': n, 'actual_type': nodes[n]['type_id']}
                for n in edge['included_nodes'] if int(nodes[n]['type_id'], 16) != expected]
    return {'expected_type': f'0x{expected:08X}', 'mismatches': mismatch,
            'unchecked_candidates': edge['candidate_count'] - len(edge['included_nodes']),
            'runtime_grant_evaluated': False}


def summarize(nodes, edges):
    bonuses = [n['starting_bonus'] for n in nodes if n.get('starting_bonus') is not None]
    counts = Counter(r['role'] for b in bonuses for r in b['references'])
    return {'declaration_counts': dict(sorted(counts.items())),
            'selector_blocks_not_evaluated': sum(len(b['selectors']) for b in bonuses),
            'definition_issues': sum(len(b['issues']) for b in bonuses),
            'target_type_mismatches': sum(len(e['target_type_check']['mismatches'])
                for e in edges if e.get('target_type_check')),
            'description_keys_empty_or_unread': sum(b.get('description', {}).get('status') not in
                (None, 'custom_text_key') for b in bonuses),
            'scope': 'Selected definition occurrences; not effective grants or rendered tooltips.'}
