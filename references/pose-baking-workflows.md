# Static pose authoring

Bake an existing model at one animation time into **new** static mesh/model definitions in the same working CLB. Existing members remain unchanged. This is a new author workflow, not creation of a new skeleton or certification of animated rendering.

```powershell
aow4mod model-pose-bake-plan '<source Mod>' --library '<source Mod>/Libraries/LOCAL.clb' --model '[LOCAL]MODEL\2' --time-us 250000 --name 'Quarter-second statue' --dxc '<pinned dxc.exe>' --output '<plan.json>'
aow4mod model-pose-bake-apply '<plan.json>' --output '<new staged Mod>'
```

By default, normalize the weighted normal/tangent xyz independently and preserve UV, color, handedness and triangle indices. The verified shader prefix applies inverse bind and mapped bone matrices to positions; directions use their upper 3x3 parts without translation or inverse transpose. The static output has empty frames/matrix maps and no animation/rig binding, conservative mesh/model bounds, and each new mesh's default material matches its source component material. Originals keep their animation and rig. The plan lists pre-merge part witnesses separately from its authoritative `static_meshes` outputs; a merged intermediate part locator is not a delivered additional mesh.

## Optional authoring policies

- `--tangents rebuild`: explicitly replace tangents from each triangle's UV basis, orthogonalize against the baked normal and compute handedness. Corners with different resulting attributes are split; identical full vertex bytes are deduplicated. Oriented position/normal/UV triangles are independently compared. This changes original tangent xyz/w and possibly vertex/index storage; it does not claim identical source shading. Degenerate UVs, collapsed directions, indeterminate handedness or invalid final basis are rejected. The current reviewed shading profile requires white packed color.
- `--merge-components`: merge components only when material declarations and component settings are identical, rebasing uint16 indices and bounding the combined geometry. One final component can use the existing TGCM mesh entry. Different materials/settings are rejected; separate preserved parts remain supported without merging.

Use these choices together when a multi-part source must become one static mesh and its imported tangent conventions do not satisfy the current UV checks. No existing component-entry check is weakened.

## Raw direction references

```powershell
aow4mod model-skinning-check '<LOCAL.clb>' --model '[LOCAL]MODEL\2' --time-us 250000 --directions --dxc '<pinned dxc.exe>' --output '<vectors.json>'
aow4mod model-skinning-verify '<vectors.json>'
```

`--directions` writes reference format 2. It compares raw model-local normal/tangent xyz before world transform and normalization with the pinned compiled shader prefixes on CPU. `direction_reference_verified` is distinct from runtime/GPU skinning; tangent w remains source data. Old format 1 position evidence remains supported.

## Example with a content entry

```powershell
python '<skill>/scripts/recipes/prepare_pose_bake_example.py' --mod '<source Mod>' --library 'Libraries/LOCAL.clb' --model '[LOCAL]MODEL\2' --time-us 250000 --name 'Statue' --dxc '<pinned dxc.exe>' --tangents rebuild --merge-components --output '<new project>'
```

The example imports a copied Mod, bakes a new static pose, overrides the reviewed Drowned city-ruin component `00000452000014F2`, connects its mesh part, and explicitly requests native build checks. It does not install or submit. A failed recipe retains `pipeline.json` with its error and completed intermediate stages.

## Validation and boundaries

- Existing same-library model/default animation, scale 1, exactly one LOD, 1..16 components. Existing model-pose restrictions apply. Each component must fit the full-reference limit of 20000 vertices and the exact reviewed PNUVCT plus three/four-weight declaration.
- Every component and ordered vertex must have diagnostic-free position/direction references for the requested time. Source inventory, native backend, requested derivation, complete native YAML and separate CLB readback are checked again before applying.
- Collider conversion, separate shadow meshes, derived shadow/strip topology and nonempty mesh collision trees are rejected. Split/merged output must fit uint16 indexing. Other formats/transforms/LOD policies remain unsupported.
- `SourcePoseBakes/<LIB>/model-N.json` retains choices and geometry findings. Build/frozen publication retains `draft_pose_bake_assets` and `pose_bake_requires_static_variant_review`. Integrity success cannot clear rendering/variant review or inherited diagnostics.
- CPU reference objects are kept by the plan in the private cache. They are not yet automatically embedded as recomputable reference evidence in build protocol 8; frozen builds prove the staged native content and policy within that protocol's existing scope.
- No GPU/actual shader selection, rendering, collision gameplay, new skeletal-model creation, animation blending or gameplay testing is claimed. Other original-template save findings remain open.
