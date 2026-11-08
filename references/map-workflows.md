# Tactical maps

```powershell
aow4mod map-inspect '<map.tam>'
aow4mod native-map-roundtrip '<map.tam>' --output '<new diagnostic directory>' --timeout 120
```

`map-inspect` is standard-library-only/read-only. The observed GMP v1 envelope is 36 bytes: signature, envelope size, version, serialized header-object length, map CID and four zero reserved words. The stored header-object length explicitly partitions the header and map body; do not parse the first offset-map object to end-of-file, because the last header field would then wrongly consume the entire map body. The header wrapper is object slot 0 with `TTacticalMapHeader` (`00474225`), and the map body has required observed `TMap`/`TTacticalMap` scopes. The envelope map class is `00470037`. Unknown versions/classes/reserved data, malformed maps or lengths outside the file are refused. Terrain/entity internals stay opaque; top-level field sizes and hashes are reported without dumping large payloads.

`native-map-roundtrip` uses the pinned DLL in two independent hidden child processes. The content loader reads actual official dependencies and may emit many original-resource diagnostic candidates. A valid open callback alone is insufficient: content +0x238 must own `TTacticalMap`; its serialized body before/after writing must agree and match the saved file's map body. The reviewed `TGCMap` GMP stream writer is RVA `39CA60`. Files, language and DLL are read-only guarded. A second process loads/saves the diagnostic map, and exact header/map field bytes are compared again. Each file/object is bounded to 256 MiB. The default 16 MiB object serialization bound is unchanged for other operations.

Output is a new diagnostic directory only. The original map is never replaced or installed. The native writer can change serialized data; those fields are listed and remain `review_required`. Byte-stable independent resaving does not excuse unexplained original changes. All native diagnostic candidates remain in the report. Playability, terrain/entity semantics, dependencies/DLC completeness, script behavior, NPC AI, rendering and gameplay are not certified. Native builds now freeze/recheck this scoped map evidence under protocol 5 (see below).

All 167 installed TAM files pass the observed envelope/scope decoder (about 2.63 GB total). Real Template_Directional native save changes its header and map fields `14`, `46`, `4C`; an independent second save is byte-identical, and the native object bodies match both saved map bodies. The unexplained original changes plus 339 repeated cross-process diagnostic candidates remain review findings. Truncated-envelope, oversized-header, wrong-map-class and missing-map-body controls are refused before loading.

## Literal metadata

```powershell
aow4mod map-metadata-plan '<staged Mod>' --map '<staged Mod>/Maps/example.tam' --field name --text 'My map' --output '<new plan.json>'
aow4mod map-metadata-apply '<plan.json>' --output '<new staged Mod>'
```

Supported fields are `name`, `subheader`, `creator`, `world_name` and `world_description`. Existing unlocalized string-value slots must appear in both header and map body. Their fallback text is changed together; localization keys and other data are preserved or refused. Text is limited to 2048 UTF-8 bytes without NUL. This command does not create a map or connect it to a game entry.

Planning runs two independent native saves on the original staged input and another two on the requested candidate. The original must already be byte-stable: unexplained first-save changes fail planning. Apply re-derives the exact requested byte edits, checks the full source inventory/backend and all six frozen artifacts for each native report, then copies into a new Mod directory. Diagnostic candidates remain in the receipt. A diagnostic canonical map is useful as a test fixture only; it does not resolve the earlier original-to-canonical transformations.

`SourceMaps/<map filename>/.aow4mod-map.json` records draft provenance and is excluded from default runtime content. Including the target TAM in a build creates `draft_map_assets` and the `draft_map_asset_requires_playability_review` release gate. Release loading and frozen-snapshot checking reject omitted provenance or gates. Protocol 5 also builds fresh TAM save evidence automatically; the metadata plan proves its scoped requested edit, while the build proves the declared binary checks. Terrain/broader entity operations, first-save normalization, entry/dependency completeness and gameplay remain unverified.

## Object-manager entries and deployment direction

