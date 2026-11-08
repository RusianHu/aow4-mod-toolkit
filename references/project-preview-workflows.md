# Fresh-project launcher preview

Use this for a new package identity. Existing identities and maintenance replacements use [maintenance preview](maintenance-preview-workflows.md). This tool verifies local installation and official launcher behavior, never gameplay or Steam publication.

```powershell
python "<skill>/scripts/project_preview.py" prepare "<release/manifest.json>" --destination "<real Mods/new preview directory>" --output "<new evidence directory>" --review-note "Launcher-only acceptance; the recorded content findings remain unresolved."
python "<skill>/scripts/project_preview.py" apply "<evidence directory>"
python "<skill>/scripts/project_preview.py" observe "<evidence directory>"
python "<skill>/scripts/project_preview.py" withdraw "<evidence directory>"
python "<skill>/scripts/project_preview.py" verify-withdrawal "<evidence directory>"
```

`prepare` requires a complete native build with no errors or data mismatches, an unused direct child of the actual local Mods directory, a package identity absent from all registered Mod rows, and a supported real launcher/database pairing. It binds the manifest and installation recipe, backs up SQLite/configuration, and records the original registration/membership state. Review-required content needs an explicit scoped note; the note never clears release findings.

`apply` appends one enabled member and retains all existing members. It refuses source, manifest, installation-plan, database or configuration drift before installation. Durable phases prevent blindly replaying an uncertain transaction. Inspect the installation and launcher receipts if a mutation stops between phases; there is no forced recovery or overwrite of later user edits.

`observe` explicitly opens the official launcher through `launcher_session`, using actual Steam settings and the actual user database on a separate inactive desktop. It never presses Play. It checks the candidate package identity, enabled path and exact original member/order retention after the application exits. Node.js 22+ is required; `--node` can select the executable.

`withdraw` removes only the registration owned by its receipt and rolls back the exact unchanged installed files. It does not restore a stale pre-restart database image. Original Mod rows, membership/order and configuration bytes must be restored; formatting-only configuration changes can be restored only after exact logical equality. Later user configuration values are retained and reported as a review failure. Cleanup can proceed if the original release has disappeared, because the installation/registration receipts own the recovery inputs.

`verify-withdrawal` opens the official launcher again and rechecks that the candidate is absent and the original rows/members/configuration remain. `status` reports the durable phase. These records document the observed lifecycle, not actual engine loading or a cryptographic attestation of the operating system.

The captured renderer shows a healthy playset and the changed total count. Its virtualized viewport does not directly capture the appended candidate row; package identity and enabled-path retention are independently checked from SQLite/configuration after the official session. Do not describe that archive as a direct visual inspection of the candidate row or game mounting.
