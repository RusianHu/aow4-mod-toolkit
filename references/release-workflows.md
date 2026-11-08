# Workshop publication and local synchronization

For installation identity, launcher/database pairing, previews and recovery pitfalls, see [deployment checks](deployment-checks.md). This page supplies commands and their evidence scope.

The shared backend is `scripts/aow4mod/publication.py`; Steam operations use `steam_transport.py` and the game's installed `steam_api64.dll`. It discovers the installation, requires Windows 64-bit and the signed-in item author, and selects an available supported Steam interface. No login credentials are stored. The old `tools/aow4_workshop_update.py` entry is only a compatibility wrapper.

For an authoring project, set `workshop` before building:

```json
{
  "published_file_id": "<existing Workshop item ID>",
  "expected_owner": "<your SteamID64>",
  "expected_title": "<your existing item title>"
}
```

Replace the placeholders with your own existing item identity. A new project starts without Workshop identity and uses the separate first-publication flow; do not create another item to update an existing Mod.

```powershell
aow4mod release-check "<manifest.json>"
aow4mod release-check "<manifest.json>" --online --proxy http://127.0.0.1:10808
aow4mod workshop-publish "<manifest.json>"
```

The last command still performs preflight only. Actual submission requires `--submit` and a user task that authorizes this publication. Development of the toolkit itself does not authorize submission. Unresolved release checks require review; `--allow-review-required` is an explicit scope override, not a bypass for hash/identity validation.

Read the scope and `review_required`, not only the exit code. `environment_blockers` groups repeated ABI/setup failures while preserving every check, error count and release gate; verification recomputes it. Integrity verification can complete while native checks still fail. Consult [current compatibility](../docs/MANTA_COMPATIBILITY.md); static-only builds do not certify native behavior.

Submission preserves the existing title, visibility, preview and tags. It updates content and description with a change note. A persistent, verified upload snapshot is kept under `.upload-attempts/`, including description/notes and the original manifest bytes. The receipt is created before submission and records the API result, legal-agreement flag, item identity and content hashes. A pre-existing receipt prevents automatic resubmission.

## Verification and recovery

```powershell
aow4mod workshop-verify "<manifest.json>" --download --proxy http://127.0.0.1:10808
aow4mod workshop-reconcile "<manifest.json>" --proxy http://127.0.0.1:10808
```

Metadata-only verification never claims content verification. Download verification compares the full installed payload file set and hashes against the reviewed manifest. Reconcile checks a prior receipt and remote desired state, records its findings in a new file, and never resubmits. A timeout or unreadable response is an unknown outcome, not proof that nothing happened.

If Steam requires accepting an agreement, leave it for the user; the tool does not accept it. After a successful submission, complete download/metadata verification before reporting the release as verified. This does not replace gameplay testing.

Native SDK diagnostics and progress go to stderr. CLI stdout remains a JSON result. HTTP requests use `--proxy` only when supplied (or normal system/environment proxy settings); the native Steam client's network configuration is not modified.

For releases built with native validation, preflight recomputes ACP body/RPK/CLB comparisons using frozen binaries and verifies complete declared target coverage. Protocol 3 added per-pack dependency preparation and frozen local RPK context; protocol 4 also automatically collects local mesh/effect-material link observations for CLBs containing supported models. Model/target object witnesses are frozen, while declared paths, target ordinals and actual types are independently rechecked against the payload CLB. Models are batched by 16, with at most 256 models and 256 MiB of frozen model evidence per library. `model_link_issue_count` enters review even when bytes and diagnostics match. A wrong material type can otherwise have ready flags and zero diagnostics. Clearing findings, forging the runtime type or omitting checks while refreshing hashes does not bypass payload graph/type validation.

Protocol 5 additionally requires a `tam_roundtrip` check for every payload TAM. It freezes both independent GMP saves and their before/after native map bodies, deduplicates identical files and binds bundled RPK/CLB lookup inputs. Offline preflight compares the payload to the first save, the first to the second save and each object body to its saved map body. Original save changes stay findings even if the second save stabilizes. Malformed maps become errors; omitted witnesses or lookup inputs, refreshed-hash data edits and protocol downgrade are rejected. Each map file is bounded to 256 MiB and unique frozen evidence to 512 MiB. See [map workflows](map-workflows.md).

Protocol 6 additionally freezes known-world identities and all explicit zone/cell-holder object/cell bodies in complete 256-entity batches. Offline preflight reconstructs the payload projection and selected bodies, validates every batch and lookup input, and re-extracts diagnostics from each frozen raw log. `world_entity_issue_count` and explicit empty-object unevaluated state retain review gates. See [map workflows](map-workflows.md) for scope, budgets and controls.

Protocols 1–6 remain readable at their historical scopes; the manifest's protocol must match its report. Native errors, mismatches, model-link findings and diagnostics must stay in the release gate. An explicit review override does not bypass integrity/comparison checks. `native-validation.json` and `native-evidence/` are copied beside the upload payload and revalidated before submission; they never become runtime files. Complete external shader/texture closure, shader variants, map semantics, engine/launcher precedence, rendering and gameplay remain unverified.