```powershell
aow4mod map-entities '<map.tam>' --limit 20
aow4mod map-entities '<map.tam>' --id 40000007 --details
aow4mod native-map-entities '<map.tam>' --id 40000007 --output '<new entity witnesses>'
aow4mod map-direction-plan '<staged Mod>' --map '<staged Mod>/Maps/example.tam' --id 40000007 --direction 3 --output '<new plan.json>'
aow4mod map-direction-apply '<plan.json>' --output '<new staged Mod>'
```

`map-entities` is offline/read-only, with `--type`, `--id`, `--limit`, `--offset` and optional bounded scope hashes. It enumerates only TTacticalHexWorld's explicit object-manager `FZones` list, not cell-held flee points/entities or terrain-painted visual objects. It preserves unknown class IDs and reports stored Direction and reviewed 12/12/8 hex-position fields; omitted values stay unknown. Decoded file values are distinct from live native observations. Duplicate IDs, malformed/sparse arrays and incorrect identity widths are refused. All 167 installed maps expose 990 entries across four deployment-zone types under this layout.

`native-map-entities` loads the map in a hidden child and reads actual manager objects. The reviewed current DLL paths are map +0x140 → tactical world, world +23376 → entity manager, array/count at manager +0x48/+0x50, ID at entity +12. The internal ordinal-property getter `14CA70` is the implementation called by official `BGetPropertyOrdinal`; it reads Direction from the loaded entity. Version-2 reports additionally read the actual three signed position integers at entity +0x118 and independently repack them through official `3DE5A0`; both must agree with the serialized object. The command checks complete live/file ID and class coverage, saves selected native object bodies (1..256 selected, 16 MiB each/64 MiB total) and compares them with the source. It never reconstructs an entity outside its world. Failed getters and data differences remain findings; actual count/identity differences fail. Legacy LineDeploymentTestMap has fourteen entities in the file but zero in both the loaded manager and native map serialization. Its cause is unresolved; do not assume harmless migration or relax coverage.

`map-direction-plan/apply` currently supports only an existing `TFacingThreeDeploymentZoneE` (`00476E5D`), not other deployment classes. Direction values follow the reviewed TGCHexMapDirection enum: 0 North East, 1 East, 2 South East, 3 South West, 4 West, 5 North West. The plan changes only its TGCHexEntity Direction slot; ID, other entities, positions, map metadata and opaque world data are preserved. Source and candidate must pass independent two-process exact file/object checks. A third process must read the exact candidate entity and return the requested Direction. The candidate for value 2 omits the stored default; it is accepted only after both exact file stability and actual getter=2 are proved. Apply re-derives the edit and checks all source/backend/artifact witnesses before writing a new Mod copy. Unknown classes, no-ops and unsupported values are refused.

Direction stages use `aow4mod-map-entity-receipt/1` under SourceMaps and retain the same draft-map build/release gates as metadata stages. This does not certify deployment shape, spawn behavior, position, terrain, new entities, original map migrations or playability.

## Existing deployment-zone position

```powershell
aow4mod map-inspect '<map.tam>' --grids
aow4mod map-position-plan '<staged Mod>' --map '<staged Mod>/Maps/example.tam' --id 40000007 --x 10 --y 12 --output '<new plan.json>'
aow4mod map-position-apply '<plan.json>' --output '<new staged Mod>'
```

Current scope is an existing `TFacingThreeDeploymentZoneE` (`00476E5D`) with an explicitly stored, in-grid source position. `--level` defaults to the source level. The file's TGCHexEntity field 8 packs X in bits 0..11, Y in 12..23 and level in 24..31; each all-ones component means -1. Valid non-sentinel codec ranges are X/Y 0..4094 and level 0..254. Actual movement additionally requires a declared grid, X < columns and Y < rows. TGCHexMap field 1 is `FNumRows`, field 2 is `FNumColumns`, and field 7 is level. Do not swap rows/columns or infer bounds from a rectangular cell count alone. The reviewed serializer is DLL `401160`; entity position serialization/unpacking is `4008E0`, and native packing is `3DE5A0`.

