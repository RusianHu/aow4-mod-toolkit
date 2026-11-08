"""Compact CLI with explicit machine-readable output and coverage boundaries."""
import argparse
import json
from pathlib import Path
import sys
import sqlite3
import struct

from . import __version__
from . import core
from .corpus import run_corpus
from .discovery import find_game, inventory
from . import catalog
from .semantics import inspect_resource
from . import snapshots
from . import projects
from . import editing
from . import field_copy, resource_diff
from . import migration
from . import maintenance
from . import localization
from . import content_index, content_queries, content_analysis, content_snapshots
from . import authoring
from . import overrides
from . import publication
from . import cloning
from . import reflection
from . import linking
from . import environment,dlc,structured,resources,synchronization,creation,steam_versions
from . import capabilities
from . import native
from . import native_validation
from . import native_roundtrip
from . import native_objects, native_authoring
from . import native_inspection
from . import native_income
from . import textures,material_parameters
from . import compressed_textures,texture_toolchain
from . import content_libraries, native_assets, asset_yaml, asset_editing
from . import pack_imports, overlay_imports, reference_arrays, loadout
from . import images,image_links,meshes,mesh_imports,animation_imports
from . import launcher, editor_bridge, resource_groups
from . import resource_templates, reference_initialization
from . import native_edits
from . import localized_links
from . import equipment_sets
from . import native_overlays
from .filesystem import file_hash