Frozen snapshot checks also verify the saved source-manifest bytes and bind their original payload/text hashes, release gate and native policy. Removing native witness paths or rewriting both report protocol and hashes cannot silently change the prepared snapshot's requested scope. This is an offline integrity check; it does not contact Steam or grant submission authorization.

Maintenance-workflow builds additionally freeze version-2 baseline/current snapshots and their derived comparison outside the runtime payload. Preflight independently rechecks package/resource fields, recomputes findings, requires native validation and preserves the maintenance review gate. Upload snapshots copy and revalidate this context against the original manifest policy. These witnesses do not grant submission authorization or clear other native/environment/draft findings; see [maintenance workflows](maintenance-workflows.md).

## First publication

```powershell
aow4mod workshop-create-plan "<manifest.json>" --owner "<Steam ID>" --preview "<PNG/JPEG/GIF>" --output "<new create-plan.json>"
aow4mod workshop-create "<create-plan.json>"
```

These commands prepare/check only. The plan binds release/package identity, author, title, tags, visibility and preview hash. Default visibility is private; set `--visibility public` only when that is the user's intended publication. The preview is checked by recognizable header and a 10 MiB toolkit size bound, not fully rendered or certified for every Steam limit. Titles are bounded to 128 UTF-8 bytes; tags use printable ASCII without commas and the supported metadata bounds.

Only after authorization for this new Workshop item, `workshop-create <plan> --submit` creates it once and uploads the frozen payload/metadata. All transmitted text and preview bytes are frozen before creation. Creation and upload have separate durable receipts beside the plan. The installed SDK exports and authenticated query layout have been checked; failure/recovery branches use fake transports.

If creation succeeds but upload has not been attempted, `--submit --resume` reuses the known ID. If an agreement is required, the user must personally accept it before `--agreement-accepted` is used. That flag records user acknowledgment; it does not accept terms on their behalf. An upload callback still reporting an agreement requirement remains unverified.

If creation outcome is unknown and no ID was recorded:

```powershell
aow4mod workshop-owned --page 1 --limit 10
aow4mod workshop-create-recover "<plan>" --item-id "<explicitly identified new item>"
aow4mod workshop-create "<plan>" --submit --resume
```

Recovery queries the signed-in account, verifies item/app/author and checks that the item does not predate the attempt. It never guesses an ID from a title or repeats CreateItem. Once an upload receipt exists, resume is blocked; inspect desired remote state instead:

```powershell
aow4mod workshop-create-verify "<plan>" --download
```

Authenticated queries support owned private items. Verification compares title, description, tags, visibility and downloaded payload hashes, plus preview presence. Exact server preview pixels are not compared. It can use the frozen attempt even if original release/preview files later change. Verification never resubmits. A successful execution returns the `workshop` configuration to use in subsequent project builds; it does not silently rewrite the author's project config.