Planning binds the full Mod, bundled RPK/CLB lookup inputs, exact grid metadata and the requested field edit. Baseline and candidate each need two independent native saves with exact data preservation. Another process must read the requested live position, reproduce its official packed value and retain Direction. Apply re-derives the edit and verifies all witnesses before writing a new Mod copy. Unrelated entities, cell/painted terrain data, metadata, IDs and other fields remain unchanged. Exact coordinate overlap with other listed entities is reported as `overlap_ids`; no unverified universal collision rule is invented. Position receipts use `aow4mod-map-position-receipt/1` under SourceMaps and keep the same draft playability gate.

Position planning requires a supported explicit grid. Opaque/nonexplicit cell layouts are refused even when other map metadata can be read. Codec validity and declared bounds do not prove legal deployment, terrain walkability, world-space pose, rendering or gameplay.

## Known world identities and cell-held flee points

```powershell
aow4mod map-world-entities '<map.tam>' --origin cell --limit 20
aow4mod native-map-world-entities '<map.tam>' --id 40000009 --output '<new world witnesses>'
aow4mod map-cell-move-plan '<staged Mod>' --map '<staged Mod>/Maps/example.tam' --id 40000009 --x 24 --y 13 --output '<new move plan.json>'
aow4mod map-cell-move-apply '<new move plan.json>' --output '<new staged Mod>'
```

The older `map-entities`/`native-map-entities` retain their FZones-only scope. `map-world-entities` additionally decodes the three explicit tactical-cell holder slots 1, 3 and 4; it reports origin/location, duplicate IDs across these containers and stored-position/cell mismatches. Nonempty unknown holder layouts, incomplete grids and more than one million total cells are refused. Unknown entity classes are retained for inspection. Other world managers and terrain-painted visual streams are outside this scope. Only the declared managers/holder layouts participate; this is not a native or gameplay test of all map contents.

`native-map-world-entities` checks actual occupancy of every known holder, all observed IDs/classes/locations and actual grid/cell coordinates. Its `identity_summary` binds the complete known projection; `selected_entity_count` describes only the object/cell bodies saved for the requested IDs (1..256). Choose `--id` for dense maps. A 2287-entity original bridge map was checked this way while freezing one flee point and its cell; an ocean-map control covers 255 identities. Native diagnostics remain findings. Source/cell/object bodies, actual directions and position packing are checked independently, and malformed/missing cell witnesses cannot be replaced by summaries. These are known-container observations, not a full registered-world entity manager or rendering/routing certification.

Current pinned DLL

Cell movement is limited to existing `TTacticalFleePointE` (`0047159E`) in main slot 4, with consistent known identities/positions and an explicitly empty destination main slot. Other destination holders may remain occupied; their bytes and entity bodies are preserved. Source holder is cleared, destination holder receives the same entity, and only its packed hex position changes. ID, resource, direction, range cursors, all other cells/entities and map metadata stay unchanged. Cross-level movement uses the requested declared grid; `--level` defaults to the source level. No-op/occupied/out-of-grid requests are refused. Native source/candidate two-process saves prove exact full-map bytes; another process checks all known identities and freezes the moved object and destination cell. Current actual test moves ID `40000009` from cell 415/(25,13,0) to cell 414/(24,13,0), keeping 12 known entities and Direction 1.

Plans bind complete source inventory, bundled RPK/CLB context, source/candidate witnesses, complete identity projection and selected object/cell evidence. Apply re-derives the coupled edit and writes a new Mod copy. `aow4mod-map-cell-move-receipt/1` under SourceMaps retains the map draft/playability gate through build and frozen publication checks. Deployment-zone copying now also rejects inconsistent known source identities/positions before native work, including preexisting cross-container collisions. Native build protocol 6 now automatically freezes the complete detailed known-world report in bounded batches, as described below; historical protocol 5 retains serialization-only scope. Legal fleeing, terrain navigation, world registration, rendering and gameplay remain unverified.

## Basic deployment-zone template copies

```powershell
aow4mod map-entity-copy-plan '<staged Mod>' --map '<staged Mod>/Maps/example.tam' --id 40000007 --x 11 --y 12 --direction 2 --output '<new copy plan.json>'
aow4mod map-entity-copy-apply '<new copy plan.json>' --output '<new staged Mod>'
```

