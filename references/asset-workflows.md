# Content-library workflows

For CLB dependency layers, blank icons and source-versus-imported animation differences, start with [asset checks](asset-checks.md). Load the command sections below only as needed.

Use the project Python environment. Static CLB inspection is standard-library-only. Native YAML/PNG tools require the optional `assets` extra (`PyYAML==6.0.3`, `Pillow==12.3.0`) and the reviewed Windows native DLL profile.

## Inspect without loading the editor

```powershell
aow4mod clb-inspect "<library.clb>" --query "Icon" --limit 10
aow4mod clb-dependencies "<library.clb>"
aow4mod clb-member "<library.clb>" --locator "[LIBRARY]TXA\0"
```

The decoder checks the CLB v8 header, section lengths/alignment, metadata maps, string-table bounds and top-level member identities. Pixel/mesh data remains opaque. The metadata fingerprint covers the header and metadata/string sections, not the opaque payload. Exact locator matches prove top-level membership; an absent top-level match does not prove that a virtual or nested subresource is missing. Do not replace this distinction with a guessed global ID rule.

## Export and compile through official native code

```powershell
aow4mod asset-export "<library.clb>" --output "<new export directory>"
aow4mod asset-search "<export/library.clb.yaml>" "FScale" --limit 10
aow4mod asset-compile "<edited native YAML>" --output "<new compiled directory>"
```

Export produces a binary CLB copy, native YAML and an independent comparison. Native saves can normalize existing data; such differences remain `review_required`. For example, the tested old texture atlas changes nested image metadata on native load/save while its opaque pixel data remains byte-identical. Do not silently replace the original with that copy.

Compile uses the native YAML reader and native CLB writer, then checks every YAML value and top-level member identity against the requested document. Unknown fields silently dropped by the engine are rejected. The compiled files are diagnostic outputs, not automatically installed into a Mod. An unmodified sky library reproduced the original binary exactly; a texture-atlas YAML reproduced the native-exported binary, including its pixel payload.

The native YAML reader has an inherited method that dereferences absent zero-length PS5/Xbox texture streams. The adapter replaces that method only on its own temporary reader instance, skips only the two verified empty cases, restores the original vtable, and rejects unsupported streams. No installed DLL or running game is patched. Nonempty console texture data remains unsupported.

Native class names with factory registrations must match their IDs. Embedded value classes can lack factory registration; their IDs/names/data must survive the complete native YAML readback. YAML aliases, anchors, explicit tags, duplicate keys/field numbers and oversized trees are rejected. Numeric scalars are retained as text because the native field serializer decides their C++ types.

The observed `CRT` text header does not provide a reliable normal-file import route: a test returned success after dropping the library member. Use the verified native YAML reader, not file-open success, as the compilation path.

## Stage a focused edit in a Mod

For a large library, first extract only explicit mesh/model/material definitions:

```powershell
aow4mod asset-extract "<library.clb>" --locator '[LIBRARY]MODEL\0' --locator '[LIBRARY]MAT\0' --output "<new template directory>"
```

This reads the original CLB through the pinned native backend, checks the exact native list class, ordinal, object type and locator, and writes a fresh subset library. A second process loads that file and compares each selected object's serialized fields plus the entire subset CLB. The tested System_Effects model/material selection produces about 20 KiB of YAML instead of loading the whole system export, which exceeds the 64 MiB YAML parser limit. The global parser limit remains unchanged.

Select 1–16 unique explicit top-level locators; the source is bounded to 256 MiB and each serialized template to 16 MiB. Current classes are mesh/mesh DEV, model and effect material. Source, language and DLL are held read-only during conversion. Selection order is preserved. Output is diagnostic and not installed. External links remain unchanged; texture/shader dependencies are not copied automatically. Model/mesh vertex compatibility, derived bounds and game entry points require separate authoring and review. A saved material or model alone does not certify rendering.

```powershell
aow4mod asset-edit-plan "<Mod>" --library "<original or Mod CLB>" --yaml-path '<JSON path from asset-search>' --value '<JSON scalar or vector>' --float32 --relative "Libraries/MyLibrary.clb" --output "<new plan.json>"
aow4mod asset-edit-apply "<plan.json>" --output "<new staged Mod>"
```

Only existing scalar fields and same-length scalar vectors are edited. Class IDs, names used for native class selection, library/member identities and structured object replacement are excluded from this operation. `--float32` records the exact float32 value using the native emitter's representation; overflow/underflow is refused. Omit it for text, bools or exact integers.

The plan freezes the source Mod inventory, original library, baseline YAML, requested delta and compiled artifacts. Apply checks those bindings and writes a separate Mod copy. The relative filename must preserve the internal library name and stay under `Libraries/` or an explicit `Title/` hierarchy. Native normalization is reported separately from the requested edit. Use `project-use` to select the stage before building.

This provides library metadata/content editing and compilation. The separate PNG workflow below creates a new UI atlas. FBX source assets, arbitrary existing-atlas rebuilds, arbitrary TAM maps and actual rendering still require additional backends or the official editor.

