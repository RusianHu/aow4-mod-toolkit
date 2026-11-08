# Native material/effect connections

Use this when a copied or edited `TREEffectMaterialResource` has an effect URI that may not actually resolve. It complements local model links and declared shader-program queries.

```powershell
aow4mod native-material-check '<Mod/Libraries/OWN.clb>' --locator '[OWN]MAT\0' --output '<new report.json>'
```

Select 1–16 explicit material locators; repeat `--locator` as needed. Optional `--dependency-library` inputs provide additional CLB search files. The checker resolves a bounded declaration/selected-target closure against these inputs and installed game files. Missing files, ambiguous names/namespaces and unsupported declared target types are refused. Declaring a search file does not add an import to the Mod. Repair missing declarations with `clb-import-plan/apply` first.

The native worker observes all selected links before explicitly opening any expected target library. It checks actual pointer identity, target type, connection state and stable resolution without reactivating resources. Before/after material bytes are compared. Fresh processes independently load the source material objects and expected effect objects; complete native bodies must agree, including effect program bytes. Ready flags, successful callbacks and empty diagnostics cannot replace these comparisons. An unresolved effect has no actual effect-object certification, even when an independently loaded expected target exists.

Exit 1 means review is required; exit 0 covers only the reported connections/object comparisons. Other material classes, property-expression evaluation, renderer-selected feature masks, complete texture closure and rendering are outside this check. `effect-programs --require/--forbid` remains an author-requested feature query, not a prediction of the material's actual renderer choice.

## Native build protocol 7

Every payload CLB containing this material class adds `clb_material_links`. Builds cover all selected-class members in fixed batches of 16, at most 256 materials per CLB. Frozen evidence includes actual material/effect objects, independent file-loaded objects, exact payload/external CLB context and raw diagnostic logs. Identical bytes share artifact storage. Offline preflight reconstructs the payload graph, context closure, object membership/type/identity, all required roles, body comparisons, issue counts and diagnostic candidates. The new `material_link_issue_count` remains gated independently of byte differences and log diagnostics; unevaluated actual effects stay explicit.

Inputs are bounded to 64 context CLBs, depth 32, 256 MiB per CLB and 512 MiB in total. Materials use a 16 MiB body bound, effects 64 MiB, each native object's group at most 256 MiB, and one CLB's unique frozen material evidence/logs at most 512 MiB. Larger or unsupported targets are errors, not skipped passes.

Protocols 1–6 retain their original scope. The manifest must match its evidence version; dropping material checks or downgrading a protocol-7 snapshot is refused. This does not remove mesh/model draft gates or authorize a Workshop submission.

## Actual static texture links and protocol 8

```powershell
aow4mod native-material-textures '<Mod/Libraries/OWN.clb>' --locator '[OWN]MAT\0' --dependency-library '<Mod/Libraries/OWN_TEX.clb>' --output '<new report.json>'
```

Select 1–16 materials, at most 256 known static `TRETextureEP` properties per batch. The worker verifies the actual `TREEffectPropertyList` class/count and each selected property ordinal, class, name and serialized link. It observes every `TREMaterialTextureLink` target before explicitly opening expected target libraries. Actual target type, connected/activated state, exact member pointer and stable resolution are checked; parent/property bytes must stay unchanged. Separate processes load the complete source material and each expected `TRETextureResourceDEV` object for full-byte comparison. The target scope includes existing compressed textures as opaque complete native objects; it does not decode or create every compressed texture format.

The reviewed DLL serializers place the list at material+0x140, its data/count at +0x10/+0x18 and the texture link at property+0x28. These are build-bound native layouts, not a stable external SDK. No pointers/vtables are fabricated and no resources are reactivated manually. Unresolved/wrong-type targets remain independent findings even with zero diagnostics and equal serialized material bytes. A material without supported static texture properties is explicitly unevaluated; dynamic expressions are not evaluated.

New builds use protocol **8**, automatically adding `clb_material_textures` for every payload effect-material library. Coverage is complete for selected-class members: 16 materials per batch, at most 256 materials per library. Exact CLB context, parent/property/target/independent objects and raw stdout logs are frozen, with hash deduplication and a 512 MiB bound per library check. Offline preflight reconstructs declarations, member identities/types, target closure, roles, all comparisons, logs and issue counters. `material_texture_issue_count` is gated separately from data mismatches/diagnostics; `material_texture_count` and unevaluated checks remain explicit. Omitting checks, objects, rows/logs or downgrading a frozen protocol is refused.

Protocols 1–7 retain their earlier scope, including protocol 7's effect-only material check. This does not clear material/model variant gates: shader feature evaluation, GPU upload and rendering remain unverified.
