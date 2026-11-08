"""Hash-bound RPK import-list edits that preserve all resource data."""
import json
from pathlib import Path, PurePosixPath
import struct

from .core import FormatError, Node, read_rpk, sha
from .authoring import string_vector
from .editing import rewrite_map
from .filesystem import copy_directory, file_hash, inventory

FORMAT = 'aow4mod-pack-import-plan/1'


def identifier(value):
    if not isinstance(value, str) or not value or any(c in value for c in '\0\r\n:'):
        raise FormatError('Import must be a nonempty relative content filename')
    path = PurePosixPath(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or path.suffix.lower() not in ('.rpk', '.clb', '.env', '.mo', '.acp'):
        raise FormatError('Import must name a relative RPK, CLB, ENV, MO or ACP file')
    return value


def rewrite(data, imports):
    imports = [identifier(x) for x in imports]
    root = Node(data, 0xb0, len(data))
    fields = root.mapping()
    payload = rewrite_map(root, {0x1e: string_vector(imports) if imports else None})
    header = bytearray(data[:0xb0])
    struct.pack_into('<I', header, 12, len(payload))
    updated = bytes(header)+payload
    after = Node(updated, 0xb0, len(updated)).mapping()
    if {k: v.raw() for k, v in fields.items() if k != 0x1e} != {k: v.raw() for k, v in after.items() if k != 0x1e}:
        raise FormatError('Import edit changed unrelated pack/resource data')
    return updated


def make_plan(path, add=(), remove=(), source_root=None, game=None):
    path = Path(path).resolve()
    info, rows = read_rpk(path)
    root = Path(source_root).resolve() if source_root else path.parent
    if not path.is_relative_to(root):
        raise FormatError('Import source is outside its Mod source root')
    original = info['dependencies']
    additions, removals = [identifier(x) for x in add], [identifier(x) for x in remove]
    if {x.casefold() for x in additions} & {x.casefold() for x in removals}:
        raise FormatError('An import cannot be added and removed in the same plan')
    if any(x.casefold() == info['name'].casefold() for x in additions):
        raise FormatError('A resource pack cannot import itself')
    missing = {x.casefold() for x in removals}-{x.casefold() for x in original}
    if missing:
        raise FormatError('Import requested for removal is absent: '+', '.join(sorted(missing)))
    desired = [x for x in original if x.casefold() not in {r.casefold() for r in removals}]
    seen = {x.casefold() for x in desired}
    for value in additions:
        if value.casefold() not in seen:
            desired.append(value)
            seen.add(value.casefold())
    if desired == original:
        raise FormatError('Import plan makes no change')
    data = path.read_bytes()
    if sha(data) != info['sha256']:
        raise FormatError('Import source changed during planning')
    updated = rewrite(data, desired)
    return {'format': FORMAT, 'source': str(path), 'source_sha256': info['sha256'],
            'source_root': str(root), 'source_files': inventory(root), 'game': str(Path(game).resolve()) if game else None,
            'before': original, 'after': desired, 'add': additions, 'remove': removals,
            'result_sha256': sha(updated), 'resource_count_preserved': len(rows),
            'coverage': 'Import declarations only; existence, ownership, cycles and effective loading need dependency validation.'}


def apply_plan(plan, output):
    if not isinstance(plan, dict) or plan.get('format') != FORMAT:
        raise FormatError('Unsupported pack import plan')
    path, root = Path(plan['source']).resolve(), Path(plan['source_root']).resolve()
    if inventory(root) != plan['source_files'] or file_hash(path) != plan['source_sha256']:
        raise FormatError('Pack import source changed since planning')
    expected = make_plan(path, plan['add'], plan['remove'], root, plan.get('game'))
    if expected != plan:
        raise FormatError('Pack import plan does not match its source and requested changes')
    before_info, before = read_rpk(path)
    data = rewrite(path.read_bytes(), plan['after'])
    if sha(data) != plan['result_sha256']:
        raise FormatError('Pack import output hash differs')
    with copy_directory(root, output, [Path(plan['game'])] if plan.get('game') else []) as (staged, files):
        target = staged/path.relative_to(root)
        target.write_bytes(data)
        info, after = read_rpk(target)
        if info['dependencies'] != plan['after'] or [r.node.raw() for r in before] != [r.node.raw() for r in after]:
            raise FormatError('Pack import result changed resources or imports unexpectedly')
        receipt = {'format': 'aow4mod-pack-import-receipt/1', 'output': str(Path(output).resolve()),
                   'file': path.relative_to(root).as_posix(), 'before': plan['before'], 'after': plan['after'],
                   'resources_preserved': len(after), 'result_sha256': info['sha256'], 'source_files': files}
        (staged/'.aow4mod-pack-imports.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return receipt
