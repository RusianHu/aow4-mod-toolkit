"""Small, explicit native CLB template selections; no implicit dependency closure."""
import json
from pathlib import Path
import shutil

from . import asset_yaml, content_libraries, native, native_objects, native_validation
from .core import FormatError
from .filesystem import file_hash, new_directory
from .native_overlays import locked_inputs

# Only reviewed mesh/model/effect-material resource families. Shader/texture
# payloads are intentionally not expanded by this template workflow.
TYPES = {0x410035, 0x41006c, 0x41004b, 0x41166f}


def selection(path, locators):
    if not isinstance(locators, list) or not 1 <= len(locators) <= 16:
        raise FormatError('Select 1..16 explicit CLB template locators')
    if any(not isinstance(x, str) or not content_libraries.LOCATOR.fullmatch(x) for x in locators):
        raise FormatError('Invalid CLB template locator')
    if len({x.casefold() for x in locators}) != len(locators):
        raise FormatError('Duplicate CLB template selection')
    library = content_libraries.read(path)
    rows = []
    for locator in locators:
        matches = [m['identity'] for m in library.members if m['identity']['locator'] and
                   m['identity']['locator'].casefold() == locator.casefold()]
        if len(matches) != 1 or int(matches[0]['type_id'], 16) not in TYPES:
            raise FormatError('Template is missing or outside the reviewed mesh/model/material classes: '+locator)
        rows.append(matches[0])
    return library.name, rows


