# Unified existing-Mod maintenance

The workflow keeps the production Mod untouched and exposes one phase/next action. Run the normal Skill CLI prefix; no installation or online submission is implicit.

```powershell
aow4mod maintenance-start "<production Mod>" --baseline "<previous version-2 snapshot>" --output "<new workspace>"
aow4mod maintenance-context "<workspace>" --max-resources 20000
aow4mod maintenance-stage "<workspace>"
aow4mod maintenance-build "<workspace>"
aow4mod maintenance-status "<workspace>"
```

For an existing Workshop item, provide `--workshop-id`, `--owner` and `--expected-title` together at start. They configure the copied project only. Optional `--mod` arguments are explicit external dependency/validation Mods. Start alone does not infer the active playset. `maintenance-context` explicitly captures the actual configuration; `--playset <content_load.json>` selects a fixture. Same-identity working-copy activation remains separate work.

The context records every enabled Mod in its exact configuration slot and requires the requested production path to be uniquely enabled. An inactive/different active variant must be selected explicitly; unreadable/duplicate entries never become a smaller empty context. It freezes all ACP/RPK and available metadata JSON witnesses, while inventories bind other files. The working candidate replaces only the original's analysis slot. All other enabled Mods remain in order. Disabled DLC values are preserved in the copied build validation settings; enabled opaque UGC is flagged unassessed. No actual configuration is written.

Context checks replay registered-definition conflicts from raw witnesses, including the payload candidate at its captured slot. Identical definitions and differing definitions remain distinct; last-listed hints do not prove runtime precedence. Other-Mod/conflict findings enter the maintenance gate. Configuration, Mod inventory, context/order or project-validation changes invalidate the assessment and require recapture. `--depth 0..12` and `--max-resources 1..100000` set explicit environment traversal budgets; truncation remains a finding. Raw witnesses are bounded to 512 MiB, each context to 128 Mods, and live paths are not needed for frozen publication rechecks.

Start imports all original files into `project/mod`, freezes the provided baseline hash, indexes the installed originals and captures current original/author fields. Every assessment gets a new immutable directory containing snapshots, comparison, rebase proposal, maintenance findings and environment report. The root ledger tracks the active project, source inventories, original inputs, artifacts and current build/preparation. Missing historical baselines remain an explicit review finding; a newly captured snapshot does not prove compatibility with a past update. The supplied historical snapshot is trusted evidence, not a notarized historical fact.

Stage rederives the migration proposal from the actual source and baseline. It copies only non-conflicting fields that the author had left equal to the old original. Author/upstream overlaps, missing/type-changed originals, unsupported metadata/class sections and capture gaps retain review. When there are no proposed edits it creates a verified unchanged stage and says **no automatic repair**, rather than inventing a fix. `--safe-partial` explicitly allows only the existing rebase planner's safe subset; unresolved findings remain gated.

Use existing `edit-plan/apply`, native edits, link commands and `project-use <workspace/project> --source <relative stage>` for researched manual corrections. Then run `maintenance-assess <workspace>` to refresh the state. Changed source/config/game inputs invalidate previous assessments. The original imported production inventory remains bound; external production edits require a new import. Builds always request native validation and keep its errors, data differences and diagnostics.

## Installation/publication preparation

```powershell
aow4mod maintenance-prepare-release "<workspace>" --online --proxy http://127.0.0.1:10808
# Optional installation plan, still no apply:
aow4mod maintenance-prepare-release "<workspace>" --destination "<explicit destination>" --backup "<new backup>"
```

This runs ordinary release preflight, optionally verifies the actual account/item/title, freezes a publication snapshot and optionally writes a sync plan. It never calls a submit API, changes a playset or applies the installation plan. A gated candidate needs `--allow-review-plan` just to prepare a local sync plan; that does not resolve the findings or authorize publication. Missing Workshop identity remains an explicit phase. Status rechecks the preparation hashes, source-manifest identity, snapshot location and frozen content. Use the separate reviewed sync/launcher flows for actual installation; actual submission still needs the corresponding user instruction.

`maintenance.json` and `maintenance-evidence/` sit **beside** the runtime payload. They freeze baseline/current snapshots and comparison, bind every payload resource's ID/type/fields/operation and package/registered-pack identities, and require native evidence. A selected playset additionally freezes `maintenance-context/` and its derived conflict report; omission, reordering, primary-slot substitution and report edits are independently checked against raw configuration/Mod bytes. Publication preflight recomputes comparisons, conflicts and findings, and upload preparation binds the requested context policy to the original manifest. Replacing the entire original trusted input before starting is outside this integrity claim.

Maintenance evidence adds its review findings to the existing release gate; it never clears environment/native/model/map findings. Consequently `assessment.review_required=false` can coexist with `phase=built_review_required` or `prepared_review_required` because other checks found issues. The command returns focused report paths and a compact release summary.

## Explain the remaining findings

`maintenance-status` derives `environment_attribution` from the hash-bound captured environment report and the actual working-source path. It lists source-owner groups and primary counts; it does not rewrite `maintenance.json`, remove findings or invalidate old frozen evidence. A stale assessment has no current attribution. Use `dependencies --section attribution` for a fresh selected-set analysis, and ordinary issue/resource sections for the individual records.

## Confirm the game version separately

Use [game-version](game-version-workflows.md) before claiming that an installed-file assessment covers the current Steam branch. An offline capture is not a fresh online check. A version comparison does not change old snapshots or clear content/native review findings.
