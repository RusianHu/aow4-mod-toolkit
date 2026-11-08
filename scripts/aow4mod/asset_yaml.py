"""Strict native-YAML data handling; no YAML object constructors or aliases."""
import math
import re
import struct

from .core import FormatError

MAX_TEXT = 64*1024*1024


def yaml_module():
    try:
        import yaml
    except ImportError as exc:
        raise FormatError('Native asset YAML requires the [assets] extra (PyYAML)') from exc
    return yaml


def load(text):
    if isinstance(text, bytes):
        text = text.decode('utf-8-sig')
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_TEXT:
        raise FormatError('Asset YAML exceeds its text bound')
    yaml = yaml_module()

    class Loader(yaml.BaseLoader):
        def construct_mapping(self, node, deep=False):
            result, numbers = {}, set()
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result:
                    raise FormatError('Asset YAML has duplicate or non-string keys')
                match = re.match(r'^(\d+)_', key)
                if match:
                    number = int(match[1])
                    if number in numbers:
                        raise FormatError('Asset YAML repeats a native field number')
                    numbers.add(number)
                result[key] = self.construct_object(value_node, deep=deep)
            return result

    try:
        for token in yaml.scan(text):
            if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken, yaml.tokens.TagToken)):
                raise FormatError('Native asset YAML does not accept aliases, anchors or explicit tags')
        data = yaml.load(text, Loader=Loader)
    except yaml.YAMLError as exc:
        raise FormatError('Invalid native asset YAML: '+str(exc)) from exc
    validate_tree(data)
    return data


def validate_tree(data):
    nodes = 0
    def visit(value, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 96 or nodes > 500000:
            raise FormatError('Asset YAML tree exceeds its depth/node bound')
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str) or '\0' in key:
                    raise FormatError('Invalid native YAML field name')
                if key.endswith(('FStreamDataPS5.Size', 'FStreamDataXbox.Size')) and str(child) != '0':
                    raise FormatError('Nonempty console texture streams are not supported by the current YAML adapter')
                if key.endswith(('FStreamDataPS5.Stream', 'FStreamDataXbox.Stream')) and child not in ('', 'null', None):
                    raise FormatError('Console texture stream payloads are not supported by the current YAML adapter')
                visit(child, depth+1)
        elif isinstance(value, list):
            for child in value:
                visit(child, depth+1)
        elif not isinstance(value, str) or '\0' in value:
            raise FormatError('Native YAML scalars must be text without NUL')
    visit(data)
    if not isinstance(data, dict) or data.get('YAMLRPKVersion') != '1' or data.get('19_*(int*)&FVersionNumber') != '8':
        raise FormatError('Expected a native content-library YAML document with schema 1 and CLB version 8')
    if not data.get('20_FName') or not isinstance(data.get('21_FList'), dict):
        raise FormatError('Native asset YAML lacks its library identity or resource list')
    if data['21_FList'].get('cid') != str(0x400081) or data['21_FList'].get('cname') != 'TEResourceList':
        raise FormatError('Unsupported native asset resource-list type')
    return data


def class_records(data):
    records = []
    def visit(value):
        if isinstance(value, dict):
            if 'cid' in value:
                try:
                    cid = int(value['cid'])
                except (TypeError, ValueError) as exc:
                    raise FormatError('Native YAML class ID must be an integer') from exc
                if not 0 <= cid <= 0xffffffff:
                    raise FormatError('Native YAML class ID exceeds uint32')
                if cid:
                    if not isinstance(value.get('cname'), str):
                        raise FormatError('Native YAML class name is missing')
                    records.append({'cid': f'{cid:08X}', 'name': value['cname']})
                elif set(value) != {'cid'}:
                    raise FormatError('A null native object must not contain hidden fields')
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(data)
    return records


def members(data):
    rows = data['21_FList'].get('1_this', [])
    if not isinstance(rows, list):
        raise FormatError('Native asset resource list is not an array')
    result = []
    for row in rows:
        if not isinstance(row, dict) or not row.get('20_FName'):
            raise FormatError('Native asset member has no locator')
        result.append({'cid': int(row['cid']), 'locator': row['20_FName'], 'name': row.get('21_FUserName')})
    if len({m['locator'].casefold() for m in result}) != len(result):
        raise FormatError('Native asset member locators are duplicated')
    return result


