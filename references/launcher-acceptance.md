# Isolated official launcher acceptance

Use this developer workflow when a candidate launcher must be checked against a user's existing database. It turns the previous one-off launcher experiments into a repeatable command. It does **not** install the launcher or migrate the actual database.

From the Skill checkout, using Python 3.10+ and Windows with Node.js 22+ on PATH:

```powershell
python scripts/launcher_acceptance.py prepare --database "<user-data/launcher-v2.sqlite>" --game "<AoW4 installation>" --launcher-asar "<reviewed launcher/resources/app.asar>" --probe-mod "<separate built test Mod>" --output "<new fixture directory>"
python scripts/launcher_acceptance.py run "<fixture directory/fixture.json>"
```

Both commands emit a short JSON result. Read `result.json` in the fixture for phase results and evidence paths. Each phase retains raw launcher logs and renderer observations; the fixture retains source hashes, copied Mod inventories, the application/profile binding, host inventory and original migration history. Failed or completed attempts cannot be replayed; prepare a new directory. Do not copy private fixture data into this repository or a Workshop package.

`prepare` copies the database, generated configuration, all registered loose-file Mods, the optional separate probe and the game's launcher assets. It retains migration records, redirects Mod paths to copied files, disables real game executables, and selects a GOG/offline fixture with Steam discovery/synchronization disabled. It refuses archive Mods, external UGC, unsupported paths, unknown application hashes, invalid publisher signatures, overlapping outputs and excessive inputs. The optional probe must have valid declarations and a package identity distinct from enabled content. The existing toolkit core remains standard-library-only; Node is required only for this acceptance workflow.

`run` rechecks application and source hashes and independently checks isolation paths. During the official process it holds deny-write handles on the original input files. The official launcher runs on a separate Windows desktop that is never activated, with private AppData directories and blocked HTTP proxies. Loopback debugging reads the actual primary renderer, skips only the disposable onboarding and opens Playsets; it never presses Play, signs in, subscribes, uploads, or accepts legal agreements. The source Mod files remain protected; this is targeted redirection and locking, not an OS security sandbox for arbitrary applications.

The complete process opens the official launcher twice. The first opening performs the official migrations in the copy. If a probe was supplied, existing `launcher-plan/apply` registers and enables its copied directory. The second opening checks state persistence and generated-config consistency. A fresh disable/rollback after reopening must restore exact bytes; the earlier receipt is not used to overwrite application changes. UI readiness uses the reviewed renderer's control IDs/routes and raw logs, independently of English or Chinese labels. A disabled Play button is expected when the fixture's game executable is deliberately absent.

Healthy UI requires a loaded AoW4 primary renderer with its game control and Mod navigation, no captured script exceptions, and no initialization/translation failure in the raw logs. Process exit alone, successful migration alone and `home_ready` alone are insufficient. The first attempt is retained if any check fails. Readiness is limited to this observation interval and does not certify all launcher screens or network services.

This fixture intentionally substitutes local copies and the distribution context. Its result always reports `production_pairing_verified=false` and `gameplay_tested=false`. It cannot close R07's actual installed-launcher/Steam-context acceptance, authorize a machine-wide upgrade, or replace installation backup and migration review.
