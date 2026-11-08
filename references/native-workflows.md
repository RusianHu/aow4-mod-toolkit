# Native editor introspection

Current installation (2026-11-08): Manta 1.1 Hotfix Build26482553 has a **partial resource-core profile**. Check `native-status.available_operations`; class discovery does not enable asset/map operations or every constructible class. See [current compatibility and evidence](../docs/MANTA_COMPATIBILITY.md).

Use these commands for current official class names, property descriptions, scalar constructor defaults, and explicit enum associations. They do not inspect the effective runtime values of a loaded Mod.

```powershell
aow4mod native-status
aow4mod native-types StructureSingle --limit 5
aow4mod native-properties 00471C71 --query "Friendly Fire" --schema .aow4mod/schema.json
aow4mod native-properties TGridSkill --query Tier
aow4mod native-inspect "<Mod or ACP>" --id "<resource ID>" --query "Friendly Fire" --schema .aow4mod/schema.json
aow4mod native-inspect "<Mod or ACP>"
aow4mod native-check "<staged.rpk>" --output "<new-report.json>"
aow4mod native-roundtrip "<staged.rpk>" --output "<new-diagnostic-directory>"
```

`native-status` checks the installed library/profile/platform and separates the implemented catalog from reviewed/available operations. A known partial profile does not enable unreviewed operations; both parent and worker reject them before execution. `native-types` and `native-properties` initialize the installed `DotNetBindings.dll` with `BInitialize(true)` in a separate hidden process. They do not open an editor or game session. The DLL hash must match a reviewed ABI profile; a new game/tool build is refused instead of reusing offsets blindly.

Class selection accepts an exact class name or hexadecimal ID. Queries paginate; use `--query`, `--limit`, and `--offset`. Cache results under `.aow4mod/native` are bound to the library, profile, and backend source hashes. `--refresh` produces a new run and retains earlier request/result/process receipts. `--cache` must stay outside original-game, local-Mod and Workshop directories. `--timeout` is 1–180 seconds; constructor crashes and hangs terminate the child, not the calling CLI.

Property records identify the actual editor class. Integers/enums, floats and bools use their corresponding native getters. Object/resource links are not read through integer getters. A failed getter remains `getter_failed`; it is not converted to zero, an empty string, or an omitted-field default. Some constructors require additional content context, so discovering a class does not guarantee that it can be independently constructed.

`--schema` optionally joins a native enum parameter to the current offline schema's exact enum ID. Both ResourceED and native-library hashes must match the reviewed profile. The join can report enum labels and a default label; it does not infer a serialized ACP/RPK field path from similar display names.

`native-inspect` deserializes the selected file data into a fresh native object and reports `value`/`value_status`, rather than constructor `default` values. It also reserializes the object and compares the data. With `--id`, it selects an ACP override or a registered/local RPK resource; an exact RPK path can disambiguate it. Without `--id`, it reports the ACP's native version metadata. Use `--query`, pagination, and `--output` for full evidence. A readback difference remains review-required; complex reference values are not resolved into a running game.

A native writer can omit an explicit default while a getter still returns it. Strict comparison must first report the difference. `native-edit-plan/apply` proves selected scalar mappings through differential setters, canonical serialization and a separate reader process; unrequested omissions remain differences.

`native-construct` and `resource-new-plan` use native scalar setters followed by both immediate getter checks and a second native object's binary readback. This is distinct from an old prototype function at RVA `0x14c0f0`, which is a YAML **writer**, not a reader. The verified binary reader uses `TEReadStorageStream` and the object's native serialization method. Do not infer success merely from a zero native return value.

## Validation boundary

Native constructor defaults do not prove that an omitted serialized field uses the same value in every loading path. Use the existing serializer evidence for such claims. Introspection commands do not save objects, compile CLB/TAM assets, or certify game behavior.

Content-open callbacks and `Content Registered` logs are not structural validation. Use full serialized-data witnesses and valid/truncated negative controls; a successful callback cannot authorize acceptance of lost data.

