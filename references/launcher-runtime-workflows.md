# Reviewed launcher runtime and user-level activation

Use this workflow when the installed launcher is too old for existing game-data history. It preserves the system installation and can activate an exact reviewed official runtime under the user's writable Programs directory. It does not require UAC elevation, uninstall the shared launcher, clear settings, or remove migration records.

## Prepare and activate

Run from the Skill checkout on Windows. Obtain the official installer from the [Paradox launcher page](https://www.paradoxinteractive.com/our-games/launcher). Currently only the reviewed **2026.12** installer/archive and the existing **2025.1** bootstrapper are supported. `DISTRIBUTIONS` binds the installer, archive, application and bootstrapper hashes; another release requires review. There is no arbitrary hash override.

```powershell
python scripts/launcher_runtime.py unpack --installer "<official installer.exe>" --output "<new archive directory>"
python scripts/launcher_runtime.py clone --output "<new user-level installation>"
python scripts/launcher_runtime.py plan --archive "<archive directory/launcher.zip>" --installation "<new user-level installation>" --output "<runtime-plan.json>"
python scripts/launcher_runtime.py apply "<runtime-plan.json>" --backup "<new runtime backup directory>"
python scripts/launcher_location.py plan --runtime-receipt "<runtime backup/receipt.json>" --output "<location-plan.json>"
python scripts/launcher_location.py apply "<location-plan.json>" --backup "<new location backup directory>"
```

`unpack` checks the exact installer hash and publisher signature. It reads bounded outer CABs, uses Windows `expand -r` to retain embedded filenames, opens the MSI **read-only** to read its `cab1.cab` stream, and checks the extracted archive hash. It never executes the installer, installs the MSI, or runs custom actions. This uses Python's standard library and Windows components; no third-party unpacker is required. Unknown packages fail before extraction. The native command's [documented syntax](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/expand) and the actual bundle extraction are verified.

`clone` copies the current reviewed installation into a new directory and verifies every file. It leaves the original installation and active location unchanged. Existing destination directories are refused. The user-level location should be a stable directory outside this Skill checkout, game data and original installation. Do not replace an existing user's directory.

The runtime plan validates the exact ZIP contents, safe Windows paths, current bootstrapper, original application/version markers, and every game's existing launcher migration history under the shared game-data parent. Unknown future migrations, SQLite sidecars, non-DELETE journals, changed inputs and insufficient disk space block the operation. The candidate must be newer than every reviewed version in the clone. `--state-root` is available for isolated acceptance copies; the default is the existing Paradox game-data parent.

Runtime apply freezes the complete old installation, official archive, all captured launcher database/configuration files and any bound discovery pointer before atomically adding one new version directory. The backup must be on the same volume. Existing files, registry and databases are not changed by this command. Native bootstrapper controls confirmed that a newer valid installation marker is selected; a newer directory without the marker is ignored. These selection controls used deliberately invalid executables and do not by themselves prove healthy UI or successful game loading. Candidate UI evidence comes from the separate [official launcher acceptance](launcher-acceptance.md).

Location apply switches only `%LOCALAPPDATA%/Paradox Interactive/launcherpath` and the existing **HKCU** `SOFTWARE/Paradox Interactive/Paradox Launcher v2` / `LauncherInstallation` string value. Their original values must agree. It requires a complete, unchanged runtime receipt and an exact clone of the original system installation. The pointer is atomically replaced with a source-hash check; a failed second write restores only this operation's known registry/pointer values. Other registry values and Windows uninstall registration are preserved. This is a managed runtime/location activation, not a new MSI installation.

## Status and recovery

```powershell
python scripts/launcher_location.py status "<location backup/receipt.json>"
python scripts/launcher_location.py rollback "<location backup/receipt.json>"
# Only after the new user-level installation has been deactivated:
python scripts/launcher_runtime.py status "<runtime backup/receipt.json>"
python scripts/launcher_runtime.py rollback "<runtime backup/receipt.json>"
```

Rollback restores exact pointer bytes and the prior registry value/type, including a known interrupted two-part switch. A modified pointer, runtime, backup or shared game database blocks automatic recovery. Runtime rollback moves only the new exact version into its bound backup's quarantine; it never recursively deletes an installation. An installation activated after runtime planning must first be deactivated through its location receipt. The original system runtime remains available.

After an official application changes/migrates shared game data, a location can remain correctly selected while `runtime_state=diverged` and `automatic_rollback_allowed=false`. That prevents silently choosing an older launcher against newer database history. Review the full state backups and subsequent user changes before any coordinated recovery; do not manually delete records to force compatibility.
