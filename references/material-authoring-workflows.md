# Material parameters and standalone textures

Use this for named material edits and ordinary PNG diffuse textures. Effect-pointer inspection remains in [material connections](material-workflows.md); FBX/model limits remain in [static meshes](mesh-workflows.md).

## Inspect and edit by parameter name

```powershell
aow4mod material-parameters "<Mod/Libraries/OWN.clb>" --locator '[OWN]MAT\0' --query Colorize
aow4mod material-plan "<Mod>" --library "<Mod/Libraries/OWN.clb>" --locator '[OWN]MAT\0' --values-file '<assignments.json>' --output '<plan.json>'
aow4mod material-apply '<plan.json>' --output '<new staged Mod>'
```

Example assignment file:

```json
[
  {"name":"Colorize","value":[0.2,0.65,1,1]},
  {"name":"UVOffset","value":[0.25,0.5]},
  {"name":"EnableShadows","value":false},
  {"name":"DiffuseTexture","library":"C:/path/to/Mod/Libraries/OWN_TEX.clb","texture":"[OWN_TEX]TX\\0"}
]
```

Names are exact and case-sensitive. The planner resolves the selected material's effect and reads its `TREConstantEPR`/`TRETextureEPR` declarations. Both the editor class and parameter type code must match a reviewed codec. Supported constants are bool, float, float2/3/4. RGB and RGBA can share an editor class, so their component count comes from the type code. Values are encoded as float32 with overflow/underflow/nonfinite checks; unused bytes in the 16-byte constant are preserved. Bool uses uint32 zero/one rather than float bits. Colors are supplied in the native material's numeric value space; the command performs no color-space conversion.

Only existing constant/texture expressions are editable. Dynamic expressions, unknown declarations and enum assignments remain unsupported and visible in inspection. Arrays of 1–32 unique assignments are accepted; missing/duplicate names, wrong vector sizes and no-op plans fail. Other material definitions and all unrelated YAML values remain unchanged.

Texture assignments require a texture already inside the source Mod. Targets are standalone `TRETextureResourceDEV` members in reviewed RGBA8/mip presets or [typed compressed DDS formats](compressed-texture-workflows.md). Atlas locators, missing members and unreviewed layouts fail. The Default effect's known normal parameters require Linear and diffuse parameters require sRGB. CLB imports are added atomically; all mip data hashes are bound. Unrequested maps stay unchanged.

Planning compiles through official code, reloads the resulting file in another process and checks actual material/effect and known static texture pointers against complete independent objects. Apply binds the complete Mod, context, declared types, requested changes, desired YAML, compiled/independently read files and raw link witnesses, and recomputes the texture graph from the binary. This verifies static texture links; it does not predict the selected shader feature mask or certify GPU upload/rendering.

## Create a standalone PNG texture

```powershell
aow4mod texture-plan '<Mod>' --image '<source.png>' --namespace OWN_TEX --output '<texture-plan.json>'
aow4mod texture-apply '<texture-plan.json>' --output '<new staged Mod>'
```

This creates `[OWN_TEX]TX\0` directly, preserving original dimensions without atlas packing or borders. Ordinary PNG constraints from [image workflows](asset-workflows.md) apply: one frame, at most 1024×1024, at most 8-bit channels, no unapplied color profile/orientation. Default output is a non-streaming single-level RGBA8 color texture. Explicit mip/normal modes are described below; compressed/cube/volume authoring and GPU upload remain outside scope.

The source PNG remains in `SourceImages/<namespace>/`. Official compilation and independent native file reload preserve the full document. Native PNG exports are compared pixel-for-pixel; actual CLB streams (bounded LZ4 or exact-sized raw small mips) are decoded independently. Updating corrupted binaries' hashes cannot hide changed pixels, including lower mip levels. Default builds include CLBs and exclude author PNGs/receipts. The default remains one sRGB mip; explicit color/data/XYZ-normal roles and full chains are available as described in [mip/normal workflows](texture-level-workflows.md).

## Reproduce a complete static asset candidate

```powershell
python '<skill>/scripts/recipes/prepare_static_asset_example.py' --output '<new project>' --fbx '<static.fbx>' --image '<diffuse.png>' --namespace OWN_STATIC --tint '[0.2,0.65,1,1]'
```

The reviewed example creates a blank native project, imports a PNUVCT52 static mesh, binds the Default material and single-LOD model, creates a standalone diffuse texture, sets the material tint and diffuse link, then replaces part 0 of original Drowned city-ruin component `00000452000014F2`. It uses the caller's FBX/PNG and the pinned installed game, with no dependency on a parent workspace fixture. Namespace length is limited to 25 characters to leave room for `_TEX`.

Material edits retain `SourceMaterials/<library>/.aow4mod-material.json` provenance. Builds retain the material variant review gate, and offline publication/frozen preparation reject missing provenance or a removed gate. Existing mesh/model draft gates remain because feature evaluation and rendering are still unverified; this workflow does not certify arbitrary materials, dynamic texture expressions, animation or a complete asset toolset.
