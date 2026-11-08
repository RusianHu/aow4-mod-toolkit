"""Hash-bound, isolated access to the installed editor's native introspection.

This backend never treats a content-open callback as a validation result.
Native code executes only in a disposable child process. The normal CLI keeps
working if an unsupported constructor crashes, exits, or stops responding.
"""
from datetime import datetime, timezone
import copy
import json
import math
import os
import re
from pathlib import Path
import struct
import subprocess
import sys
import time
import uuid

from .core import FormatError, sha
from .filesystem import file_hash, outside, reject_reparse, save_record
from . import catalog

FORMAT = 'aow4mod-native/1'
DLL = 'DotNetBindings.dll'
OPERATIONS = ('types', 'properties', 'localization-witness', 'roundtrip',
              'package-apply', 'package-dependencies', 'construct', 'object-roundtrip',
              'object-edit', 'income-edit', 'income-read', 'condition-eval',
              'library-export', 'library-extract', 'library-compile', 'surface-png',
              'mesh-import', 'skeletal-import', 'animation-import', 'animation-poses',
              'model-poses', 'skinning', 'model-links', 'material-links',
              'material-objects', 'material-textures', 'effect-programs',
              'map-roundtrip', 'map-entities', 'map-terrain')
PROFILES = {
    '58b7210aa024d26cc0c148083d4aca7a322082a75ec3740a26dc2c4b9d4a8eff': {
        'name': 'witch-1.1-25750198',
        'reviewed_operations': list(OPERATIONS),
        'skinning':{'map_rva':0xab0fe0,'mesh_frame_rva':0xb651e0},
        'model_animation':{'frames_offset':0xc0,'default_animation_offset':0x140,'map_rva':0xab5200,'pose_rva':0xab5610,
            'bone_pose_rva':0xab5310,'matrix_ctor_rva':0x27b780,'matrix_dtor_rva':0x27b7b0,'matrix_resize_rva':0x280410},
        'animation': {'recalculate_rva': 0xb449f0,'tracks_data':0xe8,'tracks_count':0xf0,
            'rotation_sample_rva':0xb431f0,'vector_sample_rva':0xb43610,'compressed_sample_rva':0x28bd60},
        'resource_editor_sha256': '60d53fd3852ae26fcb9822d78692d788b0334971582e85a776090e4af44efe88',
        'property_size': 728,
        'strings': {'name': [0, 112], 'hint': [128, 240], 'category': [384, 48],
                    'custom_data': [448, 240]},
        # These are editor class IDs, not the obsolete LocEd TEPropertyType enum.
        'editor_cid_offset': 704,
        'editor_parameter_offset': 708,
        # BGetPropertyOrdinal/Float/Bool all dispatch through this same native
        # value-result getter. Object editor 00401417 returns a borrowed child
        # pointer in its 24-byte result; verify the child's class before use.
        'nested_properties': {'get_rva': 0x14ca70, 'result_size': 24, 'status_offset': 8,
                              'object_editors': ['00401417']},
        'scalar_editors': {
            '00401418': 'i32', '0040160D': 'enum_i32',
            '00401606': 'f32', '00401607': 'bool', '00401419': 'string',
        },
        'separator_editors': ['00401425'],
        # Reviewed in-place storage from current TBaseUnitR definitions. These
        # are factory-created value objects, never fabricated resource pointers.
        'inline_resource_objects': {'0047142D': {'name':'TUnitStats','owner_cid':'00471428','property':'0x00471428/0x61'}},
        'income': {'owner_cid':0x47223d,'block_cid':0x473ea3,'value_cid':0x473ea2,
                   'owner_offset':0x290,'data_offset':0x18,'count_offset':0x20,
                   'stride':0x40,'amount_offset':0x30,'resource_cid':0x473e9f},
        # Verified against BContentEnumerateLocaTags and the current TrySave body;
        # this private ABI is used only in a child, to a fresh diagnostic copy.
        'rpk_library_offset': 0x228,
        'class_getter_vtable_offset': 0x30,
        'rpk_class_id': 0x0400001a,
        'rpk_save_rva': 0x395320,
        # ACP 0x40 updates: TEContentPackageModData's embedded list is applied by
        # TGCResourceLibrary::ApplyModResources, then verified from saved data.
        'mod_data_class_id':0x00401204,'mod_resource_class_id':0x00401206,
        'mod_data_list_offset':0x20,'mod_list_data_offset':0x10,'mod_list_count_offset':0x18,
        'mod_record_resource_offset':0x10,'mod_record_operation_offset':0x28,'mod_record_applied_offset':0x2c,
        'resource_id_offset':0x28,'pack_libraries_data_offset':0x30,'pack_libraries_count_offset':0x38,
        'library_apply_mods_vtable_offset':0xd8,'library_apply_mods_rva':0x3903b0,
        'pack_collect_mod_dependencies_rva':0x390280,
        'resource_library_classes':[0x0400017f,0x04000172,0x04000173,0x04000174,0x04013404],
        'fbx':{'sdk_sha256':'dceb2b753b5a790ce2e1ac7af67b2540a373a0d2bc298d849d3b56c8acf5c0c4',
               'import_rva':0xb682b0,'finalize_rva':0xb704d0,'object_ctor':0x1473f0,
               'object_list_ctor':0x1474f0,'object_list_clear':0x149fc0,'mesh_list_vtable':0x22b5fa8,
               'animation_list_vtable':0x22f1ea8,'rotation_vtable':0x22df038,
               'translation_ctor':0xb3f390,'compressed_rotation_vtable':0x22df0d0,
               'compressed_quat_ctor':0x288640,'frame_set_ctor':0xb602a0,
               'strings_ctor':0x135740,'string_ctor':0x135700},
        'asset_templates': {'list_offset':0x70},
        'model_links':{'get_resource_rva':0x187db0},
        'material_links':{'get_resource_rva':0x187db0,'effect_link_offset':0xd8,'array_offset':0x80,'count_offset':0x88},
        'material_textures':{'properties_offset':0x140,'data_offset':0x10,'count_offset':0x18,'link_offset':0x28},
        'conditions':{'ctor':0x1ee19a0,'dtor':0x1ee1b20,'size':0x60,'parse':0x1ee2e40,'evaluate':0x1ee3be0,
                      'mapping_create':0x9ae920,'mapping_add':0x9618d0,'mapping_attach':0x1ee36f0,
                      'string_ctor':0x135700,'string_set':0x135c60,'string_dtor':0x1358e0,'renderer_enabled_rva':0x3056a49},
        'effect_programs':{'pool_size_offset':0x230,'pool_data_offset':0x238,'data_read_rva':0x1e2ef0,'data_unlock_rva':0x1e4d40,
                           'feature_names_offset':0x218,'strings_count_vslot':0x78,'string_at_vslot':0x18,
                           'strings_count_rva':0xe8670,'string_at_rva':0xe8450,
                           'pass_array_offset':0x1b0,'pass_count_offset':0x1b8,'shader_array_offset':0x3d0,'shader_count_offset':0x3d8},
        'tactical_maps':{'content_map_offset':0x238,'write_stream_rva':0x39ca60,'world_offset':0x140,
                         'entity_manager_offset':23376,'entity_array_offset':0x48,'entity_count_offset':0x50,
                         'entity_id_offset':12,'get_ordinal_property_rva':0x14ca70,
                         'entity_position_offset':0x118,'pack_position_rva':0x3de5a0,
                         'grid_array_offset':0x70,'grid_count_offset':0x78,'grid_rows_offset':0x28,'grid_level_offset':0x24,
                         'grid_cells_offset':0x18,'grid_stride_offset':0x20,'tactical_cell_stride':168,
                         'cell_entity_offsets':{'1':0x70,'3':0x80,'4':0x90},'cell_position_offset':0x40,
                         'terrain_paint_offset':0xf50,'terrain_pixel_offset':0x18,'terrain_weight_dimensions_offset':0x20,
                         'terrain_weight_buffer_offsets':[0x38,0x40]},
        'vertex_shading':{'tangents_rva':0xb7ea00,'format_ctor_rva':0xb7e180,'format_dtor_rva':0xb7e370,
                          'append_rva':0xb7e5b0,'finalize_rva':0xb84300,'convert_rva':0xb818d0},
        'clb_library_offset': 0x200,
        'clb_class_id': 0x00410060,
        'clb_write_stream_rva': 0x1918e0,
        'yaml_read_ctor_rva': 0x294a10,
        'yaml_read_deleting_dtor_rva': 0x295700,
        'yaml_read_vtable_bytes': 0x240,
        'yaml_stream_slot': 0x150,
        'surface_class_id': 0x00410024,
        'surface_rgba_format': 83,
        'surface_row_alignment': 8,
        'surface_format_vtable_offset': 0x90,
        'pixel_format_name_rva': 0xb3c850,
        'png_write_rva': 0xb8c310,
        'borrow_object_rva': 0xdc750,
        'release_raw_object_rva': 0x14eb50,
        'memory_stream_ctor_rva': 0x15f410,
        'memory_stream_dtor_rva': 0x15f4c0,
        'memory_stream_capacity_rva': 0x1603c0,
        'serialize_object_rva': 0x14bf40,
        'read_storage_ctor_rva': 0x2947b0,
        'read_storage_dtor_rva': 0x294e20,
        'object_serialize_vtable_offset': 0x18,
        'object_is_class_vtable_offset': 0x58,
        'resource_base_class': 0x00400080,
        'stream_size': 64,
        'stream_position_offset': 12,
        'stream_length_offset': 16,
        'stream_data_offset': 24,
        'read_storage_size': 512,
        'package_version_offsets': {'minor': 0x90, 'major': 0x94},
        'package_read_defaults': {'minor': 1, 'major': 0},
        # Confirmed native read/write omission rules, restricted to this class.
        'package_omitted_fields': {'0x0040009D/0x16': '00000000',
                                   '0x0040009D/0x17': '00000000',
                                   '0x0040009D/0x18': '01000000',
                                   '0x0040009D/0x19': '00000000',
                                   '0x0040009D/0x1A': '00000000'},
    },
    '5dc698f9d2215331d182540ac2acf2ba58f53f8d6481cdef57c5decdd08e3140': {
        'name': 'manta-26274772-resource-core',
        # Reviewed independently: exported property transfer writes through 0x2d7;
        # current TrySave and content/library/class identities have fresh witnesses.
        # Do not inherit Witch's unreviewed private object/asset/map entrypoints.
        'reviewed_operations': ['types', 'properties', 'localization-witness', 'roundtrip',
                                'object-roundtrip', 'package-dependencies'],
        'resource_editor_sha256': '2d4a1bcacc99a25fd6e71efb94de3daa99c766348897c8424618a363ae903015',
        'property_size': 728,
        'strings': {'name': [0,112], 'hint': [128,240], 'category': [384,48], 'custom_data': [448,240]},
        'editor_cid_offset': 704, 'editor_parameter_offset': 708,
        'scalar_editors': {'00401418':'i32','0040160D':'enum_i32','00401606':'f32','00401607':'bool','00401419':'string'},
        'separator_editors': ['00401425'],
        'rpk_library_offset': 0x228, 'class_getter_vtable_offset': 0x30,
        'rpk_class_id': 0x0400001a, 'rpk_save_rva': 0x397c80,
        'nested_properties': {'get_rva':0x14e690,'result_size':24,'status_offset':8,'object_editors':['00401417']},
        'borrow_object_rva':0xdd8b0, 'release_raw_object_rva':0x1507c0,
        'memory_stream_ctor_rva':0x161280, 'memory_stream_dtor_rva':0x161330,
        'memory_stream_capacity_rva':0x162230, 'serialize_object_rva':0x14db60,
        'read_storage_ctor_rva':0x2967e0, 'read_storage_dtor_rva':0x296e50,
        'object_serialize_vtable_offset':0x18, 'object_is_class_vtable_offset':0x58,
        'resource_base_class':0x00400080, 'surface_class_id':0x00410024,
        'stream_size':64, 'stream_position_offset':12, 'stream_length_offset':16,
        'stream_data_offset':24, 'read_storage_size':512,
        'package_version_offsets':{'minor':0x90,'major':0x94},
        'package_read_defaults':{'minor':1,'major':0},
        'package_omitted_fields':{},
        'mod_data_class_id':0x00401204, 'mod_resource_class_id':0x00401206,
        'mod_data_list_offset':0x20, 'mod_list_data_offset':0x10, 'mod_list_count_offset':0x18,
        'mod_record_resource_offset':0x10, 'mod_record_operation_offset':0x28,
        'mod_record_applied_offset':0x2c, 'resource_id_offset':0x28,
        'pack_libraries_data_offset':0x30, 'pack_libraries_count_offset':0x38,
        'library_apply_mods_vtable_offset':0xd8, 'library_apply_mods_rva':0x392d10,
        'pack_collect_mod_dependencies_rva':0x392be0,
        'resource_library_classes':[0x0400017f,0x04000172,0x04000173,0x04000174,0x04013404],
    },
}
# The hotfix was installed during review. Only independently rechecked layout
# constants are shared; every private entrypoint is explicit for the new DLL.
_RESOURCE_LAYOUT_KEYS = ('property_size','strings','editor_cid_offset','editor_parameter_offset',
    'scalar_editors','separator_editors','rpk_library_offset','class_getter_vtable_offset',
    'rpk_class_id','object_serialize_vtable_offset','object_is_class_vtable_offset',
    'resource_base_class','surface_class_id','stream_size','stream_position_offset',
    'stream_length_offset','stream_data_offset','read_storage_size','package_version_offsets',
    'package_read_defaults','package_omitted_fields','mod_data_class_id','mod_resource_class_id',
    'mod_data_list_offset','mod_list_data_offset','mod_list_count_offset','mod_record_resource_offset',
    'mod_record_operation_offset','mod_record_applied_offset','resource_id_offset',
    'pack_libraries_data_offset','pack_libraries_count_offset','library_apply_mods_vtable_offset',
    'resource_library_classes')
