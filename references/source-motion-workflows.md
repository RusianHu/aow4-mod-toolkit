# Source motion sampling and float32 rotation precision

Compare prepared FBX linear XYZ/TRS curves directly with **actual native model matrices**, independently of the imported CLB rotation/translation pools:

```powershell
aow4mod source-motion-check '<prepared.fbx>' --library '<candidate.clb>' --model '[NS]MODEL\0' --output '<motion.json>'
aow4mod source-motion-verify '<motion.json>'
```

The default budgets are 0.001 for position matrix components (model units) and 0.0002 for linear matrix components. Set `--max-position-error` / `--max-linear-error` explicitly for another authoring budget. A failed budget produces a report and nonzero exit; it does not become a pass by clearing an issue flag.

By default, sampling includes every source key and each interval's one-third, midpoint and two-thirds, plus the native endpoint. Integer microsecond rounding is disclosed by the source/native periods. More than 256 unique samples require an explicit bounded list of `--time-us`; explicit sampling does not claim the complete default grid. Native captures run in batches of at most sixteen times. Offline verification retains its hash-bound native object/matrix/file-readback witnesses; no native process is executed during replay.

Only explicit linear curve attributes, complete XYZ/TRS groups and the existing local XYZ/default-frame profile are supported. Source FK is constructed from the FBX curves and source hierarchy, not the native clip pools. The owned identity root is excluded from source node lookup. Coordinates are in the common skeleton reference; the reference node's transform/root motion, world/bind behavior, continuous-time error bounds and rendering remain unverified.

## Higher-precision skeletal imports

```powershell
aow4mod skeletal-import-plan '<Mod>' --fbx '<source.fbx>' --namespace CLI_MOTION --name 'Motion example' --rotation-storage float32-guarded --dxc '<pinned dxc.exe>' --output '<plan.json>'
```

`snorm16` remains the default. `float32-guarded` is an explicit source-guided authoring choice:

- Retain the original float32 quaternion values and disable each converted track's compressed-rotation range. The unused compressed pool is retained, rather than silently deleting source data.
- Rebuild raw-rotation times from the prepared FBX keys. The final real key is at the native clip period; native/source duration may differ by at most one microsecond. Raw values and the requested source timing are independently re-derived when applying the plan.
- Append one duplicate final quaternion at `period + 1` microsecond. This keeps the valid playback domain inside the native raw helper's search range. The clip period is unchanged. Without this guard, a real model query at the endpoint returned a substantially wrong pose; that candidate was rejected.

The reader accepts **only** a referenced raw rotation range ending with identical quaternion values at `period` and `period+1`. An arbitrary future key, a changed endpoint quaternion, a missing in-range terminal key or an unreferenced future guard is rejected. Translation and scale keys cannot exceed the clip period. This is a precise supported format case, not removal of the time bound.

The flag is not a blanket animation-fidelity guarantee. Run `source-motion-check` against the applied library and its retained prepared FBX. Source animation samples are not automatically copied into source-release protocol 1 or native protocol 8; the build's existing local-source checks do not become continuous animation verification.