This appends one basic `TFacingThreeDeploymentZoneE` (`00476E5D`) copied from an existing template. It preserves the template resource ID and reviewed property descriptors; empty parent/zone settings and only stored position/direction are supported. Nonempty/unknown settings, other classes and missing resource data require separate review. `--direction` defaults to the template; `--level` defaults to its grid level. Default Direction 2 is omitted only after exact independent native saves and a loaded getter=2 witness. All prior entries remain byte-identical. The source must contain 1..255 FZones entries so all resulting entries can receive complete native observations.

ID selection uses the stored TGCHexWorld (`0401107C`) field `0x15`, corresponding to `FEntityIDRangePositions[1]`, and advances that slot by one. Optional `--new-id` asserts that exact value; arbitrary replacement IDs and values outside the reviewed inclusive range `40000000..5FFFFFFF` are refused. The end wraps to `40000000`, matching the official consume function. The source must contain the selected ID's four bytes only at that counter: any other occurrence, including an opaque coincidence, requires review. Do not choose an ID merely absent from FZones: the reviewed fixture also stores `TTacticalFleePointE` IDs 9..12 in cells, and a colliding FZones clone can survive exact native save without exposing the conflict. The official serializer `4022D0` stores all three range positions at world +0x4ED0; range setup is `4682F0`, readonly peek `45D2B0`, consume/increment `455BD0`. Constructor `447D10` sets the exact range bounds. Four explicit empty-table native arithmetic controls confirm peek/increment/wrap behavior. The hidden content-load world is not registered with a full entity manager, so these controls do not certify actual registered-world allocation or future editor behavior.

Planning binds all bundled RPK/CLB lookup inputs and the entire source Mod. Source and candidate each pass two-process exact native file/body preservation; another process must enumerate every resulting FZones entry, retain all object bytes, and independently return the actual positions, official packed coordinates and directions. Apply re-derives the append and counter change, checks every witness and writes a new Mod copy. `SourceMaps/<filename>/.aow4mod-map.json` uses `aow4mod-map-entity-copy-receipt/1`; build and frozen-publication checks retain the draft map playability gate. Coordinates shared with an existing zone are reported without inventing universal spawn/collision rules.

## Terrain inspection and texture weights

```powershell
aow4mod map-terrain '<map.tam>' --level 0 --x 400 --y 400 --limit 10
aow4mod native-map-terrain '<map.tam>' --x 400 --y 400 --output '<new CPU witnesses>'
aow4mod terrain-weights-plan '<staged Mod>' --map '<staged Mod>/Maps/example.tam' --x 400 --y 400 --width 8 --height 7 --layer 3 --output '<new paint plan.json>'
aow4mod terrain-weights-apply '<new paint plan.json>' --output '<new staged Mod>'
```

Coordinates in these painting commands are **weight-texture pixels**, not hex cells or world coordinates. `map-terrain` reports the existing resolution, world-unit scales/border, one eight-channel pixel and paged stored cell heights. Layer indices 0..7 follow the reviewed native brush definition; material names depend on the map's terrain resources and are not resolved by this command. `--layer` creates a single 255-weight channel; alternatively `--weights '10,20,30,40,50,60,25,20'` supplies eight uint8 channels summing to 255. A positive in-bounds rectangle is required; no-ops, unknown layouts, malformed buffers and invalid sums are refused. Texture sizes are bounded to 4096 per axis and 64 MiB per weight buffer.

Current format: tactical grid `00471515` field 0 → paint scope `040112A3` field 9 → pixel scope `040112A9`. Pixel fields 0/1 are map columns/rows, field 2 is `040112AD` weight resolution (width/height/unit-X/unit-Z/border), and fields 11/12 are two row-major RGBA8 maps. The official serializer asserts eight terrain types. Its channel addressing is buffer `layer >> 2`, byte `layer & 3`, at `(y * width + x) * 4`. Both maps form one eight-weight pixel; the native brush declares terrain indices 0..7. Masks, soil-cover data and all other fields are preserved, without assuming their unreviewed formats.

