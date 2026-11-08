# New skeletal model drafts

`skeletal-import-plan/apply` imports a new skinned mesh and motion, authors a model frame hierarchy and Default material, and checks native model poses plus CPU skin references. This creates **new definitions from FBX**, rather than rebinding an existing game skeleton. It remains a draft: source-to-game world/bind space, sampled source animation, animated bounds, real shader selection/rendering and an animated gameplay entry are not certified.

```powershell
aow4mod skeletal-import-plan '<blank Mod>' --fbx '<uniform baked source.fbx>' --namespace CLI_SKIN --name 'New skeleton model' --dxc '<pinned dxc.exe>' --output '<plan.json>'
aow4mod skeletal-import-apply '<plan.json>' --output '<new staged Mod>'
```

Create the blank project with `project-new` if needed. Put the applied stage under that project's `stages/`, then select and build it with explicit native checks:

```powershell
aow4mod project-new --name 'Skeletal Example' --author 'Author' --output '<project>'
# Plan against <project>/mod and apply into <project>/stages/imported.
aow4mod project-use '<project>' --source 'stages/imported'
aow4mod build '<project>' --native --output '<new release>'
```

Use `--rotation-storage float32-guarded` for source-guided high-precision raw rotation with an endpoint guard, then run [source motion sampling](source-motion-workflows.md). `snorm16` remains the default. This choice is retained in the source receipt and does not certify all animation times.

Strict weights are the default. Non-normalized source weights or more than four positive influences per used control point are rejected before invoking the native importer. An explicit authoring policy can prepare a new source copy:

```powershell
aow4mod skeletal-import-plan '<blank Mod>' --fbx '<source.fbx>' --namespace CLI_SKIN --name 'New skeleton model' --bake-fps 24 --bake-max-error 0.001 --weights normalize-top4 --max-dropped-fraction 0.1 --dxc '<pinned dxc.exe>' --output '<plan.json>'
```

This retains the four largest positive weights, breaks ties by cluster ID, normalizes their sum and sets discarded source entries to zero. The maximum discarded fraction is measured per control point against its original weight sum. It must not exceed the caller's explicit bound. This changes weighting deliberately; it is not a claim of equivalent deformation. Original input bytes are retained. Only weight array property bytes are replaced; node offsets are rebuilt, other property bytes and footer are copied. Optional uniform baking uses the existing explicitly linear TRS policy and its source-scalar error bound.

## Source checks and hierarchy repair

New imports reconstruct the hierarchy from source model connections before native finalization. A real two-chain case exposed an imported cross-chain parent error that self-consistent native/CPU checks missed. [Independent source checks](source-correspondence-workflows.md) now compare local geometry, normalized bone-name weights, UV/winding/corner topology and default mesh/model frames directly with CLB bytes. Triangles/quads and one normal/UV0 layer are required by this initial profile. They do not certify complete world/bind space or source animation.

## Supported input and output

- Binary FBX 7400/7500 within the existing 16 MiB / expanded-array bounds; exactly one mesh geometry/model, one skin, one take/layer, and 1..256 normalized-mode clusters with unique limb names. Geometry references and used control-point weights are bounded. Other deformers and external media/references are refused.
- Complete uniformly baked XYZ/TRS keys are required; `--bake-fps` supports explicitly linear curves. No nonlinear resampling, retargeting, extra mesh instances or multi-mesh assembly is promised.
- Native initial PNUV/four-influence streams with uint16 triangles are converted to PNUVCT68. White color and per-UV-triangle tangents are authored, differing corners split, and oriented geometry/skin bytes independently compared. Finite unit source normals and nondegenerate UVs are required; output fits uint16 indexing.
- Up to 255 mesh frames are converted into an unrigged model hierarchy. A forest receives one owned identity `_CLI_SkeletonRoot`, with explicit bind-pose policy for that frame only. Dense model bone IDs are separate from the mesh inverse-bind palette. Rig controllers/modifiers, collision, animation blending/scaling and full model/world transforms are not delivered.
- Unreferenced inverse-bind holes may remain. Every shader-read index, including zero-weight influences, must still have a real mesh bone. The pinned native map builder leaves an unreferenced hole at zero; stored, rebuilt and name-based mappings must agree.
- Default material/model templates come from the installed hash-bound System_Effects library. FBX source materials are not converted; the result uses a new Default material. Mesh/model/animation/material members and library features/dependency declarations are assembled in one new CLB.

## Verification sequence

1. Hash-bound original, optional baked and weight-prepared inputs; source Mod inventory is retained.
2. Isolated native mesh and animation imports; complete pooled-key/object witnesses and independent file readback.
3. Mesh finalization preserves oriented geometry, skin bytes, names, transforms, hierarchy and inverse bind; native-derived frame words/bounds are recorded separately.
4. A newly authored mesh library's first content load may recompute only `FHash` in the two frame aliases. Before/after witnesses remain archived. This bounded initialization allowance applies to this new-library construction stage; it does not migrate or rewrite an existing Mod.
5. Complete requested model/material/motion YAML is compiled without relaxing compilation equality. The resulting CLB must be stable under independent native file readback.
6. Actual default motion, frame mapping, hierarchy matrices, native bone-map rebuild and every vertex at three times are checked against independent math and pinned compiled shader prefixes on CPU. Only the owned synthetic root may be unmapped.
7. Source-scoped native build/frozen checks retain `draft_skeletal_assets` and `skeletal_model_requires_bind_space_bounds_variant_review`; an asset-only package also retains its no-resource/game-entry finding.

`SourceSkeletons/<NS>/original.fbx`, `prepared.fbx` and `.aow4mod-skeleton-v2.json` retain inputs and author choices. Build/frozen preflight now archives and recomputes local source checks through separate source-evidence protocol 1. Detailed animation/model/skin CPU evidence remains in the private plan and is not automatically embedded in native protocol 8. Neither protocol certifies full FBX animation/world/bind/render equivalence.

## Proven frame-reserved bytes

The reviewed `TREMeshFrameSet` serializer at DLL RVA `B6C980` writes/reads the two flags at record offsets 156/157, skips 158/159 and archives 160 bytes per record. Scratch contents can appear in those last two bytes. Known MeshDEV v3 frame comparisons project **only** 158/159, preserve raw artifacts/metadata hashes and disclose `normalized_mesh_frame_padding`; all other bytes, flags and `FHash` remain strict. Other classes, versions and layouts receive no such projection.

Local primary

## Acceptance and remaining work

The source-based case prepares 1058 control points, discarding at most about 7.05% of a point's original total weight under an explicit 10% bound. It creates 6284 vertices, 2112 triangles, 15 model frames and 13 effective model bones (14 inverse-bind slots, one unreferenced). Three-time native/CPU checks cover all vertices, with maximum position error about 1.08e-5 and direction error about 2.51e-7 after source-hierarchy repair, zero native diagnostics.
