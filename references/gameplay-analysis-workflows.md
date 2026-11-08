# Gameplay analysis, shared edits and content snapshots

Use after identifying the current resource entry with `search`/`describe`. These
commands answer which reviewed branches execute, which values were actually read,
and where a shared edit may reach. Unknown types and runtime context stay explicit.

For a loaded Mod that fails during actual casting, scripting or movement, use
[runtime diagnosis](runtime-diagnosis-workflows.md) to identify the first failing
stage and distinguish native validation from gameplay evidence before requesting
another user test.

## Follow the active entry

```powershell
aow4mod explain 0000041B000012C4 --locale ZH --native --output <new-report.json>
aow4mod explain 0000041B0000153E --native --output <unused-object-report.json>
```

The first command follows the currently attached Arch Mage researches; the second
inspects a separately selected object and does not make it part of that tome. `--file` disambiguates indexed definitions. The index must include links and the
requested language. Rules require the reviewed ResourceED build. `--native` is
optional; it creates isolated official-library workers, never attaches to a game.

Without `--output`, the full bounded report is printed. With it, stdout is a compact
summary and the file contains all nodes, classified edges, unclassified references,
cost rows, conditions, text candidates, display checks and native witnesses. Limits are `--depth` 0..20, `--max-nodes` 1..500 and `--max-edges` 1..2000; defaults
are 12/150/600. Depth/node/edge boundaries and ambiguous/missing candidates are not
silently promoted to a complete explanation. Independent same-ID definitions are
kept as candidates; no active-playset winner is inferred.

Reviewed rules cover research/subskill grants and reverse spell unlocks, spell
targeters, effects, shared statuses, player/unit upgrades, ability modifiers,
movement settings, casting/resource costs and unit upkeep. Every occurrence keeps
its property path: five links to one effect remain five applications. Appearance,
display and other unknown links remain in `unclassified_references`; a resource's
name alone never makes it an execution edge.

Scalar facts distinguish `serialized`, `loaded_native_getter`, `omitted_unread`,
unsupported storage and getter disagreement. Omission does not become zero. Damage multiplier 1.2 yields a derived +20% candidate; resistance Strength 12 yields
base 120%, separately from Ignore Target Resistances and immunity conditions. Research level is not tome tier or final knowledge cost. Cost rows are the static
resource-amount templates; player modifiers and actual settlement are not evaluated.

The reviewed flat requisite filters expose ANY/forbidden ANY/ALL lists separately;
unit-property filters have only required/forbidden lists. Tag text is retained but
its general match mode is not guessed. Unsupported filter layouts remain marked.

Text checks compare dictionary tokens with serialized replacement parameters. Each node retains up to 16 text entries and reports total/limit/truncation in
`text_candidate_coverage`; selected text is never represented as all text when truncated. Explicit replacements and unused declarations remain visible. Supported
`TAG>NumTurns`, `TAG>Duration`, `TAG>ResolvedMultiplier`, `TAG>ResolvedStrength`,
`TAG>NumThingsToDispel`, `TAG>ResolvedAccuracyModifier` and `TAG>AREA>Radius`
expressions can report values from a unique candidate already in the analyzed graph. Duplicate tags never choose the first row. Derived values are candidates, not
rendered strings: runtime namespaces, dictionary precedence, formatting and actual
second-level tooltips remain unevaluated. Missing explicit bindings are observations,
not automatic localization errors or instructions to insert guessed values.

## Society-trait starting bonuses

```powershell
aow4mod index --mod <mod-source> --links --locale ZH --catalog ZH=<mod-source/Language/catalog.mo>
aow4mod explain <society-trait-ID> --file <indexed-trait.rpk> --locale ZH --output <new-report.json>
```

Use a dedicated index when comparing alternate projects. As with other gameplay
explanations, `--file` selects an indexed definition; it does not add an unindexed
stage or pick a load-order winner. Plain `show --file` remains available without indexing.

`starting_bonus` distinguishes the trait's starting-property list from side/race/
unit/colony properties. Reviewed `TGridSkillSelectionSettings.SelectedSkillsLinkList`
entries become `starting_research` edges; requisite-filter references do not. `TStartingRaceTraitEmpireDevelopmentProperty.EmpireProgSkills` entries become
`starting_empire_skill` edges. Research and empire targets are checked against their
expected types; missing, duplicate, wrong-type and unvisited candidates stay visible. Every occurrence is preserved. The summary counts declarations in selected
definitions, not effective grants; a directly selected orphan property does not
prove that any society trait uses it.

