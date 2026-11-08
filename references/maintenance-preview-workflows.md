# Same-package launcher preview

Use this after a context-bound maintenance build to verify actual launcher registration and withdrawal **without changing the package UUID**. It uses the full actual playset, original Steam settings and user profile; it never presses Play or submits online content. Production Mod files stay protected and are never a synchronization destination.

```powershell
python scripts/maintenance_preview.py prepare "<maintenance workspace>" --destination "<game-data/Mods/new preview folder>" --output "<workspace/previews/new job>" --review-note "Launcher metadata/lifecycle only; unresolved findings remain."
python scripts/maintenance_preview.py apply "<preview job>"
python scripts/maintenance_preview.py observe "<preview job>"
python scripts/maintenance_preview.py withdraw "<preview job>"
python scripts/maintenance_preview.py status "<preview job>"
```

Prepare requires a current actual playset context, a matching native-validated candidate with zero native errors/data mismatches, a uniquely enabled exact original path, and a new distinct destination directly under the actual Mods directory. It freezes database/configuration baselines, Mod rows/member order, origin inventory, installation recipe and candidate identity. Review-gated candidates require a scoped note; it is **not** a release/compatibility approval and does not clear diagnostics. Evidence stays inside the maintenance workspace.

Apply rechecks original/candidate/recipe and live database/configuration hashes before installing. A normal guarded launcher transaction disables the original path, registers/enables the candidate at the original all-member position and preserves other enabled paths. It does not rewrite either UUID or use an empty playset. Each transaction has its own receipt and the job records the reached boundary; replaying a started job is refused.

Observe prepares and runs a fresh actual `launcher_session` with existing Mod files/other games protected. It requires a healthy UI and actual Steam initialization, then checks that the original is disabled, the candidate is enabled with the **same** UUID, and enabled paths equal exactly one path replacement in the original sequence. UI title alone cannot distinguish two variants with the same name, so database/path/configuration witnesses are required.

Withdraw creates a **fresh** transaction after official writes: re-enable the original at its saved position, disable and unregister only the receipt-owned candidate, then roll back installation. It does not force an old database snapshot over new state. Expected members/order/identities, original Mod rows, configuration values and original files must match; the candidate directory must disappear. The original configuration bytes, including unknown per-entry fields/BOM/formatting, are restored only under a hash guard when every unrelated value is unchanged and the original entry merely lost its captured keys during the owned disable/re-enable lifecycle. Unrelated user changes remain preserved and reported for review.

Official `mods_dependencies` and `metadata_relationships` include logical associations without SQL foreign keys. Unregistration checks these actual row-ID associations as well as declared foreign keys; shared package metadata is not deleted. Other-playset/dependency/remote ownership, edited Mod files, triggers and later user edits continue to block unsafe removal.

Interrupted `installing`/`enabling`/`withdrawing` boundaries require inspecting the corresponding exact receipts; do not restart solely because an observation expired. An unsuccessful observation can be withdrawn once writers are authoritatively stopped. The helper does not provide a general forced recovery/downgrade facility.
