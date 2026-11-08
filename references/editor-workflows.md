# Official editor bridge

As of 2026-11-08, the current Manta hotfix DLL resource core is reviewed, but the ResourceEd/ContentEd/LevelEd executable bridge profiles remain unreviewed. `editor-prepare` still refuses before copying/launching. Native DLL support and manual GUI use do not validate the bridge command line or collection path. See [current compatibility](../docs/MANTA_COMPATIBILITY.md).

Use the existing headless resource/asset commands first. The bridge covers preparation, process tracking and result collection when an operation needs the official GUI. It does not implement menu actions, FBX/TAM import or a batch save API.

```powershell
aow4mod editor-prepare "<Mod directory or ACP>" --editor content --output "<new session directory>"
aow4mod editor-start "<session>/editor-session.json" --show
aow4mod editor-status "<session>/editor-session.json" --limit 20
# Save inside the copied Mod and close all official editors before collecting.
aow4mod editor-collect "<session>/editor-session.json" --output "<new staged Mod>"
```

`--editor` accepts `content`, `resource` or `level`. The current executable and DLL hashes must match the reviewed Witch 1.1 profile. The confirmed arguments are `-modpath <parent Mods directory> -mod <child directory name>`; the common loader searches that child's top-level ACP. File-open/import/export flags have not been established. PackageManager is not exposed as a launch target.

Preparation copies every author file into `Mods/Working Mod`, records the source inventory and package UUID, and creates separate APPDATA/LOCALAPPDATA directories. Output inside the game or installed local/Workshop Mod trees is rejected. The working directory remains the game installation, as required by the official tools. **This is context separation, not an OS filesystem sandbox**: an interactive editor can still navigate to other accessible files. Keep all saves inside the working copy.

`editor-start` is an explicit GUI launch. Without `--show` it requests a hidden window; an editor may control its own visibility. Starting is attempted once per session, with a durable pre-launch record, stdout/stderr logs and a PID/executable/creation-time identity. It never sends UI input or terminates a process. All known official editors must be closed first. Opening secondary editors from a menu is not tracked as an owned child, so collection requires all known official editors closed as well.

Repeated or concurrent launch attempts are refused. `starting`/`unknown` means the launch outcome needs manual examination; do not delete the receipt and blindly repeat. An interrupted operation can retain `editor-operation.lock`; examine the running processes and logs before manually clearing a stale lock. No automatic recovery, adoption or forced close is provided. PID reuse is reported separately and never authorizes acting on the replacement process.

Status is read-only, paginates added/removed/modified files and reports whether the original source changed. Collection requires the reviewed tool installation and original source inventory still to match. It checks package identity, ACP/RPK parsing, duplicate local resource IDs and CLB envelopes, copies to a **new** Mod stage, then checks the inventories and process state again before committing. Missing imports/unregistered RPKs remain findings. TAM/CAM/SRM/ENV/FBX files are explicitly opaque. The CLI does not select the result as the project's source, install it or publish it; review the diff, use `project-use`, and run the normal native build/release gates as appropriate.
