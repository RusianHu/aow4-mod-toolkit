# Actual launcher migration and Mod lifecycle acceptance

This is the actual user-profile workflow, distinct from the copied/GOG candidate fixture. It uses the selected reviewed official application, unchanged installed AoW4 Steam settings, actual user AppData and the real game-data database. It never presses Play, publishes, subscribes, signs in or accepts agreements. Node.js 22+ is needed only to observe the renderer.

```powershell
python scripts/launcher_session.py prepare --output "<new session directory>"
python scripts/launcher_session.py run "<session directory/session.json>"
```

Prepare checks the selected application hash/signature, all shared database migration names and journals, game/data paths, existing loose-file Mods, game launcher assets and helper hashes. It freezes original launcher database/configuration files and the current profile's `userSettings.json` / encrypted `session.js`. Keep these backups and raw logs private, outside the Skill Git repository and release payloads.

Run revalidates the complete plan and backups. It deny-write guards existing Mod files, the game launcher assets and other games' captured launcher state. The official process runs on a separate Windows desktop that is never activated; the real user profile and original Steam settings are retained. Steam is initialized for App ID 1669000, Steam game discovery is disabled, and HTTP proxies point to a refused loopback endpoint to avoid web updates/synchronization. Native Steam IPC remains available; this is targeted file protection, not an OS security sandbox. The official application may refresh its own actual database, profile and caches. No game executable is invoked by this workflow.

The renderer helper only observes state, skips an introductory onboarding control if present, and opens Playsets. A passing session needs healthy actual UI, both exact Steam initialization log markers, supported application/database schema and migrations, matching generated configuration, and unchanged protected inputs/runtime bytes. Migration count or an exit code alone is insufficient. Raw logs and renderer observations are retained; `after/` freezes tracked mutable state. Even a failed observation attempts a checkpoint after the process stops.

`attempt.json` is committed before starting. Completed/unknown attempts cannot be replayed. Check the process/session and saved state before preparing a new attempt. A transient main/child shutdown race is handled by waiting on observed process handles for at most five seconds; it never kills a separately observed writer or relaunches on an observation timeout. Inaccessible/live writers remain blocking. The Windows guard checks actual process synchronization state, following [OpenProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess) and [WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject).

## Independent installation probe

```powershell
python scripts/recipes/prepare_launcher_probe.py --output "<new project>"
aow4mod sync-plan "<project/release/manifest.json>" --destination "<game-data/Mods/CLI Launcher Integration Probe>" --backup "<new install backup>" --output "<install-plan.json>"
aow4mod sync-apply "<install-plan.json>"
aow4mod launcher-plan --enable "<installed probe>" --position 0 --output "<enable-plan.json>"
aow4mod launcher-apply "<enable-plan.json>" --backup "<new enable backup>"
# Prepare/run a fresh actual session and inspect its saved checkpoint/renderer.
```

The recipe creates a new package UUID from the native empty project, copies one reviewed reference-free City Riot leaf, removes unused pack imports and builds with native validation. There is no vanilla override or gameplay entry; it tests installation/lifecycle, not a new playable spell. Require ACP metadata and RPK roundtrip checks on the probe before installing it; preserve every diagnostic and review finding.

After reopening, compare exact path/package identity, enabled state, position, generated configuration and visibility in the official Playsets page. An old enable receipt normally diverges after legitimate official writes and must not be forced to roll back.

## Withdraw without leaving a registry entry

```powershell
aow4mod launcher-plan --disable "<installed probe>" --unregister "<installed probe>" --registration-receipt "<enable backup/receipt.json>" --output "<cleanup-plan.json>"
aow4mod launcher-apply "<cleanup-plan.json>" --backup "<new cleanup backup>"
aow4mod sync-rollback "<install backup/receipt.json>"
# Prepare/run another fresh actual session; verify the original list and files.
```

`--unregister` only removes a local registration created by the supplied original applied receipt in this database/playset. It requires unchanged Mod files and matching identity, proves the row was absent from the frozen original database, and rejects remote ownership, other playset/dependency references and triggers. Disable explicitly first, or include `--disable` atomically in the same plan. Existing transaction, complete logical-database comparison, configuration backup and exact rollback protect unrelated data. The command removes membership/registration only; use sync rollback to withdraw the installed files. Cleanup rollback is separately available while its current result remains unchanged.
