"""Bounded CLB v8 metadata inspection; pixel/mesh payloads stay opaque.

The envelope and metadata maps are independently checked against native
read/save examples. Strings use the library string table, unlike RPK strings.
"""
from dataclasses import dataclass
import hashlib
import mmap
from pathlib import Path
import re
import struct

from .core import FormatError, Node
from . import catalog

SIGNATURE = b'CRL\0\x60\0\x41\0'
MAX_METADATA = 256*1024*1024
LOCATOR = re.compile(r'^\[([^\]]+)\]([A-Za-z0-9_]+)\\(\d+)$')


def identity(path):
    """Read only the envelope/root name for namespace checks, without decoding members."""
    path=Path(path).resolve();before=path.stat()
    with path.open('rb') as source:
        header=source.read(32)
        if len(header)!=32 or header[:8]!=SIGNATURE or any(header[24:]):raise FormatError('Unsupported CLB identity envelope')
        version,strings,metadata,payload=struct.unpack_from('<4I',header,8)
        if version!=8 or strings+metadata>MAX_METADATA or not metadata or 32+strings+metadata+payload!=before.st_size:
            raise FormatError('Invalid CLB identity section bounds')
        with mmap.mmap(source.fileno(),0,access=mmap.ACCESS_READ) as data:
            fields=Node(data,32+strings,32+strings+metadata).mapping()
            if fields[0x13].uint(4)!=8:raise FormatError('CLB identity version mismatch')
            raw=fields[0x14].raw()
            if len(raw)!=8:raise FormatError('Unsupported CLB identity string reference')
            length,offset=struct.unpack('<II',raw)
            if not length or offset+length>=strings or data[32+offset+length]!=0:raise FormatError('CLB identity string exceeds its pool')
            name=data[32+offset:32+offset+length].decode('utf-8')
            if '\0' in name:raise FormatError('Invalid CLB identity name')
    after=path.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
        raise FormatError('CLB changed during namespace inspection')
    return {'file':str(path),'name':name,'version':version,'scope':'Envelope and root name only; member/pixel data not inspected.'}


@dataclass
class Library:
    path: Path
    header: bytes
    strings: bytes
    metadata: bytes
    data_offset: int
    data_size: int
    name: str
    members: list
    root: dict

    def string(self, node):
        raw = node.raw()
        if len(raw) == 4 and raw == bytes(4):
            return ''
        if len(raw) != 8:
            raise FormatError('Unsupported CLB string reference width')
        length, offset = struct.unpack('<II', raw)
        if length == 0:
            if offset > len(self.strings):
                raise FormatError('Empty CLB string offset is out of bounds')
            return ''
        if offset+length >= len(self.strings):
            raise FormatError('CLB string reference exceeds its table')
        value = self.strings[offset:offset+length]
        if self.strings[offset+length] != 0 or b'\0' in value:
            raise FormatError('CLB string length/terminator mismatch')
        return value.decode('utf-8', 'strict')

    @property
    def metadata_sha256(self):
        result = hashlib.sha256()
        for part in (self.header, self.strings, self.metadata):
            result.update(part)
        return result.hexdigest()


