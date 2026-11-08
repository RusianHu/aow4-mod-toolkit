# AoW4 Mod Toolkit

[简体中文](README.md)

A project-scoped Codex Skill and Python CLI for creating, inspecting, editing, building, and maintaining Age of Wonders 4 Mods. The current source version is **0.4.0.dev8, a development preview**. It supports scoped authoring and maintenance workflows alongside the official editors; it does not yet replace their full functionality. Published installation packages have their own release manifests and may lag behind the source.

**Current compatibility, reviewed on 2026-11-08:** the resource core, schema extraction, and scoped gameplay explanations are available for the installed **Manta 1.1 Hotfix, Steam Build 26482553**. The Paradox Launcher **2026.13** CLI application/database pairing is supported. Native asset and map operations and official editor bridges remain unreviewed for this build. See the [compatibility report](docs/MANTA_COMPATIBILITY.md) for evidence and limits. `doctor.readiness` and `capabilities.current_environment` describe the local installation; they do not certify Mod content or gameplay.

| Topic | Start here |
| --- | --- |
| Agent instructions and task routing | [SKILL.md](SKILL.md) |
| Current capabilities and remaining limits | [Current status](docs/STATUS.md) |
| Installation, updates, and multiple computers | [Deployment](docs/DEPLOYMENT.md) |
| Requirements, roadmap, and development conventions | [Requirements](docs/REQUIREMENTS.md), [roadmap](docs/ROADMAP.md), [AGENTS.md](AGENTS.md) |
| All workflows | [Workflow index](references/workflow-index.md) |
| Repository publication assessment | [Public release review](docs/PUBLIC_RELEASE_REVIEW.md) |

Detailed documentation is currently a mix of Chinese and English. The default README remains Chinese.

## Workshop Mods published or maintained by the author

