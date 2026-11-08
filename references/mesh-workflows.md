# Static mesh authoring

```powershell
aow4mod mesh-import-plan "<Mod>" --source "<source.fbx>" --namespace OWN_STATIC_MESH --output "<new plan.json>"
aow4mod mesh-import-plan "<Mod>" --source "<source.fbx>" --namespace OWN_SHADED_MESH --vertex-profile pnuvct52 --output "<new shaded plan.json>"
aow4mod mesh-import-apply "<plan.json>" --output "<new staged Mod>"
aow4mod mesh-inspect "<native-export/library.clb.yaml>" --locator "[OWN_STATIC_MESH]MESH\0"
```

## Import scope

The importer supports little-endian binary FBX 7400/7500 with static mesh geometry/models/material metadata. Source preflight bounds file/node/array sizes and rejects textures/videos, deformers, skeleton model types, cameras/lights, animation curves and external reference sections. Empty default animation stack/layer records are allowed, but no animation resources are imported. Per-mesh bone frames and unknown output layouts are refused. Default output is MeshDEV v3 with PNUV32 (float3 position/normal + float2 UV0) and uint16 triangles. Extra UV sets, collision and complete scene semantics are not certified.

`--vertex-profile pnuvct52` explicitly requests the official Generate Tangents routine plus packed white vertex colors (`ffffffff`). The reviewed declaration is 52 bytes: position/normal/UV0, packed color at byte 32 and float4 tangent at byte 36. The official format converter initially fills new channels with zero; the recipe explicitly replaces only the color bytes with white and verifies all other fields. This is an author-selected default, not source FBX vertex-color preservation. Degenerate UV triangles are rejected before calling the native tangent generator. The output must have finite unit tangents, normal orthogonality, handedness ±1 and compatible direction/bitangent signs relative to each triangle's UV derivatives. Shared mirrored seams that cannot satisfy these checks are refused; the tool does not silently repair them.

Both parent and child check the reviewed installed FBX SDK before initialization, then verify the loaded SDK hash. A fresh hidden process calls the pinned official importer and editor finalizer. The real native mesh parent list accepts MeshDEV children and rejects non-mesh objects. The ContentEd-only derived list adds metadata and tighter element factories; those factory/serialization differences are not used for import results. Each conversion has a bounded process lifetime; result arrays stay alive until child exit and mesh-list ownership is explicitly cleared.

Finalization may reorder vertices/indices and compute OBBs. Cyclic-normalized oriented triangles include all position/normal/UV attributes: storage order may change, but face winding and those source attributes cannot silently change. The optional shaded intermediate may change only the vertex buffer; its full color/tangent triangle signature must survive finalization too. Only indices, vertices and the two known bounds fields may change during the complete process. Every object gets a fresh native deserialize/save. The complete named CLB then opens/saves in another child and is compared with its direct YAML and binary witnesses. Diagnostics remain recorded; success with zero meshes is never success.

Plans bind the entire Mod/source inventory, namespace, generator/profile/SDK, object evidence and independent library evidence. Apply writes only a new stage, adds `Libraries/<NAMESPACE>.clb`, retains FBX under `SourceMeshes/<NAMESPACE>/` and saves a per-library draft receipt. Existing namespaces or retained-source directories are not overwritten. Default builds exclude FBX sources/receipts. Included draft libraries enter `draft_mesh_asset_requires_binding_review`; publication rejects a manifest that hides that finding. Runtime material/model links are not inferred. This is a draft geometry asset, not a ready unit/city model or rendering certificate.

## Read-only geometry

Export an existing CLB with `asset-export` into a new directory, then pass its YAML and exact locator to `mesh-inspect`. This inspector is offline/read-only. Unknown layouts stay `not_evaluated`. It checks counts, stream sizes, finite values, indices, normals and degenerate faces; reports local geometry, UVs, hashes and an order-independent oriented-triangle hash. Signed zero is the only numeric normalization. Node/bone transforms are not applied.

