"""Conservative nested serialization comparison; never a gameplay equivalence test."""
from .core import FormatError, describe
from . import structured
from .catalog import check_page, page
from .filesystem import file_hash


def path_keys(path):
    if not isinstance(path, str):
        raise FormatError('Property must be a hexadecimal path or meta/<field> path')
    parts = path.split('/')
    meta = parts[0] == 'meta'
    try:
        keys = [int(p, 16) for p in parts[1:] if p] if meta else [int(p, 16) for p in parts]
    except ValueError as exc:
        raise FormatError('Invalid hexadecimal property path') from exc
    if not 1 <= len(keys) <= 64 or any(not 0 <= k <= 0xFFFFFFFF for k in keys) or '' in parts:
        raise FormatError('Property path requires 1..64 nonempty hexadecimal keys')
    if meta and keys[0] >= 0x10000 or not meta and (keys[0] < 0x10000 or len(keys) < 2):
        raise FormatError('Use meta/<field> for flat resource fields; class fields require class/field')
    return keys


def path_text(keys):
    return ('meta/' if keys[0] < 0x10000 else '') + '/'.join(f'0x{k:X}' for k in keys)


def select(resource, path):
    keys = path_keys(path)
    node = resource.fields.get(keys[0])
    if node is None:
        if len(keys) == 1:
            return None
        raise FormatError('Property ancestor is absent')
    for key in keys[1:-1]:
        node = structured.find_node(node, [key])
    if len(keys) == 1:
        return node
    try:
        _, fields, _ = structured.children(node, keys[-1])
    except FormatError:
        # Missing final fields are observable; missing/ambiguous ancestors are not.
        _, fields, _ = structured.children(node)
    return fields.get(keys[-1])


def compare_nodes(before, after, *, max_depth=64, max_nodes=100000):
    differences = []
    encoding = []
    visited = 0
    limited = False

    def walk(a, b, path, depth):
        nonlocal visited, limited
        if a is not None and b is not None and a.raw() == b.raw():
            return True
        if visited >= max_nodes:
            limited = True
            return False
        visited += 1
        if depth > max_depth:
            limited = True
            return False
        if a is not None and b is not None:
            try:
                at, af, ac = structured.children(a)
                bt, bf, bc = structured.children(b)
            except FormatError:
                pass
            else:
                # A lone false/null byte also parses as an empty map. Without
                # field-type evidence, changing that representation is unsafe.
                if (at, ac) == (bt, bc) and (at or af or bf):
                    same = True
                    for key in sorted(af.keys() | bf.keys()):
                        same = walk(af.get(key), bf.get(key), path + [key], depth + 1) and same
                        if limited:
                            break
                    if same:
                        encoding.append(path)
                    return same
        differences.append({'path': '/'.join(f'0x{k:X}' for k in path) or '(selected root)',
                            'before': describe(a.raw() if a is not None else None),
                            'after': describe(b.raw() if b is not None else None)})
        return False

    same = True if before is None and after is None else walk(before, after, [], 0)
    return {'byte_equal': (before.raw() if before is not None else None) == (after.raw() if after is not None else None),
            'recognized_data_equal': same and not limited, 'traversal_complete': not limited,
            'differences': differences, 'encoding_only_paths': ['/'.join(f'0x{k:X}' for k in p) or '(selected root)' for p in encoding],
            'visited_changed_nodes': visited,
            'scope': 'Unique recognized maps/typed objects; opaque or ambiguous values compare byte-for-byte. '
                     'Absent values differ from explicit zero. Array indices and type IDs remain significant; no loaded defaults or gameplay semantics are inferred.'}


def inspect(before, after, rid, other_id=None, pack=None, other_pack=None,
            property_path=None, other_property=None, limit=20, offset=0):
    check_page(limit, offset)
    a, ap, ar = structured.source_resource(before, rid, pack)
    b, bp, br = structured.source_resource(after, other_id or rid, other_pack)
    if other_property and not property_path:
        raise FormatError('--other-property requires --property')
    result = compare_nodes(select(ar, property_path) if property_path else ar.node,
                           select(br, other_property or property_path) if property_path else br.node)
    differences = result.pop('differences')
    encoding = result.pop('encoding_only_paths')
    return {'format': 'aow4mod-resource-diff/1',
            'before': {'file': str(a), 'sha256': file_hash(a), 'pack': ap, 'resource': ar.identity(), 'property': property_path},
            'after': {'file': str(b), 'sha256': file_hash(b), 'pack': bp, 'resource': br.identity(), 'property': other_property or property_path},
            **result, **page(differences[offset:offset + limit], len(differences), limit, offset),
            'encoding_only_path_count': len(encoding), 'encoding_only_path_examples': encoding[:20]}
