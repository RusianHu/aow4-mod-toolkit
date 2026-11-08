# Steam game version scope

**Game, editor, launcher and SDK updates are version-sensitive, including hotfixes.** Toolkit release numbers and Steam BuildIDs describe different inputs. An existing profile is not a promise of forward compatibility.

## After an update

1. Run `doctor`, `native-status` and, when needed, `game-version`. Separate the installed-file observation from a fresh Steam branch query. Inspect `available_operations`, not just whether a profile name exists.
2. Keep unknown native binaries refused. To add support, review the current exports/private entrypoints, object layouts, serializer/default rules and required context, then validate each operation with isolated positive/negative controls and independent readback. Replacing a hash without this review is not an adaptation.
3. Rebuild the relevant resource/localization indexes and regenerate the schema for the reviewed ResourceEd/extractor pair. Preserve the chosen Mods and languages. Assess upstream resource changes against an appropriate baseline; regenerated caches do not prove an old Mod remains compatible.
4. Recreate hash-bound plans and validation evidence. Check requested values, identity, unselected fields, RPK preservation and ACP preparation/application as applicable. Keep diagnostics, unexpected changes and unevaluated cases gated; static/native success does not establish gameplay.
5. Review launcher application/schema/migrations separately. Do not force an older configuration onto new migration history or delete records to make a check pass. Test actual installation/loading/UI only in the scope required for that task.

## Installed and remote version commands

`game-version` separates the installed branch/build, a saved metadata comparison, a fresh anonymous SteamCMD query and native ABI support. Build equality is not Mod compatibility or file-integrity certification.

```powershell
aow4mod game-version
aow4mod game-version --capture "<saved app_info_print stdout.log>" --output "<new report.json>"
aow4mod game-version --steamcmd "<private tool folder>/steamcmd.exe" --evidence "<new query folder>" --timeout 120
```

The live mode currently requires Windows and a valid Valve Authenticode signature before and after execution. The executable must be outside the game/Steam library and local Mods. Prepare SteamCMD separately using the [official Valve download](https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip); this command does not install it. SteamCMD can self-update its own runtime/cache, so use a dedicated tool directory. This is not system-level filesystem isolation. No login credentials are requested or stored; the fixed command is anonymous login, forced app-info refresh, app-info print for1669000, then quit. It contains no game update, branch switch or Workshop submission command.

`--evidence` is required for live mode and must be a new directory. It preserves the installed manifest snapshot, query/signature record, raw stdout and result. Query failure/timeout keeps diagnostic files and never claims the installed game is current; no automatic retry occurs. A configured timeout terminates that command's owned process. Initial self-update can require a longer first run; inspect actual process state before any later retry. Do not confuse an observation timeout with a completed query.

Without a remote source, the result is `installed_version_only`. Captured stdout is always marked `online_query_verified=false` and `server_latest_verified=false`, even if it contains a successful login and a matching BuildID. A fresh successful forced-refresh query may set `server_latest_verified=true` only when the installed/requested branches and BuildIDs match, StateFlags is exactly4 and TargetBuildID is0. No BetaKey means the public/default branch. Conflicting branch settings, missing branch metadata, wrong AppID, duplicate keys/blocks, truncation and invalid BuildIDs are rejected. Input text is bounded to8MiB and parsed as nested KeyValues rather than selecting the first BuildID with a regex.

Exit0 means the live version comparison matched in its stated scope; exit1 means mismatch, pending install or no fresh online proof; exit2 means invalid input/query failure. `native_backend.profile_supported` is a separate hash-bound editor ABI fact. Neither result certifies complete installed files, DLC access, runtime resource mounting, rendering or gameplay.

Valve documents [app_info_print and appmanifest install-state files](https://partner.steamgames.com/doc/sdk/uploading#Debugging_Build_Issues). Do not equate the [UpToDateCheck API](https://partner.steamgames.com/doc/webapi/ISteamApps#UpToDateCheck) generic version parameter with a branch BuildID without corresponding evidence.
