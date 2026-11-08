"""Private child-process entrypoint for native introspection. Never import to run."""
import ctypes as c
from collections import Counter
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import sys
import time
import struct
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aow4mod import native
from aow4mod.core import FormatError, Node, sha
from aow4mod.filesystem import file_hash, save_record
from aow4mod import native_objects


class Bindings:
    def __init__(self, binding):
        self.binding = binding
        self.profile = binding['profile']
        game = Path(binding['game'])
        c.windll.kernel32.SetErrorMode(0x8003)
        self.search = os.add_dll_directory(str(game))
        os.chdir(game)
        self.dll = c.CDLL(str(game/native.DLL))
        self.initialize = self.bind('BInitialize', [c.c_bool])
        self.finish = self.bind('BFinish', [c.c_int])
        self.count = self.bind('BClassesGetCount', [], c.c_int)
        self.cid_at = self.bind('BClassesGetCID', [c.c_int], c.c_uint32)
        self.class_name = self.bind('BERetrieveClassName', [c.c_uint32, c.c_void_p])
        self.create = self.bind('BCreateObject', [c.c_uint32], c.c_uint64)
        self.release = self.bind('BReleaseObject', [c.c_uint64])
        self.property_list = self.bind('BCreatePropertyList', [c.c_uint64, c.c_char_p, c.c_int, c.POINTER(c.c_int)], c.c_uint64)
        self.destroy_list = self.bind('BDestroyPropertyList', [c.c_uint64])
        self.property_at = self.bind('BPropertyListGet', [c.c_uint64, c.c_int, c.c_void_p])
        self.getters = {}
        for name in ['Ordinal', 'Float', 'Bool', 'String']:
            self.getters[name] = self.bind('BGetProperty'+name, [c.c_uint64, c.c_char_p, c.c_char_p, c.c_void_p, c.c_int], c.c_int)

    def bind(self, name, args, result=None):
        fn = getattr(self.dll, name)
        fn.argtypes, fn.restype = args, result
        return fn

    def types(self):
        if hasattr(self, '_registry'):
            return self._registry
        count = self.count()
        if not 0 < count <= 100000:
            raise FormatError('Invalid native class count')
        result = []
        for index in range(count):
            cid = self.cid_at(index)
            name = c.create_string_buffer(65536)
            self.class_name(cid, name)
            result.append({'cid': f'{cid:08X}', 'name': name.value.decode('utf-8', 'strict')})
        self._registry = {'types': sorted(result, key=lambda r: (r['name'].casefold(), r['cid']))}
        return self._registry

    def default(self, obj, field):
        codec = field['codec']
        if codec is None:
            return {'default_status': 'not_applicable' if field['separator'] else 'unsupported_editor'}
        if codec in ('i32', 'enum_i32'):
            name, value = 'Ordinal', c.c_int32()
        elif codec == 'f32':
            name, value = 'Float', c.c_float()
        elif codec == 'bool':
            name, value = 'Bool', c.c_ubyte()
        elif codec == 'string':
            name, value = 'String', c.create_string_buffer(1024*1024)
        else:
            return {'default_status': 'unsupported_codec'}
        try:
            status = self.getters[name](obj, field['category'].encode(), field['name'].encode(), c.byref(value), -1)
        except OSError as exc:
            # Some constructor-only getters require an initialized content context.
            # Keep the field visible, but do not invent a default from a failed read.
            return {'default_status': 'getter_failed', 'getter_error': str(exc)}
        if status != 0:
            return {'default_status': 'getter_failed', 'getter_status': status}
        actual = value.value
        if codec == 'bool':
            if actual not in (0, 1):
                raise FormatError('Native bool is not zero or one')
            actual = bool(actual)
        if codec == 'f32' and not math.isfinite(actual):
            return {'default_status': 'nonfinite', 'getter_status': status}
        if codec == 'string':
            if b'\0' not in value.raw:
                raise FormatError('Native string exceeds the output bound')
            actual = actual.decode('utf-8', 'strict')
        return {'default_status': 'read', 'getter_status': status, 'default': actual}

    def properties(self, params):
        cid = native.parse_cid(params['cid'])
        exact = [r for r in self.types()['types'] if r['cid'] == cid]
        if len(exact) != 1 or exact[0]['name'] != params['name']:
            raise FormatError('Native type identity changed before construction')
        obj = self.create(int(cid, 16))
        if not obj:
            raise FormatError('Native factory did not construct this type')
        try:
            return {**params, 'properties': self.property_records(obj, defaults=True)}
        finally:
            self.release(obj)

    def property_records(self, obj, defaults=False, selected=None):
        listing = None
        try:
            count = c.c_int()
            listing = self.property_list(obj, b'', -1, c.byref(count))
            if not 0 <= count.value <= 5000 or (count.value and not listing):
                raise FormatError('Invalid native property list')
            fields = []
            for index in range(count.value):
                # Extra zeroed guard space; parsing is restricted to the reviewed ABI.
                raw = c.create_string_buffer(self.profile['property_size']+4096)
                self.property_at(listing, index, raw)
                if any(raw.raw[self.profile['property_size']:]):
                    raise FormatError('Native property write exceeded the ABI size')
                field = native.parse_property(raw.raw[:self.profile['property_size']], self.profile)
                if defaults and (selected is None or any(choice.get('name')==field['name'] and
                        ('category' not in choice or choice['category']==field['category']) for choice in selected)):
                    field.update(self.default(obj, field))
                fields.append(field)
            return fields
        finally:
            if listing:
                self.destroy_list(listing)

    def private(self, key, args, result=None):
        return c.CFUNCTYPE(result, *args)(self.dll._handle+self.profile[key])

    def memory(self, address, size):
        read = c.windll.kernel32.ReadProcessMemory
        read.argtypes = [c.c_void_p, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]
        read.restype = c.c_int
        buffer, count = c.create_string_buffer(size), c.c_size_t()
        if not read(c.c_void_p(-1), address, buffer, size, c.byref(count)) or count.value != size:
            raise FormatError('Native object/stream pointer is not readable')
        return buffer.raw

    def qword(self, address):
        return int.from_bytes(self.memory(address, 8), 'little')

    @contextmanager
    def borrowed(self, handle, expected_cid):
        borrow = self.private('borrow_object_rva', [c.c_void_p, c.c_uint64], c.c_void_p)
        release = self.private('release_raw_object_rva', [c.c_void_p])
        holder = c.create_string_buffer(16)
        borrow(holder, handle)
        pointer = struct.unpack_from('<Q', holder.raw)[0]
        if not pointer:
            raise FormatError('Native object handle could not be resolved')
        try:
            getter = self.qword(self.qword(pointer)+self.profile['class_getter_vtable_offset'])
            code = self.memory(getter, 6)
            if code[:1] != b'\xb8' or code[5:] != b'\xc3' or int.from_bytes(code[1:5], 'little') != expected_cid:
                raise FormatError('Native object class getter differs from the requested class')
            inline=f'{expected_cid:08X}' in self.profile.get('inline_resource_objects',{})
            if not inline and expected_cid not in (0x0040009d, 0x0400001a,self.profile['surface_class_id']):
                method = self.qword(self.qword(pointer)+self.profile['object_is_class_vtable_offset'])
                is_resource = c.CFUNCTYPE(c.c_bool, c.c_void_p, c.c_uint32)(method)
                if not is_resource(pointer, self.profile['resource_base_class']):
                    raise FormatError('Native construction only supports resource and package objects')
            yield pointer
        finally:
            release(pointer)

    @contextmanager
    def memory_stream(self, data=None):
        stream = c.create_string_buffer(self.profile['stream_size'])
        new = self.private('memory_stream_ctor_rva', [c.c_void_p], c.c_void_p)
        destroy = self.private('memory_stream_dtor_rva', [c.c_void_p])
        capacity = self.private('memory_stream_capacity_rva', [c.c_void_p, c.c_int])
        new(stream)
        try:
            capacity(stream, max(1, len(data)) if data is not None else 0x200000)
            if data is not None:
                pointer = struct.unpack_from('<Q', stream.raw, self.profile['stream_data_offset'])[0]
                if not pointer:
                    raise FormatError('Native input stream allocation failed')
                c.memmove(pointer, data, len(data))
                c.c_int.from_buffer(stream, self.profile['stream_length_offset']).value = len(data)
                c.c_int.from_buffer(stream, self.profile['stream_position_offset']).value = 0
            yield stream
        finally:
            destroy(stream)

    def serialize_object(self, pointer, maximum=16*1024*1024):
        if type(maximum) is not int or not 1<=maximum<=256*1024*1024:raise FormatError('Native object serialization bound is invalid')
        serialize = self.private('serialize_object_rva', [c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p, c.c_int], c.c_int)
        with self.memory_stream() as stream:
            status = serialize(stream, pointer, None, None, 0)
            size = struct.unpack_from('<I', stream.raw, self.profile['stream_length_offset'])[0]
            data = struct.unpack_from('<Q', stream.raw, self.profile['stream_data_offset'])[0]
            if status != 0 or not data or not 1 <= size <= maximum:
                raise FormatError('Native serialized object exceeded its bounds or failed')
            result = self.memory(data, size)
            native_objects.storage_fields(result)
            return result

    def deserialize_object(self, pointer, data):
        native_objects.storage_fields(data)
        read_new = self.private('read_storage_ctor_rva', [c.c_void_p, c.c_void_p, c.c_int], c.c_void_p)
        read_destroy = self.private('read_storage_dtor_rva', [c.c_void_p])
        with self.memory_stream(data) as stream:
            reader = c.create_string_buffer(self.profile['read_storage_size'])
            read_new(reader, stream, 0)
            try:
                method = self.qword(self.qword(pointer)+self.profile['object_serialize_vtable_offset'])
                c.CFUNCTYPE(None, c.c_void_p, c.c_void_p)(method)(pointer, reader)
            finally:
                read_destroy(reader)

    def assign(self, handle, assignment):
        matches = [f for f in self.property_records(handle) if
                   f['name'] == assignment['name'] and f['category'] == assignment['category']]
        if len(matches) != 1:
            raise FormatError('Native assignment property disappeared or became ambiguous')
        field = matches[0]
        if any(field[key] != assignment[key] for key in ('codec', 'editor_cid', 'editor_parameter')):
            raise FormatError('Native property descriptor differs from its reviewed assignment')
        value = native_objects.normalized_value(field['codec'], assignment['value'])
        kind, argtype = {'i32': ('Ordinal', c.c_int32), 'enum_i32': ('Ordinal', c.c_int32),
                         'f32': ('Float', c.c_float), 'bool': ('Bool', c.c_bool),
                         'string': ('String', c.c_char_p)}[field['codec']]
        setter = self.bind('BSetProperty'+kind, [c.c_uint64, c.c_char_p, c.c_char_p, argtype, c.c_int], c.c_int)
        status = setter(handle, field['category'].encode(), field['name'].encode(),
                        value.encode('utf-8') if kind == 'String' else value, -1)
        actual = self.default(handle, field)
        if (status != 0 or actual.get('default_status') != 'read' or
                type(actual.get('default')) is not type(value) or actual.get('default') != value):
            raise FormatError('Native scalar setter did not retain the requested value')

    def construct_object(self, params, run_dir, read_existing=False):
        cid = int(native.parse_cid(params['cid']), 16)
        selected = [r for r in self.types()['types'] if r['cid'] == params['cid'] and r['name'] == params['name']]
        if len(selected) != 1:
            raise FormatError('Native object class does not match the registered identity')
        first = self.create(cid)
        if not first:
            raise FormatError('Native object factory returned no object')
        try:
            with self.borrowed(first, cid) as pointer:
                if read_existing:
                    data = bytes.fromhex(params['body_hex'])
                    if not 1 <= len(data) <= 16*1024*1024 or sha(data) != params['body_sha256']:
                        raise FormatError('Native object input body size/hash mismatch')
                    native_objects.storage_fields(data)
                else:
                    for assignment in params['assignments']:
                        self.assign(first, assignment)
                    data = self.serialize_object(pointer)
                    if params.get('package_id') is not None:
                        from aow4mod.editing import rewrite_map
                        if cid != 0x0040009d or params.get('role') != 'package':
                            raise FormatError('Package identity supplied for a non-package constructor')
                        package_id = uuid.UUID(params['package_id'])
                        if package_id.int == 0:
                            raise FormatError('Native package construction requires a nonzero UUID')
                        root = Node(data, 0, len(data))
                        body = root.mapping()[cid]
                        data = rewrite_map(root, {cid: rewrite_map(body, {0x2a: package_id.bytes_le})})
                    if params.get('identity') is not None:
                        from aow4mod.authoring import string
                        from aow4mod.editing import rewrite_map
                        identity = native_objects.validate_identity(params['identity'])
                        data = rewrite_map(Node(data, 0, len(data)), {
                            0x14: string(identity['locator']), 0x15: string(identity['name']),
                            # The native pack loader/save path marks definitions
                            # with this same observed resource metadata byte.
                            0x17: b'\x01', 0x18: struct.pack('<Q', int(identity['id'], 16))})
        finally:
            self.release(first)
        second = self.create(cid)
        if not second:
            raise FormatError('Native verification object factory returned no object')
        package_version = None
        properties = []
        nested_objects = []
        try:
            with self.borrowed(second, cid) as pointer:
                self.deserialize_object(pointer, data)
                if cid == 0x40009d:
                    package_version = {key: int.from_bytes(self.memory(pointer+offset, 4), 'little')
                                       for key, offset in self.profile['package_version_offsets'].items()}
                after = self.serialize_object(pointer)
                readbacks = [{'name': a['name'], 'category': a['category'], **self.default(second, a)}
                             for a in params.get('assignments', [])]
                if params.get('include_properties'):
                    if params.get('object_path'):
                        properties,nested_objects = self.nested_properties(second,pointer,params['object_path'],run_dir)
                        if self.serialize_object(pointer)!=after:
                            raise FormatError('Nested property inspection mutated its loaded owner')
                    else:
                        properties = self.property_records(second, defaults=True, selected=params.get('property_filter'))
        finally:
            self.release(second)
        source, saved = Path(run_dir)/'object-source.bin', Path(run_dir)/'object-serialized.bin'
        if source.exists() or saved.exists():
            raise FormatError('Native object output already exists')
        source.write_bytes(data)
        saved.write_bytes(after)
        return {'cid': params['cid'], 'name': params['name'], 'source_file': str(source), 'source_sha256': sha(data),
                'output_file': str(saved), 'output_sha256': sha(after), 'readbacks': readbacks,
                'package_version': package_version, 'properties': properties,
                **({'nested_objects':nested_objects} if params.get('object_path') else {})}

    def nested_properties(self, owner_handle, owner_pointer, selectors, run_dir):
        from aow4mod.native_inspection import validate_object_path
        selectors=validate_object_path(selectors)
        profile=self.profile['nested_properties']
        getter=c.CFUNCTYPE(c.c_int,c.c_void_p,c.c_char_p,c.c_char_p,c.c_void_p,c.c_int)(self.dll._handle+profile['get_rva'])
        fields=self.property_records(owner_handle)
        pointer=owner_pointer
        chain=[]
        addresses={pointer}

        def get(ptr,field):
            result=c.create_string_buffer(profile['result_size'])
            status=getter(ptr,field['category'].encode('utf-8'),field['name'].encode('utf-8'),result,-1)
            recorded=struct.unpack_from('<i',result.raw,profile['status_offset'])[0]
            if status!=recorded:raise FormatError('Native nested property status disagrees with its result')
            return status,result.raw

        for i,selector in enumerate(selectors):
            matches=[f for f in fields if f['name']==selector['name'] and ('category' not in selector or f['category']==selector['category'])]
            if len(matches)!=1:raise FormatError('Nested object property is missing or ambiguous')
            field=matches[0]
            if field['editor_cid'] not in profile['object_editors']:
                raise FormatError('Nested navigation supports reviewed owned-object editors only, not links/lists/scalars')
            status,result=get(pointer,field)
            if status!=0:raise FormatError('Native nested object getter failed')
            pointer=struct.unpack_from('<Q',result)[0]
            if not pointer or pointer in addresses:raise FormatError('Native nested object is null or cyclic')
            addresses.add(pointer)
            method=self.qword(self.qword(pointer)+self.profile['class_getter_vtable_offset'])
            code=self.memory(method,6)
            if code[0]!=0xb8 or code[5]!=0xc3:raise FormatError('Unsupported nested object class getter')
            cid=f'{int.from_bytes(code[1:5],"little"):08X}'
            types=[r for r in self.types()['types'] if r['cid']==cid]
            if len(types)!=1:raise FormatError('Nested object class is absent from the native registry')
            body=self.serialize_object(pointer)
            artifact=Path(run_dir)/f'nested-{i}.bin'
            artifact.write_bytes(body)
            chain.append({'selector':selector,'descriptor':field,'getter_status':status,
                          'cid':cid,'name':types[0]['name'],'file':str(artifact),'sha256':sha(body)})
            # Only descriptors come from a fresh instance of the observed type.
            # All scalar values below are read from the parent's actual child.
            metadata=self.create(int(cid,16))
            if not metadata:raise FormatError('Nested class descriptor object could not be constructed')
            try:fields=self.property_records(metadata)
            finally:self.release(metadata)
        before=self.serialize_object(pointer)
        values=[]
        for field in fields:
            item=dict(field);codec=field['codec']
            if codec not in ('i32','enum_i32','bool','f32'):
                item['default_status']='not_applicable' if field['separator'] else 'unsupported_nested_editor'
            else:
                status,result=get(pointer,field)
                item['getter_status']=status
                if status!=0:item['default_status']='getter_failed'
                else:
                    value=struct.unpack_from('<f' if codec=='f32' else '<i',result)[0] if codec!='bool' else result[0]
                    if codec=='bool':
                        if value not in (0,1):raise FormatError('Nested bool getter did not return zero or one')
                        value=bool(value)
                    if codec=='f32' and not math.isfinite(value):item['default_status']='nonfinite'
                    else:item.update(default_status='read',default=value)
            values.append(item)
        if self.serialize_object(pointer)!=before:raise FormatError('Nested scalar getters mutated their object')
        return values,chain

    def edit_object(self,params,run_dir):
        cid=int(native.parse_cid(params['cid']),16)
        if params['cid'] in native_objects.CONTAINERS or cid==self.profile['surface_class_id']:
            raise FormatError('Native scalar editing supports resources and reviewed inline objects only')
        if not any(row['cid']==params['cid'] and row['name']==params['name'] for row in self.types()['types']):
            raise FormatError('Native edit class identity changed')
        data=bytes.fromhex(params['body_hex'])
        if not 1<=len(data)<=16*1024*1024 or sha(data)!=params['body_sha256']:
            raise FormatError('Native edit input body size/hash mismatch')
        native_objects.storage_fields(data)
        assignments=params['assignments']
        if not isinstance(assignments,list) or not 1<=len(assignments)<=32:
            raise FormatError('Native edit requires 1..32 scalar assignments')

        @contextmanager
        def loaded(body):
            handle=self.create(cid)
            if not handle:raise FormatError('Native edit constructor returned no object')
            try:
                with self.borrowed(handle,cid) as pointer:
                    self.deserialize_object(pointer,body)
                    yield handle,pointer
            finally:self.release(handle)

        def actual(handle,assignment):
            matches=[field for field in self.property_records(handle) if
                     field['name']==assignment['name'] and field['category']==assignment['category']]
            if len(matches)!=1 or any(matches[0][key]!=assignment[key] for key in ('codec','editor_cid','editor_parameter')):
                raise FormatError('Native edit selected property descriptor changed before getter access')
            field=matches[0];value=self.default(handle,field)
            if value.get('default_status')!='read' or value.get('getter_status')!=0:
                raise FormatError('Native edit selected getter is unavailable')
            return {key:field[key] for key in ('name','category','codec','editor_cid','editor_parameter')}|value

        def save(name,body):
            path=Path(run_dir)/name
            if path.exists():raise FormatError('Native edit artifact already exists')
            path.write_bytes(body)
            return str(path),sha(body)

        witnesses=[]
        for index,assignment in enumerate(assignments):
            with loaded(data) as (handle,pointer):
                before_value=actual(handle,assignment)
                expected=assignment['expected_before']
                if type(before_value['default']) is not type(expected) or before_value['default']!=expected:
                    raise FormatError('Native edit getter changed since property selection')
                before=self.serialize_object(pointer)
                probe={**assignment,'value':assignment['probe_value']}
                self.assign(handle,probe)
                probe_value=actual(handle,probe)
                after=self.serialize_object(pointer)
            a,ah=save(f'edit-probe-{index}-before.bin',before)
            b,bh=save(f'edit-probe-{index}-after.bin',after)
            witnesses.append({'property':assignment['property'],'before_file':a,'before_sha256':ah,
                'after_file':b,'after_sha256':bh,'before_readback':before_value,'probe_readback':probe_value})
        with loaded(data) as (handle,pointer):
            before_values=[actual(handle,assignment) for assignment in assignments]
            baseline=self.serialize_object(pointer)
            for assignment in assignments:self.assign(handle,assignment)
            edited=self.serialize_object(pointer)
        with loaded(edited) as (handle,pointer):
            after=self.serialize_object(pointer)
            readbacks=[actual(handle,assignment) for assignment in assignments]
        input_file,input_hash=save('edit-input.bin',data)
        baseline_file,baseline_hash=save('edit-baseline.bin',baseline)
        source,source_hash=save('object-source.bin',edited)
        output,output_hash=save('object-serialized.bin',after)
        return {'cid':params['cid'],'name':params['name'],'input_file':input_file,'input_sha256':input_hash,
                'baseline_file':baseline_file,'baseline_sha256':baseline_hash,'source_file':source,'source_sha256':source_hash,
                'output_file':output,'output_sha256':output_hash,'before_values':before_values,'readbacks':readbacks,
                'mapping_witnesses':witnesses,'package_version':None,'properties':[]}

    def apply_package_records(self,content,target,params,run_dir,dependencies_only=False):
        pack=self.qword(content+self.profile['rpk_library_offset'])
        def class_id(pointer):
            getter=self.qword(self.qword(pointer)+self.profile['class_getter_vtable_offset'])
            data=self.memory(getter,6)
            if data[:1]!=b'\xb8' or data[5:]!=b'\xc3':raise FormatError('Unknown native overlay class getter')
            return int.from_bytes(data[1:5],'little')
        if class_id(pack)!=self.profile['rpk_class_id']:raise FormatError('Overlay target is not a resource pack')
        count=int.from_bytes(self.memory(pack+self.profile['pack_libraries_count_offset'],4),'little')
        array=self.qword(pack+self.profile['pack_libraries_data_offset'])
        if not 1<=count<=256:raise FormatError('Native overlay library count exceeds its bound')
        libraries=[]
        for index in range(count):
            pointer=self.qword(array+index*8);cid=class_id(pointer)
            if cid not in self.profile['resource_library_classes']:raise FormatError('Unsupported native overlay library class')
            method=self.qword(self.qword(pointer)+self.profile['library_apply_mods_vtable_offset'])
            if method!=self.dll._handle+self.profile['library_apply_mods_rva']:raise FormatError('Native library overlay method differs from its profile')
            libraries.append(pointer)
        directory=Path(run_dir)/'native-applied';directory.mkdir()
        def save(name):
            folder=directory/name;folder.mkdir()
            return self.save_rpk_copy(content,target,folder)
        baseline=save('baseline');steps=[]
        records=params['modifications']
        if not isinstance(records,list) or not 1<=len(records)<=32:raise FormatError('Overlay requires 1..32 selected package records')
        for index,record in enumerate(records):
            source=Path(record['file'])
            if file_hash(source)!=record['sha256']:raise FormatError('Overlay ACP changed before loading')
            body=bytes.fromhex(record['body_hex'])
            if not 1<=len(body)<=16*1024*1024 or sha(body)!=record['body_sha256']:raise FormatError('Invalid overlay record size/hash')
            handle=self.create(self.profile['mod_data_class_id'])
            if not handle:raise FormatError('Native Mod-data construction failed')
            holder=c.create_string_buffer(16);pointer=None
            try:
                self.private('borrow_object_rva',[c.c_void_p,c.c_uint64],c.c_void_p)(holder,handle)
                pointer=struct.unpack_from('<Q',holder.raw)[0]
                if not pointer or class_id(pointer)!=self.profile['mod_data_class_id']:raise FormatError('Native Mod-data class differs')
                self.deserialize_object(pointer,body)
                readback=self.serialize_object(pointer);readback_file=directory/f'mod-data-{index}.bin';readback_file.write_bytes(readback)
                listing=pointer+self.profile['mod_data_list_offset']
                size=int.from_bytes(self.memory(listing+self.profile['mod_list_count_offset'],4),'little')
                data=self.qword(listing+self.profile['mod_list_data_offset'])
                if not 1<=size<=4096 or size!=len(record['resources']):raise FormatError('Native overlay resource count differs')
                entries=[];items=[]
                for ordinal,expected in enumerate(record['resources']):
                    item=self.qword(data+ordinal*8)
                    if class_id(item)!=self.profile['mod_resource_class_id']:raise FormatError('Native overlay record class differs')
                    resource=self.qword(item+self.profile['mod_record_resource_offset'])
                    if not resource:raise FormatError('Null native update resources are unsupported')
                    rid=self.qword(resource+self.profile['resource_id_offset']);cid=class_id(resource)
                    operation=int.from_bytes(self.memory(item+self.profile['mod_record_operation_offset'],4),'little')
                    if f'{rid:016X}'!=expected['id'] or f'{cid:08X}'!=expected['cid'] or operation!=0x40:
                        raise FormatError('Native overlay resource identity/type/operation differs')
                    before=self.memory(item+self.profile['mod_record_applied_offset'],1)[0]
                    if before!=0:raise FormatError('Native overlay resource was already marked applied')
                    items.append(item);entries.append({'id':expected['id'],'cid':expected['cid'],'operation':operation,'applied_before':before})
                if dependencies_only:
                    self.private('pack_collect_mod_dependencies_rva',[c.c_void_p,c.c_void_p])(pack,pointer)
                else:
                    apply_list=self.private('library_apply_mods_rva',[c.c_void_p,c.c_void_p])
                    for library_pointer in libraries:apply_list(library_pointer,listing)
                for item,entry in zip(items,entries):entry['applied_after']=self.memory(item+self.profile['mod_record_applied_offset'],1)[0]
                output=save(f'step-{index}')
                if file_hash(source)!=record['sha256']:raise FormatError('Overlay ACP changed during application')
                steps.append({'file':record['file'],'sha256':record['sha256'],'entries':entries,
                    'mod_data_file':str(readback_file),'mod_data_sha256':sha(readback),'output':output})
            finally:
                if pointer:self.private('release_raw_object_rva',[c.c_void_p])(pointer)
                self.release(handle)
        return {'baseline':baseline,'steps':steps,'library_classes':[f'{class_id(pointer):08X}' for pointer in libraries]}

    def read_content(self, params, run_dir, save=False, library=False, overlays=False,dependencies_only=False,model_check=False,map_check=False,map_entity_check=False,map_terrain_check=False,effect_program_check=False,material_check=False,material_objects=False,material_texture_check=False,animation_pose_check=False,model_pose_check=False,skinning_check=False):
        target = Path(params['path'])
        language = Path(params['language'])
        if file_hash(target) != params['sha256']:
            raise FormatError('Witness input changed before native loading')
        if file_hash(language) != params['language_sha256']:
            raise FormatError('Witness language changed before native loading')
        update = self.bind('BUpdate', [])
        add_path = self.bind('BContentManagerAddSearchPath', [c.c_char_p])
        imports = self.bind('BContentManagerImportPackages', [c.c_char_p]*3)
        busy = self.bind('BContentManagerIsPackageBusy', [], c.c_bool)
        enable = self.bind('BContentManagerEnablePackages', [c.c_int])
        open_content = self.bind('BContentManagerOpenContent', [c.c_char_p]*2, c.c_void_p)
        close_content = self.bind('BContentManagerCloseContent', [c.c_void_p])
        callback_type = c.CFUNCTYPE(None, c.c_void_p, c.c_uint32, c.c_int)
        loca_type = c.CFUNCTYPE(None, c.c_void_p, c.c_char_p, c.c_char_p, c.c_uint64, c.c_uint64)
        set_callback = self.bind('BContentManagerSetOpenContentCallback', [callback_type])
        enumerate_loca = self.bind('BContentEnumerateLocaTags', [c.c_void_p, loca_type])
        callbacks, tokens, callback_errors = [], Counter(), []

        @callback_type
        def callback(content, handle, result):
            callbacks.append((content, result))

        @loca_type
        def loca(content, tag, source, obj, parent):
            try:
                if tag:
                    tokens[tag.decode('utf-8', 'strict')] += 1
            except Exception as exc:
                callback_errors.append(str(exc))

        def directory(path):
            return (str(path).rstrip('\\/')+'\\').encode('utf-8')

        game = Path(self.binding['game'])
        libraries=params.get('libraries',[])
        if native.library_context([row['file'] for row in libraries])!=libraries:
            raise FormatError('Native CLB context changed before content loading')
        from aow4mod import native_context
        packs=params.get('packs',[])
        if native_context.bind([row['file'] for row in packs])!=packs:
            raise FormatError('Native RPK context changed before content loading')
        from aow4mod import language_context
        catalogs=params.get('languages',[])
        if language_context.bind([row['file'] for row in catalogs])!=catalogs:
            raise FormatError('Native MO context changed before content loading')
        catalog_directory=Path(run_dir)/'language-context'
        if catalogs:
            catalog_directory.mkdir()
            for row in catalogs:
                copied=catalog_directory/Path(row['file']).name.upper()
                copied.write_bytes(Path(row['file']).read_bytes())
                if file_hash(copied)!=row['sha256']:raise FormatError('Native MO context changed while staging')
        empty = Path(run_dir)/'empty-packages'
        empty.mkdir()
        add_path(directory(game/'Language'))
        add_path(directory(target.parent))
        for folder in sorted({str(Path(row['file']).parent) for row in libraries}):add_path(directory(Path(folder)))
        for folder in sorted({str(Path(row['file']).parent) for row in packs}):add_path(directory(Path(folder)))
        if catalogs:add_path(directory(catalog_directory))
        imports(directory(game/'Content'), directory(empty), b'')
        deadline = time.monotonic()+20
        while busy() and time.monotonic() < deadline:
            update()
            time.sleep(0.005)
        if busy():
            raise FormatError('Native package registration timed out')
        enable(0x7fffffff)
        set_callback(callback)
        # Resource validators require real localized names. Without this setup,
        # hundreds of original properties look invalid in a fresh headless host.
        language_content = open_content(language.name.encode('utf-8'), str(language).encode('utf-8'))
        deadline = time.monotonic()+20
        while not any(row[0] == language_content for row in callbacks) and time.monotonic() < deadline:
            update()
            time.sleep(0.005)
        language_callbacks = [row for row in callbacks if row[0] == language_content]
        if not language_content or not language_callbacks or language_callbacks[-1][1] != 0:
            raise FormatError('Native language catalog did not finish loading')
        # Keep each explicitly selected catalog alive until the child finishes.
        for row in catalogs:
            path=catalog_directory/Path(row['file']).name.upper()
            handle=open_content(path.name.encode('utf8'),str(path).encode('utf8'))
            deadline=time.monotonic()+20
            while not any(event[0]==handle for event in callbacks) and time.monotonic()<deadline:
                update();time.sleep(.005)
            events=[event for event in callbacks if event[0]==handle]
            if not handle or not events or events[-1][1]!=0:raise FormatError('Native bundled language did not load')
        callbacks.clear()
        content = open_content(target.name.encode('utf-8'), str(target).encode('utf-8'))
        opened_material_content=[];material_libraries={}
        def open_material_library(value):
            path=Path(value).resolve()
            if str(path) in material_libraries:return material_libraries[str(path)]
            handle=open_content(path.name.encode('utf-8'),str(path).encode('utf-8'));opened_material_content.append(handle)
            deadline=time.monotonic()+20
            while not any(row[0]==handle for row in callbacks) and time.monotonic()<deadline:update();time.sleep(.005)
            found=[row for row in callbacks if row[0]==handle]
            if not handle or not found or found[-1][1]!=0:raise FormatError('Material target library did not load')
            pointer=self.qword(handle+self.profile['clb_library_offset']);material_libraries[str(path)]=pointer
            return pointer
        try:
            deadline = time.monotonic()+20
            while not any(row[0] == content for row in callbacks) and time.monotonic() < deadline:
                update()
                time.sleep(0.005)
            matching_callbacks = [row for row in callbacks if row[0] == content]
            if not matching_callbacks or not content:
                raise FormatError('Native content did not finish opening')
            if not library:
                enumerate_loca(content, loca)
            if callback_errors:
                raise FormatError('Invalid native localization text: '+callback_errors[0])
            if file_hash(target) != params['sha256']:
                raise FormatError('Witness input changed during native loading')
            if file_hash(language) != params['language_sha256']:
                raise FormatError('Witness language changed during native loading')
            if any(file_hash(row['file'])!=row['sha256'] for row in libraries):raise FormatError('Native CLB context changed during content loading')
            if any(file_hash(row['file'])!=row['sha256'] for row in packs):raise FormatError('Native RPK context changed during content loading')
            if any(file_hash(row['file'])!=row['sha256'] for row in catalogs):raise FormatError('Native MO context changed during content loading')
            result = {'sha256': params['sha256'], 'language_sha256': params['language_sha256'],
                    'callback_result': matching_callbacks[-1][1],
                    'content_present': bool(content), 'tokens': dict(sorted(tokens.items())),
                    'token_count': sum(tokens.values()),'context_libraries':libraries}
            if packs:result['context_packs']=packs
            if catalogs:result['context_languages']=catalogs
            if dependencies_only:result['dependencies_only']=True
            if overlays:result['application']=self.apply_package_records(content,target,params,run_dir,dependencies_only)
            if save:
                result.update(self.save_rpk_copy(content, target, run_dir))
                if file_hash(target) != params['sha256']:
                    raise FormatError('Native writer changed the source instead of only its diagnostic copy')
            if library:
                if skinning_check:
                    from aow4mod import native_skinning
                    result.update(native_skinning.worker(self,content,params,run_dir))
                elif model_pose_check:
                    from aow4mod import native_model_poses
                    result.update(native_model_poses.worker(self,content,params,run_dir))
                elif animation_pose_check:
                    from aow4mod import native_animation_poses
                    result.update(native_animation_poses.worker(self,content,params,run_dir))
                elif material_texture_check:
                    from aow4mod import material_textures
                    result.update(material_textures.worker(self,content,params,run_dir,open_material_library))
                elif material_check or material_objects:
                    from aow4mod import material_links
                    result.update(material_links.object_worker(self,content,params,run_dir) if material_objects else material_links.worker(self,content,params,run_dir,open_material_library))
                elif effect_program_check:
                    from aow4mod.native_effect_programs import worker as effect_worker
                    result.update(effect_worker(self,content,params,run_dir))
                elif map_terrain_check:
                    from aow4mod.native_map_terrain import worker as terrain_worker
                    result.update(terrain_worker(self,content,params,run_dir))
                elif map_entity_check:
                    if params.get('entity_scope'):
                        if params['entity_scope']!='zones_and_cells_v1':raise FormatError('Unsupported map entity inspection scope')
                        from aow4mod.native_world_entities import worker as map_entity_worker
                    else:
                        from aow4mod.native_map_entities import worker as map_entity_worker
                    result.update(map_entity_worker(self,content,params,run_dir))
                elif map_check:
                    from aow4mod.native_maps import worker as map_worker
                    result.update(map_worker(self,content,params,run_dir))
                elif model_check:
                    from aow4mod.model_links import worker as model_link_worker
                    result.update(model_link_worker(self,content,params,run_dir))
                elif params.get('selection') is not None:
                    from aow4mod import asset_templates
                    result.update(asset_templates.worker(self,self.qword(content+self.profile['clb_library_offset']),params,run_dir))
                else:
                    result.update(self.save_library_copy(content, target, run_dir, params['max_bytes']))
                if file_hash(target) != params['sha256']:
                    raise FormatError('Native library writer changed its source')
            return result
        finally:
            if content and callbacks:
                close_content(content)
            for handle in reversed(opened_material_content):
                if handle and handle!=content:close_content(handle)
            # Keep language content registered until BFinish tears down the child.

    def save_rpk_copy(self, content, target, run_dir):
        """Profiled private engine writer; no caller-controlled address or output path."""
        read = c.windll.kernel32.ReadProcessMemory
        read.argtypes = [c.c_void_p, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)]
        read.restype = c.c_int

        def memory(address, size):
            buffer, count = c.create_string_buffer(size), c.c_size_t()
            if not read(c.c_void_p(-1), address, buffer, size, c.byref(count)) or count.value != size:
                raise FormatError('Native resource-pack pointer is not readable')
            return buffer.raw

        def qword(address):
            return int.from_bytes(memory(address, 8), 'little')

        library = qword(content+self.profile['rpk_library_offset'])
        vtable = qword(library)
        class_getter = qword(vtable+self.profile['class_getter_vtable_offset'])
        code = memory(class_getter, 6)
        if code[0] != 0xb8 or code[5] != 0xc3:
            raise FormatError('Unrecognized native class getter')
        cid = int.from_bytes(code[1:5], 'little')
        if cid != self.profile['rpk_class_id']:
            raise FormatError('Native writer target is not a resource pack')
        output_dir = Path(run_dir)/'native-saved'
        output_dir.mkdir()
        output_file = output_dir/target.name
        if output_file.exists():
            raise FormatError('Native roundtrip output already exists')
        writer = c.CFUNCTYPE(c.c_int32, c.c_void_p, c.c_char_p, c.c_bool, c.c_bool)(
            self.dll._handle+self.profile['rpk_save_rva'])
        status = writer(library, str(output_file).encode('utf-8'), False, False)
        if status != 0 or not output_file.is_file():
            raise FormatError(f'Native RPK writer failed: {status}')
        return {'save_result': status, 'output_file': str(output_file),
                'output_sha256': file_hash(output_file), 'native_pack_class': f'{cid:08X}'}

    def save_library_copy(self, content, target, run_dir, maximum):
        library = self.qword(content+self.profile['clb_library_offset'])
        return self.write_library(library, target.name, run_dir, maximum)

    def surface_png(self,params,run_dir):
        from aow4mod import images
        source=Path(params['path'])
        with source.open('rb') as stream:data=stream.read(16*1024*1024+1)
        if len(data)>16*1024*1024:raise FormatError('Native surface packet exceeds 16 MiB')
        if sha(data)!=params['sha256']:raise FormatError('Native surface packet changed before reading')
        document=images.surface_yaml(params['width'],params['height'],data)
        name=self.private('pixel_format_name_rva',[c.c_uint16],c.c_char_p)(self.profile['surface_rgba_format'])
        if name!=b'R8G8B8A8UNorm':raise FormatError('Native RGBA format mapping differs from its reviewed profile')
        cid=self.profile['surface_class_id'];handle=self.create(cid)
        if not handle:raise FormatError('Native surface constructor failed')
        try:
            with self.borrowed(handle,cid) as pointer:
                with self.memory_stream(document) as stream:
                    reader=c.create_string_buffer(512)
                    self.private('yaml_read_ctor_rva',[c.c_void_p,c.c_void_p,c.c_int],c.c_void_p)(reader,stream,0)
                    try:
                        method=self.qword(self.qword(pointer)+self.profile['object_serialize_vtable_offset'])
                        c.CFUNCTYPE(None,c.c_void_p,c.c_void_p)(method)(pointer,reader)
                    finally:self.private('yaml_read_deleting_dtor_rva',[c.c_void_p,c.c_uint32],c.c_void_p)(reader,0)
                format_method=self.qword(self.qword(pointer)+self.profile['surface_format_vtable_offset'])
                pixel_format=c.CFUNCTYPE(c.c_uint16,c.c_void_p)(format_method)(pointer)
                if pixel_format!=self.profile['surface_rgba_format']:raise FormatError('Native surface did not retain RGBA8 format')
                with self.memory_stream() as stream:
                    writer=self.private('png_write_rva',[c.c_void_p,c.c_void_p,c.c_void_p],c.c_int)
                    status=writer(None,stream,pointer)
                    size=struct.unpack_from('<I',stream.raw,self.profile['stream_length_offset'])[0]
                    buffer=struct.unpack_from('<Q',stream.raw,self.profile['stream_data_offset'])[0]
                    if status!=0 or not buffer or not 8<=size<=32*1024*1024:raise FormatError('Native PNG writer failed or exceeded its bounds')
                    output=self.memory(buffer,size)
                if output[:8]!=b'\x89PNG\r\n\x1a\n':raise FormatError('Native writer did not produce a PNG')
                destination=Path(run_dir)/'native-surface.png';destination.write_bytes(output)
        finally:self.release(handle)
        if file_hash(source)!=params['sha256']:raise FormatError('Native surface packet changed during export')
        return {'sha256':params['sha256'],'width':params['width'],'height':params['height'],'native_format':pixel_format,
                'png_status':status,'output_file':str(destination),'output_sha256':sha(output)}

    def write_library(self, library, filename, run_dir, maximum):
        getter = self.qword(self.qword(library)+self.profile['class_getter_vtable_offset'])
        code = self.memory(getter, 6)
        if code[0] != 0xb8 or code[5] != 0xc3 or int.from_bytes(code[1:5], 'little') != self.profile['clb_class_id']:
            raise FormatError('Native asset writer target is not a supported content library')
        writer = self.private('clb_write_stream_rva', [c.c_void_p, c.c_void_p, c.c_bool, c.c_bool, c.c_bool], c.c_int)
        directory = Path(run_dir)/'native-library'
        directory.mkdir()
        artifacts = []
        for as_text in (False, True):
            with self.memory_stream() as stream:
                status = writer(library, stream, as_text, False, False)
                size = struct.unpack_from('<I', stream.raw, self.profile['stream_length_offset'])[0]
                pointer = struct.unpack_from('<Q', stream.raw, self.profile['stream_data_offset'])[0]
                if status != 0 or not pointer or not 1 <= size <= maximum:
                    raise FormatError('Native asset export failed or exceeds its output bound')
                data = self.memory(pointer, size)
            path = directory/(filename+('.yaml' if as_text else ''))
            path.write_bytes(data)
            artifacts.append({'file': str(path), 'sha256': file_hash(path), 'size': len(data),
                              'save_result': status, 'kind': 'yaml' if as_text else 'clb'})
        return {'artifacts': artifacts, 'native_library_class': f'{self.profile["clb_class_id"]:08X}'}

    def compile_library(self, params, run_dir):
        from aow4mod import asset_yaml
        from aow4mod.authoring import safe_name
        source = Path(params['yaml_path'])
        data = source.read_bytes()
        if sha(data) != params['yaml_sha256']:
            raise FormatError('Native asset YAML changed before compilation')
        document = asset_yaml.load(data)
        registry = {row['cid']: row['name'] for row in self.types()['types']}
        inline_classes = []
        for record in asset_yaml.class_records(document):
            if record['cid'] in registry and registry[record['cid']] != record['name']:
                raise FormatError('YAML contains an unregistered or mismatched native class')
            if record['cid'] not in registry:
                # Embedded value classes need not have factory registrations.
                # Their identity and data must survive the full YAML readback.
                inline_classes.append(record)
        filename = safe_name(params['file_name'])
        if not filename.lower().endswith('.clb'):
            raise FormatError('Native compiled library needs a CLB filename')
        handle = self.create(self.profile['clb_class_id'])
        if not handle:
            raise FormatError('Native content-library constructor failed')
        holder = c.create_string_buffer(16)
        borrow = self.private('borrow_object_rva', [c.c_void_p, c.c_uint64], c.c_void_p)
        release = self.private('release_raw_object_rva', [c.c_void_p])
        borrow(holder, handle)
        library = struct.unpack_from('<Q', holder.raw)[0]
        if not library:
            self.release(handle)
            raise FormatError('Native content-library handle could not be resolved')
        skipped, failures = [], []
        try:
            new = self.private('yaml_read_ctor_rva', [c.c_void_p, c.c_void_p, c.c_int], c.c_void_p)
            destroy = self.private('yaml_read_deleting_dtor_rva', [c.c_void_p, c.c_uint32], c.c_void_p)
            with self.memory_stream(data) as stream:
                reader = c.create_string_buffer(512)
                new(reader, stream, 0)
                original_vtable = struct.unpack_from('<Q', reader.raw)[0]
                if not struct.unpack_from('<Q', reader.raw, 0x58)[0]:
                    destroy(reader, 0)
                    raise FormatError('Native YAML parser did not produce a document')
                copied = c.create_string_buffer(self.memory(original_vtable, self.profile['yaml_read_vtable_bytes']))
                signature = c.CFUNCTYPE(c.c_int, c.c_void_p, c.c_void_p, c.c_int, c.c_uint32, c.c_char_p)
                original = signature(self.qword(original_vtable+self.profile['yaml_stream_slot']))

                @signature
                def zero_console_stream(owner, slot, size, field, label):
                    try:
                        pointer = self.qword(slot)
                        name = label.decode('utf-8', 'strict') if label else ''
                        if asset_yaml.empty_console_stream(field, name, size, bool(pointer)):
                            skipped.append(name)
                            return 0
                        if not pointer:
                            failures.append({'field': field, 'name': name, 'size': size})
                            return -2147024809
                        return original(owner, slot, size, field, label)
                    except Exception as exc:
                        failures.append({'error': str(exc)})
                        return -2147024809

                # Adapter is local to this reader instance. The installed DLL is
                # unchanged. Its inherited stream method dereferences absent,
                # zero-sized console streams; these two empty cases are skipped.
                c.c_uint64.from_buffer(copied, self.profile['yaml_stream_slot']).value = c.cast(zero_console_stream, c.c_void_p).value
                c.c_uint64.from_buffer(reader).value = c.addressof(copied)
                try:
                    method = self.qword(self.qword(library)+self.profile['object_serialize_vtable_offset'])
                    c.CFUNCTYPE(None, c.c_void_p, c.c_void_p)(method)(library, reader)
                finally:
                    c.c_uint64.from_buffer(reader).value = original_vtable
                    destroy(reader, 0)
            if failures:
                raise FormatError('Unsupported native asset stream: '+str(failures[0]))
            result = self.write_library(library, filename, run_dir, params['max_bytes'])
            if file_hash(source) != params['yaml_sha256']:
                raise FormatError('Native asset YAML changed during compilation')
            result.update(yaml_sha256=params['yaml_sha256'], empty_console_streams_skipped=skipped,
                          inline_classes=inline_classes)
            return result
        finally:
            release(library)
            self.release(handle)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise SystemExit('Private native worker expects request and result paths')
    request_path, result_path = [Path(x).resolve() for x in args]
    request_hash = file_hash(request_path)
    request = json.loads(request_path.read_text(encoding='utf-8'))
    result = {'format': native.FORMAT, 'request_sha256': request_hash,
              'request_id': request.get('request_id')}
    api = None
    initialized = False
    code = 0
    try:
        if request.get('format') != native.FORMAT or request['operation'] not in ('types', 'properties', 'localization-witness', 'roundtrip', 'package-apply', 'package-dependencies', 'construct', 'object-roundtrip', 'object-edit', 'income-edit','income-read','condition-eval','library-export','library-extract', 'library-compile','surface-png','mesh-import','skeletal-import','animation-import','animation-poses','model-poses','skinning','model-links','material-links','material-objects','material-textures','map-roundtrip','map-entities','map-terrain','effect-programs'):
            raise FormatError('Unsupported native worker request')
        binding = native.profile_for(request['binding']['game'])
        native.require_operation(binding, request['operation'])
        if binding != request['binding']:
            raise FormatError('Native library/profile/backend changed before initialization')
        if request['operation'] in ('mesh-import','skeletal-import','animation-import') and file_hash(Path(binding['game'])/'libfbxsdk.dll')!=binding['profile']['fbx']['sdk_sha256']:
            raise FormatError('FBX SDK changed before native initialization')
        if request['operation'] in ('map-roundtrip','map-entities'):
            from aow4mod import maps
            data,_=maps.read(request['params']['path'])
            if request['operation']=='map-entities':
                from aow4mod import map_entities
                map_entities.records(data)
        api = Bindings(binding)
        api.initialize(True)
        initialized = True
        if request['operation'] == 'types':
            result['payload'] = api.types()
        elif request['operation'] == 'properties':
            result['payload'] = api.properties(request['params'])
        elif request['operation'] in ('construct', 'object-roundtrip'):
            result['payload'] = api.construct_object(request['params'], result_path.parent, request['operation'] == 'object-roundtrip')
        elif request['operation']=='object-edit':
            result['payload']=api.edit_object(request['params'],result_path.parent)
        elif request['operation']=='condition-eval':
            from aow4mod import native_conditions
            result['payload']=native_conditions.worker(api,request['params'])
        elif request['operation'] in ('income-edit','income-read'):
            from aow4mod import native_income
            result['payload']=native_income.worker(api,request['params'],result_path.parent,request['operation']=='income-edit')
        elif request['operation'] == 'library-compile':
            result['payload'] = api.compile_library(request['params'], result_path.parent)
        elif request['operation'] == 'surface-png':
            result['payload'] = api.surface_png(request['params'],result_path.parent)
        elif request['operation']=='animation-import':
            from aow4mod import native_animations
            result['payload']=native_animations.worker(api,request['params'],result_path.parent)
        elif request['operation']=='skeletal-import':
            from aow4mod import native_skeletal
            result['payload']=native_skeletal.worker(api,request['params'],result_path.parent)
        elif request['operation']=='mesh-import':
            from aow4mod import native_meshes
            result['payload']=native_meshes.worker(api,request['params'],result_path.parent)
        else:
            result['payload'] = api.read_content(request['params'], result_path.parent,
                save=request['operation'] == 'roundtrip', library=request['operation'] in ('library-export','library-extract','animation-poses','model-poses','skinning','model-links','material-links','material-objects','material-textures','map-roundtrip','map-entities','map-terrain','effect-programs'),
                overlays=request['operation'] in ('package-apply','package-dependencies'),
                dependencies_only=request['operation']=='package-dependencies',model_check=request['operation']=='model-links',map_check=request['operation']=='map-roundtrip',map_entity_check=request['operation']=='map-entities',map_terrain_check=request['operation']=='map-terrain',effect_program_check=request['operation']=='effect-programs',material_check=request['operation']=='material-links',material_objects=request['operation']=='material-objects',material_texture_check=request['operation']=='material-textures',animation_pose_check=request['operation']=='animation-poses',model_pose_check=request['operation']=='model-poses',skinning_check=request['operation']=='skinning')
        native.validate_payload(request['operation'], result['payload'], request['params'])
    except Exception as exc:
        code = 2
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        save_record(result_path, result)
        if initialized:
            api.finish(code)  # Exits this process; never call in the parent CLI.
    return code


if __name__ == '__main__':
    raise SystemExit(main())