PROFILES['328b9eb896a10549ee038fafada6804c59462f3e46889c805cedce19c2335ddb'] = {
    **copy.deepcopy({k:PROFILES['5dc698f9d2215331d182540ac2acf2ba58f53f8d6481cdef57c5decdd08e3140'][k] for k in _RESOURCE_LAYOUT_KEYS}),
    'name':'manta-26440582-resource-core',
    'reviewed_operations':['types','properties','localization-witness','roundtrip','construct',
                           'object-roundtrip','object-edit','package-apply','package-dependencies','income-read','income-edit'],
    'resource_editor_sha256':'d9e2530a18a8665b67c3d0a941dffd071343880b7430617acb0262afa1f47e0e',
    'rpk_save_rva':0x3989d0,
    'nested_properties':{'get_rva':0x14e6b0,'result_size':24,'status_offset':8,'object_editors':['00401417']},
    'borrow_object_rva':0xdd8d0,'release_raw_object_rva':0x1507e0,
    'memory_stream_ctor_rva':0x161fd0,'memory_stream_dtor_rva':0x162080,
    'memory_stream_capacity_rva':0x162f80,'serialize_object_rva':0x14db80,
    'read_storage_ctor_rva':0x297530,'read_storage_dtor_rva':0x297ba0,
    'library_apply_mods_rva':0x393a60,'pack_collect_mod_dependencies_rva':0x393930,
    'inline_resource_objects':{'0047142D':{'name':'TUnitStats','owner_cid':'00471428','property':'0x00471428/0x61'}},
    # FIncomeFlat moved by eight bytes from the Witch profile; value stride and
    # the block's array/count were rechecked in current serializers/allocator.
    'income':{'owner_cid':0x47223d,'block_cid':0x473ea3,'value_cid':0x473ea2,
              'owner_offset':0x298,'data_offset':0x18,'count_offset':0x20,
              'stride':0x40,'amount_offset':0x30,'resource_cid':0x473e9f},
}
# Manta 1.1 hotfix: current disassembly and independent CLI readbacks recheck
# the resource ABI. This grants no asset/map/editor support from older builds.
PROFILES['9f7ff162d817073eb04a1b514f8e51ec0233a7ce48c586f2433940130de0036e'] = {
    **copy.deepcopy({k:PROFILES['328b9eb896a10549ee038fafada6804c59462f3e46889c805cedce19c2335ddb'][k]
                     for k in (*_RESOURCE_LAYOUT_KEYS,'inline_resource_objects','income')}),
    'name':'manta-26482553-resource-core',
    'reviewed_operations':['types','properties','localization-witness','roundtrip','construct',
                           'object-roundtrip','object-edit','package-apply','package-dependencies','income-read','income-edit'],
    'resource_editor_sha256':'16e19fa50154c243f92333e795ae2f789a4c952588aa42cafc911d48af82f896',
    'rpk_save_rva':0x3989d0,
    'nested_properties':{'get_rva':0x14e6b0,'result_size':24,'status_offset':8,'object_editors':['00401417']},
    'borrow_object_rva':0xdd8d0,'release_raw_object_rva':0x1507e0,
    'memory_stream_ctor_rva':0x161fd0,'memory_stream_dtor_rva':0x162080,
    'memory_stream_capacity_rva':0x162f80,'serialize_object_rva':0x14db80,
    'read_storage_ctor_rva':0x297530,'read_storage_dtor_rva':0x297ba0,
    'library_apply_mods_rva':0x393a60,'pack_collect_mod_dependencies_rva':0x393930,
    # Current serializer plus omitted/default/nondefault independent-reader
    # controls: empty description/author/registered packs, minor=1, major=0.
    # This is TEContentPackage only, never resource or gameplay defaults.
    'package_omitted_fields':{'0x0040009D/0x16':'00000000','0x0040009D/0x17':'00000000',
        '0x0040009D/0x18':'01000000','0x0040009D/0x19':'00000000','0x0040009D/0x1A':'00000000'},
}
COVERAGE = ('Native class registry and newly constructed object properties/defaults. '
            'Not a loaded-resource, serialization, asset, gameplay or compatibility certification.')

