# Existing model animation binding and hierarchical poses

```powershell
aow4mod model-animation-plan '<Mod>' --library '<Mod>/Libraries/MY_MODEL.clb' --model '[MY_MODEL]MODEL\0' --animation '[MY_MODEL]ANIM\1' --time-us 0 --time-us 250000 --output '<plan.json>'
aow4mod model-animation-apply '<plan.json>' --output '<new Mod stage>'

# Omit --animation to inspect the model's actual default link.
aow4mod native-model-poses '<library.clb>' --model '[MY_MODEL]MODEL\0' --time-us 0 --time-us 250000 --output '<report.json>'
aow4mod model-poses-verify '<report.json>'
```

This changes the default animation of an existing model in the staged Mod. Both assets must already exist in the same CLB, with a matching library filename/namespace. It does not import a new skinned mesh, retarget skeletons or attach the asset to a gameplay resource. Use the project/build workflow after apply; publication is separate.

The plan rewrites only `36_FDefaultAnimation`'s locator and display name. Compilation and independent reload must retain the complete requested YAML. Native inspection checks the model's real default-link resource pointer, constructs frame-to-track mappings with the official method and compares the actual track pointers with the selected animation's members. Matching names alone are insufficient.

The native single-animation pose path computes hierarchical frame matrices and reorders them into model bone-pose matrices. Independent quaternion/vector calculations reproduce bind scales, parent scale compensation and hierarchy multiplication. Missing whole tracks can use the frame bind pose only with explicit `--allow-unmapped`; mapped tracks need real rotation/translation channels. At least one frame must have animation channels. An incompatible animation that resolves successfully but maps no model frames is rejected.

## Supported scope

- The pinned Witch 1.1 DLL profile, 180-byte serialized model frames, 1..256 uniquely named frames, one consistent tree and densely assigned bone indices. Source topology is checked before initializing native code.
- At most 16 distinct microsecond times within the animation's take. Existing static bind scales, including nonuniform parent scales, are supported. Additional frame translation/Euler offsets and animated scaling are explicitly unsupported by this inspection profile.
- This inspects the native always-interpolated single-animation model kernel. Higher-level playback policies, blending, retargeting, model/world transforms and rendering are not certified.
- These outputs are model-local frame and bone **pose** matrices. The later [weight/position reference workflow](skinning-workflows.md) additionally inspects inverse-bind data, palette maps and LBS positions on CPU. Normals/tangents, runtime shader/GPU skinning and animated bounds remain unverified.
- The source CLB must be stable under native save. Original first-save differences remain review findings; do not silently normalize or write back original game assets.

`SourceModelAnimations/<LIBRARY>/model-<index>.json` records draft provenance. Build and frozen preflight require `draft_model_animation_assets` and `draft_model_animation_requires_skinning_review` to remain. The CPU pose evidence is currently separate from build protocol 8 and does not clear the skinning/release gate. A library-only package can also retain `no_resource_objects_checked` because it has no gameplay entry.

## Corrections found with original assets

Different-density original clips exposed a prior reader error masked by the earlier uniformly baked sample's equal pool ranges. Version-5 track IDs are now bound to the reviewed serializer: translation offset/count `1B/1C`, rotation `1E/1F`, compressed rotation `24/25`, scale `27/28`. Translation time-to-index is `1A`. Zero scale pools are valid and remain missing channels.

Original clips also exposed an overly wide linear approximation in quaternion interpolation. The pinned compressed kernel's threshold is 0.999999; the raw uncompressed helper uses 0.9. The independent evaluator now follows those observed contracts. Recorded original native channel samples and matrices then agree within normal float32 error without widening verification tolerances. Distinct 3-translation/2-rotation/zero-scale synthetic controls prevent returning to equal-range-only coverage.