def dump(data):
    validate_tree(data)
    # Quoted numeric scalars are intentionally retained as strings. The native
    # reader interprets values using each serialized field's C++ type.
    return yaml_module().safe_dump(data, sort_keys=False, allow_unicode=True, width=120)


def differences(expected, actual, max_differences=100):
    changed = []
    def walk(a, b, path):
        if len(changed) >= max_differences:
            return
        if type(a) is not type(b):
            changed.append({'path': path, 'issue': 'node_type_changed'})
        elif isinstance(a, dict):
            for key in sorted(a.keys() | b.keys()):
                if key not in a or key not in b:
                    changed.append({'path': path+[key], 'issue': 'field_added_or_missing'})
                else:
                    walk(a[key], b[key], path+[key])
                if len(changed) >= max_differences:
                    return
        elif isinstance(a, list):
            if len(a) != len(b):
                changed.append({'path': path, 'issue': 'array_length_changed'})
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, path+[i])
        elif a != b:
            changed.append({'path': path, 'issue': 'scalar_changed', 'before': a[:120], 'after': b[:120]})
    walk(expected, actual, [])
    return changed


def set_value(data, path, value):
    if not isinstance(path, list) or not path or len(path) > 96:
        raise FormatError('Asset edit path must be a nonempty JSON array')
    if path[-1] in ('cid', 'cname', '20_FName') or len(path) == 1:
        raise FormatError('This asset editor does not change class or library/member identities')
    node = data
    for key in path[:-1]:
        if isinstance(node, list) and type(key) is int and 0 <= key < len(node):
            node = node[key]
        elif isinstance(node, dict) and isinstance(key, str) and key in node:
            node = node[key]
        else:
            raise FormatError('Asset edit path does not exist')
    key = path[-1]
    if isinstance(node, list):
        if type(key) is not int or not 0 <= key < len(node):
            raise FormatError('Asset array position is out of bounds')
    elif not isinstance(node, dict) or key not in node:
        raise FormatError('Asset field is absent')
    old = node[key]
    def scalar(v):
        if type(v) is bool:
            return 'true' if v else 'false'
        if type(v) is int:
            return str(v)
        if type(v) is float and math.isfinite(v):
            return repr(v)
        if isinstance(v, str) and '\0' not in v:
            return v
        raise FormatError('Asset edits accept finite scalar values or same-length scalar vectors')
    if isinstance(old, str):
        node[key] = scalar(value)
    elif isinstance(old, list) and isinstance(value, list) and len(old) == len(value) and all(isinstance(v, str) for v in old):
        node[key] = [scalar(v) for v in value]
    else:
        raise FormatError('Asset edits do not replace structured objects or change list lengths')
    return old, node[key]


def empty_console_stream(field, label, size, present):
    return (size == 0 and not present and
            (field, label) in ((21, 'FStreamDataPS5.Stream'), (23, 'FStreamDataXbox.Stream')))


def search(data, query='', limit=20, offset=0):
    from . import catalog
    catalog.check_page(limit, offset)
    rows = []
    def visit(node, path):
        if isinstance(node, dict):
            for key, child in node.items():
                visit(child, path+[key])
        elif isinstance(node, list) and all(isinstance(x, str) for x in node):
            rendered = str(node)
            if query.casefold() in ('/'.join(map(str, path))+' '+rendered[:200]).casefold():
                rows.append({'path': path, 'value': node if len(rendered) <= 240 else rendered[:240], 'kind': 'scalar_vector'})
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, path+[index])
        else:
            if query.casefold() in ('/'.join(map(str, path))+' '+node[:200]).casefold():
                rows.append({'path': path, 'value': node[:240], 'characters': len(node), 'kind': 'scalar'})
    visit(data, [])
    return catalog.page(rows[offset:offset+limit], len(rows), limit, offset)