Random counts are reported only when serialized; omitted counts remain unread,
not constructor-default zero. Random and `ActiveOperationsTagFilter` selectors are
not executed. The latter requires tag identity, spell TagSet and final overlay
context; a reference to a tagbook is not itself a spell grant.

The starting property's `DescriptionLink` is reported separately. A custom key is
a text candidate; an empty key is `empty_key_runtime_text_unverified`. Neither
template inheritance nor an empty key proves engine-generated text or working
second-level tooltips. Hyperlinks still require the actual runtime dictionary,
entry point and hover verification. These commands do not launch games, modify
Mods or backfill old saves.

Content snapshots replay starting declarations, text keys/parameter bindings and
target type checks from their frozen resource bodies. Changed analysis rules
invalidate older snapshots: re-evaluate both sides rather than rewriting old evidence.

## Read an actual owned child

```powershell
aow4mod native-inspect <Tome_Arch_Mage.rpk> --id 0000041B00001547 --object Area --query Radius --output <area.json>
```

Repeat `--object` for up to eight owned-object steps. For ambiguous names use
`--object-path-file <selectors.json>` containing an array such as
`[{"name":"Area","category":""}]`. The alternatives are mutually exclusive. The reviewed object editor must return a non-null, non-cyclic pointer whose actual
class matches the native registry. The parent stays alive throughout the read.

Only descriptors come from a new instance of the observed child class; values come
from the original parent's loaded child. Each child has an independent serialized
body witness, and child/owner data must remain unchanged by getters. Supported child
values are i32/enums, bool and f32. Links, lists, strings, other editors and failed
getters stay unsupported/failed; they are never reported as zero.

## Assess shared impact and reconnect flat links

```powershell
aow4mod impact 0000046A0000020C --depth 4 --max-nodes 100 --output <impact.json>
aow4mod link-plan <staged.rpk-or-acp> --id <owner> --property meta/0x02 --target-id <new-target> --target-file <target.rpk> --old-target-file <original.rpk> --source-root <mod> --output <link-plan.json>
aow4mod link-apply <link-plan.json> --output <new-stage>
```

`impact` lists recognized direct reference sites and a bounded incoming graph. It
contrasts editing a shared ID with cloning and retargeting explicit callers; it does
not automatically choose a plan. Scripts, unknown encodings, duplicate definitions
and traversal boundaries prevent an exhaustive gameplay-impact claim.

`link-plan` now accepts exact flat `meta/...` paths returned by `refs`, including
nested recognized links. Identity metadata is not a reference target. Old and new
resource types must match unless the existing explicit type-change exception is
requested. IDs, labels and full locators update together; RPK imports or ACP
per-pack imports are staged atomically. Other fields remain unchanged.

ACP 0x40 record wrappers are re-encoded only around a changed flat-link resource
when a shorter payload crosses an offset-width boundary. Resource and other member
payloads are preserved. This avoids a native readback mismatch without relaxing the
package/merge comparison. Always finish with native build and release-check.

## Freeze and compare the interpreted subset

```powershell
aow4mod content-snapshot <explain-report.json> --output <snapshot.json>
aow4mod content-compare <before-snapshot.json> <after-snapshot.json> --output <comparison.json>
```

Capture rechecks current input hashes and freezes selected resource bodies plus
native request/result/process receipts and object bodies. Replay recomputes known
facts, costs, filters and reference-edge agreement; editing values and rehashing a
report does not make it agree with its witnesses. Comparison runs offline without
the game or index, but requires the same analysis fingerprint, reviewed profile,
native-value mode and traversal limits. When analysis code changes, re-evaluate
both sides under the new rules; do not silently compare incompatible meanings.

The comparison separates interpreted changes, execution-edge changes and raw body
changes outside the interpreted projection. An unchanged projection never proves
unchanged gameplay. Game definitions use game-relative file/ID identities; external
Mod files retain their explicit path, so moving a Mod may appear as added/removed
definitions. This is a bounded content snapshot, not full automatic migration,
release-gate clearance or a complete gameplay/asset snapshot.
