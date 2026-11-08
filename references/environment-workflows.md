# Mod environment checks

```powershell
aow4mod dlc --steam
aow4mod dependencies --mod "<Mod>" --depth 2 --limit 10 --output "<new environment.json>"
aow4mod dependencies --playset "<content_load.json>" --steam --max-resources 10000 --limit 10
aow4mod dependencies --mod "<Mod>" --disabled-dlc ELDRITCHREALMS --section resource_references --limit 10
aow4mod dependencies --mod "<Mod>" --section import_cycles --limit 10
aow4mod dependencies --playset "<content_load.json>" --section attribution --max-resources 20000 --limit 10
```

These commands inspect only. `--disabled-dlc` changes the analysis scenario, not the launcher configuration. Without any `--mod`, dependencies reads the active launcher playset. With explicit Mods it inspects exactly that set; `--playset` includes the chosen playset as context. Exact paths already in the playset are not added twice by `--mod`. Keep the original game resource index current with `index`; its binary hashes and parser version are checked.

Default output is a compact summary and paginated issues. Other `--section` choices: `attribution`, `resources`, `resource_references`, `pack_dependencies`, `asset_references`, `import_cycles`. Full reports can be large; do not load them wholesale into the agent context. `--depth` defaults to 2 and `--max-resources` to 2000. Root resources count toward the cap. The report states truncation and unvisited references at the depth boundary; reaching a cap is review-required.

## What is established

- Selected definitions come from ACP overrides and registered/dependent local RPKs. The report can identify targets present only in indexed but unselected Mods. It does not activate them.
- A unique selected Mod definition is shown as a candidate overriding an original. Multiple differing selected definitions remain ambiguous; no engine precedence is invented. Identical definitions are reported separately.
- Original RPK import lists include CLBs, environments and localization files as well as RPKs. Broad original imports can include stale editor declarations; they are kept as advisory findings rather than equated with direct gameplay requirements.
- Resolved RPK imports are checked for cycles independently of resource references. Each cyclic component reports its files and a concrete closed path; self-imports also fail. Cycles enter the build review gate even when every target exists. Hash-identical aliases are collapsed, while ambiguous/missing required targets remain unresolved and prevent a dependency-first order. The graph includes ACP per-pack additive declarations and the RPK's original/Mod required/optional lists. Missing optional imports are advisory and are absent from the graph; present optional targets can still form cycles. New required ACP imports are not treated as inherited original advisory findings. The report does not treat ordinary resource-reference cycles as pack-import errors. This follows the circular-dependency warning on page 14 of the [official Modding Guide 1.3](https://forumcontent.paradoxplaza.com/public/1271140/Age%20of%20Wonders%204%20-%20Modding%20Guide%20-%20V1.3.pdf). Effective engine mounting remains a separate check.
- Official `Launcher/dlc/*.dlc.json` maps `id.paradox` to content-directory names and `id.steam` to Steam IDs. Folder names alone do not prove ownership. `--steam` uses [ISteamApps.BIsDlcInstalled](https://partner.steamgames.com/doc/api/ISteamApps#BIsDlcInstalled), which checks owned-and-installed state in the current Steam client. Disabled launcher IDs are reported separately; unmapped bonus content stays unknown.
- Recognized asset locators such as `[GAMEPLAY_ICONS]TXA\51` are checked for their CLB file. Root content URIs and explicit asset-reference maps are checked against loose files. CLB member indices, archived/virtual files, engine fallbacks and actual runtime use remain unverified.
- `TFXResource` (`00730034`) stores its observed effect graph in metadata `0x1E`; the bounded asset scan includes that explicit carrier as well as gameplay class scopes. It reports the real `meta/0x1E/...` paths. Developer names and unrelated/unknown metadata carriers are not scanned as graph declarations. Seventeen original resource definitions across five examined packs expose the known System_Effects model through this path; this is reference discovery, not effect execution or editable graph semantics.
- Distinct `.mo` language directories are reported as localized variants, without choosing a language. Identical duplicate asset files are verified by hash. Different same-name files remain ambiguous.

Missing assets retain `inherited_from_original` when their exact path and resource property already occur in the original definition. This avoids blaming the Mod for introducing the path, but does not prove the path is harmless. Ordinary filename-like strings without a recognized reference context remain candidates. The report records evidence levels and does not convert these checks into gameplay certification.

## Build behavior

Every build writes `environment.json` and binds its hash and summary in the release manifest. It uses the build payload plus explicitly configured dependency Mods, not an implicit global playset. The publisher rejects modified environment evidence or a release gate that hides review-required findings. See the `validation` configuration in [authoring-workflows.md](authoring-workflows.md).

## Finding ownership and original references

`--section attribution` returns paginated groups for selected Mod sources, installed-game sources and unavailable/ambiguous ownership. Full reports retain every original issue and add an `attribution` object with issue indices and group counts. `maintenance-status` additionally reports the captured primary Mod's counts in `environment_attribution`, without expanding all issue rows or changing the maintenance ledger.

This is source ownership, not proof that another Mod cannot affect the primary Mod. Roots are compared as paths, not text prefixes; overlapping selected roots and old Mod-added imports lacking their declaring ACP stay ambiguous/unavailable. New ACP import findings retain `declared_by` so an import added to an original game pack is attributed to the declaring Mod rather than blamed on the original pack.

Resource references now have `inherited_from_original` when the same property and target ID occur in the uniquely matched original of the same resource type. Asset inheritance still requires the same property and path. Matching an original reference does not prove its runtime use is safe. Missing original proof does not prove a reference was introduced by the author. No attribution/inheritance result clears an environment or publication gate; scan limits and unknown ownership remain visible.

Native diagnostic control: original-only read/save gives Mayhem57 and Corruption77 lines. The same official ACP application with all four resource bodies restored exactly to the originals gives57/78, matching the example Mod reports with no additional diagnostic text. Thus135 occurrences reproduce without the author's field changes; the extra Corruption atlas message is operation-dependent and must not be incorrectly attributed to the Mod by comparing different operations. This does not prove those diagnostics harmless. Both raw logs and review requirements remain.
