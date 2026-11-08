# Runtime, repository and development

The Skill directory is a self-contained local Git repository. Its runtime does not depend on a particular parent-directory depth or the original CE workspace.

When developing this toolkit, read [requirements](../docs/REQUIREMENTS.md), [roadmap and progress control](../docs/ROADMAP.md), and [current status](../docs/STATUS.md). These are the maintained product-development documents in this repository; ordinary Mod tasks do not need them. Update status against user-facing acceptance criteria, not just individual native-interface experiments. Original reports and case material remain private, outside the repository.

```text
aow4-mod-audit/
  SKILL.md                     discovery, routing and shared constraints
  agents/openai.yaml           Codex UI metadata; implicit use remains enabled
  scripts/aow4_mod_audit.py     direct Python CLI entry; no package installation needed
  scripts/aow4mod/              canonical shared backend
  scripts/recipes/              reviewed authoring examples
  references/                  task-specific workflows
  docs/                        requirements, roadmap and current development status
  tests/                       synthetic core regression fixtures
  pyproject.toml               optional CLI packaging and dependency groups
```

Use Python 3.10+ from the target workspace's environment. Core commands need only the standard library. Optional localization, analysis and asset dependencies are declared in `pyproject.toml`; install only the extras needed for the task. Native commands additionally require 64-bit Windows and an installed, hash-matched AoW4 editor DLL/SDK. The game and its binaries are external dependencies and are not bundled.

From any working directory:

```powershell
python '<skill directory>/scripts/aow4_mod_audit.py' doctor
python '<skill directory>/scripts/aow4_mod_audit.py' capabilities authoring
python '<skill directory>/scripts/aow4_mod_audit.py' --version
```

`doctor.readiness` and `capabilities.current_environment` inspect the selected installation without loading native code. They distinguish resource-operation support, extraction dependencies, curated gameplay rules, editor executables and the launcher app/schema pair. The command catalog is not a promise that all operations work on that machine; use `native-status.available_operations`.

Workflow references use `aow4mod` as shorthand for that command prefix. An optional `python -m pip install -e '<skill directory>'` installs the equivalent `aow4mod` console entry into the selected Python environment. Run operations from the intended workspace: relative inputs, caches and new output paths are relative to the current working directory, not the Skill repository.

Run core tests from the Skill root:

```powershell
python -m unittest discover -s tests -p 'test_*.py'
```

Tests use synthetic files and mocked mutation transports. Keep one canonical backend in `scripts/aow4mod`; workspace adapters must delegate to it rather than maintaining another copy.

For the full suite, use an environment with all optional extras: `python -m pip install -e '<skill directory>[localization,analysis,assets]'`. The portable-entry tests copy the runtime to a temporary directory and run it without the original workspace or `PYTHONPATH`.

The original `tools/aow4_workshop_update.py` and production-specific release/repair recipes are workspace compatibility helpers. The reusable Incite authoring recipe is bundled at `scripts/recipes/prepare_cli_incite_example.py`.

Keep bytecode, virtual environments, generated caches, private research evidence and release output out of this repository. The source repository is RusianHu/aow4-mod-toolkit; the Skill directory/name remains `aow4-mod-audit`. See README.md and docs/DEPLOYMENT.md for installation and updates. Source publishing never authorizes Steam publication.