def read(path):
    path = Path(path).resolve()
    before = path.stat()
    size = before.st_size
    with path.open('rb') as source:
        header = source.read(32)
        if len(header) != 32 or header[:8] != SIGNATURE:
            raise FormatError('Unsupported CLB signature/class')
        version, strings_size, metadata_size, data_size = struct.unpack_from('<IIII', header, 8)
        if version != 8:
            raise FormatError(f'Unsupported CLB version {version}; metadata decoder currently supports version 8')
        if any(header[24:32]):
            raise FormatError('Unsupported nonzero CLB reserved header bytes')
        if any(n % 16 for n in (strings_size, metadata_size, data_size)):
            raise FormatError('CLB sections are not aligned to the observed 16-byte boundary')
        if strings_size+metadata_size > MAX_METADATA or 32+strings_size+metadata_size+data_size != size:
            raise FormatError('CLB section sizes are invalid or exceed the metadata bound')
        strings, metadata = source.read(strings_size), source.read(metadata_size)
        if len(strings) != strings_size or len(metadata) != metadata_size:
            raise FormatError('CLB metadata became truncated while reading')
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise FormatError('CLB changed while reading metadata')
    if not metadata:
        raise FormatError('CLB has no metadata map')
    root = Node(metadata, 0, len(metadata)).mapping()
    if not {0x13, 0x14, 0x15}.issubset(root):
        raise FormatError('CLB required metadata fields are missing')
    if root[0x13].uint(4) != version:
        raise FormatError('CLB header and metadata versions disagree')
    lib = Library(path, header, strings, metadata, 32+strings_size+metadata_size, data_size, '', [], root)
    lib.name = lib.string(root[0x14])
    members = root[0x15].array()
    seen = set()
    for index, node in members.items():
        cid, fields = node.typed()
        locator = lib.string(fields[0x14]) if 0x14 in fields else None
        name = lib.string(fields[0x15]) if 0x15 in fields else None
        match = LOCATOR.fullmatch(locator or '')
        identity = {'ordinal': index, 'type_id': f'0x{cid:08X}', 'locator': locator, 'name': name,
                    'category': match[2] if match else None, 'member': int(match[3]) if match else None,
                    'library': match[1] if match else None, 'locator_recognized': bool(match)}
        if locator:
            if locator.casefold() in seen:
                raise FormatError('Duplicate CLB member locator')
            seen.add(locator.casefold())
        lib.members.append({'identity': identity, 'fields': fields, 'node': node})
    return lib


def inspect(path, query='', category=None, limit=20, offset=0):
    catalog.check_page(limit, offset)
    lib = read(path)
    items = [m['identity'] for m in lib.members]
    if query:
        items = [m for m in items if query.casefold() in ((m['name'] or '')+' '+(m['locator'] or '')).casefold()]
    if category:
        items = [m for m in items if (m['category'] or '').casefold() == category.casefold()]
    return {'format': 'aow4mod-clb-inspection/1', 'file': str(lib.path), 'name': lib.name, 'version': 8,
            'metadata_sha256': lib.metadata_sha256, 'total_members': len(lib.members),
            'opaque_data_bytes': lib.data_size,
            **catalog.page(items[offset:offset+limit], len(items), limit, offset),
            'coverage': 'Top-level named CLB members and metadata envelope only. '
                        'Nested atlas images, pixel/mesh payload validity and runtime rendering are not certified.'}


def member(path, locator):
    lib = read(path)
    matches = [m['identity'] for m in lib.members if (m['identity']['locator'] or '').casefold() == locator.casefold()]
    return {'file': str(lib.path), 'metadata_sha256': lib.metadata_sha256, 'locator': locator,
            'status': 'found_top_level_member' if len(matches) == 1 else 'not_found_in_top_level_members',
            'member': matches[0] if len(matches) == 1 else None,
            'coverage': 'Exact top-level locator membership; absence is not proof about nested or virtual subresources.'}


def data_hash(lib):
    result = hashlib.sha256()
    with lib.path.open('rb') as stream:
        stream.seek(lib.data_offset)
        remaining = lib.data_size
        while remaining:
            chunk = stream.read(min(1024*1024, remaining))
            if not chunk:
                raise FormatError('CLB data section changed or became truncated')
            result.update(chunk)
            remaining -= len(chunk)
    return result.hexdigest()


def member_fields(item):
    result = {}
    for key, value in item['fields'].items():
        if key >= 0x10000:
            for field, leaf in value.mapping().items():
                result[f'0x{key:08X}/0x{field:X}'] = leaf.raw()
        else:
            result[f'meta/0x{key:X}'] = value.raw()
    return result


