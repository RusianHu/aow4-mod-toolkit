# Local animation channels and source baking

```powershell
# Explicitly bake linear source curves before importing; retain original and baked FBX.
aow4mod animation-import-plan '<project>/mod' --source '<action.fbx>' --bake-fps 24 --bake-max-error 0.001 --namespace MY_ACTION --title 'Walk' --output '<plan.json>'
aow4mod animation-import-apply '<plan.json>' --output '<new Mod stage>'

# Find exact track names without initializing the native engine.
aow4mod animation-tracks '<library.clb>' --animation '[MY_ACTION]ANIM\0' --query Bone --limit 20

# Times are integer microseconds, within the take. Repeat --track / --time-us as needed.
aow4mod native-animation-poses '<library.clb>' --animation '[MY_ACTION]ANIM\0' --track Bone --time-us 0 --time-us 250000 --output '<report.json>'
aow4mod animation-poses-verify '<report.json>'
```

The pose command loads the real CLB in a fresh content-only native process. It checks the selected member and actual track-to-resource key-pool pointers, then uses the reviewed quaternion/vector samplers. A separate process exports the library; complete native object tracks/pools are compared with that YAML and source library. Serialized data must remain unchanged after sampling. Verification replays the value calculations and source/object/file bindings without initializing the engine or accessing the installed DLL.

Inspect at most 32 selected tracks and 64 distinct time points per call; use `--output` to keep stdout compact. Nonexistent tracks, out-of-take times, invalid ranges/quaternions and unsupported native layouts are errors. Quaternion compression uses signed 16-bit components; independent decoding, shortest-arc spherical interpolation and float32 time mapping are compared with actual CPU output. Vector channels use linear or nearest interpolation according to the resource's disable-interpolation flag. The raw uncompressed-rotation helper's after-last-key search is unsuitable for a clamped inspector; this command explicitly reads its legitimate final key at that boundary.

These are **local animation channels**, not a complete hierarchical pose. An absent channel remains `null`; it requires the model's bind pose. No identity rotation, zero translation or unit scale is invented. `sampled_channels_verified` may be true while `full_pose_verified`, `model_binding_verified` and `rendering_verified` remain false. The missing-channel count counts sampled channel slots, not unique bones or bugs. This command does not certify source FBX timing, game behavior or skinning.

## Why uniform baking is required

Further CPU sampling found that the official importer redistributes imported keys to uniform time spacing. The previous 14-key, irregular-time sample preserved imported values but changed the original motion timing. Its Bone.005 source comparison differs by up to about 10.68 degrees. It remains historical data-preservation evidence and is no longer accepted as a timing-correct author example.

Default import now requires explicit XYZ/TRS keys on a uniform grid covering the complete take. With `--bake-fps`, the tool can first resample explicitly linear transform curves onto a shared uniform grid. It preserves other raw properties and the footer while rebuilding node offsets, retains both original and baked FBX, and re-derives the bake during apply. Requested FPS and error tolerance are separately bound in the plan; removing the bake policy or changing its nested tolerance alone is refused.

The scalar error bound defaults to 0.001 in each source curve's units: rotation degrees, source translation units or dimensionless scale. It measures the maximum piecewise-linear difference at the union of original/baked breakpoints, including float32 quantization. It is not a bound on full model/world-space error. The frame count is rounded to cover the unchanged take duration; `fps_effective` reports the resulting density. Cubic/step curves, multiple layers, constraints, retargeting and arbitrary missing-axis reconstruction are not supported by this baker. Do not silently convert them to linear curves.
