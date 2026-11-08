# FBX source correspondence and frozen checks

Native loading and a CPU formula can agree on an incorrect imported hierarchy. Reconstruct parent/sibling/child links from the source FBX `OO` connections before native finalization, then compare against an independent source reference. Keep native and source-correspondence witnesses separate; a hierarchy repair must preserve unrelated vertices, weights, default transforms and inverse-bind values.

## Fast offline check

```powershell
aow4mod skeletal-source-check '<prepared.fbx>' --library '<candidate.clb>' --mesh '[NS]MESH\0' --model '[NS]MODEL\0' --output '<source-check.json>'
aow4mod skeletal-source-verify '<source-check.json>'
```

Both commands read the actual CLB metadata and bounded raw/LZ4 streams. No official editor, native process, GPU or original private cache is needed. The saved assessment is recomputed from both hash-bound files; an edited YAML witness cannot substitute for the binary mesh/model. Omit `--model` only for a mesh-only check.

## What is proved

- Every native vertex matches source float32 local position and normal values, plus source UV0 with the pinned `V = float32(1 - float32(V))` conversion. Signed zero is treated as the same numeric value. No fitted transform or nearest-point search is used.
- Weights are compared by exact source bone names, aggregating duplicate native slots. Source normalized weights must match within four float32 epsilons (about 4.77e-7). All shader-read palette entries remain bounded even at zero weight.
- Each native triangle must have unique source-polygon/corner ownership, reversed winding, complete triangle/quad boundary coverage and correctly paired internal edges. Degenerate or incorrectly oriented triangles are rejected. Ambiguous duplicate faces are not guessed.
- Source parents/siblings/children and default local XYZ transforms are compared against the mesh frames. One common excluded parent defines skeleton reference space (the case uses `Armature`). Default matrix precision uses the existing native-model bound `max(2e-5, max(abs(matrix))*3e-6)`; it is not byte identity. The case's maximum difference is about 1.64e-6.
- With `--model`, the runtime and LOD default frames, owned identity root, source parent relationships, selected mesh, model scale and bone palette must agree. This profile is a single-LOD, single-component unrigged model without colliders/modifiers.

The initial limits are 20000 native vertices and source polygons, triangles/quads only, one normal/UV layer at index zero, supported direct/indexed vertex/control-point/polygon/corner mappings, XYZ local rotation and no additional pivots/pre/post rotation. FBX model property-template defaults are included. Unsupported profiles fail explicitly.

## Build and publication integration

New skeletal imports use plan/receipt version 2 and retain `SourceSkeletons/<NS>/.aow4mod-skeleton-v2.json`. Building such an asset requires a fresh source comparison against the actual payload CLB, not just its import-time result. The retained prepared FBX must still match the receipt.

The build writes `skeletal-source-validation.json` and a bounded `source-evidence/` snapshot **outside the runtime content**. Release preflight and frozen preflight reconstruct local geometry, weights and default-model checks from those files. The source inventory, requirement flag, report hash/summary and draft policy are bound to the source-manifest snapshot. Clearing a requirement or refreshing a corrupted report's hash does not bypass recomputation.

This is separate source-evidence protocol 1 alongside native build protocol 8. It does not embed the animation-channel/model-pose/CPU skin-reference cache or clear bind/bounds/variant findings. Old v1 packages retain their historical narrower evidence; integrity success does not retroactively accept their source hierarchy. The `9311612` skeletal case is superseded for source fidelity, while its historical native/transport integrity facts remain intact.

## Boundaries

Local geometry/weights/default frames are now checked. A separate [source-motion workflow](source-motion-workflows.md) can check actual source/native sampled poses; that evidence is not automatically part of these frozen local checks. Source materials/colors, complete source animation, the excluded reference node's world transform/root motion, complete bind-space behavior, animated bounds and rendering remain unverified. A shared default pose or close matrix values alone do not prove those properties. Autodesk's [cluster deformation example](https://help.autodesk.com/cloudhelp/2020/ENU/FBX-API-Reference/cpp_ref/_view_scene_2_draw_scene_8cxx-example.html) treats mesh-reference and link transforms as separate parts of deformation; the toolkit does not collapse that distinction into a claim of complete FBX equivalence.