def reviewed_operations(profile):
    """An explicit allowlist supports incremental ABI review, never fall-through."""
    selected=profile.get('reviewed_operations', ())
    if (not isinstance(selected,(list,tuple)) or any(not isinstance(x,str) or x not in OPERATIONS for x in selected)
            or len(set(selected))!=len(selected)):
        raise FormatError('Invalid reviewed native operation allowlist')
    return tuple(selected)


def require_operation(binding, operation):
    if operation not in OPERATIONS:
        raise FormatError('Unsupported native operation')
    if operation not in reviewed_operations(binding['profile']):
        raise FormatError(f"Native operation {operation!r} is not reviewed for {binding['profile']['name']}; this is a partial ABI profile")


def backend_fingerprint():
    root = Path(__file__).parent
    return sha(b''.join((root/name).read_bytes() for name in ('native.py', 'native_worker.py', 'core.py', 'structured.py', 'editing.py',
        'resource_diff.py', 'native_overlays.py', 'native_income.py', 'native_conditions.py','condition_syntax.py','native_validation.py','content_index.py',
        'native_roundtrip.py', 'native_objects.py', 'native_assets.py', 'content_libraries.py', 'asset_yaml.py',
        'native_inspection.py', 'native_release.py', 'native_dependencies.py', 'native_context.py','language_context.py','map_entities.py','native_map_entities.py','map_world_entities.py','native_world_entities.py','native_world_release.py','map_terrain.py','native_map_terrain.py','clb_streams.py','effect_programs.py','native_effect_programs.py','native_map_release.py','hex_positions.py',
        'native_meshes.py','native_fbx.py','native_skeletal.py','skeletal_data.py','skeletal_sources.py','pose_baking.py','source_correspondence.py','native_animations.py','animation_data.py','animation_sources.py','animation_baking.py','animation_imports.py','animation_poses.py','native_animation_poses.py','model_poses.py','native_model_poses.py','skin_meshes.py','native_skinning.py','model_skinning.py','shader_reference.py','mesh_imports.py','mesh_shading.py','meshes.py','fbx.py','images.py','asset_templates.py','model_links.py','native_model_release.py','material_links.py','material_textures.py','native_texture_release.py','native_material_release.py','clb_imports.py','maps.py','native_maps.py')))


def profile_for(game):
    game = Path(game).resolve()
    dll = game/DLL
    if not dll.is_file():
        raise FormatError(f'Native editor library not found: {dll}')
    digest = file_hash(dll)
    if digest not in PROFILES:
        raise FormatError(f'Unsupported native editor build {digest}; a reviewed ABI profile is required')
    return {'game': str(game), 'dll': str(dll), 'dll_sha256': digest,
            'profile': PROFILES[digest], 'backend_sha256': backend_fingerprint()}


def status(game):
    dll = Path(game).resolve()/DLL
    digest = file_hash(dll) if dll.is_file() else None
    profile=PROFILES.get(digest)
    platform=os.name == 'nt' and struct.calcsize('P') == 8
    reviewed=reviewed_operations(profile) if profile is not None else ()
    return {'format': FORMAT, 'dll': str(dll), 'dll_sha256': digest,
            'platform_supported': platform,
            'profile_supported': digest in PROFILES,
            'profile': PROFILES.get(digest, {}).get('name'),
            'profile_scope': 'unsupported' if profile is None else 'full_implemented_scope' if set(reviewed)==set(OPERATIONS) else 'partial',
            'operations': list(OPERATIONS),
            'reviewed_operations': list(reviewed),
            'available_operations': list(reviewed) if platform else [],
            'unreviewed_operations': [x for x in OPERATIONS if x not in reviewed],
            'operations_meaning': 'operations is the implemented catalog; available_operations reflects this platform and reviewed binary profile, not content-specific acceptance',
            'executes_in_child_process': True,
            'content_open_is_validation': False, 'coverage': COVERAGE}


def check_cache_directory(path, game, protected=()):
    path = Path(path).absolute()
    for parent in [path, *path.parents]:
        if parent.exists():
            reject_reparse(parent)
    # Do not write research/cache artifacts inside installed content or local Mods.
    from .discovery import user_data, steam_roots
    roots = [game, user_data()/'Mods', *protected]
    roots.extend(r/'steamapps/workshop/content/1669000' for r in steam_roots())
    return outside(path, roots)


