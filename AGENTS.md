# Toolkit development

- Read docs/REQUIREMENTS.md, docs/ROADMAP.md and docs/STATUS.md before changing product scope. Current status is authoritative. Keep local debug/acceptance records outside this repository; retain only sanitized reusable methods in public documentation.
- Keep SKILL.md concise and task-routed. The sole backend is scripts/aow4mod; do not add a copied second backend or require the original parent workspace.
- Keep each cross-project lesson in its matching reference/checks page. SKILL.md routes to it; project notes retain identities, version-bound evidence and acceptance limits. Revise the existing owner instead of duplicating a checklist or appending a session diary.
- Preserve staged plans, source hashes, unknown bytes, original diagnostics and explicit unevaluated states. Native callbacks/markers alone are not success. Do not normalize away a failing comparison.
- Use synthetic fixtures for tests. Do not use installed game, production Mods, real launcher databases or Workshop items as mutation-test outputs. Real native/UI/Steam checks require their corresponding environment and task scope.
- No automatic agent delegation; honor the user's instructions. GitHub source publishing does not authorize Steam content submission.
- Keep runtime source LF: several backend/generator fingerprints bind exact source bytes. Verify an independent committed export after native-facing changes.
- Do not commit game binaries, tools, user/Steam credentials, private caches, receipt databases or authoring output. Optional runtime dependencies belong in pyproject.toml.
- For routine verification run python -m unittest discover -s tests -p 'test_*.py' with all dependency extras. CI has no game. New tests should cover meaningful behavior/invariants.
- Use codex/<topic> branches for new development. Update STATUS with the actual user outcome and remaining limits; keep historical logs private rather than appending them to public docs. No overall completion percentage inferred from test/command counts.
- scripts/package_skill.py builds the distribution only from a clean committed repository, outside its checkout. Install ZIPs are not editable Git checkouts; use Git for development.
