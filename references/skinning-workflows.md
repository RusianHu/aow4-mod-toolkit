# Skin weights and position references

```powershell
aow4mod shader-toolchain --download --output '<new private DXC directory>' --proxy http://127.0.0.1:10808
aow4mod shader-toolchain --dxc '<directory>/dxc.exe'
aow4mod skin-weights-plan '<Mod>' --library '<Mod>/Libraries/MY_MODEL.clb' --mesh '[MY_MODEL]MESH\0' --values-file '<weights.json>' --output '<plan.json>'
aow4mod skin-weights-apply '<plan.json>' --output '<new Mod stage>'
aow4mod model-skinning-check '<CLB>' --model '[MY_MODEL]MODEL\0' --lod 0 --component 0 --time-us 0 --time-us 250000 --dxc '<directory>/dxc.exe' --output '<report.json>'
aow4mod model-skinning-verify '<report.json>'
```

Example three-influence `weights.json`:

```json
[{"vertex":0,"bones":["Hip","Spine","Chest"],"weights":[0.25,0.5,0.25]}]
```

Select 1..64 distinct vertices per plan and exactly three/four distinct mesh-palette bone names per edit. Weights must be finite within 0..1 and sum to one. Only the first two/three are stored; the last is the shader's sequential float32 residual. No automatic normalization is applied. An unused fourth index byte in a three-influence layout is preserved.

The plan retains the source inventory, checks all vertex weights/palette bounds and changes only selected bone-index/weight bytes. Full native CLB compilation and independent reload must preserve the requested YAML. Apply re-derives edits into a new Mod directory. Positions, normals/tangents, colors/UVs, triangle indices and other vertices remain unchanged. `SourceSkinWeights/<LIBRARY>/mesh-<index>.json` retains draft provenance and the runtime/variant review gate.

## What the position check proves

The official native map builder independently rebuilds the real loaded component's mesh-bone-to-model-bone mapping. The result is checked against stored maps and a name-based reconstruction; actual runtime bone IDs are also compared with serialized records. Mesh palette order can differ from model frame order.

[Native model poses](model-animation-workflows.md) supply model-local bone matrices. Position calculation applies each inverse-bind transform and its mapped bone pose, then combines influences. A separate bounded scalar evaluator follows the exact compiled game vertex shader's position dataflow. The two formulations are compared at each selected vertex/time.

Reference programs are hash-pinned Default-effect `BONESW3`/`BONESW4`, `NOUV2` containers extracted from the user's installed game. Pinned Microsoft DXC v1.9.2609 disassembles them privately; only the position prefix is evaluated on CPU. No game shader text/binary or downloaded executable is distributed with the Skill. Saved evidence can be recomputed without DXC, the native DLL or a GPU.

**This is an offline LBS reference.** `position_reference_verified` does not imply runtime shader selection, GPU execution or rendering. Optional `--directions` compares raw normal/tangent xyz for the two reviewed PNUVCT skin declarations; it does not include world transform/normalization or certify runtime skinning. See [static pose authoring](pose-baking-workflows.md) for new static model copies and explicit tangent-rebuild/merge choices. Other deformations, model/world transforms and animated bounds remain unverified. A compatible declaration and candidate program do not prove renderer selection.

## Scope and limits

- MeshDEV v3 with float3 positions, uint8x4 bone indices, float2/float3 weights and reviewed 160-byte mesh frames. Inverse-bind matrices and all shader-read palette indices must be valid. Other layouts are errors.
- One explicit LOD/component per report. Defaults inspect every component vertex up to 20000; larger components require repeated `--vertex` IDs. At most 16 times are supplied by the model-pose workflow. A report does not claim all model components were checked.
- Small negative residuals from float32 subtraction are retained within 1e-6, without clamping. All indices actually read by the shader are bounded even at zero weight; the fourth index in the three-influence program is not read.
- An inverse-bind palette hole is accepted only if no shader-read index uses it; native rebuilt mapping must use the pinned zero default for that unreferenced entry. Reserved frame bytes 158/159 are compared by the proven serializer rule, with raw hashes retained; meaningful flags and matrices stay strict. See [new skeletal drafts](skeletal-authoring-workflows.md).
- Existing model-pose hierarchy/bind-scale restrictions apply. CPU positions are not automatically part of build protocol 8 and do not clear weight/model-animation review gates. No game entry is added.