def parse_cid(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        if not isinstance(value, (str, int)):
            raise ValueError()
        result = int(value, 16) if isinstance(value, str) else value
        if not 0 < result <= 0xffffffff:
            raise ValueError()
    except (TypeError, ValueError, OverflowError) as exc:
        raise FormatError('Class ID must be a nonzero 32-bit hexadecimal value') from exc
    return f'{result:08X}'


def library_context(paths):
    """Hash-bind explicitly selected CLB search inputs, refusing ambiguous namespaces."""
    from . import content_libraries
    paths=list(paths)
    if len(paths)>512:raise FormatError('Native library context exceeds 512 files')
    result=[];seen={};names={}
    for value in paths:
        path=Path(value).resolve()
        if path.suffix.casefold()!='.clb' or path.stat().st_size>256*1024*1024:
            raise FormatError('Native context requires CLB files no larger than 256 MiB each')
        digest=file_hash(path);name=content_libraries.identity(path)['name'].casefold()
        for table,key in [(seen,path.name.casefold()),(names,name)]:
            if key in table and table[key]!=digest:raise FormatError('Ambiguous native CLB context: '+str(path))
            table[key]=digest
        row={'file':str(path),'sha256':digest}
        if row not in result:result.append(row)
    return result


def parse_property(raw, profile):
    if len(raw) != profile['property_size']:
        raise FormatError('Unexpected native property record size')
    result = {}
    for name, (offset, limit) in profile['strings'].items():
        value = raw[offset:offset+limit]
        if len(value) != limit or b'\0' not in value:
            raise FormatError(f'Unterminated native property {name}')
        result[name] = value.split(b'\0', 1)[0].decode('utf-8', 'strict')
    editor = struct.unpack_from('<I', raw, profile['editor_cid_offset'])[0]
    result['editor_cid'] = f'{editor:08X}'
    result['editor_parameter'] = struct.unpack_from('<I', raw, profile['editor_parameter_offset'])[0]
    result['codec'] = profile['scalar_editors'].get(result['editor_cid'])
    result['separator'] = result['editor_cid'] in profile['separator_editors']
    return result


def validate_payload(operation, payload, params):
    if not isinstance(payload, dict):
        raise FormatError('Native worker returned an invalid payload')
    if operation in ('income-edit','income-read'):
        from . import native_income
        return native_income.validate_payload(operation,payload,params)
    if operation=='condition-eval':
        from . import native_conditions
        return native_conditions.validate_payload(payload,params)
    if operation in ('roundtrip','localization-witness') and payload.get('context_languages',[])!=params.get('languages',[]):
        raise FormatError('Native MO context binding differs')
    if operation == 'types':
        rows = payload.get('types')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100000:
            raise FormatError('Native registry is empty or exceeds its bound')
        ids = set()
        for row in rows:
            cid = parse_cid(row['cid'])
            if row['cid'] != cid or cid in ids or not isinstance(row['name'], str) or not row['name']:
                raise FormatError('Native registry has invalid or duplicate type identities')
            ids.add(cid)
    elif operation == 'properties':
        if payload.get('cid') != params['cid'] or payload.get('name') != params['name']:
            raise FormatError('Native constructor identity does not match the request')
        rows = payload.get('properties')
        if not isinstance(rows, list) or not 0 <= len(rows) <= 5000:
            raise FormatError('Invalid native property count')
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('name'), str):
                raise FormatError('Invalid native property')
            if row.get('default_status') == 'read':
                value = row.get('default')
                codec = row.get('codec')
                valid = ((codec in ('i32', 'enum_i32') and type(value) is int and -2**31 <= value < 2**31)
                         or (codec == 'bool' and type(value) is bool)
                         or (codec == 'f32' and type(value) is float and math.isfinite(value))
                         or (codec == 'string' and type(value) is str))
                if not valid or row.get('getter_status') != 0:
                    raise FormatError('Unverified or invalid native default value')
    elif operation == 'localization-witness':
        if payload.get('sha256') != params['sha256']:
            raise FormatError('Native witness source hash mismatch')
        if payload.get('language_sha256') != params['language_sha256']:
            raise FormatError('Native witness language hash mismatch')
        if not params.get('expected_tokens'):
            raise FormatError('A native witness requires nonempty expected content')
        tokens = payload.get('tokens')
        if not isinstance(tokens, dict) or any(not isinstance(k, str) or type(v) is not int or v < 1 for k, v in tokens.items()):
            raise FormatError('Invalid native token multiset')
        if payload.get('tokens') != params['expected_tokens']:
            raise FormatError('Native localization content does not match the expected serialized tokens')
        if payload.get('callback_result') != 0 or not payload.get('content_present'):
            raise FormatError('Native content callback failed despite localization witnesses')
        if payload.get('context_packs',[])!=params.get('packs',[]):raise FormatError('Native RPK context binding differs')
    elif operation == 'roundtrip':
        if payload.get('sha256') != params['sha256'] or payload.get('language_sha256') != params['language_sha256']:
            raise FormatError('Native roundtrip input binding mismatch')
        if payload.get('save_result') != 0 or payload.get('callback_result') != 0 or not payload.get('content_present'):
            raise FormatError('Native roundtrip did not finish successfully')
        if not isinstance(payload.get('output_file'), str) or not isinstance(payload.get('output_sha256'), str):
            raise FormatError('Native roundtrip did not return an output file witness')
        if payload.get('context_libraries',[])!=params.get('libraries',[]):raise FormatError('Native library context binding differs')
        if payload.get('context_packs',[])!=params.get('packs',[]):raise FormatError('Native RPK context binding differs')
    elif operation in ('package-apply','package-dependencies'):
        if (payload.get('sha256')!=params['sha256'] or payload.get('language_sha256')!=params['language_sha256']
                or payload.get('callback_result')!=0 or not payload.get('content_present')
                or payload.get('context_libraries',[])!=params.get('libraries',[])):
            raise FormatError('Native overlay content/source/context binding differs')
        if payload.get('context_packs',[])!=params.get('packs',[]):raise FormatError('Native RPK context binding differs')
        if payload.get('dependencies_only',False)!=(operation=='package-dependencies'):
            raise FormatError('Native overlay preparation/application phase differs')
        application=payload.get('application',{})
        if not isinstance(application.get('steps'),list) or len(application['steps'])!=len(params['modifications']):
            raise FormatError('Native overlay step coverage is incomplete')
        for step,record in zip(application['steps'],params['modifications']):
            if step.get('file')!=record['file'] or step.get('sha256')!=record['sha256'] or len(step.get('entries',[]))!=len(record['resources']):
                raise FormatError('Native overlay record binding/coverage differs')
    elif operation in ('construct', 'object-roundtrip', 'object-edit'):
        if payload.get('cid') != params['cid'] or payload.get('name') != params['name']:
            raise FormatError('Native constructed object type mismatch')
        for key in ('source_file', 'source_sha256', 'output_file', 'output_sha256'):
            if not isinstance(payload.get(key), str) or not payload[key]:
                raise FormatError('Native object serialization did not return bounded artifacts')
        if not isinstance(payload.get('readbacks'), list):
            raise FormatError('Native object readback evidence is missing')
        if params.get('include_properties'):
            validate_payload('properties', {'cid': params['cid'], 'name': params['name'],
                                            'properties': payload.get('properties')}, params)
        if params.get('object_path'):
            from .native_inspection import validate_object_path
            path = validate_object_path(params['object_path'])
            nested = payload.get('nested_objects')
            if not isinstance(nested,list) or len(nested)!=len(path):
                raise FormatError('Nested property path witness is incomplete')
            for request, item in zip(path,nested):
                if item.get('selector')!=request or item.get('getter_status')!=0 or not item.get('cid') or not item.get('name'):
                    raise FormatError('Nested property selector/type witness differs')
                for key in ('file','sha256'):
                    if not isinstance(item.get(key),str):raise FormatError('Nested object binary witness is missing')
        if operation=='object-edit':
            if not isinstance(params.get('assignments'),list) or not 1<=len(params['assignments'])<=32:
                raise FormatError('Native edit assignment count is invalid')
            if payload.get('input_sha256')!=params['body_sha256']:
                raise FormatError('Native edit source body binding mismatch')
            for key in ('input_file','baseline_file','baseline_sha256'):
                if not isinstance(payload.get(key),str) or not payload[key]:raise FormatError('Native edit input/baseline artifact is missing')
            if (not isinstance(payload.get('mapping_witnesses'),list) or not isinstance(payload.get('before_values'),list)
                    or len(payload['mapping_witnesses'])!=len(params['assignments']) or len(payload['before_values'])!=len(params['assignments'])):
                raise FormatError('Native edit mapping/getter coverage is incomplete')
    elif operation in ('library-export','library-extract'):
        if payload.get('sha256') != params['sha256'] or payload.get('language_sha256') != params['language_sha256']:
            raise FormatError('Native library input binding mismatch')
        if payload.get('callback_result') != 0 or not payload.get('content_present'):
            raise FormatError('Native library did not finish opening')
        if len(payload.get('artifacts', [])) != 2:
            raise FormatError('Native library export is missing its binary/text artifact')
        if operation=='library-extract':
            if payload.get('selection')!=params['selection'] or payload.get('library_name')!=params['library_name']:
                raise FormatError('Native template selection/library binding differs')
            objects=payload.get('objects')
            if not isinstance(objects,list) or len(objects)!=len(params['selection']):
                raise FormatError('Native template object coverage differs')
            if [o.get('locator') for o in objects]!=[o['locator'] for o in params['selection']]:
                raise FormatError('Native template object order/identity differs')
    elif operation=='effect-programs':
        if (payload.get('sha256')!=params['sha256'] or payload.get('language_sha256')!=params['language_sha256'] or
                payload.get('callback_result')!=0 or not payload.get('content_present') or payload.get('locator')!=params['locator']):
            raise FormatError('Native effect-program source/locator differs')
        if not isinstance(payload.get('tables'),list) or not isinstance(payload.get('pool_artifact'),dict) or not isinstance(payload.get('artifact_directory'),str):
            raise FormatError('Native effect-program artifact coverage is absent')
        names=payload.get('feature_names')
        if not isinstance(names,list) or len(names)>256 or any(not isinstance(n,str) or not 1<=len(n.encode('utf-8'))<=1024 or '\0' in n for n in names):
            raise FormatError('Native effect feature-name vector is absent or exceeds bounds')
    elif operation=='map-terrain':
        if (payload.get('sha256')!=params['sha256'] or payload.get('language_sha256')!=params['language_sha256'] or
                payload.get('callback_result')!=0 or not payload.get('content_present') or payload.get('level')!=params['level']):
            raise FormatError('Native terrain source/level differs')
        if payload.get('context_libraries',[])!=params.get('libraries',[]) or payload.get('context_packs',[])!=params.get('packs',[]):
            raise FormatError('Native terrain lookup context differs')
        samples=payload.get('samples')
        if not isinstance(samples,list) or [{k:r.get(k) for k in ('x','y')} for r in samples]!=params['samples']:
            raise FormatError('Native terrain sample selection differs')
        if not isinstance(payload.get('pixel_artifact'),dict):raise FormatError('Native terrain pixel artifact is absent')
    elif operation=='map-entities':
        if (payload.get('sha256')!=params['sha256'] or payload.get('language_sha256')!=params['language_sha256'] or
                payload.get('callback_result')!=0 or not payload.get('content_present')):raise FormatError('Native map entity source/context binding differs')
        items=payload.get('entities')
        empty_world=params.get('entity_scope')=='zones_and_cells_v1' and params.get('world_entity_count')==0 and params.get('entity_ids')==[]
        if not isinstance(items,list) or not (1<=len(items)<=256 or empty_world and not items) or [r.get('id') for r in items]!=params['entity_ids']:
            raise FormatError('Native map entity selection coverage differs')
        if not isinstance(payload.get('entity_directory'),str):raise FormatError('Native map entity witness directory is absent')
        if payload.get('context_libraries',[])!=params.get('libraries',[]) or payload.get('context_packs',[])!=params.get('packs',[]):
            raise FormatError('Native map entity lookup context differs')
        if params.get('entity_scope'):
            if (params['entity_scope']!='zones_and_cells_v1' or payload.get('entity_scope')!=params['entity_scope'] or
                    payload.get('world_entity_count')!=params.get('world_entity_count')):
                raise FormatError('Native world entity scope/count differs')
            summary=payload.get('identity_summary',{})
            if summary.get('count')!=params['world_entity_count'] or summary.get('sha256')!=params.get('world_identity_sha256'):
                raise FormatError('Native world identity summary differs')
    elif operation=='map-roundtrip':
        if (payload.get('sha256')!=params['sha256'] or payload.get('language_sha256')!=params['language_sha256'] or
                payload.get('map_class')!='00470037' or payload.get('save_result')!=0 or payload.get('callback_result')!=0 or not payload.get('content_present')):
            raise FormatError('Native tactical-map source/owner/save evidence differs')
        if set(payload.get('artifacts',{}))!={'before','after','saved'}:raise FormatError('Native tactical-map witness coverage differs')
        if payload.get('context_libraries',[])!=params.get('libraries',[]) or payload.get('context_packs',[])!=params.get('packs',[]):
            raise FormatError('Native tactical-map lookup context differs')
    elif operation in ('material-links','material-objects','material-textures'):
        if (payload.get('sha256')!=params['sha256'] or payload.get('language_sha256')!=params['language_sha256'] or
                payload.get('callback_result')!=0 or not payload.get('content_present') or payload.get('library_name')!=params['library_name']):
            raise FormatError('Native material input/context binding differs')
        if operation in ('material-links','material-textures'):
            rows=payload.get('materials')
            if not isinstance(rows,list) or len(rows)!=len(params['materials']) or payload.get('context')!=params['context']:
                raise FormatError('Native material selection/context coverage differs')
            for row,identity,target in zip(rows,params['materials'],params['targets']):
                if row.get('locator')!=identity['locator']:raise FormatError('Native material identity differs')
                if operation=='material-links' and row.get('expected')!=target:raise FormatError('Native material target identity differs')
        elif not isinstance(payload.get('objects'),list) or [r.get('identity') for r in payload['objects']]!=params['selection']:
            raise FormatError('Independent material effect object selection differs')
    elif operation=='model-links':
        if (payload.get('sha256')!=params['sha256'] or payload.get('language_sha256')!=params['language_sha256'] or
                payload.get('callback_result')!=0 or not payload.get('content_present') or payload.get('library_name')!=params['library_name']):
            raise FormatError('Native model-link input/context binding differs')
        if not isinstance(payload.get('models'),list) or [m.get('locator') for m in payload['models']]!=[m['locator'] for m in params['models']]:
            raise FormatError('Native model-link selected object coverage differs')
    elif operation == 'library-compile':
        if payload.get('yaml_sha256') != params['yaml_sha256'] or len(payload.get('artifacts', [])) != 2:
            raise FormatError('Native library compilation input/output binding mismatch')
    elif operation=='skinning':
        if (payload.get('sha256')!=params['sha256'] or payload.get('language_sha256')!=params['language_sha256'] or payload.get('callback_result')!=0 or not payload.get('content_present') or
                payload.get('model')!=params['model'] or payload.get('lod')!=params['lod'] or payload.get('component')!=params['component'] or not payload.get('native_palette_rebuilt')):
            raise FormatError('Native skinning source/selection differs')
        if set(payload.get('objects',{}))!={'model_before','model_after','mesh_before','mesh_after','map_stored','map_rebuilt'}:raise FormatError('Native skinning object coverage differs')
    elif operation=='model-poses':
        if (payload.get('sha256')!=params['sha256'] or payload.get('language_sha256')!=params['language_sha256'] or payload.get('callback_result')!=0 or
                not payload.get('content_present') or payload.get('model')!=params['model'] or payload.get('animation')!=params['animation'] or not payload.get('guards_preserved')):
            raise FormatError('Native model pose input/guard binding differs')
        if not isinstance(payload.get('poses'),list) or [p.get('time_us') for p in payload['poses']]!=params['times'] or set(payload.get('objects',{}))!={'model_before','model_after','animation_before','animation_after'}:
            raise FormatError('Native model pose coverage differs')
    elif operation=='animation-poses':
        if (payload.get('sha256')!=params['sha256'] or payload.get('language_sha256')!=params['language_sha256'] or
                payload.get('callback_result')!=0 or not payload.get('content_present') or payload.get('animation')!=params['selection']):
            raise FormatError('Native animation pose input/identity differs')
        if not isinstance(payload.get('samples'),list) or len(payload['samples'])!=len(params['tracks'])*len(params['times']) or set(payload.get('objects',{}))!={'before','after'}:
            raise FormatError('Native animation pose coverage differs')
    elif operation=='animation-import':
        if (payload.get('sha256')!=params['sha256'] or payload.get('namespace')!=params['namespace'] or
                payload.get('title')!=params['title'] or not payload.get('guards_preserved') or len(payload.get('artifacts',[]))!=2):
            raise FormatError('Native animation input/output binding differs')
        if payload.get('sdk_sha256') not in {p.get('fbx',{}).get('sdk_sha256') for p in PROFILES.values()}:
            raise FormatError('Native animation SDK is not reviewed')
        if not isinstance(payload.get('objects'),dict) or not 10<=len(payload['objects'])<=521:
            raise FormatError('Native animation witness coverage differs')
    elif operation=='skeletal-import':
        if payload.get('sha256')!=params['sha256'] or payload.get('namespace')!=params['namespace'] or not payload.get('guards_preserved') or len(payload.get('artifacts',[]))!=2 or set(payload.get('objects',{}))!={'before','hierarchy_authored','hierarchy_loaded','finalized','extended','readback','stable'}:raise FormatError('Native skeletal import source/coverage differs')
        if payload.get('sdk_sha256') not in {p.get('fbx',{}).get('sdk_sha256') for p in PROFILES.values()}:raise FormatError('Native skeletal SDK is not reviewed')
    elif operation=='mesh-import':
        if payload.get('sha256')!=params['sha256'] or payload.get('namespace')!=params['namespace'] or not payload.get('guards_preserved'):
            raise FormatError('Native mesh import source/namespace/guard binding differs')
        if not isinstance(payload.get('meshes'),list) or not 1<=len(payload['meshes'])<=64 or len(payload.get('artifacts',[]))!=2:
            raise FormatError('Native mesh import output coverage differs')
        if payload.get('sdk_sha256') not in {p.get('fbx',{}).get('sdk_sha256') for p in PROFILES.values()}:
            raise FormatError('Native mesh SDK is not reviewed')
        if payload.get('vertex_profile','pnuv32')!=params.get('vertex_profile','pnuv32'):
            raise FormatError('Native mesh vertex profile differs from the request')
    elif operation == 'surface-png':
        if (payload.get('sha256')!=params['sha256'] or payload.get('width')!=params['width'] or
            payload.get('height')!=params['height'] or payload.get('native_format')!=83 or payload.get('png_status')!=0):
            raise FormatError('Native surface PNG identity/format/result mismatch')
        if not isinstance(payload.get('output_file'),str) or not isinstance(payload.get('output_sha256'),str):
            raise FormatError('Native surface PNG output witness is missing')
    else:
        raise FormatError('Unsupported native operation')
    return payload