`native-check` first checks the RPK container/structure and registered top-level classes. It then compares every recognized nonempty localization key and its multiplicity against `BContentEnumerateLocaTags`. The child loads the official English `EN.MO` before the RPK; without a language catalog many original validators incorrectly report missing display text. Repeat `--language <file.mo>` and `--dependency-pack <file.rpk>` for explicit additional lookup context. Input and context names/hashes are checked before/after loading; different bytes sharing a namespace are refused. User ACP overlays are not imported; DLC ownership is not evaluated by this command.

The result is `scoped_witness_matched` only when the native multiset equals the expected serialized multiset. A pack with no recognized keys is `not_evaluated`. Keep structural and missing-witness negative controls, including truncated inputs, even if content callbacks succeed. A match does not establish resource ID preservation, effective field values, complete dependencies, ACP merge behavior or gameplay correctness.

Native diagnostic candidates are retained in the full report; the CLI shows a count and five examples. Exit 1 indicates diagnostics or an unevaluated witness, even when the recognized keys match. Do not dismiss a diagnostic merely because the same message occurs while reading an original pack. Review content context and compare independently when useful.

## Native RPK serialization roundtrip

An editor-selected ACP path is not an effective-overlay witness. Raw callback pointers and pool object handles are distinct and require matching class/representation checks. Localization callbacks may expose a projection rather than the complete loaded resource; use full file deserialization and independent readback for gameplay values.

`native-roundtrip` uses the current official library's `TGCResourcePack::TrySave` implementation. This is a reviewed private ABI, not a publicly supported SDK: both the loaded DLL hash and the actual resource-pack class getter must match the profile. It writes only a new file in its own child-run directory, then stages a verified copy and `roundtrip.json` into the requested new diagnostic directory. It never replaces the source or installs the output into a Mod.

The comparison checks every resource ID/type/field (including opaque leaf bytes), category metadata, resource order, other pack fields and header metadata. `byte_identical` means the entire file matches. `equivalent_data` permits different offset-map ordering/encoding and the corresponding derived payload length only when all compared data is equal. Resource loss, other field/header changes, or an unparseable result require review. Original native diagnostic findings still produce exit 1 even when serialization is equivalent.

This also works for packs with no localization keys, unlike the narrower `native-check` witness. Real 71-resource and two-resource generated packs have preserved their complete serialized data, including a source path containing Chinese characters. The real truncated negative control can be saved by the native engine as an empty pack, but the resource-ID/count comparison detects the loss. Native serialization preservation is stronger evidence than an open callback; it still does not establish effective gameplay behavior, ACP merging, or CLB/TAM support.

`native-inspect` also returns a hash-bound binary `roundtrip_artifact` for durable inspection evidence. Builds using `--native` copy these bodies and native RPK/CLB outputs into release-side `native-evidence/`; release verification recomputes comparisons from those files rather than trusting result flags. See [authoring-workflows.md](authoring-workflows.md).

For isolated RPKs referencing Mod CLBs, repeat `native-roundtrip --dependency-library "<file.clb>"` to supply an explicit search context. Files, hashes and namespaces are checked before/after loading; different data sharing a namespace is refused. Native builds supply all bundled CLBs automatically and freeze the relative context binding in their evidence. This adds only explicit CLB lookup directories; it does not import user ACP overlays or prove their merge precedence. Use `--language <file.mo>` and `--dependency-pack <file.rpk>` for explicit catalogs/RPKs. They are hash-bound and rechecked; user localization catalogs and external Mod contexts are not silently inferred.

PNG authoring uses a separate `surface-png` worker operation through `image-plan`; it remains unreviewed on the current Manta DLL. The following witness belongs to the reviewed Witch profile. It constructs the reviewed `TRECustomSurface`, checks the current format-name mapping (83=`R8G8B8A8UNorm`) and invokes the pinned official PNG writer. Frontend PNG decoding independently verifies all RGBA pixels. This is an image-surface witness, not a GPU-rendering test; source images and native cache outputs never replace original assets.

Whole-Mod runtime loading remains incomplete. The exported importer did not prove effective overrides, and localization callback parents remain unsuitable witnesses. A later pinned private `TGCResourceLibrary` application entry now supports `native-merge-check`: explicit `0x40` records are applied to the selected in-memory RPK, then actual saved resources and per-record markers are compared. This proves a scoped native ACP application path, not game activation or actual launcher precedence. See [overlay workflows](overlay-workflows.md); never attribute this evidence to registration/callback success alone.
