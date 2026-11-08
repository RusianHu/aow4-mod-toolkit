# Mip chains, numeric textures and XYZ normal maps

```powershell
aow4mod texture-plan '<Mod>' --image '<diffuse.png>' --namespace OWN_COLOR --role color --mips full --output '<color-plan.json>'
aow4mod texture-plan '<Mod>' --image '<normal.png>' --namespace OWN_NORMAL --role normal-xyz --mips full --output '<normal-plan.json>'
aow4mod texture-apply '<plan.json>' --output '<new staged Mod>'
```

The existing PNG constraints apply. Roles are explicit: `color` uses the official sRGB color space (enum value 2); `data` and `normal-xyz` use Linear (value 1). These values were checked against the installed native enum and original diffuse/normal textures. Defaults remain `color` and `--mips none` for existing callers.

`full` includes the unchanged source image and halves each dimension with integer truncation until 1×1, clamping each axis to at least one. For example, 9×7 produces 9×7, 4×3, 2×1, 1×1. The filter uses exact integer area-overlap weights so non-power-of-two edges are not discarded. It does not pad the image to a power of two or pack it into an atlas.

## Role-specific filtering

- **color**: decode RGB from sRGB into linear light, filter premultiplied RGB and alpha over the footprint, unpremultiply, then encode RGB into sRGB. Fully transparent lower-level footprints become zero RGBA; hidden source RGB is retained in the unchanged base level.
- **data**: filter all four channels independently as linear numeric values. Alpha is data and does not attenuate RGB.
- **normal-xyz**: interpret RGB as full signed XYZ (`channel/127.5 - 1`), normalize source directions for filtering, area-average vectors and renormalize generated directions. Alpha is averaged independently. Base pixels remain unchanged. Degenerate/out-of-range input directions and exact cancellation are rejected rather than replaced with an arbitrary normal.

The normal mode preserves the caller's chosen axis convention; it does not flip Y, reconstruct a missing channel, interpret BC5/DXT5 layouts, derive normals from height maps, adjust roughness/normal variance, or preserve alpha-test coverage. It is a defined RGB-XYZ authoring algorithm, not certification that every shader interprets that encoding. Material/model rendering gates remain.

The filtering principles were cross-checked with Microsoft's [DirectXTex filter documentation](https://github.com/microsoft/DirectXTex/wiki/Filter-Flags), which distinguishes sRGB conversion, alpha-as-data handling and signed-normal conversion. Level-count conventions follow [GenerateMipMaps](https://github.com/microsoft/DirectXTex/wiki/GenerateMipMaps). This tool uses its own bounded area/vector filter; it does not call or claim binary equivalence to DirectXTex.

## Native and binary verification

Each level becomes a separate native `TRECustomSurface` under the texture surface list. Official YAML compilation and an independent file reload must preserve every level. Every mip receives a native PNG pixel witness; dimensions, padded row bytes and decoded RGBA must match the regenerated algorithm output. The actual CLB's surface count, dimensions, mip progression, color space, pixel format and all pixel streams are decoded again during apply.

The native writer may use mode-2 LZ4 for larger surfaces and exact-sized mode-0 raw blocks for small mips. Raw mode is explicitly enabled only by the reviewed texture reader; the shader stream reader retains its original mode-2 restriction. Reserved flags, size mismatches, missing/reordered/extra levels and corrupted pixels are rejected, including changes to lower mips with refreshed binary hashes.

`textures.binary_surfaces(path, locator)` exposes all reviewed levels. `binary_surface` remains a base-level view with color space and level count. The Default-material binding workflow requires Linear for its known normal parameters and sRGB for its known diffuse parameters; other effects retain their own review boundary. Texture receipts record role, mip policy, level count and color space.

## Static asset example

```powershell
python '<skill>/scripts/recipes/prepare_static_asset_example.py' --output '<new project>' --fbx '<static.fbx>' --image '<diffuse.png>' --normal-map '<normal-xyz.png>' --mips full --namespace OWN_STATIC
```

The optional normal map becomes `[OWN_STATIC_NRM]TX\0` and is linked to `NormalTexture`; the diffuse map remains `[OWN_STATIC_TEX]TX\0`. Original input axes are unchanged.