def run_worker(game, operation, params, cache_dir, timeout=30, runner=None):
    if not 1 <= timeout <= 180:
        raise FormatError('Native timeout must be between 1 and 180 seconds')
    if runner is None and (os.name != 'nt' or struct.calcsize('P') != 8):
        raise FormatError('Native editor introspection requires 64-bit Windows')
    binding = profile_for(game)
    require_operation(binding, operation)
    protected = [Path(params['path']).parent] if operation in ('localization-witness', 'roundtrip', 'package-apply', 'package-dependencies', 'library-export','library-extract', 'surface-png','mesh-import','skeletal-import','animation-import','animation-poses','model-poses','skinning','model-links','material-links','material-objects','material-textures','map-roundtrip','map-entities','map-terrain','effect-programs') else []
    if operation in ('package-apply','package-dependencies'):
        protected.extend(Path(row['file']).parent for row in params['modifications'])
        protected.extend(Path(row['file']).parent for row in params.get('packs',[]))
    protected.extend(Path(row['file']).parent for row in params.get('languages',[]))
    protected.extend(Path(row['file']).parent for row in params.get('packs',[]))
    if operation == 'library-compile':
        protected = [Path(params['yaml_path']).parent]
    if operation in ('material-links','material-objects','material-textures'):protected.extend(Path(row['file']).parent for row in params.get('libraries',[]))
    directory = check_cache_directory(cache_dir, game, protected)
    run_dir = directory/'runs'/uuid.uuid4().hex
    run_dir.mkdir(parents=True)
    request = {'format': FORMAT, 'operation': operation, 'params': params, 'binding': binding,
               'request_id': run_dir.name}
    request_path, result_path = run_dir/'request.json', run_dir/'result.json'
    save_record(request_path, request)
    request_hash = file_hash(request_path)
    worker = Path(__file__).with_name('native_worker.py')
    command = [sys.executable, str(worker), str(request_path), str(result_path)]
    start = time.monotonic()
    if runner is None:
        with (run_dir/'stdout.log').open('wb') as out, (run_dir/'stderr.log').open('wb') as err:
            process = subprocess.Popen(command, stdout=out, stderr=err, cwd=run_dir,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            timed_out = False
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                code = process.wait(timeout=5)
    else:
        code, timed_out = runner(command, request, request_hash, result_path, timeout)
    receipt = {'exit_code': code, 'timed_out': timed_out,
               'elapsed_seconds': round(time.monotonic()-start, 3), 'request_sha256': request_hash}
    save_record(run_dir/'process.json', receipt)
    if timed_out or not result_path.is_file():
        raise FormatError(f'Native worker failed (exit={code}, timeout={timed_out}); evidence: {run_dir}')
    if file_hash(request_path) != request_hash or file_hash(binding['dll']) != binding['dll_sha256']:
        raise FormatError('Native request or editor library changed during execution')
    result = json.loads(result_path.read_text(encoding='utf-8'))
    if (result.get('format') != FORMAT or result.get('request_sha256') != request_hash
            or result.get('request_id') != request['request_id']):
        raise FormatError('Native worker result does not match this request')
    if result.get('error'):
        raise FormatError(f'Native worker: {result["error"]}; evidence: {run_dir}')
    if code != 0:
        raise FormatError(f'Native worker exited unsuccessfully ({code}); evidence: {run_dir}')
    payload = validate_payload(operation, result.get('payload'), params)
    if operation == 'roundtrip':
        output_file = Path(payload['output_file'])
        expected_output = run_dir/'native-saved'/Path(params['path']).name
        if output_file.resolve() != expected_output.resolve():
            raise FormatError('Native writer returned a path outside its assigned output')
        reject_reparse(output_file)
        if file_hash(output_file) != payload['output_sha256']:
            raise FormatError('Native saved output does not match its receipt')
    if operation in ('construct', 'object-roundtrip', 'object-edit'):
        for key, name in [('source', 'object-source.bin'), ('output', 'object-serialized.bin')]:
            artifact = Path(payload[key+'_file'])
            if artifact.resolve() != (run_dir/name).resolve():
                raise FormatError('Native object artifact is outside its assigned output')
            reject_reparse(artifact)
            if not 1 <= artifact.stat().st_size <= 16*1024*1024 or file_hash(artifact) != payload[key+'_sha256']:
                raise FormatError('Native object artifact size/hash mismatch')
        for i,item in enumerate(payload.get('nested_objects',[])):
            artifact=Path(item['file'])
            if artifact.resolve()!=(run_dir/f'nested-{i}.bin').resolve():raise FormatError('Nested object artifact escapes its run')
            reject_reparse(artifact)
            if not 1<=artifact.stat().st_size<=16*1024*1024 or file_hash(artifact)!=item['sha256']:
                raise FormatError('Nested object artifact size/hash mismatch')
    if operation=='object-edit':
        artifacts=[('input','edit-input.bin',payload),('baseline','edit-baseline.bin',payload)]
        for index,witness in enumerate(payload['mapping_witnesses']):
            artifacts.extend([('before',f'edit-probe-{index}-before.bin',witness),('after',f'edit-probe-{index}-after.bin',witness)])
        for key,name,item in artifacts:
            artifact=Path(item[key+'_file'])
            if artifact.resolve()!=(run_dir/name).resolve():raise FormatError('Native edit artifact is outside its assigned output')
            reject_reparse(artifact)
            if not 1<=artifact.stat().st_size<=16*1024*1024 or file_hash(artifact)!=item[key+'_sha256']:
                raise FormatError('Native edit artifact size/hash mismatch')
    if operation in ('package-apply','package-dependencies'):
        application=payload['application'];name=Path(params['path']).name
        outputs=[(application['baseline'],'native-applied/baseline/native-saved/'+name)]
        for index,step in enumerate(application['steps']):
            outputs.append((step['output'],f'native-applied/step-{index}/native-saved/'+name))
            artifact=Path(step['mod_data_file'])
            if artifact.resolve()!=(run_dir/f'native-applied/mod-data-{index}.bin').resolve():raise FormatError('Native overlay record artifact escapes its run')
            reject_reparse(artifact)
            if not 1<=artifact.stat().st_size<=16*1024*1024 or file_hash(artifact)!=step['mod_data_sha256']:
                raise FormatError('Native overlay record artifact size/hash differs')
        for item,relative in outputs:
            artifact=Path(item['output_file'])
            if artifact.resolve()!=(run_dir/relative).resolve() or item['save_result']!=0:
                raise FormatError('Native overlay saved artifact escapes its run or failed')
            reject_reparse(artifact)
            if not 1<=artifact.stat().st_size<=256*1024*1024 or file_hash(artifact)!=item['output_sha256']:
                raise FormatError('Native overlay saved artifact size/hash differs')
    if operation=='skeletal-import':
        for key,item in payload['objects'].items():
            artifact=Path(item['file']);expected=run_dir/'native-skeletal'/(key+'.bin')
            if artifact.resolve()!=expected.resolve():raise FormatError('Skeletal object escapes native run')
            reject_reparse(artifact)
            if not 1<=artifact.stat().st_size<=16*1024*1024 or file_hash(artifact)!=item['sha256']:raise FormatError('Skeletal object size/hash differs')
        for item,suffix in zip(payload['artifacts'],['','.yaml']):
            artifact=Path(item['file']);expected=run_dir/'native-skeletal/native-library'/(params['namespace']+'.clb'+suffix)
            if artifact.resolve()!=expected.resolve() or item.get('save_result')!=0:raise FormatError('Skeletal library escapes native run')
            reject_reparse(artifact)
            if not 1<=artifact.stat().st_size<=32*1024*1024 or file_hash(artifact)!=item['sha256']:raise FormatError('Skeletal library size/hash differs')
    if operation in ('library-export', 'library-extract', 'library-compile'):
        for item, suffix in zip(payload['artifacts'], ['', '.yaml']):
            artifact = Path(item['file'])
            name = Path(params['path']).name if operation in ('library-export','library-extract') else params['file_name']
            expected = run_dir/'native-library'/(name+suffix)
            if artifact.resolve() != expected.resolve() or item.get('save_result') != 0:
                raise FormatError('Native library artifact is outside its assigned output or failed to save')
            reject_reparse(artifact)
            if not 1 <= artifact.stat().st_size <= params['max_bytes'] or file_hash(artifact) != item['sha256']:
                raise FormatError('Native library artifact size/hash mismatch')
    if operation=='library-extract':
        for index,item in enumerate(payload['objects']):
            artifact=Path(item['file'])
            if artifact.resolve()!=(run_dir/f'template-objects/{index}.bin').resolve():
                raise FormatError('Native template object escapes its run')
            reject_reparse(artifact)
            if not 1<=artifact.stat().st_size<=16*1024*1024 or file_hash(artifact)!=item.get('sha256'):
                raise FormatError('Native template object artifact size/hash differs')
    if operation=='material-textures':
        from . import material_textures
        seen=set();total=0
        for item in material_textures.artifacts(payload):
            path=Path(item['file']);reject_reparse(path)
            if path.parent.resolve()!=(run_dir/'native-textures').resolve() or not re.fullmatch(r'(m\d+-(?:t\d+-)?(?:before|after)|target-\d+)\.bin',path.name):
                raise FormatError('Native texture artifact role/path differs')
            if not 1<=path.stat().st_size<=64*1024*1024 or file_hash(path)!=item['sha256']:raise FormatError('Native texture artifact size/hash differs')
            if path not in seen:total+=path.stat().st_size;seen.add(path)
            if total>256*1024*1024:raise FormatError('Native texture artifacts exceed 256 MiB')
    if operation in ('material-links','material-objects'):
        objects=[]
        if operation=='material-objects':objects=[(row['artifact'],f'object-{i}.bin') for i,row in enumerate(payload['objects'])]
        else:
            for i,row in enumerate(payload['materials']):
                objects.extend((row['artifacts'][key],f'{i}-{key}.bin') for key in ('before','after'))
                if row.get('target_artifact'):
                    path=Path(row['target_artifact']['file']);name=path.name
                    if not re.fullmatch(r'linked-\d+\.bin',name):raise FormatError('Material target artifact name differs')
                    objects.append((row['target_artifact'],name))
        total=0;seen=set()
        for item,name in objects:
            path=Path(item['file']);reject_reparse(path)
            if path.resolve()!=(run_dir/'native-material'/name).resolve():raise FormatError('Material witness escapes its run')
            if not 1<=path.stat().st_size<=64*1024*1024 or file_hash(path)!=item.get('sha256'):raise FormatError('Native material witness size/hash differs')
            if name not in seen:total+=path.stat().st_size;seen.add(name)
            if total>256*1024*1024:raise FormatError('Native material witnesses exceed 256 MiB')
    if operation=='model-links':
        seen=set();total=0
        for mi,model in enumerate(payload['models']):
            objects=[(model['artifacts'][key],f'{mi}-{key}.bin') for key in ('before','after')]
            for component in model['components']:
                for label in ('mesh','material','shadow'):
                    link=component[label]
                    if link.get('target_artifact'):
                        ordinal=link.get('target_ordinal')
                        if type(ordinal) is not int or not 0<=ordinal<len(params['members']):raise FormatError('Model target ordinal is outside its library')
                        if params['members'][ordinal]['locator']!=link['resolved_local_locator']:raise FormatError('Model target ordinal/identity differs')
                        objects.append((link['target_artifact'],f'target-{ordinal}.bin'))
            for item,name in objects:
                path=Path(item['file'])
                if path.resolve()!=(run_dir/'native-model'/name).resolve():raise FormatError('Model native object artifact escapes its run')
                reject_reparse(path)
                if not 1<=path.stat().st_size<=16*1024*1024 or file_hash(path)!=item.get('sha256'):raise FormatError('Model native object artifact size/hash differs')
                if name not in seen:total+=path.stat().st_size;seen.add(name)
                if total>64*1024*1024:raise FormatError('Model native object evidence exceeds 64 MiB')
    if operation=='effect-programs':
        root=Path(payload['artifact_directory'])
        if root.resolve()!=(run_dir/'native-effect-programs').resolve():raise FormatError('Native effect-program directory escapes its run')
        reject_reparse(root);pool=payload['pool_artifact']
        if pool.get('file')!='pool.bin':raise FormatError('Native effect pool filename differs')
        total=0;seen=set()
        for row in [pool,*payload['tables']]:
            name=row.get('file')
            if row is not pool:
                pi=row.get('pass');stage=row.get('stage');count=row.get('count')
                if type(pi) is not int or pi<0 or type(stage) is not int or not 0<=stage<8 or type(count) is not int or not 1<=count<=100000:
                    raise FormatError('Native effect table identity/count differs')
                if name!=f'pass-{pi:03d}-stage-{stage}.bin':raise FormatError('Native effect table filename differs')
            if name in seen:raise FormatError('Duplicate native effect artifact')
            seen.add(name);path=root/name;reject_reparse(path)
            if not 1<=path.stat().st_size<=64*1024*1024 or file_hash(path)!=row.get('sha256'):raise FormatError('Native effect artifact size/hash differs')
            total+=path.stat().st_size
            if total>128*1024*1024:raise FormatError('Native effect evidence exceeds bounds')
    if operation=='map-terrain':
        artifact=payload['pixel_artifact'];path=Path(artifact['file'])
        if path.resolve()!=(run_dir/'native-terrain/pixel-body.bin').resolve():raise FormatError('Native terrain pixel witness escapes its run')
        reject_reparse(path)
        if not 1<=path.stat().st_size<=256*1024*1024 or file_hash(path)!=artifact.get('sha256'):raise FormatError('Native terrain pixel witness size/hash differs')
    if operation=='map-entities':
        root=Path(payload['entity_directory'])
        if root.resolve()!=(run_dir/'native-entities').resolve():raise FormatError('Native map entity directory escapes its run')
        reject_reparse(root);total=0;seen=set();cell_names=set()
        for item in payload['entities']:
            ordinal=item.get('ordinal');name=item.get('file')
            if type(ordinal) is not int or ordinal<0 or ordinal in seen or name!=f'entity-{ordinal:06d}.bin':raise FormatError('Native map entity artifact identity differs')
            seen.add(ordinal);path=root/name;reject_reparse(path)
            if not 1<=path.stat().st_size<=16*1024*1024 or file_hash(path)!=item.get('sha256'):raise FormatError('Native map entity artifact size/hash differs')
            total+=path.stat().st_size
            if total>64*1024*1024:raise FormatError('Native map entity witnesses exceed bounds')
            if params.get('entity_scope') and item.get('location',{}).get('origin')=='cell':
                location=item['location'];grid=location.get('grid_ordinal');index=location.get('cell_index')
                if type(grid) is not int or not 0<=grid<64 or type(index) is not int or not 0<=index<1000000:
                    raise FormatError('Native cell witness identity exceeds bounds')
                name=f'cell-{grid:02d}-{index:06d}.bin'
                if item.get('cell_file')!=name:raise FormatError('Native cell witness filename differs')
                path=root/name;reject_reparse(path)
                if not 1<=path.stat().st_size<=16*1024*1024 or file_hash(path)!=item.get('cell_sha256'):
                    raise FormatError('Native cell witness size/hash differs')
                if name not in cell_names:total+=path.stat().st_size;cell_names.add(name)
                if total>64*1024*1024:raise FormatError('Native world witnesses exceed bounds')
    if operation=='map-roundtrip':
        for key,item in payload['artifacts'].items():
            path=Path(item['file']);name='saved.tam' if key=='saved' else key+'.bin'
            if path.resolve()!=(run_dir/'native-map'/name).resolve():raise FormatError('Native tactical-map artifact escapes its run')
            reject_reparse(path)
            if not 1<=path.stat().st_size<=256*1024*1024 or file_hash(path)!=item.get('sha256'):raise FormatError('Native tactical-map artifact size/hash differs')
    if operation=='skinning':
        for role,item in payload['objects'].items():
            artifact=Path(item['file']);expected=run_dir/'skinning'/(role+'.bin')
            if artifact.resolve()!=expected.resolve():raise FormatError('Skinning witness escapes its run')
            reject_reparse(artifact)
            if not 1<=artifact.stat().st_size<=16*1024*1024 or file_hash(artifact)!=item['sha256']:raise FormatError('Skinning witness size/hash differs')
    if operation=='model-poses':
        artifacts=list(payload['objects'].items())+[(f'{i:02d}-{k}',v) for i,p in enumerate(payload['poses']) for k,v in p.items() if k in ('frames','bones')]
        for role,item in artifacts:
            artifact=Path(item['file']);expected=run_dir/'model-poses'/(role+'.bin')
            if artifact.resolve()!=expected.resolve():raise FormatError('Model pose witness escapes its run')
            reject_reparse(artifact)
            if not 1<=artifact.stat().st_size<=16*1024*1024 or file_hash(artifact)!=item['sha256']:raise FormatError('Model pose witness hash/size differs')
    if operation=='animation-poses':
        for role,item in payload['objects'].items():
            artifact=Path(item['file']);expected=run_dir/'animation-poses'/(role+'.bin')
            if artifact.resolve()!=expected.resolve():raise FormatError('Animation pose artifact escapes its run')
            reject_reparse(artifact)
            if not 1<=artifact.stat().st_size<=16*1024*1024 or file_hash(artifact)!=item['sha256']:raise FormatError('Animation pose artifact size/hash differs')
    if operation=='animation-import':
        allowed={'default','rotation','translation','compressed_rotation','scale','frames','assembled','readback','independent'}
        for label,item in payload['objects'].items():
            if label not in allowed and not re.fullmatch(r'track-[0-9]{3}',label):raise FormatError('Unsupported animation witness name')
            artifact=Path(item['file']);expected=run_dir/'native-animation'/(label+'.bin')
            if artifact.resolve()!=expected.resolve():raise FormatError('Animation witness escapes its run')
            reject_reparse(artifact)
            if not 1<=artifact.stat().st_size<=16*1024*1024 or file_hash(artifact)!=item['sha256']:raise FormatError('Animation witness size/hash differs')
        for item,suffix in zip(payload['artifacts'],('', '.yaml')):
            artifact=Path(item['file']);expected=run_dir/'native-animation/native-library'/(params['namespace']+'.clb'+suffix)
            if artifact.resolve()!=expected.resolve():raise FormatError('Animation library escapes its run')
            reject_reparse(artifact)
            if not 1<=artifact.stat().st_size<=32*1024*1024 or file_hash(artifact)!=item['sha256']:raise FormatError('Animation library size/hash differs')
    if operation=='mesh-import':
        for index,row in enumerate(payload['meshes']):
            for kind,item in row['artifacts'].items():
                artifact=Path(item['file'])
                if kind not in ('before','shaded','finalized','readback') or artifact.resolve()!=(run_dir/f'native-mesh/mesh-{index}-{kind}.bin').resolve():
                    raise FormatError('Native mesh object artifact escapes its run')
                reject_reparse(artifact)
                if not 1<=artifact.stat().st_size<=16*1024*1024 or file_hash(artifact)!=item['sha256']:
                    raise FormatError('Native mesh object artifact size/hash differs')
        for item,suffix in zip(payload['artifacts'],('', '.yaml')):
            artifact=Path(item['file']);expected=run_dir/'native-mesh/native-library'/(params['namespace']+'.clb'+suffix)
            if artifact.resolve()!=expected.resolve() or file_hash(artifact)!=item['sha256'] or not 1<=artifact.stat().st_size<=32*1024*1024:
                raise FormatError('Native mesh library artifact size/path/hash differs')
            reject_reparse(artifact)
    return {'format': FORMAT, 'binding': binding, 'operation': operation, 'params': params,
            'payload': payload, 'run': str(run_dir), 'created_at': datetime.now(timezone.utc).isoformat(),
            'coverage': COVERAGE}


def cached(game, operation, params=None, cache_dir=Path('.aow4mod/native'), refresh=False, timeout=30, runner=None):
    params = params or {}
    binding = profile_for(game)
    require_operation(binding, operation)
    directory = check_cache_directory(cache_dir, game)
    key = sha(json.dumps({'binding': binding, 'operation': operation, 'params': params}, sort_keys=True).encode())
    path = directory/'cache'/f'{key}.json'
    if path.is_file() and not refresh:
        reject_reparse(path)
        result = json.loads(path.read_text(encoding='utf-8'))
        if (result.get('format') != FORMAT or result.get('binding') != binding
                or result.get('operation') != operation or result.get('params') != params):
            raise FormatError('Native cache provenance mismatch; inspect it before refreshing')
        validate_payload(operation, result.get('payload'), params)
        return {**result, 'cached': True}
    result = run_worker(game, operation, params, directory, timeout, runner)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        reject_reparse(path)
    save_record(path, result)
    return {**result, 'cached': False}


def select_type(registry, value):
    rows = registry['payload']['types']
    try:
        cid = parse_cid(value)
    except FormatError:
        cid = None
    matches = [r for r in rows if r['cid'] == cid or r['name'].casefold() == str(value).casefold()]
    if len(matches) != 1:
        raise FormatError('Native type must resolve to one exact class ID or class name; use native-types')
    return matches[0]


def public_binding(binding):
    return {k: binding[k] for k in ('game', 'dll_sha256', 'backend_sha256')} | {'profile': binding['profile']['name']}


def enrich_enums(fields, binding, schema_path):
    """Join the native enum parameter to an independently hash-bound table.

    This does not guess a serialized field path from its display name.
    """
    from . import reflection
    editor = Path(binding['game'])/'ResourceED.exe'
    expected = binding['profile']['resource_editor_sha256']
    if file_hash(editor) != expected:
        raise FormatError('ResourceED does not match the native enum profile')
    schema = reflection.load(schema_path, expected)
    result = []
    for field in fields:
        field = dict(field)
        if field.get('codec') == 'enum_i32':
            cid = f'0x{field["editor_parameter"]:08X}'
            table = schema.get('enums', {}).get(cid)
            if table and isinstance(table.get('values'), list):
                field['enum'] = {'cid': cid, 'name': table['name'], 'values': table['values']}
                if field.get('default_status') == 'read':
                    labels = [v['label'] for v in table['values'] if v['value'] == field['default']]
                    if len(labels) == 1:
                        field['default_label'] = labels[0]
        result.append(field)
    return result


def types(game, query='', cache_dir=Path('.aow4mod/native'), refresh=False, timeout=30, limit=20, offset=0):
    catalog.check_page(limit, offset)
    result = cached(game, 'types', cache_dir=cache_dir, refresh=refresh, timeout=timeout)
    rows = [r for r in result['payload']['types'] if query.casefold() in (r['cid']+' '+r['name']).casefold()]
    return {'binding': public_binding(result['binding']), 'cached': result['cached'], 'run': result['run'],
            **catalog.page(rows[offset:offset+limit], len(rows), limit, offset), 'coverage': COVERAGE}


def properties(game, type_value, query='', cache_dir=Path('.aow4mod/native'), refresh=False,
               timeout=30, limit=20, offset=0, schema_path=None):
    catalog.check_page(limit, offset)
    registry = cached(game, 'types', cache_dir=cache_dir, refresh=refresh, timeout=timeout)
    selected = select_type(registry, type_value)
    result = cached(game, 'properties', selected, cache_dir, refresh, timeout)
    rows = result['payload']['properties']
    if schema_path is not None:
        rows = enrich_enums(rows, result['binding'], schema_path)
    filtered = [r for r in rows if query.casefold() in (r['name']+' '+r['hint']).casefold()]
    return {'binding': public_binding(result['binding']), 'cached': result['cached'], 'run': result['run'],
            'type': selected, 'property_count': len(rows),
            'defaults_read': sum(r.get('default_status') == 'read' for r in rows),
            **catalog.page(filtered[offset:offset+limit], len(filtered), limit, offset), 'coverage': COVERAGE}