def compare(before_path, after_path):
    before, after = read(before_path), read(after_path)
    # Ordinal + locator preserves ordering and avoids collapsing unnamed objects.
    a = {(m['identity']['ordinal'], m['identity']['locator']): m for m in before.members}
    b = {(m['identity']['ordinal'], m['identity']['locator']): m for m in after.members}
    changed = [];normalized_frames=[]
    for key in a.keys() & b.keys():
        left, right = member_fields(a[key]), member_fields(b[key])
        fields = sorted(k for k in left.keys() | right.keys() if left.get(k) != right.get(k))
        # Witch serializer B6C980 archives 160-byte MeshDEV v3 records but
        # writes/reads only through byte 157. Keep raw hashes; only the two
        # reserved bytes may differ. Flags 156/157 and FHash remain strict.
        if a[key]['identity']['type_id']==b[key]['identity']['type_id']=='0x00410035' and 0x3c in a[key]['fields'] and 0x3c in b[key]['fields'] and a[key]['fields'][0x3c].uint(4)==b[key]['fields'][0x3c].uint(4)==3:
            for number in (0x1a,0x3f):
                label=f'meta/0x{number:X}'
                if label not in fields:continue
                x,y=a[key]['fields'].get(number),b[key]['fields'].get(number)
                if x is None or y is None:continue
                xf,yf=x.mapping(),y.mapping()
                if 0x15 not in xf or 0x15 not in yf or 0x16 not in xf or 0x16 not in yf:continue
                count=xf[0x15].uint(4)
                if not 1<=count<=256 or yf[0x15].uint(4)!=count or len(xf[0x16].raw())!=count*160 or len(yf[0x16].raw())!=count*160:continue
                bx,by=bytearray(x.raw()),bytearray(y.raw())
                for frame in range(count):
                    for blob,node,field in ((bx,x,xf[0x16]),(by,y,yf[0x16])):
                        start=field.start-node.start+frame*160+158;blob[start:start+2]=b'\0\0'
                if bx==by:
                    fields.remove(label);normalized_frames.append({'ordinal':key[0],'locator':key[1],'field':label,'frames':count,'reserved_offsets':[158,159]})
        if fields or a[key]['identity']['type_id'] != b[key]['identity']['type_id']:
            changed.append({'ordinal': key[0], 'locator': key[1], 'changed_fields': fields,
                            'type_changed': a[key]['identity']['type_id'] != b[key]['identity']['type_id']})
    root_changes = []
    for key in before.root.keys() | after.root.keys():
        if key == 0x15:
            continue
        left = before.root[key].raw() if key in before.root else None
        right = after.root[key].raw() if key in after.root else None
        if key == 0x1c:
            # Only the observed empty separator list has an unambiguous trailing
            # metadata-alignment suffix. Other cases remain raw comparisons.
            def empty(value):
                return value is not None and 4 <= len(value) <= 19 and value[:4] == b'\x01\x01\0\0' and not any(value[4:])
            if empty(left) and empty(right):
                continue
        if left != right:
            root_changes.append(f'0x{key:X}')
    data_equal = before.data_size == after.data_size and data_hash(before) == data_hash(after)
    missing, added = sorted(a.keys()-b.keys()), sorted(b.keys()-a.keys())
    result={'source_members': len(a), 'saved_members': len(b), 'missing_members': missing, 'added_members': added,
            'changed_members': changed, 'changed_library_fields': sorted(root_changes),
            'string_table_equal': before.strings == after.strings, 'opaque_data_equal': data_equal,
            'source_metadata_sha256': before.metadata_sha256, 'saved_metadata_sha256': after.metadata_sha256,
            'all_compared_data_equal': not (missing or added or changed or root_changes)
                                       and before.strings == after.strings and data_equal,
            'coverage': 'Exact string table, known member identity/type/field bytes, library metadata and opaque data bytes. '
                        'Does not prove the payload can render or import correctly.'}
    if normalized_frames:
        result['normalized_mesh_frame_padding']=normalized_frames
        result['coverage']='String table, known members/library and opaque payload; only proven reserved MeshDEV v3 frame bytes 158..159 are projected out. Meaningful fields and raw metadata hashes remain bound; no rendering claim.'
    return result