## Add dependencies to an existing library

```powershell
aow4mod clb-import-plan '<Mod>' --library '<Mod/Libraries/OWN.clb>' --add 'SYSTEM_EFFECTS.CLB' --output '<new import plan.json>'
aow4mod clb-import-apply '<import plan.json>' --output '<new staged Mod>'
```

CLB field 22 (`FDependencies`) is separate from an RPK/ACP CLB import. A copied material can keep a correct `[SYSTEM_EFFECTS]EFFECT\0` URI yet fail native resolution when its new library does not declare `SYSTEM_EFFECTS.CLB`. Explicitly opening the original effect library did not repair that missing declaration in the tested context. Adding it resolved the correct actual effect pointer for both existing staged materials and the new Default-material recipe.

The additive plan preserves spelling/order, reuses an existing case-insensitive match and refuses self-imports, duplicate names and paths. It binds the complete source Mod, native backend, baseline export, requested additions, compiler and a separate file export. Apply re-derives the recipe and compares actual source/candidate members, ordering, strings, opaque payload and root metadata; only field 22 may change. Known trailing alignment after the unchanged one-byte root `FIsOriginalContent` bool may change by at most 15 zero bytes and is recorded separately. Changed flags, nonzero suffixes and unrelated data are refused even with refreshed hashes.

Apply writes a new Mod copy and retains existing author sources/draft receipts. The inspector reports literal declarations. These commands do not remove dependencies or certify target existence, full closure/cycles, every runtime link or rendering. Native diagnostics stay in the plan receipts.

## Create a new PNG atlas

```powershell
aow4mod image-plan "<staged Mod>" --image "<first.png>" --image "<second.png>" --library MY_UI_ICONS --output "<image-plan.json>"
aow4mod image-apply "<image-plan.json>" --output "<new staged Mod>"
aow4mod image-sprites "<new Mod/Libraries/MY_UI_ICONS.clb>" --locator '[MY_UI_ICONS]TXA\0' --limit 10
```

Accepts 1–64 single-frame ordinary PNGs, at most 1024 pixels per side, up to 8 bits per channel. Palette transparency/RGB/RGBA are decoded to straight RGBA without implicit resizing or compositing. Embedded ICC profiles, unusual gamma, unapplied orientation, animation, high bit depth and oversized inputs are refused. Convert unsupported source color profiles beforehand. Source filenames must be unique ignoring case. The library name is normalized to uppercase ASCII letters/digits/underscores (1–30 characters, initial letter). Existing Mod/game library filenames and internal namespaces are checked without fully decoding all members.

The deterministic shelf packer preserves input sprite IDs (1-based), adds a one-pixel extruded border and chooses a bounded power-of-two canvas, up to 2048x2048. Generated native surfaces use RGBA8, eight-byte-aligned rows and one mip. Each source surface and the composite are exported through the current official `TPNGWriter`, then independently decoded and compared pixel-for-pixel. RGBA channels and transparent pixels must match, not merely produce a successful callback. The library is compiled by the official YAML/CLB writer and independently reloaded from the resulting file with exact data preservation. Tested odd widths expose both insufficient padding and overpadding; assertions/differences are rejected.

Apply binds the Mod inventory, PNG bytes, regenerated layout/YAML, native backend, compiled CLB and all PNG/binary witnesses. It preserves the ACP, writes the new CLB under `Libraries/`, and keeps author PNGs under `SourceImages/<library>/`. Existing different author data is not overwritten. Source PNGs and internal receipts are excluded from the default runtime build; the CLB contains the compiled pixels. Re-plan after backend/generator changes rather than altering hashes by hand.

The alpha bit map uses the observed nonzero-alpha layout; native pixel equality does not verify mouse hit testing or color-managed GPU rendering. Compression, mip chains, normal maps, layered materials and FBX remain outside this PNG recipe. A new atlas does not automatically replace a game icon or establish a gameplay entry point.

## Connect a supported game icon

First add the original icon override with `override-plan/apply`, or select a local RPK icon. Then:

```powershell
aow4mod image-link-plan "<Mod>" --id "<TGameIconR ID>" --library "<Mod/Libraries/MY_UI_ICONS.clb>" --locator '[MY_UI_ICONS]TXA\0' --sprite-id 1 --output "<icon-link.json>"
aow4mod image-link-apply "<icon-link.json>" --output "<new staged Mod>"
```

This currently supports the reviewed `TGameIconR` property layout only. It changes the atlas locator, atlas label and sprite ID together, preserving renderer, environment and other sibling settings. The target must already be staged inside the Mod, and its selected atlas/sprite ID must exist in native CLB metadata. For RPK sources use `--source-root <whole Mod>`; that pack must be registered/reachable from the ACP. For ACP overrides, one registered local RPK is selected as the CLB import anchor; if more than one exists, select it explicitly with `--import-pack`. An ACP-only Mod without a local RPK anchor is not handled by this link command. Reference and import edits are staged atomically and source hashes are rechecked.
