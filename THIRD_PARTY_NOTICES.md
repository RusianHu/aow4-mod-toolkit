# Third-party notices and scope of the MIT license

The [MIT license](LICENSE) covers the original toolkit source and documentation. It does not grant rights in Age of Wonders 4, Paradox/Triumph products, official editors or SDKs, Steam, Autodesk components, or third-party dependencies. This is an independent community project, not an official product or an endorsement by those vendors. Product names identify compatibility and remain the property of their respective owners.

## Separately installed Python dependencies

The repository and Skill ZIP do not vendor these packages. Python package installers obtain them separately; retain the license files supplied with those distributions when redistributing an environment. Version pins are in [pyproject.toml](pyproject.toml).

| Package | Declared version | Upstream license/source |
| --- | --- | --- |
| polib | 1.2.0 | [MIT; package metadata](https://pypi.org/project/polib/1.2.0/) |
| Capstone | 5.0.7 | [BSD-3-Clause](https://github.com/capstone-engine/capstone/blob/5.0.7/LICENSE.TXT) |
| pefile | 2023.2.7 | [MIT](https://github.com/erocarrera/pefile/blob/v2023.2.7/LICENSE) |
| PyYAML | 6.0.3 | [MIT](https://github.com/yaml/pyyaml/blob/6.0.3/LICENSE) |
| Pillow | 12.3.0 | [MIT-CMU](https://github.com/python-pillow/Pillow/blob/12.3.0/LICENSE) |
| setuptools | Build requirement, >=61 | [MIT and component notices](https://github.com/pypa/setuptools/blob/main/LICENSE) |

Upstream packages may carry additional component notices. The table is an inventory, not a replacement for their complete distribution licenses.

## Game, editor and tool dependencies

Use a lawfully obtained local installation of the game and required editors/SDKs. Official binaries, game assets, extracted resource corpora, Steam credentials, launcher databases, and local debugging/acceptance records are not distributed by this project. Optional Node.js, SteamCMD, DirectXTex, DXC, FBX SDK and similar tools must be obtained separately under their own terms.

The [Paradox User Agreement](https://legal.paradoxplaza.com/eula) and [AoW4 EULA listed by Steam](https://store.steampowered.com/eula/1669000_eula_1) describe rights and restrictions for the game and related content. The toolkit's MIT license does not override them, authorize circumvention, or establish an additional license to proprietary code. The presence of a hash-bound private ABI profile is a technical compatibility statement, not vendor approval or a legal determination about every use or jurisdiction.

Native calls run only against reviewed binary hashes and supported operations. Game, editor, launcher or SDK updates can invalidate that support, including minor hotfixes; see the [version compatibility workflow](references/game-version-workflows.md). Third-party rights and native compatibility are separate checks.