The inspector recognizes the exact PNUV32 and PNUVCT52 declarations. Unknown extensions remain not evaluated. The observed 60-byte OBB is nine axis floats, three half extents, then three center floats. Checks include finite values, nonnegative extents, orthonormal axes and stored-vertex containment for the vertex OBB. Main OBB may include a bone transform and is not required to contain untransformed vertices. Missing/zero data is not fabricated. Exit 0 only covers the checked geometry/basis; material shader feature selection and rendering are always unverified.

## Stage a static model and material

```powershell
aow4mod model-bind-plan "<Mod>" --library "<Mod/Libraries/OWN_SHADED_MESH.clb>" --mesh '[OWN_SHADED_MESH]MESH\0' --name 'My static model' --output "<new model plan.json>"
aow4mod model-bind-apply "<model plan.json>" --output "<new staged Mod>"
```

The library must already be inside the Mod. The selected mesh must use the reviewed PNUVCT52 white-color/tangent declaration, with empty bone frames and no prebound mesh material/mask. The recipe extracts only the installed System_Effects Default material and Material Plane model definitions through the checked native template workflow. It clones them into the existing library, allocates new MAT/MODEL indices, links the selected mesh and new material, and appends missing Default category-0 material/model features. Existing members and their order remain semantically unchanged. The template must have scale 1, one LOD/component, empty rig/animation/collider/shadow links and empty frame/matrix mappings. Original material properties, external shader/texture references and template settings (including cast-shadow false) are inherited; this does not generate collision or shadow meshes.

Model/LOD boxes are computed from stored mesh coordinates, with float32 centers and outward-rounded half extents. Every vertex must fit. Native compilation and independent file/YAML readback must preserve the requested document, and frozen native model-link witnesses must resolve the new model's local targets correctly. Apply re-derives the exact recipe, verifies hashes, identities, proof coverage and backend binding, then writes a new Mod copy. Source changes or a candidate changed during copying abort and remove the output stage. Repeated creation allocates new IDs and reuses category features.

The Default-material recipe now adds `SYSTEM_EFFECTS.CLB` to the CLB's own field-22 dependencies, preserving existing imports. The reviewed effect link must remain `[SYSTEM_EFFECTS]EFFECT\0`. Repeated authoring reuses the declaration. Existing draft libraries created before this fix can use `clb-import-plan/apply`; see [CLB dependencies](asset-workflows.md#add-dependencies-to-an-existing-library). Native observations confirm that omitting this declaration leaves the copied material effect unresolved even after explicitly opening System_Effects; adding it resolves the exact original effect object. This does not prove shader feature selection or rendered results.

Draft provenance is retained under `SourceModels/<library>/.aow4mod-model.json`; repeated additions append their records. Default publishing excludes these author receipts. The build manifest records `draft_model_assets` and `draft_model_asset_requires_variant_review`; dropping either provenance or its gate is rejected, including during frozen snapshot verification. Original mesh-import draft receipts remain as provenance. Shader variant selection, game resource entry, collision and rendering are still unverified, so the model remains a draft.

## Connect a supported game component

Current unit/building rendering frequently uses `TGCMComponentResource` mesh parts rather than a direct CLB model link. After creating a local model/material with the preceding workflow, stage the original component override (or select a registered local RPK resource), then:

```powershell
aow4mod component-model-plan "<Mod>" --id '<TGCMComponentResource ID>' --piece 0 --library '<Mod/Libraries/OWN_SHADED_MESH.clb>' --model '[OWN_SHADED_MESH]MODEL\0' --output '<new component plan.json>'
aow4mod component-model-apply '<component plan.json>' --output '<new staged Mod>'
```

The selected part must be `TGCMComponentMesh` inside the reviewed components property. Its mesh URI/label changes to the selected single-LOD static model's local mesh. The mesh's two default material aliases (native YAML fields 27/68) are bound together to that model's local effect material; a different existing default material is refused to avoid silently rebinding a shared mesh. Other parts, attachment/orientation, modifier references, model-setting overrides and unrelated data are preserved. The model's frame/matrix/rig/animation/collider/shadow and mesh bone/mask layouts must fit the supported static recipe. Inherited component settings still affect the visual; this does not copy a whole entity or remove its other parts.

