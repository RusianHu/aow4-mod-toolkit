# Skeletal animation library import

Import one FBX take as a new native `TREAnimationResource` in its own CLB. This delivers an animation asset, not an animated unit or a skinned model. Existing static mesh commands retain their separate static-only policy.

```powershell
aow4mod project-new --name 'My Animation' --author 'Author' --output '<new project>'
aow4mod animation-import-plan '<project>/mod' --source '<baked action.fbx>' --namespace MY_ACTION --title 'Walk' --output '<plan.json>'
aow4mod animation-import-apply '<plan.json>' --output '<project>/stages/action'
aow4mod project-use '<project>' --source 'stages/action'
aow4mod build '<project>' --output '<new release>'
```

Planning requires the installed, hash-matched official DLL and FBX SDK on Windows x64. It uses a hidden child process and the same reviewed scene-owner constructors as static import. It does not launch an editor window or the game. No source or production Mod is overwritten.

## Accepted source

- Binary FBX 7400/7500 within the shared 16 MiB file / 64 MiB expanded-array bounds.
- One stack and one layer; a zero-based take lasting at most 120 seconds; at most 512 uniquely named nodes. UTF-8 node names must fit the native 32-byte frame name.
- Explicit XYZ rotation, translation and scaling curves must be baked on identical **uniform** key times covering the complete take. `--bake-fps` can explicitly resample linear curves with a source-scalar error bound; see [baking and CPU channels](animation-pose-workflows.md).
- Nonfinite/excessive curve values, unordered/out-of-range times, duplicate identities, cyclic model parents, external references and unsupported objects/media are refused.
- The native scene's tracks are imported; these can include empty mesh-node tracks requiring bind poses. Mesh, material, camera and light assets are not delivered. Skeleton retargeting, animation layers/blending and constraints are not provided.

The baked-channel restriction addresses a demonstrated official importer failure. The original Assimp test FBX returned success while asserting on unequal XYZ key counts and losing intermediate rotation keys. Aligning only rotation exposed another shared-count assumption affecting translation/scale. Both inputs now fail before native import. An importer assertion also blocks plan creation even when its return code is zero.

## Data and evidence

The importer retains all per-node tracks and shared rotation, translation, scale and compressed-quaternion pools. A complete native animation resource is reconstructed; the official period/FPS recalculation method is called on a legitimate factory object. Independent float32 arithmetic checks the derived property. `derived_fps` is the engine's key-density property, not a declaration of the FBX author's capture frame rate.

Track ranges, quaternion norms, finite values, time ordering, source bone coverage and take duration are checked. Native before/after objects and a second object are retained. A separate process loads/saves the CLB; complete YAML tracks and decoded pool bytes are bound back to the native body, alongside metadata and opaque-payload comparisons. These checks preserve data; `pose_sampling_verified`, `model_binding_verified` and `rendering_verified` remain false.

Apply retains the full FBX and a receipt under `SourceAnimations/<NAMESPACE>/`. Default runtime selection excludes them. Build records `draft_animation_assets` and `draft_animation_asset_requires_binding_pose_review`; normal and frozen preflight require that provenance and gate to remain. A standalone library has no game animation entry and can also retain `no_resource_objects_checked`. Build/transfer success must not be called an animated Mod acceptance.
