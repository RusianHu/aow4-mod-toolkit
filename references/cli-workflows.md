# Toolkit commands

The canonical backend is this Skill's `scripts/aow4mod`. Run `python <Skill>/scripts/aow4_mod_audit.py`; a parent-workspace entry may delegate to the same backend. No package installation is required. `doctor` discovers Steam libraries and the Windows Documents known folder; `--game` or `AOW4_GAME_DIR` overrides discovery.

Use `capabilities <area>` for compact command routing and limits. Areas: `resources`, `authoring`, `maintenance`, `assets`, `release`. Optional dependency availability is reported; core file tooling needs only Python 3.10+.

## Read-only navigation

```powershell
aow4mod doctor
aow4mod inspect "<ACP/RPK>" --limit 10
aow4mod corpus "<Mods directory>" --game "<game>" --output "<new report.json>"
aow4mod index --mod "<Mod directory>" --links
aow4mod search "Incite Revolt" --limit 10
aow4mod show 0000043700000112 --file "<exact ACP or RPK>" --property FriendlyFire
aow4mod refs 0000043700000112 --reverse --limit 10
```

The default index is `.aow4mod/catalog.sqlite`, outside source directories. `index` treats supplied game/Mod roots and language/catalog selections as the complete scope; repeat all desired `--mod`, `--locale` and `--catalog` arguments. `--links` is required for reverse references, tome membership and graph tracing. Search and reference results support `--limit`/`--offset`; returned evidence is hash checked. All installed original packs being present does not imply DLC ownership or enablement. Re-index after changing inputs. Schema 1 toolkit catalogs upgrade/rebuild on the next `index`; unrelated databases are refused.

`search` also supports localization keys, DataStoreTag and indexed text: run `index --links --locale ZH --locale EN`, then `search "大法师魔典" --locale ZH --exact`. Use `--field name|id|key|tag|text` to narrow matches. `loc-catalogs`, `loc-search`, `describe` and `trace` provide discovery, dictionary-only entries, content summaries and bounded graphs; see [content research](content-research-workflows.md) for candidate-language, duplicate-tag, flat-field and coverage boundaries.

Direct `inspect` also paginates by default (20 objects) and needs no index. `--output` saves the complete report while stdout remains paginated. The underlying Python `inspect_file` API retains its complete result for programmatic callers.

`show --file` reads the supplied ACP/RPK directly without an index; `--pack` disambiguates ACP definitions. Without `--file`, lookup remains index-bound. `show` supports property-name filtering and `--limit`/`--offset`. It merges curated semantics with a matching local extracted schema, when available; unknown properties stay raw. See [schema-workflows.md](schema-workflows.md) for evidence levels and cache refresh. Field labels alone do not enable typed editing.

## Maintenance

For one existing Mod, prefer the unified `maintenance-start/assess/stage/build/status/prepare-release` flow in [maintenance-workflows.md](maintenance-workflows.md). It retains explicit phase/next-action information and frozen review findings. The lower-level snapshot/comparison/editing commands below remain available for research and manual corrections.

```powershell
aow4mod snapshot "<Mod>" --dependency-depth 2 --output "<new snapshot.json>"
aow4mod compare "<old snapshot.json>" "<new snapshot.json>" --output "<new comparison.json>"
aow4mod playset
aow4mod conflicts --limit 10
aow4mod conflicts --mod "<Mod A>" --mod "<Mod B>" --output "<new conflicts.json>"
aow4mod dependencies --mod "<Mod>" --steam --limit 10 --output "<new environment.json>"
```

Version 2 snapshots are independent of package description/version edits. They retain previous original and Mod fields so comparisons can distinguish untouched old-original values from deliberate author edits. `safe_rebase_candidate` is a proposal only. Missing snapshots, type changes, unavailable dependencies, unknown defaults and capture issues require review. Dependency depth is bounded and defaults to zero; positive depths use the resource index.

`conflicts` follows the selected Mods' registered RPKs and local RPK dependencies. Undeclared files are excluded unless `--include-unregistered` is explicit. Reported last-listed candidates are configuration-order hints, not verified runtime winners. Shared IDs with identical data are distinct from differing definitions. Never silently change a playset to resolve conflicts.

`dependencies` adds an explicit Mod-set graph, official DLC metadata and loose asset availability. It defaults to the active playset only when no `--mod` is supplied. Use `--section` and pagination for focused results; the complete report belongs on disk. Boundaries and build integration are in [environment-workflows.md](environment-workflows.md).

Legacy `audit --baseline` remains supported for version 1 audit snapshots and retains its exact-ACP hash restriction. Prefer `snapshot`/`compare` for ongoing development.

## Staged authoring

For atomic selected-field transfers across explicitly paired resources, original
omission restoration and nested/encoding comparisons, use `field-copy-plan/apply`,
`resource-diff` and optional `field-copy-canonicalize` from the [field restoration
workflow](field-copy-workflows.md). This avoids reconstructing binary edits in a
project-specific script. Dependency and native validation remain separate.

```powershell
aow4mod edit-plan "<Mod or ACP/RPK>" --id "<resource ID>" --property "0x00471C6D/0x01" --value 3 --output "<new plan.json>"
aow4mod edit-apply "<plan.json>" --output "<new staged directory>"
```

`auto` chooses a curated scalar codec or an extracted validated bool. Extracted integer storage requires explicit `u8/u16/u32/u64` or `i8/i16/i32/i64` of matching width, because signedness is not inferred. Unverified storage, `f32/f64`, `vec2f/vec3f/vec4f`, `string`, `hex` and `unset` require `--allow-opaque` and remain structural-only. Floats/vectors accept finite JSON numbers only. `--value` is JSON; `unset` uses null. Adding unverified class sections and unknown ACP operations are unsupported.

For nested properties, inspect a bounded subtree with `tree <ACP/RPK> --id <ID> --property <class/field/path> --limit 10`. This reads a supplied file directly and does not require index registration. Pass the longer path to `edit-plan` with an explicit codec and `--allow-opaque`. The plan retains both the inner operation and reconstructed outer property; applying verifies they agree and that unrelated payload bytes are preserved. Removing an array item with `unset` reindexes its array; removing a mapped property drops just that field. Missing or ambiguous nested layouts are refused rather than guessed.

Use `array-plan <file> --id <ID> --property <array-envelope path> --action append|insert|replace|remove --allow-opaque --output <plan.json>` for list edits, then `edit-apply`. Insert/replace/remove use decimal `--at`; append does not. Supply `--codec`/`--value` for a scalar element, or `--copy-from-property <element path>` to copy exact serialized bytes from an existing element. Optional `--copy-from-file` and `--copy-from-id` choose another source. Templates are hash-bound; copying preserves IDs, so retarget a copied resource link separately when needed. Array order follows numeric indices even if payload storage order differs.

Apply validates source/schema hashes, expected field bytes and plan codecs, rebuilds offset tables, reparses the output, and verifies only intended fields changed. It writes a new staged copy and `.aow4mod-edit.json` receipt, never the source. When editing a local RPK, use `edit-apply ... --source-root <Mod directory>` to preserve the entire Mod tree; without it, only the RPK is staged. Staged copies retain the package identity and should not be enabled alongside the original by accident. This is not a gameplay test or a publishing command.

Blank projects, original-resource overrides, RPK clones with new IDs, link retargeting, rebasing, builds and localization are documented in [authoring-workflows.md](authoring-workflows.md). Existing-item publication and recovery are in [release-workflows.md](release-workflows.md). `tools/aow4_workshop_update.py` is a compatibility wrapper over the same publication backend.