ACP overrides receive an additive required CLB dependency for their target base pack; registered local RPK sources receive the CLB import directly. The transaction includes resource data, CLB and dependency declaration. Plans check full Mod inventory, exact derived edits/imports, compiler and independent file readback, local model links and a byte-identical native component-object roundtrip. Apply writes a new stage and refuses changed inputs or evidence. Normal native builds independently apply the ACP to its original base and freeze that result; the tested Drowned city-ruins component retains the new mesh reference in the applied original pack, with no data mismatch. Rendering, actual mounting and shader variant selection are not certified. Model/mesh draft gates remain.

TFX effect graphs have a separate model-reference path. A current raw `TFXResource` object save removes per-particle editor names, and its property-hint introspection is unsupported for one observed descriptor; those experiments are not used as a lossless effect-entry authoring API.

## Observe native model links

```powershell
aow4mod native-model-check "<local library.clb>" --locator '[LIBRARY]MODEL\0'
```

Select 1–16 model locators. The native loader already initializes and activates resources: never call the model's activation method again as a validation step. The repeated call triggers the same already-activated assertion for an original model and a new model; its failure is not evidence that the original load failed. The checker observes connected/activated status, each LOD component's readiness, and its live mesh/material/shadow pointers. It checks declared locators, actual types and pointer equality with the exact loaded local library members, then freezes serialized before/after model and resolved target witnesses. Serialized data must remain unchanged. Evidence is bounded to 256 components and 64 MiB; individual objects are at most 16 MiB.

Only local mesh/MeshDEV and effect-material targets are certified by this command. Empty shadow links are optional; mesh and material links are required. An existing external target remains outside the local identity scope and is reported for review. Missing mesh targets clear component readiness, while a material link to a mesh can retain readiness with no log diagnostic; checking the resolved type is therefore essential. `local_links_resolved` means these scoped links agree. It does not prove external shader/texture closure, a particular shader variant, GPU compatibility, game entry, collision or rendering. All observations run in a hidden child and do not launch an editor or game.

Protocol-4 native builds automatically collect this evidence for every CLB containing the supported `TREModelResource` type. Models are processed in batches of 16, at most 256 per library. Frozen evidence is bounded to 256 MiB per library and is rechecked against the payload model graph, target ordinal and actual target type without the DLL. Historical protocols retain their older scope. `model_link_issue_count` is separate from serialized-data mismatches: zero binary differences/diagnostics cannot hide bad or unverified model links. Publication copies and revalidates these witnesses.

## Evidence and continuation

Earlier raw imports had zero OBB blocks. A real native parent list produces identical output, excluding that list choice as the cause for the tested cube. The editor finalizer (ContentEd `4CBC30`, matching DLL `B704D0`) computes both bounds and reorders streams without changing oriented triangle attributes. A cube and an independently translated/rotated/nonuniformly scaled variant pass containment and fresh-object checks. The formal plan/apply path creates a draft CLB; a five-file Mod build completes eight native checks with zero errors/mismatches. Binding and inherited diagnostics remain review-required.

Shaded controls include a native cube, mirrored U coordinates (handedness flips), and baked rotation/translation/nonuniform scale. Source attributes and fresh-object readback match; collapsed UV input is rejected without changing its native object. Real plan tampering with refreshed hashes (color, tangent, omitted shaded witness or requested profile) is rejected before any stage output. The shaded asset remains a draft: these vertex checks do not establish a particular material feature variant, model/game entry, collision or rendering.

Primary references: [official Modding Guide 1.3](https://forumcontent.paradoxplaza.com/public/1271140/Age%20of%20Wonders%204%20-%20Modding%20Guide%20-%20V1.3.pdf), [ufbx fixture](https://github.com/ufbx/ufbx/blob/master/data/maya_cube_7400_binary.fbx), [ufbx license](https://github.com/ufbx/ufbx/blob/master/LICENSE). Fixtures remain private; no community implementation was copied.

Native builds now independently freeze material/effect connections under protocol 7; [material connections](material-workflows.md) describes direct checks and limits. Matching connections do not clear shader variant or rendering draft gates.