def extract(path, locators, game, output, cache_dir=Path('.aow4mod/native'), timeout=60):
    path, game = Path(path).resolve(), Path(game).resolve()
    if path.stat().st_size > 256*1024*1024:
        raise FormatError('Template source exceeds 256 MiB')
    name, rows = selection(path, locators)
    digest = file_hash(path)
    cache_dir = native.check_cache_directory(cache_dir, game, [path.parent, Path(output).resolve()])
    language = game/'Language/EN/EN.MO'
    params = {'path': str(path), 'sha256': digest, 'language': str(language),
              'language_sha256': file_hash(language), 'max_bytes': 64*1024*1024,
              'selection': rows, 'library_name': name}
    with locked_inputs([path,language,game/native.DLL]), new_directory(output, [path.parent, game]) as stage:
        first = native.run_worker(game, 'library-extract', params, cache_dir, timeout)
        binary, text = first['payload']['artifacts']
        def read_checked(artifact):
            from .core import sha
            raw=Path(artifact['file']).read_bytes()
            if sha(raw)!=artifact['sha256']:raise FormatError('Template artifact changed during verification')
            return raw
        document = asset_yaml.load(read_checked(text))
        expected = [{'cid': int(x['type_id'],16), 'locator': x['locator'], 'name': x['name']} for x in rows]
        if document['20_FName'] != name or asset_yaml.members(document) != expected:
            raise FormatError('Extracted template YAML identities differ from the explicit selection')
        subset = Path(binary['file'])
        _, second_rows = selection(subset, [x['locator'] for x in rows])
        second_params = {**params, 'path': str(subset), 'sha256': binary['sha256'], 'selection': second_rows}
        second = native.run_worker(game, 'library-extract', second_params, cache_dir, timeout)
        if (native.public_binding(first['binding'])!=native.public_binding(second['binding']) or
                native.public_binding(first['binding'])!=native.public_binding(native.profile_for(game))):
            raise FormatError('Native template backend changed during extraction')
        if file_hash(subset)!=binary['sha256']:
            raise FormatError('Template binary changed during independent loading')
        comparison = content_libraries.compare(subset, second['payload']['artifacts'][0]['file'])
        if not comparison['all_compared_data_equal']:
            raise FormatError('Extracted template native file readback differs')
        witnesses = []
        for left, right in zip(first['payload']['objects'], second['payload']['objects']):
            before, after = read_checked(left), read_checked(right)
            result = native_objects.compare_bodies(before, after)
            if not result['data_equal']:
                raise FormatError('Extracted template object changed after independent file loading')
            witnesses.append({'locator': left['locator'], **result})
        if file_hash(path) != digest or file_hash(language) != params['language_sha256']:
            raise FormatError('Template input changed during extraction')
        for artifact in (binary, text):
            if file_hash(artifact['file']) != artifact['sha256']:
                raise FormatError('Template output changed before copying')
            dest = stage/Path(artifact['file']).name
            shutil.copyfile(artifact['file'], dest)
            if file_hash(dest) != artifact['sha256']:
                raise FormatError('Template output changed during copying')
        report = {'format': 'aow4mod-asset-template-extract/1', 'source': str(path), 'source_sha256': digest,
                  'library': name, 'selection': rows, 'binding': native.public_binding(first['binding']),
                  'runs': [first['run'], second['run']], 'files': first['payload']['artifacts'],
                  'comparison': comparison, 'object_comparisons': witnesses,
                  'diagnostics': native_validation.diagnostics(first['run'])+native_validation.diagnostics(second['run']),
                  'coverage': 'Explicit native mesh/model/material definitions only; independent object and CLB file readback. '
                              'External links remain unchanged. Dependencies, model/mesh/material compatibility and rendering are not certified.'}
        (stage/'asset-template-extract.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


def worker(api, library, params, run_dir):
    """Pinned TEResourceLibrary list access, never edits the loaded source library."""
    import ctypes as c
    import struct
    from . import editing
    from .core import Node, sha
    profile = api.profile['asset_templates']
    getter=api.qword(api.qword(library)+api.profile['class_getter_vtable_offset'])
    if api.memory(getter,6)!=b'\xb8\x60\x00\x41\x00\xc3':
        raise FormatError('Native template source is not a content library')
    listing = library+profile['list_offset']
    getter = api.qword(api.qword(listing)+api.profile['class_getter_vtable_offset'])
    if api.memory(getter,6) != b'\xb8\x81\x00\x40\x00\xc3':
        raise FormatError('Native template resource-list class differs')
    count = int.from_bytes(api.memory(listing+0x18,4),'little')
    array = api.qword(listing+0x10)
    if not 1 <= count <= 100000 or not 1 <= len(params['selection']) <= 16:
        raise FormatError('Native template list/selection bounds differ')
    members, objects = [], []
    directory = Path(run_dir)/'template-objects'; directory.mkdir()
    for index, row in enumerate(params['selection']):
        ordinal, cid = row['ordinal'], int(row['type_id'],16)
        if type(ordinal) is not int or not 0 <= ordinal < count or cid not in TYPES:
            raise FormatError('Native template ordinal/type is outside its bound')
        pointer = api.qword(array+ordinal*8)
        getter = api.qword(api.qword(pointer)+api.profile['class_getter_vtable_offset'])
        if api.memory(getter,6) != b'\xb8'+struct.pack('<I',cid)+b'\xc3':
            raise FormatError('Native template object type differs')
        body = api.serialize_object(pointer)
        fields = Node(body,0,len(body)).mapping()
        if len(body)>16*1024*1024 or fields[0x14].string()!=row['locator']:
            raise FormatError('Native template locator/size differs')
        output = directory/f'{index}.bin';output.write_bytes(body)
        objects.append({'file':str(output),'sha256':sha(body),'locator':row['locator']})
        members.append(struct.pack('<I',cid)+body)
    handle = api.create(api.profile['clb_class_id']); holder = c.create_string_buffer(16)
    if not handle:raise FormatError('Template library factory failed')
    api.private('borrow_object_rva',[c.c_void_p,c.c_uint64],c.c_void_p)(holder,handle)
    fresh = struct.unpack_from('<Q',holder.raw)[0]
    if not fresh:raise FormatError('Template library borrowing failed')
    try:
        body = api.serialize_object(fresh);fields=Node(body,0,len(body)).mapping()
        desired = editing.rewrite_map(Node(body,0,len(body)),{
            0x14:editing.encode_value('string',params['library_name']),
            0x15:editing.replace_array_items(fields[0x15],members)})
        api.deserialize_object(fresh,desired)
        result = api.write_library(fresh,Path(params['path']).name,run_dir,params['max_bytes'])
        return {**result,'selection':params['selection'],'objects':objects,'library_name':params['library_name']}
    finally:
        api.private('release_raw_object_rva',[c.c_void_p])(fresh);api.release(handle)