def emit(value, output=None):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if output:
        core.write_json(Path(output), value, [])
    print(text)


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr,'reconfigure'):sys.stderr.reconfigure(encoding='utf-8')
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("audit", "log"):
        if argv[0] == "audit" and "--game" not in argv:
            try:
                argv.extend(["--game", str(find_game())])
            except core.FormatError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
        return core.main(argv)
    parser = argparse.ArgumentParser(prog="aow4mod", description="AoW4 resource development and maintenance tools")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    p=sub.add_parser('capabilities',help='compact supported-workflow routing and explicit validation limits')
    p.add_argument('area',nargs='?',choices=tuple(capabilities.AREAS))
    p.add_argument('--game',type=Path,help='inspect readiness for this installation without loading native code')
    p=sub.add_parser('inspect',help='paginated ACP/RPK identities without building an index')
    p.add_argument('path',type=Path);p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p.add_argument('--output',type=Path,help='save the complete inspection outside the input directory')
    p = sub.add_parser("doctor", help="discover installed game, branch, tools and Mod locations")
    p.add_argument("--game", type=Path)
    p=sub.add_parser('game-version',help='compare installed Steam branch/build with a capture or an official anonymous metadata query')
    p.add_argument('--game',type=Path)
    remote=p.add_mutually_exclusive_group()
    remote.add_argument('--capture',type=Path,help='saved app_info_print stdout; never treated as a fresh server query')
    remote.add_argument('--steamcmd',type=Path,help='valid Valve-signed steamcmd.exe in a separate tool directory; may self-update its runtime')
    p.add_argument('--branch',help='branch to compare; defaults to the installed branch/public')
    p.add_argument('--evidence',type=Path,help='required new query directory with --steamcmd')
    p.add_argument('--timeout',type=int,default=120)
    p.add_argument('--output',type=Path,help='optional new JSON report outside protected inputs')
    p = sub.add_parser('native-status', help='check the installed native editor ABI profile without executing it')
    p.add_argument('--game', type=Path)
    for command in ('native-types', 'native-properties'):
        p = sub.add_parser(command, help='isolated native class registry/default-object inspection; no resource writes')
        if command == 'native-properties':
            p.add_argument('type', help='exact class name or hexadecimal class ID')
            p.add_argument('--query', default='')
            p.add_argument('--schema', type=Path, help='join enum tables from a hash-bound offline schema')
        else:
            p.add_argument('query', nargs='?', default='')
        p.add_argument('--game', type=Path)
        p.add_argument('--cache', type=Path, default=Path('.aow4mod/native'))
        p.add_argument('--refresh', action='store_true')
        p.add_argument('--timeout', type=int, default=30)
        p.add_argument('--limit', type=int, default=20)
        p.add_argument('--offset', type=int, default=0)
    p = sub.add_parser('native-check', help='partial independent RPK localization witness; never a gameplay certification')
    p.add_argument('path', type=Path)
    p.add_argument('--game', type=Path)
    p.add_argument('--cache', type=Path, default=Path('.aow4mod/native'))
    p.add_argument('--timeout', type=int, default=45)
    p.add_argument('--output', type=Path)
    p.add_argument('--language',type=Path,action='append',default=[],help='explicit hash-bound MO lookup context')
    p.add_argument('--dependency-pack',type=Path,action='append',default=[],help='explicit hash-bound RPK lookup context')
    p = sub.add_parser('native-construct', help='construct a native object with reviewed scalar values and fresh-object readback')
    p.add_argument('type')
    p.add_argument('--values-file', type=Path)
    p.add_argument('--schema', type=Path, default=Path('.aow4mod/schema.json'))
    p.add_argument('--index', type=Path, default=Path('.aow4mod/catalog.sqlite'))
    p.add_argument('--game', type=Path)
    p.add_argument('--cache', type=Path, default=Path('.aow4mod/native'))
    p.add_argument('--timeout', type=int, default=30)
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('native-roundtrip', help='save an RPK using the official native writer into a new diagnostic directory')
    p.add_argument('path', type=Path)
    p.add_argument('--game', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cache', type=Path, default=Path('.aow4mod/native'))
    p.add_argument('--timeout', type=int, default=45)
    p.add_argument('--dependency-library',type=Path,action='append',default=[],help='explicit hash-bound CLB search context; repeat as needed')
    p.add_argument('--language',type=Path,action='append',default=[],help='explicit bundled MO context; repeat to match the build context')
    p.add_argument('--dependency-pack',type=Path,action='append',default=[],help='explicit hash-bound RPK lookup context')
    p = sub.add_parser('native-inspect', help='read actual primitive values from a selected resource through the native deserializer')
    p.add_argument('path', type=Path)
    p.add_argument('--id')
    p.add_argument('--pack')
    nested=p.add_mutually_exclusive_group()
    nested.add_argument('--object',action='append',help='owned object property name; repeat for a nested path')
    nested.add_argument('--object-path-file',type=Path,help='JSON array of name/category selectors for owned objects')
    p.add_argument('--query', default='')
    p.add_argument('--schema', type=Path)
    p.add_argument('--game', type=Path)
    p.add_argument('--cache', type=Path, default=Path('.aow4mod/native'))
    p.add_argument('--timeout', type=int, default=30)
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--offset', type=int, default=0)
    p.add_argument('--output', type=Path)
    p = sub.add_parser("corpus", help="read-only checks of local ACP/RPK fixtures")
    p.add_argument("roots", nargs="*", type=Path)
    p.add_argument("--game", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--details", action="store_true")
    p=sub.add_parser('clb-inspect',help='inspect bounded CLB v8 metadata and named top-level members')
    p.add_argument('path',type=Path);p.add_argument('--query',default='');p.add_argument('--category')
    p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('clb-member',help='check an exact top-level content-library locator')
    p.add_argument('path',type=Path);p.add_argument('--locator',required=True)
    p=sub.add_parser('clb-dependencies',help='read explicit CLB library dependency declarations; no closure or link certification')
    p.add_argument('path',type=Path)
    p=sub.add_parser('clb-import-plan',help='plan additive CLB dependencies with native compilation and independent readback')
    p.add_argument('path',type=Path);p.add_argument('--library',type=Path,required=True);p.add_argument('--add',action='append',required=True)
    p.add_argument('--game',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60)
    p=sub.add_parser('clb-import-apply',help='stage an evidence-bound additive CLB dependency plan into a new Mod copy')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('editor-prepare',help='copy a Mod into a hash-bound official editor session; does not launch the GUI')
    p.add_argument('path',type=Path);p.add_argument('--editor',choices=tuple(editor_bridge.EDITORS),required=True)
    p.add_argument('--game',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('editor-start',help='launch the prepared official GUI once; no batch import/save is implied')
    p.add_argument('session',type=Path);p.add_argument('--show',action='store_true',help='request a visible editor window')
    p=sub.add_parser('editor-status',help='inspect owned process identity and paginated changed file hashes')
    p.add_argument('session',type=Path);p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('editor-collect',help='copy a closed editor session into a new structurally checked Mod stage')
    p.add_argument('session',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('mesh-inspect',help='inspect the observed static MeshDEV geometry layout in exported native library YAML')
    p.add_argument('path',type=Path);p.add_argument('--locator',required=True)
    p=sub.add_parser('native-model-check',help='observe activated native model links to local mesh/material objects without reactivating them')
    p.add_argument('path',type=Path);p.add_argument('--locator',action='append',required=True);p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60)
    p=sub.add_parser('material-conditions',help='evaluate loaded material scalar conditions with the official CPU evaluator; no renderer-selection claim')
    p.add_argument('path',type=Path);p.add_argument('--locator',required=True);p.add_argument('--game',type=Path)
    p.add_argument('--dependency-library',type=Path,action='append',default=[]);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120)
    p.add_argument('--dynamic-material-defaults',action='store_true',help='explicit what-if evaluation of dynamic conditions using stored material defaults')
    p.add_argument('--program-candidates',action='store_true',help='conditional table query; first 32 descriptor bytes and actual renderer state remain unresolved')
    p.add_argument('--pass-index',type=int,default=0);p.add_argument('--stage',type=int,choices=(0,1),default=1)
    p.add_argument('--require',action='append',default=[]);p.add_argument('--forbid',action='append',default=[])
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('native-material-textures',help='check actual static material texture pointers and independently loaded full texture objects')
    p.add_argument('path',type=Path);p.add_argument('--locator',action='append',required=True)
    p.add_argument('--dependency-library',type=Path,action='append',default=[]);p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('native-material-check',help='check live material/effect links against independently loaded full object witnesses')
    p.add_argument('path',type=Path);p.add_argument('--locator',action='append',required=True);p.add_argument('--game',type=Path)
    p.add_argument('--dependency-library',type=Path,action='append',default=[]);p.add_argument('--output',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120)
    p=sub.add_parser('source-motion-check',help='compare prepared source FBX FK with native model matrices at keys and inter-key samples')
    p.add_argument('path',type=Path);p.add_argument('--library',type=Path,required=True);p.add_argument('--model',required=True);p.add_argument('--time-us',type=int,action='append');p.add_argument('--max-position-error',type=float,default=0.001);p.add_argument('--max-linear-error',type=float,default=0.0002);p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('source-motion-verify',help='recompute saved source/native sampled motion evidence without native execution')
    p.add_argument('path',type=Path)
    p=sub.add_parser('skeletal-source-check',help='independently verify FBX local geometry/weights/topology/default frames against direct CLB data; no native execution')
    p.add_argument('path',type=Path);p.add_argument('--library',type=Path,required=True);p.add_argument('--mesh',required=True);p.add_argument('--model');p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('skeletal-source-verify',help='recompute saved FBX-to-CLB correspondence evidence offline')
    p.add_argument('path',type=Path)
    p=sub.add_parser('skeletal-import-plan',help='author a draft new skinned mesh/model/material/motion library from a bounded FBX source')
    p.add_argument('path',type=Path);p.add_argument('--fbx',type=Path,required=True);p.add_argument('--namespace',required=True);p.add_argument('--name',required=True);p.add_argument('--dxc',type=Path,required=True)
    p.add_argument('--rotation-storage',choices=('snorm16','float32-guarded'),default='snorm16')
    p.add_argument('--weights',choices=('strict','normalize-top4'),default='strict');p.add_argument('--max-dropped-fraction',type=float);p.add_argument('--bake-fps',type=float);p.add_argument('--bake-max-error',type=float,default=0.001)
    p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('skeletal-import-apply',help='stage a checked draft new skeletal model and retained source FBX files')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('model-pose-bake-plan',help='bake all components of one existing single-LOD animation pose into new static mesh/model definitions')
    p.add_argument('path',type=Path);p.add_argument('--library',type=Path,required=True);p.add_argument('--model',required=True);p.add_argument('--time-us',type=int,required=True);p.add_argument('--name',required=True);p.add_argument('--dxc',type=Path,required=True)
    p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--tangents',choices=('preserve','rebuild'),default='preserve');p.add_argument('--merge-components',action='store_true')
    p=sub.add_parser('model-pose-bake-apply',help='stage verified baked static definitions in a new Mod copy')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('skin-weights-plan',help='stage typed three/four-influence mesh bone-weight edits with native readback')
    p.add_argument('path',type=Path);p.add_argument('--library',type=Path,required=True);p.add_argument('--mesh',required=True);p.add_argument('--values-file',type=Path,required=True)
    p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('skin-weights-apply',help='apply a checked skin-weight plan only to a new Mod stage')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('shader-toolchain',help='inspect or explicitly download the pinned DXC disassembler for skin-position references')
    p.add_argument('--dxc',type=Path);p.add_argument('--download',action='store_true');p.add_argument('--output',type=Path);p.add_argument('--proxy');p.add_argument('--game',type=Path)
    p=sub.add_parser('model-skinning-check',help='compare model-local LBS positions with pinned compiled game shader prefixes on CPU')
    p.add_argument('path',type=Path);p.add_argument('--model',required=True);p.add_argument('--time-us',type=int,action='append',required=True);p.add_argument('--dxc',type=Path,required=True)
    p.add_argument('--lod',type=int,default=0);p.add_argument('--component',type=int,default=0);p.add_argument('--vertex',type=int,action='append');p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--allow-unmapped',action='store_true',help='explicitly use bind pose for model frames without an animation track')
    p.add_argument('--directions',action='store_true',help='also reference raw normal/tangent xyz for reviewed PNUVCT skin declarations')
    p=sub.add_parser('model-skinning-verify',help='recompute saved bone palette, mesh and shader-prefix skin-position evidence without native execution')
    p.add_argument('path',type=Path)
    p=sub.add_parser('model-animation-plan',help='bind an existing local model to a compatible same-library animation')
    p.add_argument('path',type=Path);p.add_argument('--library',type=Path,required=True);p.add_argument('--model',required=True);p.add_argument('--animation',required=True)
    p.add_argument('--time-us',type=int,action='append');p.add_argument('--allow-unmapped',action='store_true');p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('model-animation-apply',help='stage an independently verified default animation binding')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('native-model-poses',help='inspect native frame mapping and model-local hierarchy/bone poses')
    p.add_argument('path',type=Path);p.add_argument('--model',required=True);p.add_argument('--animation');p.add_argument('--time-us',type=int,action='append',required=True)
    p.add_argument('--allow-unmapped',action='store_true');p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'))
    p.add_argument('--timeout',type=int,default=60);p.add_argument('--output',type=Path)
    p=sub.add_parser('model-poses-verify',help='recompute saved native frame mapping and hierarchy pose evidence')
    p.add_argument('path',type=Path)
    p=sub.add_parser('animation-tracks',help='list animation track names and key counts without loading the engine')
    p.add_argument('path',type=Path);p.add_argument('--animation',required=True);p.add_argument('--query',default='')
    p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('animation-poses-verify',help='recompute a saved local-channel report from raw objects and independent CLB witnesses')
    p.add_argument('path',type=Path)
    p=sub.add_parser('native-animation-poses',help='sample native local animation channels and compare independent CPU math')
    p.add_argument('path',type=Path);p.add_argument('--animation',required=True);p.add_argument('--track',action='append',required=True)
    p.add_argument('--time-us',type=int,action='append',required=True);p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60);p.add_argument('--output',type=Path)
    p=sub.add_parser('animation-import-plan',help='import one skeletal FBX take as a draft native animation library')
    p.add_argument('path',type=Path);p.add_argument('--source',type=Path,required=True);p.add_argument('--namespace',required=True)
    p.add_argument('--title',required=True);p.add_argument('--bake-fps',type=float,help='explicitly bake linear TRS curves uniformly before native import');p.add_argument('--bake-max-error',type=float,default=0.001,help='maximum linear resampling error in each source scalar unit');p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'))
    p.add_argument('--timeout',type=int,default=60);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('animation-import-apply',help='stage a verified native animation library and retain its complete FBX source')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('mesh-import-plan',help='create a reviewed draft static FBX mesh library with native geometry/file witnesses')
    p.add_argument('path',type=Path);p.add_argument('--source',type=Path,required=True);p.add_argument('--namespace',required=True)
    p.add_argument('--vertex-profile',choices=('pnuv32','pnuvct52'),default='pnuv32',help='optional native tangents and explicit packed white vertex colors')
    p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'))
    p.add_argument('--timeout',type=int,default=60);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('mesh-import-apply',help='stage a verified draft mesh library and retain its FBX source')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('asset-search',help='search native asset YAML without dumping binary/text payloads')
    p.add_argument('path',type=Path);p.add_argument('query',nargs='?',default='')
    p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    for command in ('asset-export','asset-compile'):
        p=sub.add_parser(command,help='official native CLB/YAML conversion into a new verified diagnostic directory')
        p.add_argument('path',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--game',type=Path)
        p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60)
        p.add_argument('--max-mib',type=int,default=256)
    p=sub.add_parser('asset-extract',help='extract 1..16 explicit mesh/model/material CLB templates with independent native readback')
    p.add_argument('path',type=Path);p.add_argument('--locator',action='append',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60)
    p=sub.add_parser('asset-edit-plan',help='plan a native-compiled existing CLB scalar/vector edit for a staged Mod')
    p.add_argument('path',type=Path);p.add_argument('--library',type=Path,required=True)
    p.add_argument('--yaml-path',required=True,help='JSON array from asset-search')
    p.add_argument('--value',required=True,help='JSON scalar or vector');p.add_argument('--float32',action='store_true')
    p.add_argument('--relative',required=True,help='destination relative to the staged Mod, e.g. Libraries/MyLibrary.clb')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60)
    p=sub.add_parser('asset-edit-apply',help='apply a reviewed native CLB edit to a new staged Mod copy')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('model-bind-plan',help='create a draft static model/material in an existing shaded mesh library with native link proof')
    p.add_argument('path',type=Path);p.add_argument('--library',type=Path,required=True);p.add_argument('--mesh',required=True);p.add_argument('--name',required=True)
    p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('model-bind-apply',help='stage a reviewed static model/material library and retain draft provenance')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('component-model-plan',help='link a TGCM mesh part to an authored static model, its mesh material and CLB import atomically')
    p.add_argument('path',type=Path);p.add_argument('--id',required=True);p.add_argument('--piece',type=int,required=True)
    p.add_argument('--library',type=Path,required=True);p.add_argument('--model',required=True);p.add_argument('--pack');p.add_argument('--source-root',type=Path)
    p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('component-model-apply',help='stage a reviewed game-component/model/material/import transaction')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('dxil-inspect',help='inspect bounded DXIL program pools and declared vertex inputs without executing shaders')
    p.add_argument('path',type=Path);p.add_argument('--vertex-profile',choices=('pnuv32','pnuvct52'));p.add_argument('--limit',type=int,default=10);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('effect-variants',help='query serialized CLB effect-function feature masks and declared variants')
    p.add_argument('path',type=Path);p.add_argument('--locator',required=True);p.add_argument('--stage',type=int,choices=(0,1));p.add_argument('--pass-id',type=int)
    p.add_argument('--require',action='append',default=[]);p.add_argument('--forbid',action='append',default=[])
    p.add_argument('--limit',type=int,default=10);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('effect-programs',help='map serialized pass descriptor keys to exact DXIL programs and vertex input declarations')
    p.add_argument('path',type=Path);p.add_argument('--locator',required=True);p.add_argument('--pass-index',type=int);p.add_argument('--stage',type=int,choices=range(8))
    p.add_argument('--key',help='exact 64-byte descriptor prefix in hexadecimal');p.add_argument('--vertex-profile',choices=('pnuv32','pnuvct52'))
    p.add_argument('--require',action='append',default=[]);p.add_argument('--forbid',action='append',default=[])
    p.add_argument('--limit',type=int,default=10);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('native-effect-programs',help='freeze loaded CPU DXIL pool and complete pass/stage program lookup tables; no shader execution')
    p.add_argument('path',type=Path);p.add_argument('--locator',required=True);p.add_argument('--game',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120)
    p=sub.add_parser('map-inspect',help='inspect the observed bounded GMP tactical-map envelope and top-level scopes')
    p.add_argument('path',type=Path);p.add_argument('--grids',action='store_true',help='also decode reviewed tactical grid dimensions, levels and cell counts')
    p=sub.add_parser('map-terrain',help='inspect stored height scalars and explicit terrain texture-weight pixels')
    p.add_argument('path',type=Path);p.add_argument('--level',type=int,default=0);p.add_argument('--x',type=int,default=0);p.add_argument('--y',type=int,default=0)
    p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('native-map-terrain',help='observe native CPU terrain-weight buffers and the complete pixel object')
    p.add_argument('path',type=Path);p.add_argument('--level',type=int,default=0);p.add_argument('--x',type=int,required=True);p.add_argument('--y',type=int,required=True);p.add_argument('--game',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120)
    p.add_argument('--dependency-library',type=Path,action='append',default=[]);p.add_argument('--dependency-pack',type=Path,action='append',default=[])
    p=sub.add_parser('terrain-weights-plan',help='plan an exact eight-channel terrain-texture pixel rectangle with independent native evidence')
    p.add_argument('path',type=Path);p.add_argument('--map',dest='map_file',type=Path,required=True);p.add_argument('--level',type=int,default=0)
    p.add_argument('--x',type=int,required=True);p.add_argument('--y',type=int,required=True);p.add_argument('--width',type=int,required=True);p.add_argument('--height',type=int,required=True)
    weights=p.add_mutually_exclusive_group(required=True);weights.add_argument('--layer',type=int,choices=range(8));weights.add_argument('--weights',help='eight comma-separated uint8 values summing to 255')
    p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('terrain-weights-apply',help='stage a reviewed terrain-texture weight rectangle with map draft provenance')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('map-entities',help='page explicit tactical object-manager entities, IDs and stored direction bytes')
    p.add_argument('path',type=Path);p.add_argument('--type',dest='entity_type');p.add_argument('--id',dest='entity_id')
    p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0);p.add_argument('--details',action='store_true')
    p=sub.add_parser('map-world-entities',help='inspect known FZones and cell-held entities, identity collisions and cell positions')
    p.add_argument('path',type=Path);p.add_argument('--type',dest='entity_type');p.add_argument('--id',dest='entity_id');p.add_argument('--origin',choices=('zone','cell'))
    p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('native-map-entities',help='read loaded-map entity bodies, actual Direction and native hex-position/packing witnesses')
    p.add_argument('path',type=Path);p.add_argument('--id',dest='entity_ids',action='append');p.add_argument('--game',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120)
    p.add_argument('--dependency-library',type=Path,action='append',default=[]);p.add_argument('--dependency-pack',type=Path,action='append',default=[])
    p=sub.add_parser('native-map-world-entities',help='observe known native FZones and cell-held entity occupancy, objects, positions and cell bodies')
    p.add_argument('path',type=Path);p.add_argument('--id',dest='entity_ids',action='append');p.add_argument('--game',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120)
    p.add_argument('--dependency-library',type=Path,action='append',default=[]);p.add_argument('--dependency-pack',type=Path,action='append',default=[])
    p=sub.add_parser('native-map-roundtrip',help='save a diagnostic tactical-map copy and independently reload/recompare it')
    p.add_argument('path',type=Path);p.add_argument('--game',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120)
    p.add_argument('--dependency-library',type=Path,action='append',default=[]);p.add_argument('--dependency-pack',type=Path,action='append',default=[])
    p=sub.add_parser('map-metadata-plan',help='plan a native-verified literal metadata edit on a stable staged tactical map')
    p.add_argument('path',type=Path);p.add_argument('--map',dest='map_file',type=Path,required=True)
    p.add_argument('--field',choices=('name','subheader','creator','world_name','world_description'),required=True);p.add_argument('--text',required=True)
    p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('map-metadata-apply',help='stage a reviewed map metadata edit and draft provenance in a new Mod copy')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('map-direction-plan',help='plan a native/getter-verified Direction edit for a reviewed existing deployment zone')
    p.add_argument('path',type=Path);p.add_argument('--map',dest='map_file',type=Path,required=True);p.add_argument('--id',dest='entity_id',required=True)
    p.add_argument('--direction',type=int,choices=range(6),required=True);p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('map-direction-apply',help='stage a reviewed deployment-zone Direction edit with map draft provenance')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('map-position-plan',help='plan a grid-bounded deployment-zone move with actual native position/packer witnesses')
    p.add_argument('path',type=Path);p.add_argument('--map',dest='map_file',type=Path,required=True);p.add_argument('--id',dest='entity_id',required=True)
    p.add_argument('--x',type=int,required=True);p.add_argument('--y',type=int,required=True);p.add_argument('--level',type=int)
    p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('map-position-apply',help='stage a reviewed deployment-zone move and map draft provenance in a new Mod copy')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('map-entity-copy-plan',help='plan one basic deployment-zone template copy using the world next-ID position and complete native list witnesses')
    p.add_argument('path',type=Path);p.add_argument('--map',dest='map_file',type=Path,required=True);p.add_argument('--id',dest='entity_id',required=True)
    p.add_argument('--new-id',help='optional assertion of the stored next entity ID');p.add_argument('--x',type=int,required=True);p.add_argument('--y',type=int,required=True);p.add_argument('--level',type=int)
    p.add_argument('--direction',type=int,choices=range(6));p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('map-entity-copy-apply',help='stage a reviewed deployment-zone template copy with map draft provenance')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('map-cell-move-plan',help='plan a native-verified flee-point move with coupled cell ownership and position')
    p.add_argument('path',type=Path);p.add_argument('--map',dest='map_file',type=Path,required=True);p.add_argument('--id',dest='entity_id',required=True)
    p.add_argument('--x',type=int,required=True);p.add_argument('--y',type=int,required=True);p.add_argument('--level',type=int);p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('map-cell-move-apply',help='stage a reviewed flee-point cell move while retaining map draft gates')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('image-plan',help='compile PNGs into a new native UI atlas with independent official PNG pixel witnesses')
    p.add_argument('path',type=Path);p.add_argument('--image',type=Path,action='append',required=True)
    p.add_argument('--library',required=True);p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'))
    p.add_argument('--timeout',type=int,default=60);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('image-apply',help='stage the reviewed PNG atlas and preserve author source images')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('image-sprites',help='inspect nested sprite IDs/names of one native CLB atlas')
    p.add_argument('path',type=Path);p.add_argument('--locator',required=True);p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('image-link-plan',help='link a known game icon to a staged atlas/sprite and declare its local RPK import')
    p.add_argument('path',type=Path);p.add_argument('--id',required=True);p.add_argument('--library',type=Path,required=True)
    p.add_argument('--locator',required=True);p.add_argument('--sprite-id',type=int,required=True);p.add_argument('--pack')
    p.add_argument('--source-root',type=Path);p.add_argument('--import-pack',type=Path);p.add_argument('--game',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('image-link-apply',help='atomically stage the reviewed icon reference and CLB import')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p = sub.add_parser("playset", help="read active launcher Mod paths and duplicate package identities")
    p.add_argument("--file",type=Path)
    p = sub.add_parser('loadout-plan',help='prepare reversible content_load.json changes; does not change launcher database')
    p.add_argument('--file',type=Path);p.add_argument('--enable',type=Path,action='append',default=[])
    p.add_argument('--disable',type=Path,action='append',default=[]);p.add_argument('--position',type=int)
    p.add_argument('--game',type=Path);p.add_argument('--output',type=Path,required=True)
    p = sub.add_parser('loadout-apply',help='write a reviewed launch configuration after making a durable backup')
    p.add_argument('plan',type=Path);p.add_argument('--backup',type=Path,required=True)
    for operation in ('status','rollback'):
        p = sub.add_parser('loadout-'+operation,help='inspect or restore a guarded launch configuration change')
        p.add_argument('receipt',type=Path)
    p=sub.add_parser('launcher-inspect',help='read the persistent launcher playset and compare its generated launch configuration')
    p.add_argument('--database',type=Path);p.add_argument('--playset');p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p.add_argument('--launcher-asar',type=Path)
    p=sub.add_parser('launcher-plan',help='plan persistent local registration, enable/disable and order changes; no live writes')
    p.add_argument('--database',type=Path);p.add_argument('--playset');p.add_argument('--enable',type=Path,action='append',default=[])
    p.add_argument('--disable',type=Path,action='append',default=[]);p.add_argument('--position',type=int)
    p.add_argument('--launcher-asar',type=Path);p.add_argument('--game',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--unregister',type=Path,action='append',default=[]);p.add_argument('--registration-receipt',type=Path)
    p=sub.add_parser('launcher-apply',help='commit a reviewed launcher database/configuration change with a durable backup')
    p.add_argument('plan',type=Path);p.add_argument('--backup',type=Path,required=True)
    for operation in ('status','rollback'):
        p=sub.add_parser('launcher-'+operation,help='inspect or restore a persistent launcher change')
        p.add_argument('receipt',type=Path)
    p = sub.add_parser("conflicts",help="compare resource definitions across selected Mods")
    p.add_argument("--mod",type=Path,action="append",default=[])
    p.add_argument("--playset",type=Path)
    p.add_argument("--include-unregistered",action="store_true")
    p.add_argument("--limit",type=int,default=20)
    p.add_argument("--offset",type=int,default=0)
    p.add_argument("--output",type=Path)
    p = sub.add_parser("index", help="build/update a local resource index")
    p.add_argument("--game", type=Path)
    p.add_argument("--mod", action="append", default=[], type=Path)
    p.add_argument("--index", type=Path, default=Path(".aow4mod/catalog.sqlite"))
    p.add_argument("--links", action="store_true")
    p.add_argument('--locale', action='append', default=[], help='index installed MO catalogs for this language; repeat for ZH/EN')
    p.add_argument('--catalog', action='append', default=[], help='explicit language assignment LOCALE=path.mo; repeat all desired inputs')
    p = sub.add_parser('loc-catalogs', help='discover installed language and Mod MO candidates without selecting a runtime dictionary')
    p.add_argument('--game', type=Path)
    p.add_argument('--mod', action='append', default=[], type=Path)
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--offset', type=int, default=0)
    p = sub.add_parser('loc-search', help='search indexed dictionary text/keys, including keys with no recognized resource users')
    p.add_argument('query', nargs='?', default='')
    p.add_argument('--locale')
    p.add_argument('--field', choices=('any', 'key', 'text'), default='any')
    p.add_argument('--exact', action='store_true')
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--offset', type=int, default=0)
    p.add_argument('--index', type=Path, default=Path('.aow4mod/catalog.sqlite'))
    p = sub.add_parser("snapshot", help="capture original and Mod fields independently of package metadata")
    p.add_argument("path", type=Path)
    p.add_argument("--game", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--index", type=Path, default=Path(".aow4mod/catalog.sqlite"))
    p.add_argument("--dependency-depth", type=int, default=0)
    p.add_argument("--max-dependencies", type=int, default=250)
    p = sub.add_parser("compare", help="three-way comparison of two version 2 snapshots")
    p.add_argument("old", type=Path)
    p.add_argument("new", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--details", action="store_true")
    p = sub.add_parser("search", help="resource name/ID/text-key/DataStoreTag/localized-text search with match evidence")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--id")
    p.add_argument("--type")
    p.add_argument("--origin", choices=("game", "mod"))
    p.add_argument('--field', choices=content_queries.FIELDS, default='any')
    p.add_argument('--locale')
    p.add_argument('--exact', action='store_true')
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--index", type=Path, default=Path(".aow4mod/catalog.sqlite"))
    p = sub.add_parser('describe', help='source-bound texts, tome membership, references and fields; no inferred gameplay values')
    p.add_argument('id')
    p.add_argument('--file', type=Path)
    p.add_argument('--locale')
    p.add_argument('--section', choices=('summary', 'texts', 'members', 'references', 'fields'), default='summary')
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--offset', type=int, default=0)
    p.add_argument('--index', type=Path, default=Path('.aow4mod/catalog.sqlite'))
    p = sub.add_parser('trace', help='bounded incoming/outgoing resource graph; preserves repeated edges, cycles and ambiguous targets')
    p.add_argument('id')
    p.add_argument('--file', type=Path)
    p.add_argument('--direction', choices=('outgoing', 'incoming'), default='outgoing')
    p.add_argument('--depth', type=int, default=2)
    p.add_argument('--max-nodes', type=int, default=40)
    p.add_argument('--max-edges', type=int, default=100)
    p.add_argument('--index', type=Path, default=Path('.aow4mod/catalog.sqlite'))
    p.add_argument('--output', type=Path)
    p = sub.add_parser('explain', help='follow reviewed research/spell/effect/cost branches with explicit value provenance and unknowns')
    p.add_argument('id');p.add_argument('--file',type=Path);p.add_argument('--locale')
    p.add_argument('--native',action='store_true',help='read supported loaded scalar and owned Area values in isolated native workers')
    p.add_argument('--depth',type=int,default=12);p.add_argument('--max-nodes',type=int,default=150);p.add_argument('--max-edges',type=int,default=600)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=30)
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'));p.add_argument('--output',type=Path)
    p = sub.add_parser('impact',help='inspect recognized shared-definition users and exact clone/retarget sites')
    p.add_argument('id');p.add_argument('--file',type=Path)
    p.add_argument('--depth',type=int,default=4);p.add_argument('--max-nodes',type=int,default=100);p.add_argument('--max-edges',type=int,default=300)
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'));p.add_argument('--output',type=Path)
    p=sub.add_parser('content-snapshot',help='freeze an explain report with source object and native getter witnesses')
    p.add_argument('report',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('content-compare',help='compare frozen interpreted content offline, retaining raw/unknown changes')
    p.add_argument('before',type=Path);p.add_argument('after',type=Path);p.add_argument('--output',type=Path)
    p = sub.add_parser("show", help="inspect one resource and evidenced property semantics")
    p.add_argument("id")
    p.add_argument("--file", type=Path)
    p.add_argument("--property")
    p.add_argument("--pack", help="disambiguate a direct ACP definition")
    p.add_argument("--game", type=Path, help="optional exact game installation for schema evidence")
    p.add_argument('--schema',type=Path)
    p.add_argument('--limit',type=int,default=30)
    p.add_argument('--offset',type=int,default=0)
    p.add_argument("--index", type=Path, default=Path(".aow4mod/catalog.sqlite"))
    p = sub.add_parser("refs", help="inspect outgoing or indexed reverse references")
    p.add_argument("id")
    p.add_argument("--file", type=Path)
    p.add_argument("--reverse", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--index", type=Path, default=Path(".aow4mod/catalog.sqlite"))
    p = sub.add_parser("edit-plan", help="prepare a hash-pinned property edit without writing Mod data")
    p.add_argument("path",type=Path)
    p.add_argument("--id",required=True)
    p.add_argument("--pack")
    p.add_argument("--property",required=True)
    p.add_argument("--value",default="null",help="JSON value; use null with unset")
    p.add_argument("--codec",choices=("auto","u8","u16","u32","u64","i8","i16","i32","i64","bool","f32","f64","vec2f","vec3f","vec4f","string","hex","unset"),default="auto")
    p.add_argument('--schema',type=Path)
    p.add_argument("--allow-opaque",action="store_true",help="explicit structural-only edits without evidenced property semantics")
    p.add_argument("--game",type=Path)
    p.add_argument("--output",type=Path,required=True)
    p = sub.add_parser("edit-apply",help="apply a reviewed plan to a new staged directory")
    p.add_argument("plan",type=Path)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument('--source-root',type=Path,help='stage a complete project tree containing the edited RPK')
    p=sub.add_parser('field-copy-plan',help='plan atomic selected-field transfers from explicit same-type templates, including original omissions')
    p.add_argument('path',type=Path,help='Mod source directory to stage')
    p.add_argument('--spec',type=Path,required=True)
    p.add_argument('--allow-opaque',action='store_true')
    p.add_argument('--game',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('field-copy-apply',help='rebuild a field-copy plan from its bound inputs and commit a new Mod stage')
    p.add_argument('plan',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('field-copy-canonicalize',help='bind native RPK readbacks and adopt encoding-only changes confined to the copied fields')
    p.add_argument('plan',type=Path)
    p.add_argument('--readback',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('resource-diff',help='compare selected resource trees directly, preserving opaque bytes, absence and array order')
    p.add_argument('before',type=Path);p.add_argument('after',type=Path)
    p.add_argument('--id',required=True);p.add_argument('--other-id')
    p.add_argument('--pack');p.add_argument('--other-pack')
    p.add_argument('--property');p.add_argument('--other-property')
    p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p.add_argument('--output',type=Path)
    p=sub.add_parser('maintenance-start',help='import an existing Mod and assess a staged maintenance workspace')
    p.add_argument('path',type=Path);p.add_argument('--baseline',type=Path);p.add_argument('--game',type=Path)
    p.add_argument('--mod',type=Path,action='append',default=[]);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--workshop-id');p.add_argument('--owner');p.add_argument('--expected-title')
    for command in ('assess','status','stage','build'):
        p=sub.add_parser('maintenance-'+command,help='advance or inspect a staged maintenance workflow')
        p.add_argument('workspace',type=Path)
        if command=='stage':p.add_argument('--safe-partial',action='store_true')
        if command=='build':p.add_argument('--version')
    p=sub.add_parser('maintenance-prepare-release',help='freeze maintenance evidence and prepare optional installation/online identity checks; never submit')
    p.add_argument('workspace',type=Path);p.add_argument('--destination',type=Path);p.add_argument('--backup',type=Path)
    p.add_argument('--online',action='store_true');p.add_argument('--proxy');p.add_argument('--allow-review-plan',action='store_true')
    p=sub.add_parser('maintenance-context',help='capture actual enabled Mods/order and freeze raw conflict witnesses; no playset writes')
    p.add_argument('workspace',type=Path);p.add_argument('--playset',type=Path)
    p.add_argument('--depth',type=int);p.add_argument('--max-resources',type=int)
    p=sub.add_parser('maintenance-review',help='record immutable scoped conclusions and replay preview evidence; never clear release gates')
    p.add_argument('workspace',type=Path);p.add_argument('--note',required=True);p.add_argument('--preview',type=Path)
    p = sub.add_parser('rebase-plan',help='propose non-conflicting original updates using a previous snapshot')
    p.add_argument('previous',type=Path)
    p.add_argument('path',type=Path)
    p.add_argument('--game',type=Path)
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'))
    p.add_argument('--allow-partial',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    p = sub.add_parser('rebase-apply',help='apply a reviewed rebase plan to a new directory')
    p.add_argument('plan',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('loc-lint',help='validate PO/MO keys, completeness and optional source placeholders')
    p.add_argument('path',type=Path)
    p.add_argument('--key-mode',choices=('msgid','context'),default='msgid')
    p.add_argument('--reference',type=Path)
    p.add_argument('--limit',type=int,default=20)
    p.add_argument('--output',type=Path)
    p=sub.add_parser('loc-compile',help='compile a new MO without changing its PO source')
    p.add_argument('path',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--key-mode',choices=('msgid','context'),default='msgid')
    p.add_argument('--reference',type=Path)
    p.add_argument('--require-complete',action='store_true')
    p=sub.add_parser('project-new',help='create a blank project using native defaults, or an explicit ACP template')
    mode=p.add_mutually_exclusive_group()
    mode.add_argument('--template',type=Path)
    mode.add_argument('--native',action='store_true',help='use official native construction (default when no template is given)')
    p.add_argument('--name',required=True)
    p.add_argument('--author',default='')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'))
    p.add_argument('--timeout',type=int,default=30)
    p=sub.add_parser('resource-new-plan',help='construct a native resource in a new or existing RPK and verify native read/save before staging')
    p.add_argument('path',type=Path)
    p.add_argument('--type',required=True)
    p.add_argument('--name',required=True)
    target=p.add_mutually_exclusive_group(required=True)
    target.add_argument('--pack',help='new RPK filename')
    target.add_argument('--into-pack',type=Path,help='existing reachable local RPK, relative to the Mod root or absolute')
    p.add_argument('--container',type=int,help='existing generic container key from pack-layout')
    p.add_argument('--initialize-empty-script',action='store_true',help='accept only reviewed first-load owner binding of four empty regular-building callbacks')
    p.add_argument('--category',default='Resources')
    p.add_argument('--dependency',action='append',default=[])
    p.add_argument('--values-file',type=Path)
    p.add_argument('--schema',type=Path,default=Path('.aow4mod/schema.json'))
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'))
    p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'))
    p.add_argument('--timeout',type=int,default=45)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('resource-new-apply',help='stage a reviewed native resource plan into a new Mod copy')
    p.add_argument('plan',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('pack-layout',help='inspect RPK editor category IDs and available locator positions')
    p.add_argument('path',type=Path);p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('resource-copy-plan',help='copy selected RPK templates with new IDs/internal links into an existing pack and verify native preservation')
    p.add_argument('path',type=Path);p.add_argument('--template',type=Path,required=True)
    p.add_argument('--resource-id',action='append',required=True);p.add_argument('--into-pack',type=Path,required=True)
    p.add_argument('--category',default='Copies');p.add_argument('--container',type=int)
    p.add_argument('--name-prefix',default='Copy - ');p.add_argument('--name');p.add_argument('--with-local-dependencies',action='store_true')
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'));p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=45)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('resource-copy-apply',help='rebuild and stage a verified resource-template append without changing existing sources')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('resource-copy-map',help='page copied resource identities from a template plan or receipt')
    p.add_argument('path',type=Path);p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('localized-link-plan',help='retarget existing localization keys while preserving text parameter objects')
    p.add_argument('path',type=Path);p.add_argument('--values-file',type=Path,required=True)
    p.add_argument('--source-root',type=Path);p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=45)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('localized-link-apply',help='recheck localized-link edits and native witnesses before staging')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('equipment-sets',help='review declared named equipment-set unions and priority shadowing; not an executed engine lookup')
    p.add_argument('name');p.add_argument('--mod',type=Path,action='append',required=True)
    p.add_argument('--required-item');p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'))
    p.add_argument('--game',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('texture-toolchain',help='inspect or explicitly download the pinned Microsoft DirectXTex x64 CPU tool')
    p.add_argument('--texconv',type=Path);p.add_argument('--download',action='store_true');p.add_argument('--output',type=Path);p.add_argument('--proxy');p.add_argument('--game',type=Path)
    p=sub.add_parser('texture-dds-plan',help='import a typed compressed 2D DDS with exact native block and independent decoder proof')
    p.add_argument('path',type=Path);p.add_argument('--dds',type=Path,required=True);p.add_argument('--namespace',required=True)
    p.add_argument('--role',choices=('color','data','normal-xy-snorm'),required=True);p.add_argument('--texconv',type=Path,required=True)
    p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=180);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('texture-compress-plan',help='compress a role-aware PNG/mip chain on CPU and enforce decoded quality bounds')
    p.add_argument('path',type=Path);p.add_argument('--image',type=Path,required=True);p.add_argument('--namespace',required=True)
    p.add_argument('--codec',choices=('bc1','bc3','bc7','bc5s'),required=True);p.add_argument('--role',choices=('color','data','normal-xyz'),default='color');p.add_argument('--mips',choices=('none','full'),default='full')
    p.add_argument('--texconv',type=Path,required=True);p.add_argument('--max-error',type=int,default=32);p.add_argument('--max-normal-angle',type=float,default=8)
    p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=180);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('texture-dds-apply',help='replay compressed DDS/source/quality proofs and stage a new Mod')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('texture-plan',help='create a standalone RGBA8 texture/mip chain from PNG with native CPU-pixel readback')
    p.add_argument('path',type=Path);p.add_argument('--image',type=Path,required=True);p.add_argument('--namespace',required=True)
    p.add_argument('--role',choices=('color','data','normal-xyz'),default='color');p.add_argument('--mips',choices=('none','full'),default='none')
    p.add_argument('--game',type=Path);p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('texture-apply',help='rebuild texture pixels/identity from source evidence and stage a new library')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('material-parameters',help='inspect named parameters using actual effect-declared types and material bytes')
    p.add_argument('path',type=Path);p.add_argument('--locator',required=True);p.add_argument('--game',type=Path)
    p.add_argument('--dependency-library',type=Path,action='append',default=[]);p.add_argument('--query',default='')
    p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('material-plan',help='edit named material constants and staged RGBA8 texture declarations with native file/effect proof')
    p.add_argument('path',type=Path);p.add_argument('--library',type=Path,required=True);p.add_argument('--locator',required=True)
    p.add_argument('--values-file',type=Path,required=True);p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=120);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('material-apply',help='replay material declarations/changes/native evidence and stage an explicit draft')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('native-edit-plan',help='edit loaded resource scalars through native setters with differential field mapping and canonical readback')
    p.add_argument('path',type=Path);p.add_argument('--id',required=True);p.add_argument('--pack');p.add_argument('--source-root',type=Path)
    p.add_argument('--embedded',help='reviewed existing inline member, currently TBaseUnitR Stat Modifiers only')
    selection=p.add_mutually_exclusive_group(required=True)
    selection.add_argument('--values-file',type=Path);selection.add_argument('--property')
    p.add_argument('--name');p.add_argument('--category');p.add_argument('--value',help='JSON scalar');p.add_argument('--probe-value',help='optional distinct JSON scalar for no-op mapping')
    p.add_argument('--schema',type=Path,default=Path('.aow4mod/schema.json'));p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=45)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('native-edit-apply',help='recheck native setter/field witnesses and stage only the reviewed resource edit')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('income-plan',help='edit an existing colony flat-income row using official value-object IO and an independent native reader')
    p.add_argument('path',type=Path);p.add_argument('--id',required=True);p.add_argument('--resource',required=True)
    p.add_argument('--amount',type=int,required=True);p.add_argument('--pack');p.add_argument('--source-root',type=Path)
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'));p.add_argument('--game',type=Path)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=45)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('income-apply',help='replay income row/parent/pack witnesses and stage into a new Mod directory')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('native-merge-check',help='apply explicit ACP 0x40 records to one native RPK and compare actual output snapshots')
    p.add_argument('path',type=Path);p.add_argument('--mod',type=Path,action='append',required=True)
    p.add_argument('--game',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));p.add_argument('--timeout',type=int,default=60)
    p.add_argument('--dependency-library',type=Path,action='append',default=[])
    p.add_argument('--dependency-pack',type=Path,action='append',default=[],help='explicit external RPK context; bundled reachable packs are discovered automatically')
    p=sub.add_parser('project-import',help='copy an existing Mod into a standalone authoring workspace')
    p.add_argument('path',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('project-check',help='inspect an authoring config and its declared source content')
    p.add_argument('path',type=Path)
    p=sub.add_parser('build',help='create an allowlisted release with hashes and static audit; never publishes')
    p.add_argument('path',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--game',type=Path)
    p.add_argument('--version')
    p.add_argument('--steam',action='store_true',help='include read-only Steam DLC eligibility in the build environment report')
    p.add_argument('--native',action='store_true',default=None,help='collect and gate frozen native ACP/RPK/CLB serialization evidence')
    p=sub.add_parser('override-plan',help='plan a new ACP override copied exactly from an existing RPK resource')
    p.add_argument('path',type=Path)
    p.add_argument('--id',required=True)
    p.add_argument('--source-file',type=Path)
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'))
    p.add_argument('--game',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('override-apply',help='add the planned override in a new staged Mod copy')
    p.add_argument('plan',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('project-use',help='select a staged source in this authoring project and save rollback history')
    p.add_argument('path',type=Path)
    p.add_argument('--source',required=True)
    p=sub.add_parser('project-rollback',help='restore a prior source selection from verified local history')
    p.add_argument('path',type=Path)
    p.add_argument('--history',type=Path,required=True)
    p=sub.add_parser('release-check',help='verify release payload hashes; optional read-only Workshop preflight')
    p.add_argument('manifest',type=Path)
    p.add_argument('--online',action='store_true')
    p.add_argument('--game',type=Path)
    p.add_argument('--proxy')
    for name in ('workshop-publish','workshop-verify','workshop-reconcile'):
        p=sub.add_parser(name,help={'workshop-publish':'existing-item preflight; --submit is required for publication',
            'workshop-verify':'verify server metadata and optionally downloaded payload',
            'workshop-reconcile':'resolve a prior receipt by checking desired remote state; never resubmits'}[name])
        p.add_argument('manifest',type=Path);p.add_argument('--game',type=Path);p.add_argument('--proxy')
        p.add_argument('--timeout',type=int,default=90 if name!='workshop-publish' else 240)
        if name=='workshop-publish':
            p.add_argument('--submit',action='store_true')
            p.add_argument('--allow-review-required',action='store_true')
        if name=='workshop-verify':p.add_argument('--download',action='store_true')
    p=sub.add_parser('clone-rpk-plan',help='plan a complete or selected-resource RPK clone with unique IDs and remapped links')
    p.add_argument('path',type=Path);p.add_argument('--template',type=Path,required=True)
    p.add_argument('--name',required=True);p.add_argument('--name-prefix',default='')
    p.add_argument('--resource-id',action='append',help='clone only selected resource IDs into the new pack; repeat as needed')
    p.add_argument('--with-local-dependencies',action='store_true',help='include recognized dependency closure within the template pack')
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'))
    p.add_argument('--game',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('clone-rpk-apply',help='write the planned RPK and its registration in a new staged Mod')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('schema-extract',help='extract build-bound fields/types/enums from installed ResourceED; never executes it')
    p.add_argument('--game',type=Path);p.add_argument('--profile',type=Path)
    p.add_argument('--output',type=Path,default=Path('.aow4mod/schema.json'))
    p.add_argument('--refresh',action='store_true',help='archive and replace an existing extracted schema cache')
    p=sub.add_parser('schema-search',help='search field, type or enum metadata with evidence boundaries')
    p.add_argument('query',nargs='?',default='');p.add_argument('--kind',choices=('fields','types','enums'),default='fields')
    p.add_argument('--game',type=Path);p.add_argument('--schema',type=Path,default=Path('.aow4mod/schema.json'))
    p.add_argument('--validated-only',action='store_true');p.add_argument('--details',action='store_true')
    p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('link-plan',help='retarget one recognized reference to an existing or newly cloned resource')
    p.add_argument('path',type=Path);p.add_argument('--id',required=True);p.add_argument('--property',required=True)
    p.add_argument('--target-id',required=True);p.add_argument('--target-file',type=Path);p.add_argument('--old-target-file',type=Path)
    p.add_argument('--pack');p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'))
    p.add_argument('--game',type=Path);p.add_argument('--allow-type-change',action='store_true')
    p.add_argument('--source-root',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('link-apply',help='verify the link plan and stage the project copy with the selected reference changed')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('pack-import-plan',help='plan RPK content imports while preserving all resource bytes')
    p.add_argument('path',type=Path);p.add_argument('--add',action='append',default=[]);p.add_argument('--remove',action='append',default=[])
    p.add_argument('--source-root',type=Path);p.add_argument('--game',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('pack-import-apply',help='stage a reviewed RPK import-list edit')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('overlay-import-plan',help='plan additive ACP imports for one existing target-pack record')
    p.add_argument('path',type=Path);p.add_argument('--pack',required=True)
    p.add_argument('--add',action='append',default=[]);p.add_argument('--remove',action='append',default=[])
    p.add_argument('--optional',action='store_true');p.add_argument('--source-root',type=Path)
    p.add_argument('--game',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('overlay-import-apply',help='stage an ACP Mod import edit with unchanged resource bodies')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('link-array-plan',help='append and retarget a verified reference element, adding its RPK import when needed')
    p.add_argument('path',type=Path);p.add_argument('--id',required=True);p.add_argument('--property',required=True)
    p.add_argument('--template-file',type=Path,required=True);p.add_argument('--template-id',required=True);p.add_argument('--template-property',required=True)
    p.add_argument('--element-link-path',default='[]',help='JSON array of hexadecimal keys within the copied element')
    p.add_argument('--target-id',required=True);p.add_argument('--target-file',type=Path);p.add_argument('--old-target-file',type=Path)
    p.add_argument('--pack');p.add_argument('--game',type=Path);p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'))
    p.add_argument('--source-root',type=Path);p.add_argument('--allow-type-change',action='store_true');p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('link-array-apply',help='stage a verified reference append and import update atomically')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('link-init-plan',help='fill an empty direct resource reference from the same field of a same-class template')
    p.add_argument('path',type=Path);p.add_argument('--id',required=True);p.add_argument('--property',required=True)
    p.add_argument('--template-file',type=Path,required=True);p.add_argument('--template-id',required=True)
    p.add_argument('--target-id',required=True);p.add_argument('--target-file',type=Path);p.add_argument('--old-target-file',type=Path)
    p.add_argument('--pack');p.add_argument('--game',type=Path);p.add_argument('--source-root',type=Path)
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'));p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('link-init-apply',help='recheck same-field initialization evidence and stage the reference/import change')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('tree',help='inspect children of one nested resource property without dumping the entire object')
    p.add_argument('path',type=Path);p.add_argument('--id',required=True);p.add_argument('--property',required=True)
    p.add_argument('--pack');p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    p=sub.add_parser('array-plan',help='append, insert, replace or remove a serialized array element; apply with edit-apply')
    p.add_argument('path',type=Path);p.add_argument('--id',required=True);p.add_argument('--property',required=True)
    p.add_argument('--action',choices=('append','insert','replace','remove'),required=True);p.add_argument('--at',type=int)
    p.add_argument('--codec',choices=('u8','u16','u32','u64','i8','i16','i32','i64','bool','f32','f64','vec2f','vec3f','vec4f','string','hex'))
    p.add_argument('--value',default='null');p.add_argument('--copy-from-file',type=Path)
    p.add_argument('--copy-from-id');p.add_argument('--copy-from-property');p.add_argument('--pack')
    p.add_argument('--allow-opaque',action='store_true');p.add_argument('--game',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('resource-plan',help='rename an internal resource, remove a local RPK definition or revert an ACP override')
    p.add_argument('path',type=Path);p.add_argument('--id',required=True)
    p.add_argument('--action',choices=('rename','remove','revert'),required=True);p.add_argument('--name')
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'))
    p.add_argument('--allow-dangling',action='store_true');p.add_argument('--game',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('resource-apply',help='apply a hash-bound resource operation to a new staged Mod')
    p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('sync-plan',help='prepare a local release synchronization with a full backup; no writes to the destination')
    p.add_argument('manifest',type=Path);p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--backup',type=Path,required=True);p.add_argument('--game',type=Path)
    p.add_argument('--extra-runtime',choices=('block','keep','prune'),default='block')
    p.add_argument('--allow-review-required',action='store_true');p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('sync-apply',help='apply the local sync plan, preserving a verified full backup')
    p.add_argument('plan',type=Path)
    for command in ('sync-status','sync-rollback'):
        p=sub.add_parser(command,help='inspect observed sync state' if command=='sync-status' else 'restore the original local Mod if its synced state is unchanged')
        p.add_argument('receipt',type=Path)
    p=sub.add_parser('workshop-create-plan',help='prepare first-publication metadata; no Steam mutation')
    p.add_argument('manifest',type=Path);p.add_argument('--owner',required=True);p.add_argument('--preview',type=Path,required=True)
    p.add_argument('--title');p.add_argument('--tag',action='append')
    p.add_argument('--visibility',choices=tuple(creation.VISIBILITY),default='private')
    p.add_argument('--allow-review-required',action='store_true');p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('workshop-create',help='first-publication preflight; --submit creates/uploads, --resume reuses a known ID')
    p.add_argument('plan',type=Path);p.add_argument('--game',type=Path);p.add_argument('--timeout',type=int,default=240)
    p.add_argument('--submit',action='store_true');p.add_argument('--resume',action='store_true')
    p.add_argument('--agreement-accepted',action='store_true',help='only after the user has personally accepted the required Workshop agreement')
    p=sub.add_parser('workshop-create-recover',help='bind an explicitly selected newly created owned item after an unknown outcome; never submits')
    p.add_argument('plan',type=Path);p.add_argument('--item-id',required=True);p.add_argument('--game',type=Path)
    p.add_argument('--timeout',type=int,default=30)
    p=sub.add_parser('workshop-create-verify',help='authenticated metadata/download verification; supports private owned items')
    p.add_argument('plan',type=Path);p.add_argument('--download',action='store_true');p.add_argument('--game',type=Path)
    p.add_argument('--timeout',type=int,default=90)
    p=sub.add_parser('workshop-owned',help='list current author items for inspection or explicit creation recovery')
    p.add_argument('--game',type=Path);p.add_argument('--page',type=int,default=1)
    p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0);p.add_argument('--timeout',type=int,default=30)
    for command in ('dlc','dependencies'):
        p=sub.add_parser(command,help='inspect content eligibility' if command=='dlc' else 'inspect a selected Mod set and its resource/asset dependencies')
        p.add_argument('--game',type=Path);p.add_argument('--playset',type=Path)
        p.add_argument('--disabled-dlc',action='append',default=[])
        p.add_argument('--steam',action='store_true',help='read owned-and-installed DLC eligibility from the current Steam client')
        p.add_argument('--output',type=Path)
        if command=='dependencies':
            p.add_argument('--mod',action='append',type=Path,default=[])
            p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'))
            p.add_argument('--depth',type=int,default=2);p.add_argument('--max-resources',type=int,default=2000)
            p.add_argument('--section',choices=('issues','attribution','resources','resource_references','pack_dependencies','asset_references','import_cycles'),default='issues')
            p.add_argument('--limit',type=int,default=20);p.add_argument('--offset',type=int,default=0)
    # These commands retain their established argument parsers.
    for name in ("audit", "log"):
        sub.add_parser(name, help=f"{name}: run '{name} --help' for arguments")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            from . import availability
            result=inventory(args.game)
            result['readiness']=availability.report(args.game)
            emit(result)
        elif args.command=='game-version':
            game=find_game(args.game)
            if args.steamcmd:
                if not args.evidence:raise core.FormatError('--steamcmd requires a new --evidence directory')
                result=steam_versions.query(game,args.steamcmd,args.evidence,args.branch,args.timeout)
            else:
                if args.evidence:raise core.FormatError('--evidence requires --steamcmd')
                if args.capture:result=steam_versions.from_capture(game,args.capture,args.branch)
                else:
                    if args.branch:raise core.FormatError('--branch requires a capture or online query')
                    result={'format':'aow4mod-steam-version/1','app_id':steam_versions.APP_ID,
                            'installed':steam_versions.local(game),'status':'installed_version_only',
                            'online_query_verified':False,'server_latest_verified':False}
            backend=native.status(game)
            result['native_backend']={k:backend[k] for k in ('profile_supported','profile','dll_sha256','profile_scope','available_operations')}
            if args.output:
                protected=[game.parent.parent.parent,steam_versions.user_data()/'Mods']
                if args.capture:protected.append(args.capture)
                if args.steamcmd:protected.append(args.steamcmd.parent)
                core.write_json(args.output,result,protected)
            emit(result)
            return 0 if result['server_latest_verified'] else 1
        elif args.command=='capabilities':emit(capabilities.report(args.area,args.game))
        elif args.command=='inspect':
            catalog.check_page(args.limit,args.offset);result=core.inspect_file(args.path)
            key='resources' if 'resources' in result else 'changes'
            if args.output:
                source=args.path.resolve();protected=[source if source.is_dir() else source.parent]
                try:protected.append(find_game())
                except core.FormatError:pass
                core.write_json(args.output,result,protected)
            values=result.pop(key);paged=catalog.page(values[args.offset:args.offset+args.limit],len(values),args.limit,args.offset)
            paged[key]=paged.pop('items');emit({**result,**paged})
        elif args.command=='tree':
            emit(structured.inspect(args.path,args.id,args.property,args.pack,args.limit,args.offset))
        elif args.command=='array-plan':
            game=find_game(args.game);template=None
            if args.copy_from_property:
                template={'file':args.copy_from_file or args.path,'id':args.copy_from_id or args.id,'property':args.copy_from_property}
            elif args.copy_from_file or args.copy_from_id:raise core.FormatError('A template requires --copy-from-property')
            plan=structured.make_array_plan(args.path,args.id,args.property,args.action,args.at,args.codec,json.loads(args.value),
                                           template,args.pack,game,args.allow_opaque)
            protected=[Path(plan['source']).parent,game]+[Path(p) for p in plan.get('required_inputs',{})]
            core.write_json(args.output,plan,protected)
            detail=plan['operations'][0]['array_edit']
            emit({'plan':str(args.output.resolve()),'action':detail['action'],'before_count':detail['before_count'],
                  'after_count':detail['after_count'],'validation_level':plan['validation_level']})
        elif args.command=='resource-plan':
            game=find_game(args.game)
            plan=resources.make_plan(args.path,args.id,args.action,args.name,args.index,args.allow_dangling,game)
            core.write_json(args.output,plan,[Path(plan['source_root']),game])
            emit({'plan':str(args.output.resolve()),'id':plan['id'],'action':plan['action'],'proof':plan['proof'],
                  'incoming_project_references':len(plan['incoming_project_references']),
                  'external_references':len(plan['external_references']['references']),'review_required':plan['review_required']})
        elif args.command=='resource-apply':
            result=resources.apply_plan(json.loads(args.plan.read_text(encoding='utf8')),args.output)
            emit({k:v for k,v in result.items() if k not in ('source_files','plan')})
        elif args.command=='sync-plan':
            game=find_game(args.game)
            plan=synchronization.make_plan(args.manifest,args.destination,args.backup,game,args.extra_runtime,args.allow_review_required)
            core.write_json(args.output,plan,[game,args.destination,args.backup,args.manifest.parent])
            emit({'plan':str(args.output.resolve()),'destination':plan['destination'],'backup':plan['backup'],
                  'files_written':plan['files_written'],'files_removed':plan['files_removed'],
                  'preserved_source_files':len(plan['preserved_files']),'release_review_required':plan['release_review_required']})
        elif args.command=='sync-apply':emit(synchronization.apply_plan(json.loads(args.plan.read_text(encoding='utf8'))))
        elif args.command=='sync-status':emit(synchronization.status(args.receipt))
        elif args.command=='sync-rollback':emit(synchronization.rollback(args.receipt))
        elif args.command=='workshop-create-plan':
            plan=creation.make_plan(args.manifest,args.owner,args.preview,args.title,args.tag,args.visibility,args.allow_review_required)
            core.write_json(args.output,plan,[args.manifest.parent,args.preview])
            emit(plan)
        elif args.command=='workshop-create':
            if args.timeout<1:raise core.FormatError('timeout must be positive')
            emit(creation.execute(args.plan,args.submit,args.resume,args.agreement_accepted,args.game,args.timeout))
        elif args.command=='workshop-create-recover':
            if args.timeout<1:raise core.FormatError('timeout must be positive')
            emit(creation.recover(args.plan,args.item_id,args.game,args.timeout))
        elif args.command=='workshop-create-verify':
            if args.timeout<1:raise core.FormatError('timeout must be positive')
            result=creation.verify(args.plan,args.download,args.game,args.timeout);emit(result)
            return int(not result['desired_state_verified'])
        elif args.command=='workshop-owned':
            from .steam_transport import Steam
            catalog.check_page(args.limit,args.offset)
            if args.timeout<1:raise core.FormatError('timeout must be positive')
            client=Steam(args.game)
            try:result=client.owned_items(args.page,args.timeout)
            finally:client.close()
            items=[{k:v for k,v in row.items() if k!='description'} for row in result['items']]
            emit({'owner':result['owner'],'server_page':args.page,'total_owned':result['total'],'next_server_page':result['next_page'],
                  **catalog.page(items[args.offset:args.offset+args.limit],len(items),args.limit,args.offset)})
        elif args.command in ('dlc','dependencies'):
            game=find_game(args.game);steam=None;playset=None
            mods=getattr(args,'mod',[])
            if args.playset or (args.command=='dependencies' and not mods):
                playset=projects.load_playset(args.playset)
                selected=[Path(m['path']) for m in playset['mods']]
                mods=selected+[p for p in mods if p.resolve() not in {x.resolve() for x in selected}]
            disabled=args.disabled_dlc+(playset['disabled_dlc'] if playset else [])
            try:
                if args.steam:
                    from .steam_transport import Steam
                    steam=Steam(game)
                if args.command=='dlc':result=dlc.inventory(game,disabled,steam)
                else:
                    catalog.check_page(args.limit,args.offset)
                    result=environment.analyze(mods,game,args.index,disabled,steam,args.depth,args.max_resources)
                if playset:
                    if core.sha(Path(playset['source']).read_bytes())!=playset['source_sha256']:
                        raise core.FormatError('Playset changed during analysis; run again against its current state')
                    result['playset_source']=playset['source'];result['playset_sha256']=playset['source_sha256']
                    result['playset_issues']=playset['issues']
                if args.output:core.write_json(args.output,result,[game,*mods])
                if args.command=='dlc':emit({k:v for k,v in result.items() if k!='metadata_inputs'})
                else:
                    items=(result['attribution']['groups'] if args.section=='attribution' else
                           result['pack_import_graph']['cycles'] if args.section=='import_cycles' else result[args.section])
                    groups=result['dlc']['referenced_groups']
                    direct=[{k:g[k] for k in ('group','state','steam_app_id','reasons') if k in g}
                            for g in groups if any(r not in ('declared_import','optional_import') for r in g['reasons'])]
                    compact_dlc={'steam_checked':result['dlc']['steam_checked'],'disabled_dlc':disabled,
                                 'resource_or_asset_groups':direct,'additional_import_only_groups':len(groups)-len(direct)}
                    emit({k:result[k] for k in ('summary','capture','coverage')}|{'dlc':compact_dlc}|
                         {'section':args.section,**catalog.page(items[args.offset:args.offset+args.limit],len(items),args.limit,args.offset)})
                return int(bool(result['issues']) if args.command=='dlc' else result['summary']['review_required'])
            finally:
                if steam:steam.close()
        elif args.command=='link-plan':
            game=find_game(args.game)
            plan=linking.make_plan(args.path,args.id,args.property,args.target_id,args.index,args.target_file,
                args.old_target_file,args.pack,game,args.allow_type_change,args.source_root)
            core.write_json(args.output,plan,[Path(plan['source_root']),game,Path(plan['target']['file'])])
            emit({'plan':str(args.output.resolve()),**{k:v for k,v in plan.items() if k!='edit'}})
        elif args.command=='pack-import-plan':
            game=find_game(args.game)
            plan=pack_imports.make_plan(args.path,args.add,args.remove,args.source_root,game)
            core.write_json(args.output,plan,[game,Path(plan['source_root'])])
            emit({'plan':str(args.output.resolve()),'before':plan['before'],'after':plan['after'],'resources_preserved':plan['resource_count_preserved']})
        elif args.command=='pack-import-apply':
            result=pack_imports.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output)
            emit({k:v for k,v in result.items() if k!='source_files'})
        elif args.command=='overlay-import-plan':
            game=find_game(args.game)
            plan=overlay_imports.make_plan(args.path,args.pack,args.add,args.remove,args.optional,args.source_root,game)
            core.write_json(args.output,plan,[game,Path(plan['source_root'])])
            emit({key:plan[key] for key in ('pack','optional','before','after','coverage')}|{'plan':str(args.output.resolve())})
        elif args.command=='overlay-import-apply':
            emit(overlay_imports.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='link-array-plan':
            game=find_game(args.game)
            plan=reference_arrays.make_plan(args.path,args.id,args.property,args.template_file,args.template_id,args.template_property,
                args.target_id,args.index,args.target_file,args.old_target_file,json.loads(args.element_link_path),args.pack,game,args.source_root,args.allow_type_change)
            core.write_json(args.output,plan,[game,Path(plan['source_root']),args.template_file])
            emit({'plan':str(args.output.resolve()),'target':plan['target'],'imports':plan['imports'],'review_required':plan['review_required']})
        elif args.command=='link-array-apply':
            result=reference_arrays.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output)
            emit({k:v for k,v in result.items() if k not in ('source_files','files')})
        elif args.command=='link-apply':
            result=linking.apply_plan(json.loads(args.plan.read_text(encoding='utf8')),args.output)
            emit({k:v for k,v in result.items() if k not in ('source_files','files')})
        elif args.command=='native-status':
            emit(native.status(find_game(args.game)))
        elif args.command=='native-types':
            emit(native.types(find_game(args.game), args.query, args.cache, args.refresh,
                              args.timeout, args.limit, args.offset))
        elif args.command=='native-properties':
            emit(native.properties(find_game(args.game), args.type, args.query, args.cache, args.refresh,
                                   args.timeout, args.limit, args.offset, args.schema))
        elif args.command=='native-check':
            game = find_game(args.game)
            result = native_validation.check(args.path, game, args.cache, args.timeout,languages=args.language,packages=args.dependency_pack)
            if args.output:
                core.write_json(args.output, result, [game, args.path.parent,
                    *[path.parent for path in [*args.language,*args.dependency_pack]]])
            compact = {k: v for k, v in result.items() if k not in ('native_result', 'diagnostics')}
            compact['checks'] = {k: v for k, v in result['checks'].items() if k != 'localized_resource_ids'}
            compact['checks']['localized_resource_count'] = len(result['checks']['localized_resource_ids'])
            compact['diagnostic_count'] = len(result.get('diagnostics', []))
            compact['diagnostic_examples'] = result.get('diagnostics', [])[:5]
            emit(compact)
            return int(result['status'] == 'not_evaluated' or bool(result.get('diagnostics')))
        elif args.command=='native-roundtrip':
            result = native_roundtrip.run(args.path, find_game(args.game), args.output, args.cache, args.timeout,libraries=args.dependency_library,languages=args.language,packages=args.dependency_pack)
            emit({k: v for k, v in result.items() if k != 'diagnostics'} | {'diagnostic_count': len(result['diagnostics'])})
            return int(result['status'] == 'review_required' or bool(result['diagnostics']))
        elif args.command=='native-construct':
            emit(native_objects.construct_object(find_game(args.game), args.type, args.output,
                schema_path=args.schema, index=args.index, cache_dir=args.cache, timeout=args.timeout,
                values_file=args.values_file))
        elif args.command=='clb-inspect':
            emit(content_libraries.inspect(args.path,args.query,args.category,args.limit,args.offset))
        elif args.command=='clb-member':
            result=content_libraries.member(args.path,args.locator);emit(result)
            return int(result['status']!='found_top_level_member')
        elif args.command=='clb-dependencies':
            from . import clb_imports
            emit(clb_imports.inspect(args.path))
        elif args.command=='clb-import-plan':
            from . import clb_imports
            game=find_game(args.game);plan=clb_imports.make_plan(args.path,args.library,args.add,game,args.cache,args.timeout)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent,args.library])
            emit({'plan':str(args.output.resolve()),'change':plan['change'],'preservation':plan['preservation'],
                  'native_diagnostic_count':sum(len(plan[k]['diagnostics']) for k in ('export','compile','readback'))})
        elif args.command=='clb-import-apply':
            from . import clb_imports
            result=clb_imports.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output)
            emit({k:v for k,v in result.items() if k!='source_files'})
        elif args.command=='source-motion-check':
            from . import source_motion
            game=find_game(args.game);r=source_motion.inspect(args.path,args.library,args.model,game,args.time_us,args.max_position_error,args.max_linear_error,args.cache,args.timeout);core.write_json(args.output,r,[game,args.path.parent,args.library.parent]);emit({'report':str(args.output.resolve()),'assessment':{k:v for k,v in r['assessment'].items() if k!='rows'}});return int(not r['assessment']['sampled_source_motion_verified'])
        elif args.command=='source-motion-verify':
            from . import source_motion
            r=source_motion.assess(json.loads(args.path.read_text(encoding='utf-8')));emit({k:v for k,v in r.items() if k!='rows'});return int(not r['sampled_source_motion_verified'])
        elif args.command=='skeletal-source-check':
            from . import source_correspondence
            r=source_correspondence.inspect(args.path,args.library,args.mesh,args.model);core.write_json(args.output,r,[args.path.parent,args.library.parent]);emit({'report':str(args.output.resolve()),'assessment':r['assessment']})
        elif args.command=='skeletal-source-verify':
            from . import source_correspondence
            emit(source_correspondence.verify(json.loads(args.path.read_text(encoding='utf-8'))))
        elif args.command=='skeletal-import-plan':
            from . import skeletal_imports
            game=find_game(args.game);result=skeletal_imports.make_plan(args.path,args.fbx,args.namespace,args.name,game,args.dxc,args.weights,args.max_dropped_fraction,args.bake_fps,args.bake_max_error,args.cache,args.timeout,args.rotation_storage)
            core.write_json(args.output,result,[game,Path(result['source']).parent]);emit({'plan':str(args.output.resolve()),'change':result['change'],'assessment':result['reference']['assessment'],'draft':True})
        elif args.command=='skeletal-import-apply':
            from . import skeletal_imports
            r=skeletal_imports.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output);emit({'model':r['model'],'target':r['target'],'draft':True,'bind_space_verified':False,'rendering_verified':False})
        elif args.command=='model-pose-bake-plan':
            from . import pose_baking
            game=find_game(args.game);result=pose_baking.make_plan(args.path,args.library,args.model,args.time_us,args.name,game,args.dxc,args.cache,args.timeout,args.tangents,args.merge_components)
            core.write_json(args.output,result,[game,Path(result['source']).parent]);emit({'plan':str(args.output.resolve()),'model':result['change']['model'],'time_us':result['change']['time_us'],'parts':[{'mesh':r['mesh'],'vertices':r['geometry']['vertex_count'],'geometry_findings':r['geometry']['issues']} for r in result['change']['static_meshes']],'draft':True})
        elif args.command=='model-pose-bake-apply':
            from . import pose_baking
            result=pose_baking.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output);emit({'target':result['target'],'model':result['model'],'draft':True,'rendering_verified':False})
        elif args.command=='skin-weights-plan':
            from . import skin_weights
            game=find_game(args.game);plan=skin_weights.make_plan(args.path,args.library,args.mesh,json.loads(args.values_file.read_text(encoding='utf-8')),game,args.cache,args.timeout)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent]);emit({'plan':str(args.output.resolve()),'change':plan['change'],'draft':True})
        elif args.command=='skin-weights-apply':
            from . import skin_weights
            emit(skin_weights.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='shader-toolchain':
            from . import shader_reference
            if args.download:
                if not args.output or args.dxc:raise core.FormatError('DXC download requires --output and no --dxc')
                from .filesystem import outside
                from .discovery import user_data
                outside(args.output,[find_game(args.game),user_data()/'Mods']);emit(shader_reference.download(args.output,args.proxy))
            else:
                if not args.dxc or args.output:raise core.FormatError('DXC inspection requires only --dxc')
                emit(shader_reference.inspect_tool(args.dxc))
        elif args.command=='model-skinning-check':
            from . import model_skinning
            game=find_game(args.game);result=model_skinning.inspect(args.path,args.model,args.time_us,game,args.dxc,args.lod,args.component,args.vertex,args.cache,args.timeout,directions=args.directions,allow_unmapped=args.allow_unmapped)
            core.write_json(args.output,result,[game,args.path.parent]);emit({'report':str(args.output.resolve()),'assessment':result['assessment'],'native_diagnostic_count':len(result['diagnostics'])})
            return int(bool(result['assessment']['position_issue_count'] or result['assessment'].get('direction_reference_verified') is False or result['diagnostics']))
        elif args.command=='model-skinning-verify':
            from . import model_skinning
            result=model_skinning.verify(json.loads(args.path.read_text(encoding='utf-8')));emit(result);return int(bool(result['position_issue_count'] or result.get('direction_reference_verified') is False))
        elif args.command=='model-animation-plan':
            from . import model_animation_bindings
            game=find_game(args.game);plan=model_animation_bindings.make_plan(args.path,args.library,args.model,args.animation,game,args.time_us,args.allow_unmapped,args.cache,args.timeout)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent])
            emit({'plan':str(args.output.resolve()),'change':plan['change'],'assessment':plan['pose']['assessment'],'draft':True})
        elif args.command=='model-animation-apply':
            from . import model_animation_bindings
            emit(model_animation_bindings.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='native-model-poses':
            from . import model_poses
            game=find_game(args.game);result=model_poses.inspect(args.path,args.model,args.time_us,game,args.animation,args.allow_unmapped,args.cache,args.timeout)
            if args.output:
                core.write_json(args.output,result,[game,args.path.parent]);emit({'report':str(args.output.resolve()),'assessment':result['assessment'],'native_diagnostic_count':len(result['diagnostics'])})
            else:emit(result)
            return int(bool(result['assessment']['binding_issue_count'] or result['assessment']['matrix_issue_count'] or result['diagnostics']))
        elif args.command=='model-poses-verify':
            from . import model_poses
            result=model_poses.verify(json.loads(args.path.read_text(encoding='utf-8')));emit(result)
            return int(bool(result['binding_issue_count'] or result['matrix_issue_count']))
        elif args.command=='animation-tracks':
            from . import animation_poses
            emit(animation_poses.tracks(args.path,args.animation,args.query,args.limit,args.offset))
        elif args.command=='animation-poses-verify':
            from . import animation_poses
            result=animation_poses.verify(json.loads(args.path.read_text(encoding='utf-8')));emit(result)
            return int(bool(result['channel_issue_count']))
        elif args.command=='native-animation-poses':
            from . import animation_poses
            game=find_game(args.game);result=animation_poses.inspect(args.path,args.animation,args.track,args.time_us,game,args.cache,args.timeout)
            if args.output:
                core.write_json(args.output,result,[game,args.path.parent])
                emit({'report':str(args.output.resolve()),'assessment':result['assessment'],'native_diagnostic_count':len(result['diagnostics'])})
            else:emit(result)
            return int(bool(result['assessment']['channel_issue_count'] or result['diagnostics']))
        elif args.command=='animation-import-plan':
            game=find_game(args.game)
            plan=animation_imports.make_plan(args.path,args.source,args.namespace,args.title,game,args.cache,args.timeout,args.bake_fps,args.bake_max_error)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent,args.source.parent])
            emit({'plan':str(args.output.resolve()),'namespace':plan['namespace'],'assessment':{k:v for k,v in plan['assessment'].items() if k!='tracks'},
                'native_diagnostic_count':len(plan['diagnostics']),'draft':True})
        elif args.command=='animation-import-apply':
            emit(animation_imports.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='mesh-import-plan':
            game=find_game(args.game)
            plan=mesh_imports.make_plan(args.path,args.source,args.namespace,game,args.cache,args.timeout,args.vertex_profile)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent,args.source.parent])
            emit({'plan':str(args.output.resolve()),'namespace':plan['namespace'],'assessment':plan['assessment'],
                  'native_diagnostic_count':len(plan['diagnostics']),'coverage':plan['coverage']})
        elif args.command=='mesh-import-apply':
            emit(mesh_imports.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='mesh-inspect':
            result=meshes.inspect(args.path,args.locator);emit(result)
            return int(result['review_required'])
        elif args.command=='material-conditions':
            from . import material_conditions
            game=find_game(args.game);result=material_conditions.check(args.path,args.locator,game,args.dependency_library,args.cache,args.timeout,
                args.dynamic_material_defaults,args.program_candidates,args.pass_index,args.stage,args.require,args.forbid)
            core.write_json(args.output,result,[game,args.path.parent])
            emit({'summary':result['summary'],'conditional_program_candidates':result.get('program_query',{}).get('candidate_count'),
                  'renderer_selection_executed':False,'report':str(args.output.resolve())})
        elif args.command=='native-material-textures':
            from . import material_textures
            game=find_game(args.game);result=material_textures.check(args.path,args.locator,game,args.dependency_library,args.cache,args.timeout)
            core.write_json(args.output,result,[game,args.path.parent])
            emit({'status':result['status'],'comparison':result['comparison'],'diagnostic_count':len(result['diagnostics']),'report':str(args.output.resolve())})
            return int(result['status']=='review_required')
        elif args.command=='native-material-check':
            from . import material_links
            game=find_game(args.game);result=material_links.check(args.path,args.locator,game,args.dependency_library,args.cache,args.timeout)
            if args.output:core.write_json(args.output,result,[game,args.path.parent,*[p.parent for p in args.dependency_library]])
            emit({'status':result['status'],'comparison':result['comparison'],'diagnostic_count':len(result['diagnostics']),
                  'report':str(args.output.resolve()) if args.output else None,'runs':result['runs'],'coverage':result['coverage']})
            return int(result['status']=='review_required')
        elif args.command=='native-model-check':
            from . import model_links
            result=model_links.check(args.path,args.locator,find_game(args.game),args.cache,args.timeout)
            emit({k:v for k,v in result.items() if k!='diagnostics'}|{'diagnostic_count':len(result['diagnostics'])})
            return int(result['status']=='review_required')
        elif args.command=='asset-search':
            emit(asset_yaml.search(asset_yaml.load(args.path.read_bytes()),args.query,args.limit,args.offset))
        elif args.command=='model-bind-plan':
            from . import model_authoring
            game=find_game(args.game);plan=model_authoring.make_plan(args.path,args.library,args.mesh,args.name,game,args.cache,args.timeout)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent])
            emit({'plan':str(args.output.resolve()),'addition':plan['addition'],'draft':True,'coverage':plan['coverage']})
        elif args.command=='model-bind-apply':
            from . import model_authoring
            emit(model_authoring.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='component-model-plan':
            from . import component_models
            game=find_game(args.game);plan=component_models.make_plan(args.path,args.id,args.piece,args.library,args.model,game,args.pack,args.source_root,args.cache,args.timeout)
            core.write_json(args.output,plan,[game,Path(plan['source_root'])]);emit({'plan':str(args.output.resolve()),'before':plan['before'],'target':plan['target'],'imports':plan['imports'],'coverage':plan['coverage']})
        elif args.command=='component-model-apply':
            from . import component_models
            emit(component_models.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='dxil-inspect':
            from . import shaders
            emit(shaders.inspect(args.path,args.vertex_profile,args.limit,args.offset))
        elif args.command=='effect-programs':
            from . import effect_programs
            emit(effect_programs.inspect(args.path,args.locator,args.pass_index,args.stage,args.key,args.vertex_profile,args.limit,args.offset,args.require,args.forbid))
        elif args.command=='native-effect-programs':
            from . import native_effect_programs
            r=native_effect_programs.run(args.path,args.locator,find_game(args.game),args.output,args.cache,args.timeout)
            emit({k:v for k,v in r.items() if k!='diagnostics'}|{'diagnostic_count':len(r['diagnostics'])})
            return int(bool(r['diagnostics']))
        elif args.command=='effect-variants':
            from . import effect_variants
            emit(effect_variants.inspect(args.path,args.locator,args.stage,args.pass_id,args.require,args.forbid,args.limit,args.offset))
        elif args.command=='map-inspect':
            from . import maps
            result=maps.inspect(args.path)
            if args.grids:
                from . import hex_positions
                data,_=maps.read(args.path)
                if core.sha(data)!=result['sha256']:raise core.FormatError('Map changed during grid inspection')
                result['hex_grids']=[{k:v for k,v in grid.items() if k!='cells'} for grid in hex_positions.grids(data).values()]
            emit(result)
        elif args.command=='map-entities':
            from . import map_entities
            cid=int(native.parse_cid(args.entity_type),16) if args.entity_type else None
            eid=int(native.parse_cid(args.entity_id),16) if args.entity_id else None
            emit(map_entities.inspect(args.path,args.limit,args.offset,cid,eid,args.details))
        elif args.command=='map-terrain':
            from . import map_terrain
            emit(map_terrain.inspect(args.path,args.level,args.x,args.y,args.limit,args.offset))
        elif args.command=='native-map-terrain':
            from . import native_map_terrain
            r=native_map_terrain.run(args.path,find_game(args.game),args.output,args.level,[{'x':args.x,'y':args.y}],args.cache,args.timeout,args.dependency_library,args.dependency_pack)
            emit({k:v for k,v in r.items() if k!='diagnostics'}|{'diagnostic_count':len(r['diagnostics'])})
            return int(not r['pixel_data_equal'] or bool(r['diagnostics']))
        elif args.command=='terrain-weights-plan':
            from . import terrain_weights
            weights=[255 if i==args.layer else 0 for i in range(8)] if args.layer is not None else [int(v.strip()) for v in args.weights.split(',')]
            game=find_game(args.game);r=terrain_weights.make_plan(args.path,args.map_file,args.level,{'x':args.x,'y':args.y,'width':args.width,'height':args.height},weights,game,args.cache,args.timeout)
            core.write_json(args.output,r,[game,Path(r['source']).parent]);emit({'plan':str(args.output.resolve()),'level':r['level'],'rectangle':r['rectangle'],'weights':r['weights'],'changed_pixels':r['changed_pixels'],'coverage':r['coverage']})
        elif args.command=='terrain-weights-apply':
            from . import terrain_weights
            emit(terrain_weights.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='map-world-entities':
            from . import map_world_entities
            cid=int(native.parse_cid(args.entity_type),16) if args.entity_type else None;eid=int(native.parse_cid(args.entity_id),16) if args.entity_id else None
            result=map_world_entities.inspect(args.path,args.limit,args.offset,cid,eid,args.origin);emit(result)
            return int(bool(result['duplicate_ids'] or result['cell_position_mismatches']))
        elif args.command=='native-map-world-entities':
            from . import native_world_entities
            ids=[int(native.parse_cid(v),16) for v in args.entity_ids] if args.entity_ids is not None else None
            result=native_world_entities.run(args.path,find_game(args.game),args.output,ids,args.cache,args.timeout,args.dependency_library,args.dependency_pack)
            emit({k:v for k,v in result.items() if k!='diagnostics'}|{'diagnostic_count':len(result['diagnostics'])})
            return int(result['status']!='data_preserved')
        elif args.command=='native-map-entities':
            from . import native_map_entities
            ids=[int(native.parse_cid(v),16) for v in args.entity_ids] if args.entity_ids is not None else None
            result=native_map_entities.run(args.path,find_game(args.game),args.output,ids,args.cache,args.timeout,args.dependency_library,args.dependency_pack)
            emit({k:v for k,v in result.items() if k!='diagnostics'}|{'diagnostic_count':len(result['diagnostics'])})
            return int(result['status']=='review_required')
        elif args.command=='native-map-roundtrip':
            from . import native_maps
            result=native_maps.run(args.path,find_game(args.game),args.output,args.cache,args.timeout,args.dependency_library,args.dependency_pack)
            emit({k:v for k,v in result.items() if k!='diagnostics'}|{'diagnostic_count':len(result['diagnostics'])})
            return int(result['status']=='review_required')
        elif args.command=='map-metadata-plan':
            from . import map_metadata
            game=find_game(args.game);plan=map_metadata.make_plan(args.path,args.map_file,args.field,args.text,game,args.cache,args.timeout)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent]);emit({'plan':str(args.output.resolve()),'field':plan['field'],'before':plan['before'],'text':plan['text'],'coverage':plan['coverage']})
        elif args.command=='map-metadata-apply':
            from . import map_metadata
            emit(map_metadata.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='map-direction-plan':
            from . import map_entity_edits
            game=find_game(args.game);plan=map_entity_edits.make_plan(args.path,args.map_file,int(native.parse_cid(args.entity_id),16),args.direction,game,args.cache,args.timeout)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent]);emit({'plan':str(args.output.resolve()),'before':plan['before'],'direction':plan['direction'],'coverage':plan['coverage']})
        elif args.command=='map-direction-apply':
            from . import map_entity_edits
            emit(map_entity_edits.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='map-position-plan':
            from . import map_positions
            game=find_game(args.game);plan=map_positions.make_plan(args.path,args.map_file,int(native.parse_cid(args.entity_id),16),{'x':args.x,'y':args.y,'level':args.level},game,args.cache,args.timeout)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent]);emit({'plan':str(args.output.resolve()),'before':plan['before'],'position':plan['position'],'grid':plan['grid'],'overlap_ids':plan['overlap_ids'],'coverage':plan['coverage']})
        elif args.command=='map-position-apply':
            from . import map_positions
            emit(map_positions.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='map-entity-copy-plan':
            from . import map_entity_copies
            game=find_game(args.game);plan=map_entity_copies.make_plan(args.path,args.map_file,int(native.parse_cid(args.entity_id),16),int(native.parse_cid(args.new_id),16) if args.new_id else None,
                {'x':args.x,'y':args.y,'level':args.level},game,args.direction,args.cache,args.timeout)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent]);emit({'plan':str(args.output.resolve()),'template':plan['template'],'created':plan['created'],
                'grid':plan['grid'],'overlap_ids':plan['overlap_ids'],'coverage':plan['coverage']})
        elif args.command=='map-entity-copy-apply':
            from . import map_entity_copies
            emit(map_entity_copies.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='map-cell-move-plan':
            from . import map_cell_moves
            game=find_game(args.game);plan=map_cell_moves.make_plan(args.path,args.map_file,int(native.parse_cid(args.entity_id),16),
                {'x':args.x,'y':args.y,'level':args.level},game,args.cache,args.timeout)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent]);emit({'plan':str(args.output.resolve()),'before':plan['before'],'position':plan['position'],'coverage':plan['coverage']})
        elif args.command=='map-cell-move-apply':
            from . import map_cell_moves
            emit(map_cell_moves.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='asset-extract':
            from . import asset_templates
            result=asset_templates.extract(args.path,args.locator,find_game(args.game),args.output,args.cache,args.timeout)
            emit({k:v for k,v in result.items() if k!='diagnostics'}|{'diagnostic_count':len(result['diagnostics'])})
            return int(bool(result['diagnostics']))
        elif args.command in ('asset-export','asset-compile'):
            game=find_game(args.game)
            function=native_assets.export_library if args.command=='asset-export' else native_assets.compile_library
            result=function(args.path,game,args.output,args.cache,args.timeout,args.max_mib*1024*1024)
            emit({k:v for k,v in result.items() if k!='diagnostics'}|{'diagnostic_count':len(result['diagnostics'])})
            return int(result.get('status')=='review_required' or bool(result['diagnostics']))
        elif args.command=='asset-edit-plan':
            game=find_game(args.game)
            plan=asset_editing.make_plan(args.path,args.library,json.loads(args.yaml_path),json.loads(args.value),
                args.relative,game,args.cache,args.timeout,args.float32)
            core.write_json(args.output,plan,[game,Path(plan['mod_source']).parent,args.library])
            emit({'plan':str(args.output.resolve()),'edit':plan['edit'],'relative_path':plan['relative_path'],
                  'normalization_review_required':plan['normalization_review_required'],'compiled_sha256':plan['compiled_sha256']})
        elif args.command=='asset-edit-apply':
            result=asset_editing.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output)
            emit({k:v for k,v in result.items() if k!='source_files'})
        elif args.command=='native-inspect':
            game=find_game(args.game)
            catalog.check_page(args.limit,args.offset)
            object_path=json.loads(args.object_path_file.read_text(encoding='utf-8-sig')) if args.object_path_file else ([{'name':name} for name in args.object] if args.object else None)
            result=native_inspection.inspect(args.path,game,args.id,args.pack,args.schema,args.cache,args.timeout,object_path)
            if args.output:core.write_json(args.output,result,[game,Path(result['source']['file']).parent])
            rows=[row for row in result['properties'] if args.query.casefold() in (row['name']+' '+row['hint']).casefold()]
            emit({k:v for k,v in result.items() if k!='properties'}|
                 catalog.page(rows[args.offset:args.offset+args.limit],len(rows),args.limit,args.offset))
            return int(not result['comparison']['data_equal'])
        elif args.command=='resource-new-plan':
            game=find_game(args.game)
            values_binding=None;values=None
            if args.values_file:
                raw=args.values_file.read_bytes();values=json.loads(raw.decode('utf-8-sig'))
                values_binding={'file':str(args.values_file.resolve()),'sha256':core.sha(raw)}
            plan=native_authoring.make_resource_plan(args.path,game,args.type,args.name,args.pack,values,
                args.category,args.dependency,args.schema,args.index,args.cache,args.timeout,args.into_pack,args.container,
                **({'initialize_empty_script':True} if args.initialize_empty_script else {}))
            if values_binding and core.sha(args.values_file.read_bytes())!=values_binding['sha256']:
                raise core.FormatError('Native initialization values changed during planning')
            plan['values_source']=values_binding
            core.write_json(args.output,plan,[game,Path(plan['mod_source']).parent])
            emit({'plan':str(args.output.resolve()),'identity':plan['identity'],'type':plan['type'],
                  'pack':plan['new_pack_name'],'pack_mode':plan['pack_mode'],'placement':plan['append']['proof'] if plan['append'] else None,
                  'native_roundtrip':plan['native_roundtrip']['status'],
                  'diagnostic_count':len(plan['native_roundtrip']['diagnostics']),'readiness':plan['readiness']})
        elif args.command=='resource-new-apply':
            result=native_authoring.apply_resource_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output)
            emit({**{k:v for k,v in result.items() if k not in ('source_files','native_diagnostics')},
                  'native_diagnostic_count':len(result['native_diagnostics']),
                  'native_diagnostic_examples':result['native_diagnostics'][:3]})
        elif args.command=='pack-layout':emit(resource_groups.inspect(args.path,args.limit,args.offset))
        elif args.command=='resource-copy-plan':
            game=find_game(args.game)
            plan=resource_templates.make_plan(args.path,args.template,args.resource_id,args.into_pack,game,args.index,
                args.category,args.container,args.name_prefix,args.name,args.with_local_dependencies,args.cache,args.timeout)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent,args.template])
            emit({'plan':str(args.output.resolve()),'copied_resources':len(plan['id_map']),'target':plan['target']['relative'],
                'id_map_preview':dict(list(plan['id_map'].items())[:5]),'native_roundtrip':plan['native_roundtrip']['status'],
                'native_diagnostic_count':len(plan['native_roundtrip']['diagnostics']),'coverage':plan['coverage']})
        elif args.command=='resource-copy-apply':
            result=resource_templates.apply_plan(json.loads(args.plan.read_text(encoding='utf8')),args.output)
            emit({**{key:value for key,value in result.items() if key!='id_map'},
                  'id_map_file':str(args.output.resolve()/'.aow4mod-resource-copy.json'),'id_map_preview':dict(list(result['id_map'].items())[:5])})
        elif args.command=='resource-copy-map':
            catalog.check_page(args.limit,args.offset);record=json.loads(args.path.read_text(encoding='utf8'))
            if record.get('format') not in (resource_templates.FORMAT,'aow4mod-resource-template-receipt/1'):
                raise core.FormatError('Expected a resource-copy plan or receipt')
            rows=[{'source_id':key,**value} for key,value in record['id_map'].items()]
            emit(catalog.page(rows[args.offset:args.offset+args.limit],len(rows),args.limit,args.offset))
        elif args.command=='localized-link-plan':
            raw=args.values_file.read_bytes();values=json.loads(raw.decode('utf-8-sig'))
            game=find_game(args.game)
            plan=localized_links.make_plan(args.path,values,game,args.source_root,args.cache,args.timeout)
            if args.values_file.read_bytes()!=raw:raise core.FormatError('Localized assignments changed while planning')
            core.write_json(args.output,plan,[game,Path(plan['source_root'])])
            emit({'plan':str(args.output.resolve()),'assignments':plan['proof'],
                  'native_diagnostic_count':len(plan['native_pack']['diagnostics']) if plan['native_pack'] else 0})
        elif args.command=='localized-link-apply':
            emit(localized_links.apply_plan(json.loads(args.plan.read_text(encoding='utf8')),args.output))
        elif args.command=='equipment-sets':
            catalog.check_page(args.limit,args.offset);game=find_game(args.game)
            result=equipment_sets.check(args.name,args.mod,game,args.index,args.required_item)
            if args.output:core.write_json(args.output,result,[game,*[core.mod_file(p).parent for p in args.mod]])
            ids=result.pop('expected_union_ids');result.pop('inputs')
            selected=result.pop('selected_sets');shadowed=result.pop('shadowed_sets')
            result.update(selected_named_set_count=len(selected),selected_root_sets=selected.get(args.name,[]),
                          shadowed_set_count=sum(len(rows) for rows in shadowed.values()),issue_count=len(result['issues']))
            result['issues']=result['issues'][:10]
            emit({**result,'expected_union_count':len(ids),**catalog.page(ids[args.offset:args.offset+args.limit],len(ids),args.limit,args.offset)})
            return int(result['review_required'])
        elif args.command=='texture-toolchain':
            if args.download:
                if not args.output or args.texconv:raise core.FormatError('Toolchain download requires --output and no --texconv')
                from .filesystem import outside
                from .discovery import user_data
                outside(args.output,[find_game(args.game),user_data()/'Mods'])
                emit(texture_toolchain.download(args.output,args.proxy))
            else:
                if not args.texconv or args.output:raise core.FormatError('Toolchain inspection requires only --texconv')
                emit(texture_toolchain.inspect(args.texconv))
        elif args.command in ('texture-dds-plan','texture-compress-plan'):
            game=find_game(args.game)
            if args.command=='texture-dds-plan':plan=compressed_textures.make_plan(args.path,args.dds,args.namespace,args.role,args.texconv,game,args.cache,args.timeout)
            else:plan=compressed_textures.compress_plan(args.path,args.image,args.namespace,args.codec,args.role,args.texconv,game,args.cache,args.timeout,args.mips,args.max_error,args.max_normal_angle)
            core.write_json(args.output,plan,[game,Path(plan['mod_source']).parent])
            emit({'plan':str(args.output.resolve()),'locator':plan['locator'],'dxgi':plan['dxgi'],'levels':plan['level_count'],'quality':plan.get('compression',{}).get('quality'),
                  'native_blocks_preserved':True,'native_pixel_decode_verified':False,'gpu_verified':False})
        elif args.command=='texture-dds-apply':emit(compressed_textures.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='texture-plan':
            game=find_game(args.game);plan=textures.make_plan(args.path,args.image,args.namespace,game,args.cache,args.timeout,args.role,args.mips)
            core.write_json(args.output,plan,[game,Path(plan['source']).parent,Path(args.image)])
            emit({'plan':str(args.output.resolve()),'locator':plan['locator'],'width':plan['image']['width'],'height':plan['image']['height'],
                  'role':plan['role'],'levels':plan['level_count'],'color_space':plan['color_space'],'native_pixels_equal':True})
        elif args.command=='texture-apply':emit(textures.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='material-parameters':emit(material_parameters.inspect(args.path,args.locator,find_game(args.game),args.dependency_library,args.query,args.limit,args.offset))
        elif args.command=='material-plan':
            game=find_game(args.game);raw=args.values_file.read_bytes();values=json.loads(raw.decode('utf-8-sig'))
            plan=material_parameters.make_plan(args.path,args.library,args.locator,values,game,args.cache,args.timeout)
            if core.sha(args.values_file.read_bytes())!=core.sha(raw):raise core.FormatError('Material assignment file changed')
            core.write_json(args.output,plan,[game,Path(plan['source']).parent])
            emit({'plan':str(args.output.resolve()),'changes':plan['changes'],'review_required':True})
        elif args.command=='material-apply':emit(material_parameters.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='native-edit-plan':
            values_source=None
            if args.values_file:
                if any(value is not None for value in (args.name,args.category,args.value,args.probe_value)):
                    raise core.FormatError('--values-file cannot be combined with individual assignment flags')
                raw=args.values_file.read_bytes();values=json.loads(raw.decode('utf-8-sig'))
                values_source={'file':str(args.values_file.resolve()),'sha256':core.sha(raw)}
            else:
                if args.name is None or args.value is None:raise core.FormatError('A single native edit needs --property, --name and --value')
                job={'property':args.property,'name':args.name,'value':json.loads(args.value)}
                if args.category is not None:job['category']=args.category
                if args.probe_value is not None:job['probe_value']=json.loads(args.probe_value)
                values=[job]
            game=find_game(args.game)
            plan=native_edits.make_plan(args.path,args.id,values,game,args.pack,args.source_root,args.schema,args.cache,args.timeout,
                                       **({'embedded':args.embedded} if args.embedded else {}))
            if values_source and file_hash(args.values_file)!=values_source['sha256']:
                raise core.FormatError('Native edit assignments changed during planning')
            plan['values_source']=values_source
            core.write_json(args.output,plan,[game,Path(plan['source_root'])])
            emit({'plan':str(args.output.resolve()),'resource':plan['source']['resource_id'],'mapping_count':len(plan['proof']['mapping']),
                  'embedded':plan.get('embedded'),
                  'mapping_preview':plan['proof']['mapping'][:5],'changed_fields':plan['proof']['changed_fields'],
                  'source_normalization_fields':plan['proof']['source_normalization_fields'],
                  'review_required':plan['review_required'],'native_diagnostic_count':len(plan['native_pack']['diagnostics']) if plan['native_pack'] else 0})
        elif args.command=='native-edit-apply':emit(native_edits.apply_plan(json.loads(args.plan.read_text(encoding='utf8')),args.output))
        elif args.command=='income-plan':
            game=find_game(args.game)
            plan=native_income.make_plan(args.path,args.id,args.resource,args.amount,game,args.index,args.pack,args.source_root,args.cache,args.timeout)
            core.write_json(args.output,plan,[game,Path(plan['source_root'])])
            emit({'plan':str(args.output.resolve()),'proof':plan['proof'],'review_required':plan['review_required']})
        elif args.command=='income-apply':emit(native_income.apply_plan(json.loads(args.plan.read_text(encoding='utf8')),args.output))
        elif args.command=='native-merge-check':
            result=native_overlays.run(args.path,args.mod,find_game(args.game),args.output,args.cache,args.timeout,args.dependency_library,args.dependency_pack)
            emit({'status':result['status'],'steps':len(result['proof']['steps']),
                  'scoped_application_witness_matched':result['proof']['scoped_application_witness_matched'],
                  'diagnostic_count':len(result['diagnostics']),'review_required':result['review_required'],
                  'report':str(args.output.resolve()/'native-overlay.json'),'coverage':result['coverage']})
            return int(result['review_required'])
        elif args.command=='link-init-plan':
            game=find_game(args.game)
            plan=reference_initialization.make_plan(args.path,args.id,args.property,args.template_file,args.template_id,
                args.target_id,args.index,args.target_file,args.old_target_file,args.pack,game,args.source_root)
            core.write_json(args.output,plan,[game,Path(plan['source_root']),args.template_file])
            emit({'plan':str(args.output.resolve()),'before':plan['before'],'target':plan['target'],
                  'imports':plan['imports'],'validation_level':plan['validation_level']})
        elif args.command=='link-init-apply':
            result=reference_initialization.apply_plan(json.loads(args.plan.read_text(encoding='utf8')),args.output)
            emit({key:result[key] for key in ('output','property','before','target','imports','validation_level','coverage')})
        elif args.command=='schema-extract':
            game=find_game(args.game)
            profile=json.loads(args.profile.read_text(encoding='utf8')) if args.profile else None
            result=reflection.extract(game/'ResourceED.exe',game,profile)
            reflection.save(result,args.output,game,args.refresh)
            emit({'schema':str(args.output.resolve()),'summary':result['summary'],
                  'elapsed_seconds':result['elapsed_seconds'],'coverage':result['coverage']})
            return int(bool(result['summary']['corpus_failures']))
        elif args.command=='schema-search':
            game=find_game(args.game)
            schema=reflection.load(args.schema,core.sha((game/'ResourceED.exe').read_bytes()))
            emit(reflection.search(schema,args.query,args.kind,args.limit,args.offset,args.validated_only,args.details))
        elif args.command=='clone-rpk-plan':
            game=find_game(args.game)
            plan=cloning.make_plan(args.path,args.template,args.name,args.index,args.name_prefix,game,args.resource_id,args.with_local_dependencies)
            core.write_json(args.output,plan,[core.mod_file(args.path).parent,game,args.template])
            emit({'plan':str(args.output.resolve()),'pack':plan['new_pack_name'],'proof':plan['proof'],
                  'id_mapping_entries':len(plan['id_map']),'coverage':plan['coverage']})
        elif args.command=='clone-rpk-apply':
            result=cloning.apply_plan(json.loads(args.plan.read_text(encoding='utf8')),args.output)
            emit({k:v for k,v in result.items() if k not in ('plan','source_files','id_map')})
        elif args.command=='release-check':
            emit(publication.check(args.manifest,args.online,args.game,args.proxy))
        elif args.command=='workshop-publish':
            if args.timeout<1:raise core.FormatError('timeout must be positive')
            emit(publication.publish_existing(args.manifest,args.submit,args.game,args.proxy,args.timeout,
                                             allow_review_required=args.allow_review_required))
        elif args.command=='workshop-verify':
            if args.timeout<1:raise core.FormatError('timeout must be positive')
            result=publication.verify(args.manifest,args.download,args.game,args.proxy,args.timeout);emit(result)
            return int(not (result['desired_state_verified'] if args.download else result['remote']['description_matches']))
        elif args.command=='workshop-reconcile':
            if args.timeout<1:raise core.FormatError('timeout must be positive')
            result=publication.reconcile(args.manifest,args.game,args.proxy,args.timeout);emit(result)
            return int(not result['desired_state_verified'])
        elif args.command=='project-use':
            emit(authoring.select_source(args.path,args.source))
        elif args.command=='project-rollback':
            emit(authoring.rollback_source(args.path,args.history))
        elif args.command=='override-plan':
            game=find_game(args.game)
            plan=overrides.make_plan(args.path,args.id,args.index,args.source_file,game)
            core.write_json(args.output,plan,[core.mod_file(args.path).parent,game])
            emit(plan)
        elif args.command=='override-apply':
            result=overrides.apply_plan(json.loads(args.plan.read_text(encoding='utf8')),args.output)
            emit({k:v for k,v in result.items() if k not in ('source_files','plan')})
        elif args.command=='project-new':
            if args.template:
                emit(authoring.create_project(args.template,args.name,args.author,args.output))
            else:
                emit(native_authoring.create_project(find_game(args.game),args.name,args.author,args.output,args.cache,args.timeout))
        elif args.command=='project-import':
            emit(authoring.import_project(args.path,args.output))
        elif args.command=='project-check':
            path,config,source=authoring.load_project(args.path)
            result=projects.inspect_project(source)
            emit({'config':str(path),'source':str(source),'title':config.get('title'),'version':config.get('version'),
                  'resource_definitions':len(result['definitions']),'packs':result['packs'],
                  'external_pack_dependencies':result['external_pack_dependencies'],
                  'unregistered_rpk_files':result['unregistered_rpk_files'],'issues':result['issues']})
            return int(bool(result['issues']))
        elif args.command=='build':
            game=find_game(args.game);steam=None
            try:
                if args.steam:
                    from .steam_transport import Steam
                    steam=Steam(game)
                result=authoring.build_project(args.path,args.output,game,args.version,steam,args.native)
            finally:
                if steam:steam.close()
            excluded=result.pop('excluded_source_files')
            emit({**result,'excluded_source_count':len(excluded),'excluded_source_examples':excluded[:20]})
            return int(bool(result['release_gate']['review_required']))
        elif args.command=='loc-lint':
            catalog.check_page(args.limit,0)
            result=localization.validate(args.path,args.key_mode,args.reference)
            if args.output:core.write_json(args.output,result,[args.path,args.reference] if args.reference else [args.path])
            issues=result.pop('issues')
            emit({**result,'issues':issues[:args.limit],'issue_count':len(issues)})
            return int(bool(result['summary']['errors'] or result['summary']['warnings']))
        elif args.command=='loc-compile':
            emit(localization.compile_file(args.path,args.output,args.key_mode,args.reference,args.require_complete))
        elif args.command == "edit-plan":
            game=find_game(args.game)
            plan=editing.make_plan(args.path,args.id,args.property,json.loads(args.value),args.codec,args.pack,game,args.allow_opaque,args.schema)
            core.write_json(args.output,plan,[Path(plan['source']).parent,game])
            emit(plan)
        elif args.command == "edit-apply":
            plan=json.loads(args.plan.read_text(encoding='utf-8'))
            receipt=editing.apply_project_plans([plan],args.source_root,args.output) if args.source_root else editing.apply_plan(plan,args.output)
            if args.source_root:
                emit({'output':receipt['output'],'files':[{k:v for k,v in f.items() if k!='plan'} for f in receipt['files']],
                      'gameplay_tested':False})
            else:emit({k:v for k,v in receipt.items() if k!='plan'})
        elif args.command=='field-copy-plan':
            spec=json.loads(args.spec.read_text(encoding='utf-8-sig'))
            plan=field_copy.make_plan(args.path,spec,allow_opaque=args.allow_opaque,
                                      spec_directory=args.spec.resolve().parent,game=args.game)
            core.write_json(args.output,plan,[args.path,args.spec,*[Path(p).parent for p in plan['required_inputs']],
                                              *([args.game] if args.game else [])])
            emit({'plan':str(args.output.resolve()),**plan['summary'],'validation_level':plan['validation_level'],'note':plan['note']})
        elif args.command=='field-copy-apply':
            plan=json.loads(args.plan.read_text(encoding='utf-8-sig'))
            receipt=field_copy.apply_plan(plan,args.output)
            emit({'output':receipt['output'],**receipt['summary'],'gameplay_tested':False})
        elif args.command=='field-copy-canonicalize':
            plan=json.loads(args.plan.read_text(encoding='utf-8-sig'))
            result=field_copy.canonicalize_plan(plan,args.readback)
            protected=[args.plan,Path(plan['source_root']),*[Path(p).parent for p in plan['required_inputs']]]
            for witness in result['canonicalization']:protected.extend(Path(witness[k]).parent for k in ('report','source','saved'))
            core.write_json(args.output,result,protected)
            emit({'plan':str(args.output.resolve()),**result['summary'],'readbacks':len(result['canonicalization']),
                  'note':'Only selected field encodings adopted. Original readback diagnostics/comparison remain bound; native build and release gates are unchanged.'})
        elif args.command=='resource-diff':
            result=resource_diff.inspect(args.before,args.after,args.id,args.other_id,args.pack,args.other_pack,
                                         args.property,args.other_property,args.limit,args.offset)
            if args.output:core.write_json(args.output,result,[args.before,args.after])
            emit(result)
        elif args.command=='maintenance-start':
            identity=None
            if any((args.workshop_id,args.owner,args.expected_title)):
                if not all((args.workshop_id,args.owner,args.expected_title)):raise core.FormatError('Workshop item, owner and exact expected title must be supplied together')
                identity={'published_file_id':args.workshop_id,'expected_owner':args.owner,'expected_title':args.expected_title}
            emit(maintenance.start(args.path,args.output,find_game(args.game),args.baseline,args.mod,identity))
        elif args.command=='maintenance-assess':emit(maintenance.assess(args.workspace))
        elif args.command=='maintenance-stage':emit(maintenance.stage(args.workspace,args.safe_partial))
        elif args.command=='maintenance-build':emit(maintenance.build(args.workspace,args.version))
        elif args.command=='maintenance-status':emit(maintenance.status(args.workspace))
        elif args.command=='maintenance-prepare-release':emit(maintenance.prepare_release(args.workspace,args.destination,args.backup,args.online,args.proxy,args.allow_review_plan))
        elif args.command=='maintenance-context':
            from .discovery import user_data
            emit(maintenance.set_context(args.workspace,args.playset or user_data()/'content_load.json',args.depth,args.max_resources))
        elif args.command=='maintenance-review':emit(maintenance.record_review(args.workspace,args.note,args.preview))
        elif args.command=='rebase-plan':
            game=find_game(args.game)
            plan=migration.make_rebase_plan(snapshots.load(args.previous),args.path,game,args.index,args.allow_partial)
            core.write_json(args.output,plan,[core.mod_file(args.path).parent,game,args.previous])
            emit({'plan':str(args.output.resolve()),'status':plan['status'],'applicable':plan['applicable'],
                  'files_to_edit':len(plan['plans']),'comparison':plan['comparison']['summary'],'skipped':plan['skipped']})
            return int(plan['status']=='review_required')
        elif args.command=='rebase-apply':
            receipt=migration.apply_rebase(json.loads(args.plan.read_text(encoding='utf8')),args.output)
            emit({'output':receipt['output'],'files_edited':len(receipt['files']),
                  'changed_fields':sum(f['verified_changed_fields'] for f in receipt['files']),
                  'partial':receipt['rebase_partial'],'gameplay_tested':False})
        elif args.command == 'image-plan':
            game=find_game(args.game);plan=images.make_plan(args.path,args.image,args.library,game,args.cache,args.timeout)
            core.write_json(args.output,plan,[Path(plan['mod_source']).parent,game,*args.image])
            emit({'plan':str(args.output.resolve()),'library':plan['library'],'images':len(plan['inputs']),
                  'atlas_dimensions':plan['atlas_dimensions'],'compiled_sha256':plan['compiled_sha256'],
                  'diagnostic_count':len(plan['diagnostics']),'coverage':plan['coverage']})
            return int(bool(plan['diagnostics']))
        elif args.command == 'image-apply':emit(images.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command == 'image-sprites':
            catalog.check_page(args.limit,args.offset);result=image_links.sprites(args.path,args.locator);rows=result.pop('sprites')
            emit({**result,**catalog.page(rows[args.offset:args.offset+args.limit],len(rows),args.limit,args.offset)})
        elif args.command == 'image-link-plan':
            game=find_game(args.game);plan=image_links.make_plan(args.path,args.id,args.library,args.locator,args.sprite_id,
                game,args.pack,args.source_root,args.import_pack)
            core.write_json(args.output,plan,[Path(plan['source_root']),game])
            emit({'plan':str(args.output.resolve()),'before':plan['before'],'after':plan['after'],'sprite':plan['sprite'],
                  'import_pack':plan['import_pack'],'added_imports':plan['imports']['add'] if plan['imports'] else []})
        elif args.command == 'image-link-apply':emit(image_links.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.output))
        elif args.command=='editor-prepare':emit(editor_bridge.prepare(args.path,args.editor,find_game(args.game),args.output))
        elif args.command=='editor-start':emit(editor_bridge.start(args.session,args.show))
        elif args.command=='editor-status':
            result=editor_bridge.status(args.session,args.limit,args.offset);emit(result)
            return int(result['state'] in ('starting','unknown') or not result['original_source_unchanged'])
        elif args.command=='editor-collect':emit(editor_bridge.collect(args.session,args.output))
        elif args.command=='launcher-inspect':
            catalog.check_page(args.limit,args.offset);result=launcher.inspect(args.database,args.playset,args.launcher_asar);rows=result.pop('members')
            emit({**result,**catalog.page(rows[args.offset:args.offset+args.limit],len(rows),args.limit,args.offset)})
            return int(not result['pairing_supported'] or result['configuration_matches_database'] is False)
        elif args.command=='launcher-plan':
            game=find_game(args.game);plan=launcher.make_plan(args.database,args.enable,args.disable,args.position,args.playset,args.launcher_asar,game,
                unregister=args.unregister,registration_receipt=args.registration_receipt)
            core.write_json(args.output,plan,[Path(plan['database']),Path(plan['configuration']),game,*[Path(x['path']) for x in plan['mod_inputs']]])
            emit({'plan':str(args.output.resolve()),'playset':plan['playset_name'],'enable':plan['enable'],'disable':plan['disable'],
                  'new_local_registrations':len(plan['registrations']),'after_members':len(plan['after_members']),
                  'removed_local_registrations':len(plan['unregistrations']),
                  'after_enabled':sum(r['enabled'] for r in plan['after_members']),'scope':plan['scope']})
        elif args.command=='launcher-apply':emit(launcher.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.backup))
        elif args.command=='launcher-status':
            result=launcher.status(args.receipt);emit(result)
            return int(result['observed_state'] in ('partial','diverged'))
        elif args.command=='launcher-rollback':emit(launcher.rollback(args.receipt))
        elif args.command == 'loadout-plan':
            from .discovery import user_data
            game=find_game(args.game)
            plan=loadout.make_plan(args.file or user_data()/'content_load.json',args.enable,args.disable,args.position,game)
            core.write_json(args.output,plan,[Path(plan['file']),game,*[Path(x['path']) for x in plan['selected_mods']]])
            emit({'plan':str(args.output.resolve()),'enable':plan['enable'],'disable':plan['disable'],
                  'resulting_mod_count':len(plan['selected_mods']),
                  'order':[{'position':i,'name':x['name'],'package_id':x['package_id']} for i,x in enumerate(plan['selected_mods'])],
                  'coverage':plan['coverage']})
        elif args.command == 'loadout-apply':
            emit(loadout.apply_plan(json.loads(args.plan.read_text(encoding='utf-8')),args.backup))
        elif args.command == 'loadout-status':emit(loadout.status(args.receipt))
        elif args.command == 'loadout-rollback':emit(loadout.rollback(args.receipt))
        elif args.command == "playset":
            result=projects.load_playset(args.file)
            emit(result)
            return int(bool(result['issues']))
        elif args.command == "conflicts":
            catalog.check_page(args.limit,args.offset)
            paths=list(args.mod)
            playset=None
            if args.playset or not paths:
                playset=projects.load_playset(args.playset)
                paths.extend(Path(m['path']) for m in playset['mods'])
            result=projects.conflicts(paths,args.include_unregistered)
            if playset:
                result['playset_issues']=playset['issues']
                result['disabled_dlc']=playset['disabled_dlc']
            if args.output:
                core.write_json(args.output,result,[p.resolve() if p.is_dir() else p.resolve().parent for p in paths])
            overlaps=result.pop('overlaps')
            emit({**result,**catalog.page(overlaps[args.offset:args.offset+args.limit],len(overlaps),args.limit,args.offset)})
            return int(bool(result['issues'] or result['summary']['different_definitions'] or (playset and playset['issues'])))
        elif args.command == "index":
            result = catalog.build(args.index, find_game(args.game), args.mod, args.links, args.locale, args.catalog)
            emit(result)
            return int(bool(result["failures"]))
        elif args.command == "search":
            emit(catalog.search(args.index, args.query, args.id, args.type, args.origin, args.limit, args.offset,
                                args.field, args.locale, args.exact))
        elif args.command == 'loc-catalogs':
            catalog.check_page(args.limit, args.offset)
            rows = content_index.discover(find_game(args.game), args.mod)
            emit({**catalog.page(rows[args.offset:args.offset+args.limit], len(rows), args.limit, args.offset),
                  'coverage': 'Filesystem catalog candidates; directory labels and assignments do not prove runtime dictionary selection.'})
        elif args.command == 'loc-search':
            emit(content_queries.text_search(args.index, args.query, args.locale, args.field, args.exact, args.limit, args.offset))
        elif args.command == 'describe':
            emit(content_queries.describe(args.index, args.id, args.file, args.locale, args.section, args.limit, args.offset))
        elif args.command == 'explain':
            result=content_analysis.explain(args.index,args.id,args.file,args.locale,with_native=args.native,
                depth=args.depth,max_nodes=args.max_nodes,max_edges=args.max_edges,cache_dir=args.cache,timeout=args.timeout)
            if args.output:
                core.write_json(args.output,result,[args.index,*[Path(p).parent for p in result['inputs']]])
                emit({'output':str(args.output.resolve()),'nodes':len(result['nodes']),'edges':len(result['edges']),
                      'native_witnesses':len(result['native_witnesses']),'repeated_effects':result['repeated_effects'],
                      'starting_bonus_summary':result['starting_bonus_summary'],
                      'boundaries':result['boundaries'],'coverage':result['coverage']})
            else:emit(result)
        elif args.command == 'impact':
            result=content_analysis.impact(args.index,args.id,args.file,args.depth,args.max_nodes,args.max_edges)
            if args.output:
                core.write_json(args.output,result,[args.index,*[Path(n['file']).parent for n in result['graph']['nodes']]])
                emit({'output':str(args.output.resolve()),'direct_uses':len(result['direct_retarget_sites']),
                      'potential_users':len(result['graph']['nodes'])-1,'boundaries':result['graph']['boundaries'],
                      'complete_impact_verified':False})
            else:emit(result)
        elif args.command=='content-snapshot':
            result=content_snapshots.capture(args.report)
            original=json.loads(args.report.read_text(encoding='utf-8-sig'))
            core.write_json(args.output,result,[args.report,*[Path(p).parent for p in original['inputs']]])
            emit({'output':str(args.output.resolve()),'resources':len(result['resources']),'native_witnesses':len(result['native_evidence']),'scope':result['scope']})
        elif args.command=='content-compare':
            result=content_snapshots.compare(json.loads(args.before.read_text(encoding='utf-8-sig')),json.loads(args.after.read_text(encoding='utf-8-sig')))
            if args.output:core.write_json(args.output,result,[args.before,args.after])
            emit(result)
        elif args.command == 'trace':
            result = content_queries.trace(args.index, args.id, args.file, args.direction, args.depth, args.max_nodes, args.max_edges)
            if args.output:
                core.write_json(args.output, result, [args.index] + [Path(n['file']).parent for n in result['nodes']])
                emit({'output': str(args.output.resolve()), 'nodes': len(result['nodes']), 'edges': len(result['edges']),
                      'boundaries': result['boundaries'], 'coverage': result['coverage']})
            else:
                emit(result)
        elif args.command == "snapshot":
            game = find_game(args.game)
            result = snapshots.capture(args.path, game, args.index, args.dependency_depth, args.max_dependencies)
            source = core.mod_file(args.path)
            core.write_json(args.output,result,[source.parent,game])
            emit({"snapshot":str(args.output.resolve()),"mod":result["mod"]["name"],
                  "original_records":len(result["originals"]),"mod_records":len(result["modified"]),
                  "gameplay_sha256":result["mod_gameplay_sha256"],"dependency_capture":result["dependency_capture"],
                  "audit_summary":result["audit_summary"]})
            return int(bool(result["audit_issues"] or result["dependency_capture"]["issues"]))
        elif args.command == "compare":
            result = snapshots.compare(snapshots.load(args.old),snapshots.load(args.new))
            if args.output:
                core.write_json(args.output,result,[args.old,args.new])
            emit(result if args.details else {k:v for k,v in result.items() if k != "resources"})
            return int(bool(result["summary"]["manual_conflicts"]))
        elif args.command == "show":
            catalog.check_page(args.limit,args.offset)
            if args.file:
                direct, pack, resource = structured.source_resource(args.file,args.id,args.pack)
                if args.game:game=find_game(args.game)
                else:
                    try:game=find_game()
                    except core.FormatError:game=None
                row={'file':str(direct)}
            else:
                if args.pack:raise core.FormatError('--pack requires --file')
                resource, row, game = catalog.load_resource(args.index, args.id)
                if args.game:game=find_game(args.game)
            result=inspect_resource(resource,game,args.property,args.schema);properties=result.pop('properties')
            paged=catalog.page(properties[args.offset:args.offset+args.limit],len(properties),args.limit,args.offset)
            paged['properties']=paged.pop('items')
            emit({"file": row["file"], **result,**paged})
        elif args.command == "refs":
            if args.reverse:
                emit(catalog.reverse_references(args.index, args.id, args.limit, args.offset))
            else:
                catalog.check_page(args.limit, args.offset)
                resource, row, _ = catalog.load_resource(args.index, args.id, args.file)
                items = core.references(resource)
                emit({"file": row["file"], **catalog.page(items[args.offset:args.offset+args.limit], len(items), args.limit, args.offset),
                      "coverage": "Recognized encodings only"})
        elif args.command == "corpus":
            if not args.roots and not args.game:
                raise core.FormatError("provide corpus directories and/or --game")
            result = run_corpus(args.roots, args.game)
            if args.output:
                protected = [p.resolve() if p.is_dir() else p.resolve().parent for p in args.roots]
                if args.game:
                    protected.append(args.game.resolve())
                core.write_json(args.output, result, protected)
            emit(result if args.details else {k: v for k, v in result.items() if k != "files"})
            return 1 if any(k.endswith("failed") and v for k, v in result["summary"].items()) or result["summary"].get("property_layout_errors") else 0
        return 0
    except (core.FormatError,OSError,ValueError,sqlite3.Error,RuntimeError,KeyError,TypeError,IndexError,OverflowError,AttributeError,struct.error) as exc:
        print(json.dumps({"error":str(exc),"error_type":type(exc).__name__},ensure_ascii=False),file=sys.stderr)
        return 2