API references: [ISteamUGC creation, updates and queries](https://partner.steamgames.com/doc/api/ISteamUGC), [metadata size constants](https://partner.steamgames.com/doc/api/ISteamRemoteStorage#Constants).

## Local synchronization and rollback

```powershell
aow4mod sync-plan "<manifest.json>" --destination "<local Mod>" --backup "<new backup directory>" --output "<sync-plan.json>"
aow4mod sync-apply "<sync-plan.json>"
aow4mod sync-status "<backup/receipt.json>"
aow4mod sync-rollback "<backup/receipt.json>"
```

Synchronization writes only when the task authorizes updating that local Mod. It requires matching package UUIDs for an existing Mod, freezes source/destination hashes, commits a full backup before replacement, and preserves source artwork/notes. The backup must be outside the destination and release trees. Game and Steam Workshop cache destinations are refused. New/empty local directories are also supported.

Previously managed obsolete files are removed. Other runtime files block planning unless an explicit `--extra-runtime keep` or `prune` policy is chosen; pruning remains fully backed up. Unmanaged source assets are preserved. `--allow-review-required` applies only to already reviewed release findings, not to identity/hash mismatches.

Directory swaps use a checked staging directory on the destination's volume. Failed installation attempts restore the old tree when possible and preserve the committed backup. `sync-status` compares actual file hashes with both planned states, so a receipt alone is not proof of completion. Rollback refuses to overwrite post-sync user edits and retains a copy of the synced tree. No synchronization command changes the launcher playset or publishes to Steam.

## Game launch configuration

```powershell
aow4mod loadout-plan --enable "<installed Mod>" --output "<loadout-plan.json>"
aow4mod loadout-apply "<loadout-plan.json>" --backup "<new backup directory>"
aow4mod loadout-status "<backup/receipt.json>"
aow4mod loadout-rollback "<backup/receipt.json>"
```

Default input is the user's existing `content_load.json`; `--file` selects an isolated test copy. Repeated `--enable` and `--disable` operate on exact resolved paths. `--position` is a zero-based insertion position among retained entries; default append preserves existing order. Entries and unknown top-level data, disabled DLC and enabled UGC values are preserved. Duplicate package UUIDs are refused; replacing a local/Workshop duplicate requires explicitly disabling its other path in the same plan. A selected Mod needs a readable UUID and valid local pack declarations.

Apply requires authorization to change that configuration. Toolkit development uses a disposable copy only. It rechecks the source hash, selected identities and newly enabled Mod's full inventory. Known running game/launcher processes block apply and rollback. A durable original-byte backup and prepared receipt are committed before atomic file replacement. Failed or uncertain writes are inspected with status, not retried automatically; an existing backup directory is never overwritten. Rollback refuses changes made after apply and restores the exact original bytes, including formatting/BOM.

This edits the game's generated launch configuration, **not** the launcher's database or UI. A subsequent launcher run may regenerate it. Hash verification proves the configuration file state only; neither actual engine mounting nor launcher persistence has been established.

## Persistent launcher playsets

```powershell
aow4mod launcher-inspect --limit 10
# Select a different reviewed archive explicitly for inspection or planning:
aow4mod launcher-inspect --launcher-asar "<launcher/resources/app.asar>" --limit 10
aow4mod launcher-plan --enable "<installed local Mod>" --output "<launcher-plan.json>"
aow4mod launcher-apply "<launcher-plan.json>" --backup "<new durable backup directory>"
aow4mod launcher-status "<backup/receipt.json>"
aow4mod launcher-rollback "<backup/receipt.json>"
```

Receipt-owned local registrations can be fully withdrawn after an official reopen: `launcher-plan --disable "<path>" --unregister "<path>" --registration-receipt "<original enable backup/receipt.json>" --output "<cleanup plan>"`, followed by normal apply with a new backup. This does not delete Mod files. It proves the original row did not exist before that receipt, binds unchanged Mod contents and refuses remote/other-playset/dependency ownership or triggers. Use `sync-rollback` separately for installed files. See the [actual session workflow](launcher-session-workflows.md).

`--database` selects a `launcher-v2.sqlite`, including an isolated fixture. Default is AoW4's user-data database. `--launcher-asar` can select the exact supported installed `resources/app.asar`; automatic discovery uses the launcher's existing installation pointer. Mutations require a reviewed application hash, exact owned-table schema, known migration registry, DELETE journal mode with no sidecars, and one active owned private/custom playset with `syncState=NOT_ELIGIBLE`. Remote IDs/checksums are preserved. Cloud-eligible playsets, unknown migrations/schema/app versions and pending SQLite journals are refused rather than guessed or silently repaired.

Enable/disable uses exact resolved Mod paths. Newly registered local Mods must be direct children of the database's sibling `Mods/` directory, with matching ACP/launcher metadata UUIDs and valid declarations. Steam Mods must already be registered by the launcher; this command does not subscribe or synthesize Steam records. Duplicate enabled package UUIDs are refused, so replacing a local/Workshop duplicate requires an explicit disable in the same plan. `--position` is a zero-based insertion position among **all retained playset members**, including disabled members. Repeating `--enable` on an already enabled Mod with `--position` permits moving it; default toggles retain its relative slot. All resulting positions are normalized in the preview.

The plan binds database/config bytes, full logical database content/schema, new registrations, selected Mod inventories, application/profile and requested order. Apply checks known game/launcher processes, takes an exclusive SQLite transaction, commits a durable full-byte database/config backup, verifies the complete proposed logical result, atomically replaces configuration, then commits SQLite. A config-write or pre-commit failure restores the original state. Interrupted two-file writes can be reported as `partial`; known before/after combinations support guarded rollback. Post-commit/user edits are reported as `diverged` and never overwritten. Existing backup directories block resubmission. Rollback restores exact original database/config bytes. This is not a general SQLite migration repair facility; hot journals/unknown state need separate investigation.

Three exact application profiles are reviewed: **2025.12** with 27 migrations, **2026.12** with 36, and **2026.13** with 37. `launcher-inspect` reports the selected application, `schema_supported`, its own migration registry and `pairing_supported`. Automatic discovery selects the numerically newest installed version under the existing installation pointer; an unknown newer archive fails rather than falling back to an older reviewed binary. Missing applications leave pairing unassessed. A different known database schema remains readable for diagnostics but cannot be written with the wrong application profile. Plans and transactions recheck the application and profile source hashes.

For 2026.13, membership/order edits invalidate only the selected playset's `modConflictCompositionHash` and `modConflictResult`; the plan exposes these columns and rollback restores the original bytes. Official GUI persistence requires a separate corresponding-version check.

Obtain the launcher from the [official download page](https://www.paradoxinteractive.com/our-games/launcher), verify its publisher/signature, and use a reviewed application/schema/migration pair. CLI edits preserve unrelated fields; this does not add a DLC authoring command.

Protocol 7 additionally freezes every known effect-material link, its independent source/effect object bodies, exact CLB context and raw logs. Material findings cannot be hidden by zero diagnostics or matching material bytes; see [material connections](material-workflows.md).