- [Create an intruder](https://steamcommunity.com/sharedfiles/filedetails/?id=3366718295)
- [大地崩解：地下基岩 | Earth Shatter: Bedrock](https://steamcommunity.com/sharedfiles/filedetails/?id=3825464145)
- [开局种族转换·中文拓展版 | Starting Transformations CN](https://steamcommunity.com/sharedfiles/filedetails/?id=3826138175)
- [龙族转化能飞 | Draconian Transformation Can Fly](https://steamcommunity.com/sharedfiles/filedetails/?id=3828760081)

These are separate Workshop projects. Consult each page for its dependencies, supported game versions and attribution.

## What it supports

- Content discovery by Chinese or other indexed text, localization key, internal name, resource ID, and DataStoreTag, with bounded reference tracing.
- Hash-bound plans and fresh staging directories for resource edits, selective field restoration, non-overlapping upstream migration, and dependency review.
- Native construction, scoped scalar and income edits, independent readback, RPK serialization checks, and explicit ACP overlay application within reviewed profiles.
- Localization, build manifests, frozen validation evidence, local installation and rollback, launcher state, and separate Steam Workshop preflight and publishing workflows.
- Historical, limited asset and map workflows whose current availability must be checked separately. See the compatibility report before using native operations.

Unknown bytes, original diagnostics, unresolved references, and unevaluated cases remain visible. Static checks, native host validation, effective package application, and gameplay acceptance are separate claims. Workshop submissions and visibility changes require authorization for the specific action. This toolkit does not handle Cheat Engine tables or game-memory cheats.

## Getting started on another computer

If access is restricted, sign in on that computer with an authorized GitHub account using `gh auth login`. Do not copy credentials from another computer.

Run the following in the target Mod workspace, using PowerShell:

```powershell
# Keep the actual Skill checkout inside the Mod workspace.
$skillDir = Join-Path (Get-Location).Path '.agents/skills/aow4-mod-audit'
gh repo clone RusianHu/aow4-mod-toolkit $skillDir -- --branch main

# Keep the virtual environment and caches out of the distribution.
$runtimeDir = Join-Path $env:LOCALAPPDATA 'AoW4ModToolkit/venv'
py -3.10 -m venv $runtimeDir
$pythonExe = Join-Path $runtimeDir 'Scripts/python.exe'
& $pythonExe -m pip install -e "${skillDir}[localization,analysis,assets]"

# Check this computer before starting native work.
& $pythonExe "$skillDir/scripts/aow4_mod_audit.py" doctor
& $pythonExe "$skillDir/scripts/aow4_mod_audit.py" native-status
& $pythonExe "$skillDir/scripts/aow4_mod_audit.py" capabilities authoring
```

Keep the entire checkout at `<Mod workspace>/.agents/skills/aow4-mod-audit`, including `SKILL.md`, `agents`, `scripts`, `references`, and `tests`. The directory is both the project Skill and its independent Git repository. Develop and update this one copy; keep Mod sources, staged output, builds, and private evidence in the Mod workspace. You can invoke the Skill explicitly as `$aow4-mod-audit`.

For a fixed baseline, check out an appropriate published tag and review its version-specific limits. A shared user-level installation at `~/.agents/skills` is an option when explicitly needed across projects; avoid duplicate project and user installations with the same name. Every computer needs its own Python environment, game/editor installation, account access, and environment checks.

For a Release ZIP, extract the entire `aow4-mod-audit` directory into the project's `.agents/skills` directory. ZIP installations contain no Git history. Replace them with a new directory when updating; use a Git checkout for ongoing development.

## Version-sensitive tooling

**Game, editor, launcher or SDK updates can require another compatibility adaptation, even for a minor hotfix.** Support binds exact binary hashes and specific operations, not every Mod or a matching display version. Unknown binaries remain refused. Never just replace a hash and reuse unreviewed offsets. After updating, run `doctor` and `native-status`, regenerate indexes/schema and plans, then verify the workflows you need. See the [version workflow](references/game-version-workflows.md).

## Requirements

- **Core inspection:** Python 3.10 or newer, using the standard library. The current regression environment is Windows with Python 3.10.
- **Native editing and validation:** Windows x64 and your own installed copy of Age of Wonders 4 with matching, reviewed editor/SDK binaries. Witch historical profiles and Manta resource profiles have different scopes. Check `native-status.available_operations`; unknown binaries are refused.
- **Optional Python packages:** the `localization`, `analysis`, and `assets` extras are pinned in [pyproject.toml](pyproject.toml). Install all extras for the full synthetic test suite.
- **Other tools:** launcher GUI acceptance scripts require Node.js 22 or newer. Actual Steam Workshop operations require the local Steam account and appropriate item permissions. Texture compression, shader analysis, and live version queries use separately prepared optional tools described in their workflow references.

After a game/editor or toolkit update, rerun the environment checks, regenerate the schema and relevant indexes, and recreate stale plans. The same source version does not establish that another machine or another Mod has passed validation.

## Distribution and development

Repository: [RusianHu/aow4-mod-toolkit](https://github.com/RusianHu/aow4-mod-toolkit). `main` is the distribution baseline.

Original toolkit code and documentation are licensed under [MIT](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for dependency licenses and the proprietary game/SDK boundary. This is independent community tooling, not an official product. Distribution policy is described in the [public release review](docs/PUBLIC_RELEASE_REVIEW.md). Game files, official DLLs/SDKs, third-party executables, credentials, and the author's private acceptance data are not included in the installation package.

Build a checked ZIP and SHA-256 manifest from a clean, committed checkout:

```powershell
python scripts/package_skill.py --output "<new directory outside the checkout>"
```

Public materials retain sanitized methods rather than local debugging records or private cases. Databases, native caches and receipts stay in the user's private workspace. Repository history has been sanitized for distribution: reclone pre-cleanup copies rather than merging old branches or tags back into the clean baseline.

Run the synthetic and isolated tests from the Skill repository:

```powershell
python -m unittest discover -s tests -p 'test_*.py'
```

GitHub CI has no installed game. Passing CI does not establish gameplay, editor GUI behavior, Steam publishing, save compatibility, or multiplayer compatibility. See the [current validation record](docs/VALIDATION.md) for the exact scope.