Plans edit serialized weight storage before loading; they do not run an official brush session or initialize a renderer. Source/candidate each pass independent two-process exact map saves. Another native process serializes the complete CPU pixel object, hashes both actual native buffers and reads the requested corner/center samples. Apply re-derives every changed byte, verifies the selected grid and sample coverage, source/backend and bundled RPK/CLB context, then stages a new Mod. `aow4mod-terrain-weight-receipt/1` under SourceMaps retains the existing map draft/playability gate; protocol 6 independently builds/freeze-checks the full map and known entity evidence. No geometry/collision/navigation/visible material result is certified.

Do not use the misleading `CellTerrainHeight` label as a sculpt-height value. Base `TGCHexCell` field 2 is geometric float32 height (+0x18); field 9 is stand height (+0x4C). Tactical-cell field 6 (+0x98) selects terrain-limit settings. A native height setter requires a validated world/owner context that this workflow does not provide. Do not fabricate context pointers, infer a full manager from a constructor, or claim sculpt/brush support from a raw field label. No direct geometric-height writer or complete native brush session is provided.

## Automatic native build and frozen release checks

`build --native` uses native release protocol 7, retaining protocol 6's map/world checks. Every payload `.tam` is a declared `tam_roundtrip` target, including malformed or unsupported maps, which become explicit errors rather than silently returning to `not_native_checked`. The collector binds all bundled RPK/CLB lookup inputs, runs the existing independent two-process map save and freezes six roles: before/after object bodies and saved GMP file for each process. Identical witnesses share a frozen file. Each file is limited to 256 MiB and each map's unique frozen evidence to 512 MiB. Evidence is outside runtime upload content.

Publication preflight and frozen upload snapshots re-read the payload and every required witness, derive exact local lookup lists/hashes, compare source→first save and first→independent second save, and compare each process's object before/after with its saved map body. Original first-save differences and native diagnostics stay gated; a stable second save cannot erase them. Missing roles/context, refreshed-hash file/body edits, changed summaries and protocol mismatches are rejected. Protocols 1–6 preserve their historical scope; a protocol-4 release's TAM remains explicitly unchecked, and protocol 5 covers map serialization without detailed known-world observations. Native serialization still does not establish terrain semantics, complete dependency/ACP mounting, deployment behavior or gameplay.

For a standalone diagnostic with explicit context, use `native-map-roundtrip ... --dependency-library '<local.clb>' --dependency-pack '<local.rpk>'`; repeat either option for multiple inputs. These are guarded lookup directories/files, not proof that all resources are mounted or ACP overrides have been applied. Build collection supplies the bundled inputs automatically.

Protocol 6 also declares a `tam_world_entities` check for every TAM. It re-derives known FZones/cell-holder selection from the payload and freezes every object plus occupied cell body, using fixed batches of at most 256 entities. Each process independently verifies the whole known identity projection, then writes its selected bodies. Shared cells retain one canonical file across batches; inconsistent repeated cells fail. Unrelated first-save changes remain findings even when all selected entity bodies match.

Each batch keeps its own hash-bound raw `stdout.log`; offline validation re-extracts diagnostic candidates instead of trusting edited lists. The world index binds source, binding, exact complete selections, identity projection and bundled RPK/CLB context. Every object/cell filename, body, type, ID, holder/location and position/direction observation is rechecked against the payload. Missing/duplicated batches, aliased log roles, omitted cells/context, rewritten bodies with refreshed hashes, cleared diagnostics and changed summary flags fail. Frozen world evidence is limited to 512 MiB per map, 16 MiB per object/cell and 64 MiB per raw log; the separate map-save evidence keeps its existing bounds.

An explicitly empty known identity set still receives a native occupancy check, but object data is `not_evaluated`, `data_equal=null`, and `world_objects_unevaluated_maps`/`world_entity_issue_count` keep review required. Getter findings also enter `world_entity_issue_count` independently of data differences and logs. Inconsistent source identities or unsupported layouts are explicit check errors. These checks cover known containers and recorded native data, not other managers, registered-world allocation/routing, legal placement, rendering or gameplay.
