# Selected field restoration and native encoding review

Use this when restoring part of a cloned resource to its original configuration,
or transferring selected fields between explicitly paired resources. It retains
resource identity and unselected data. It does not revert an entire original
override; use `resource-plan` for that separate operation.

## Inspect directly

```powershell
aow4mod show <id> --file <pack.rpk> --property TypeDescriptor
aow4mod resource-diff <before.rpk> <after.rpk> --id <id> --property meta/0x07 --limit 10
```

Neither command requires an index when explicit files are supplied. `show --pack`
disambiguates ACP definitions; optional `--game` selects schema evidence. `resource-diff --other-id`, `--other-pack` and `--other-property` compare a different
template identity or component position. Output includes source hashes, paginated
differences, byte equality, recognized-data equality and traversal completeness. Its `--output` saves the requested page, not an unbounded full dump.

Unique maps/typed objects compare by keys and exact leaf bytes. Map encoding/order
changes are reported separately; array indices, type IDs and absent versus explicit
zero remain significant. Ambiguous or opaque nodes compare byte-for-byte. A depth
or node limit reports incomplete traversal and cannot establish equality. This
does not read engine getters or infer loaded/default values.

## Declare the fields, then stage once

```json
{
  "format": "aow4mod-field-copy-spec/1",
  "operations": [
    {
      "file": "Packs/My_Nests.rpk",
      "id": "8000000000000001",
      "property": "0x00471425/0x5F",
      "template": {
        "file": "C:/game/Content/Title/Packs/Strategic/Spawners_Strategic.rpk",
        "id": "000004200000002F"
      },
      "missing": "remove"
    }
  ]
}
```

The IDs/paths above illustrate the schema; select and verify actual resources. Target paths resolve against the Mod source directory. Relative template paths
resolve against the spec file's directory. An optional `pack` selects an ACP pack;
`template.property` defaults to the target path. Nested component fields can use
`meta/0x07/0x01/<hex-index>/<class-id>/<field-id>`. Different component indices are
allowed only when the remaining path and typed ancestry match. Inspect the actual
component lists; do not reuse indices from another variant.

```powershell
aow4mod field-copy-plan <mod-directory> --spec <fields.json> --allow-opaque --game <game> --output <plan.json>
aow4mod field-copy-apply <plan.json> --output <new-staged-mod>
```

Inspect the saved plan: it includes every before/template value, omission, nested
comparison, input inventory and expected output hash. The CLI prints a compact
summary. Up to 1024 explicit non-overlapping operations and 32 MiB of replacement
data can span multiple RPKs or existing ACP overrides in one atomic stage.

`missing` defaults to `error`. Explicit `remove` copies the template's absence;
it never writes an assumed zero/false. Only the final field may be absent: missing
ancestor containers are refused. The resource type, field and typed path must
match. Root identity/metadata fields, whole class sections, individual array
elements and overlapping writes are refused. Use dedicated identity/array/link
commands for those tasks. Source or template changes, plan/payload disagreement,
and backend fingerprint changes require a fresh plan; a failed apply leaves no
partial output directory.

This is structural template evidence, hence `--allow-opaque`. References retain
their IDs; imports are unchanged. Run dependency checks and a native build. Copying
a field does not prove that a source resource's gameplay assumptions hold in its
new owner. Whole list fields may be copied, but that explicitly selects the full
list and all contained identities.

## Review native normalization without accepting data loss

If a native readback reports a difference, inspect it before adopting anything. Use the same CLB/MO context as the intended build:

```powershell
aow4mod native-roundtrip <stage/Packs/My_Nests.rpk> --dependency-library <stage/Libraries/UI.clb> --language <stage/Language/Text.mo> --output <new-readback>
aow4mod resource-diff <stage/Packs/My_Nests.rpk> <new-readback/My_Nests.rpk> --id <id> --property meta/0x07
aow4mod field-copy-canonicalize <original-plan.json> --readback <new-readback/roundtrip.json> --output <new-canonical-plan.json>
aow4mod field-copy-apply <new-canonical-plan.json> --output <new-canonical-stage>
```

Repeat `--readback` for multiple edited RPKs. Canonicalization binds the original
report and both files, recomputes the strict pack comparison, and requires the
readback source hash to equal the original plan's exact output. Complete recognized
resource data must agree. Encoding changes are allowed only in selected subtrees
and their ancestor offset maps; every unselected sibling stays byte-identical. The resulting resource leaves must pass the existing strict native comparator. Lost references, changed values/types/array order, changed pack metadata, ambiguous
data differences, and unrelated normalization are refused.

The original report, diagnostics and raw readback mismatch remain evidence. This
command does not clear a build gate or attest native-report authenticity beyond
the supplied bound artifacts. Finish with a fresh native build and release check;
their independent comparison and diagnostic rules are unchanged. ACP readback
canonicalization is not supported by this command.
