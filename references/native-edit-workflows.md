# Existing-resource native scalar edits

Use `native-edit-plan/apply` to edit an existing ACP override or local RPK resource through the official property setter and save its canonical representation. It does not add an original-game override automatically, edit package identity/metadata or initialize links/arrays. Those tasks have their existing authoring commands.

```powershell
aow4mod native-edit-plan "<staged Mod>" --id "<resource ID>" --property "0x00471C6D/0x1" --name "Friendly Fire Type" --value 2 --output "<new plan.json>"
aow4mod native-edit-apply "<plan.json>" --output "<new staged Mod>"
```

The resource must already exist in the selected Mod. A directory selects both its ACP overrides and registered local packs. Use an exact ACP/RPK and optional `--pack` when ambiguous; an exact RPK needs `--source-root <complete Mod>` so other files are preserved. Outputs are always new directories; original files stay unchanged.

`--name` is the native property label from `native-properties` or `native-inspect`; `--category` disambiguates repeated labels. `--property` is a **direct class/property path** from `show`/`schema-search`. Similar labels do not establish their association: planning proves it using setter-induced binary changes. Resource ID/name/locator metadata and arbitrary nested/object/link/list properties are excluded. The reviewed DataStoreTag metadata (`meta/0x12C`) and existing regular-unit stat-modifier member (`--embedded 0x00471428/0x61`) are explicit exceptions; see [unit workflows](unit-workflows.md). Failed getters remain unavailable rather than becoming zero/default values.

For multiple assignments on one object, use `--values-file` instead of individual assignment flags:

```json
[
  {"property":"0x004714CD/0x07","name":"Effects Apply Order Random","value":true},
  {"property":"0x004714CD/0x0B","name":"Effect Multiplier","value":1.25}
]
```

The limit is 32 distinct properties/paths. The values file is hash-bound. Supported native editor codecs are signed 32-bit ordinal/enum values, finite float32, bool and UTF-8 string. Enum targets and probes require the matching offline schema's exact native enum table. Float32 rounding is recorded, and signed-zero readbacks are compared by bits. Scalar-compatible serialized bytes are checked as well; packed/compound representation mismatches are refused. Domain-specific gameplay meaning is not inferred from an editor codec.

## Evidence required before staging

1. Read the source into a native object and retrieve only the selected scalar getters, after checking their actual descriptors.
2. In disposable objects, set each selected property to a different value. Only its declared serialized field may change. Ordinary edits use the desired value as this probe. For no-op requests, bool toggles or another verified enum value can prove the mapping. A no-op integer/float/string request needs an explicit distinct `probe_value` (`--probe-value` for one assignment); the tool does not invent a domain constraint for those probes.
3. Apply the requested values to a freshly loaded source object, serialize, and deserialize into another object. Getter values and every unrequested serialized field/class scope must remain consistent. Primitive field encodings are checked against the observed getters.
4. Start a separate reader process that calls no setters. Its values and canonical body must still match. A different run directory, type/binding, request data and complete selected-property coverage are required.
5. Rewrap only that resource. For RPKs, perform independent whole-pack read/save with bundled CLB context. Apply later rechecks every source, probe, reader, wrapper, input-file hash and generator/native binding before copying to a new stage.

Temporary probe values never enter the final Mod; their before/after binaries remain in the development cache. There is no generic “ignore default differences” table. An explicitly stored default may be omitted only within a requested field whose mapping/getters/encoding and separate-reader result pass. Unrequested default omission, unknown field loss, extra setter effects, missing/ambiguous mappings and descriptor changes refuse the plan. Declare another scalar explicitly if its canonicalization is intentional and can be proved; do not edit a plan's flags/hashes to hide findings.

Whole-pack diagnostics and comparison differences remain review findings. The payload uses the source wrapper plus the proven object body; a diagnostic native saved pack never replaces other resources. This supports continued staged repair when other pack data still needs review. An ACP edit checks its structure and selected native object, **not effective ACP merging** or complete dependency availability. A false review flag on a plan/receipt is not a compatibility pass; run normal native build/release gates on the final project.

For `--embedded`, the owner class, direct member path and inline class are bound to the reviewed profile. Probe/edit/readback processes operate on official factory-created `TUnitStats`; apply reselects that exact source member and rewraps only it in the original resource. Removing or changing the embedding, substituting the parent reader type, refreshing hashes after modifying other parent/stat fields or using an unreviewed owner cannot authorize extra changes. This path does not initialize missing members or relax the direct scalar field requirement inside the selected object.
