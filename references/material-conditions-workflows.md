# Native material conditions

```powershell
aow4mod material-conditions '<Mod/Libraries/OWN.clb>' --locator '[OWN]MAT\0' --dependency-library '<Mod/Libraries/OWN_TEX.clb>' --output '<conditions.json>'
```

This checks the actual loaded material/effect and their independent objects, then evaluates supported scalar **conditions** through the installed engine's CPU `TEExpression`. It does not select a renderer program or clear model/material variant gates. Use it to understand declared material features and pass predicates before attempting a renderer preview.

## What is evaluated

Effect feature field 41 is `FEnableStaticExpression`; field 42 is `FEnableDynamicExpression`. They are **not** vertex/pixel stages. Pass field 1 is its declared static enable expression. An empty pass condition evaluates to true, which does not mean that the renderer executes that pass. Likewise, property-definition field 43 is `FUIInsertSeparator`, not a declaration that the property is static.

The command reads actual native scalar parameter bodies and actual effect declarations. Supported values are bool, float32 and read-only int32 enum storage. It preserves unsupported vector/dynamic expression types as unavailable inputs. Exact names are bound using the official `TEExpressionVariableMapping` creation/add/attach methods; `true`/`false` are proper bool values. The official expression constructor/destructor and mapping ownership are used; no vtables, pointers, scene objects or renderer flags are fabricated.

A bounded grammar preflights expressions before native calls: numeric literals, named scalars, parentheses, logical `&&`/`||`/`!`, unary signs and comparisons. Functions, swizzles, arithmetic and unknown names remain unevaluated. Float suffixes are lowered to explicit decimal float32 literals and unary plus to an identity expression. Every native result is compared with an independent float32 evaluation, and native variable name/type/value bytes must match the requested mapping. Scope: at most 128 native jobs, 256 tokens/expression, 32 nesting levels and 256 scalar mappings/job.

Default mode evaluates supported static feature/pass conditions. Dynamic feature conditions remain `not_evaluated` because a material file does not provide a draw-time context. The tested material has 24 declared conditions: 17 evaluated and seven dynamic conditions deferred. Its content-loader renderer is actually uninitialized; compiled-material handles may be placeholders. Never toggle the renderer's global flag to manufacture a context or interpret a null program query as a selected shader.

## Explicit material-default what-if

```powershell
aow4mod material-conditions '<Mod/Libraries/OWN.clb>' --locator '[OWN]MAT\0' --dependency-library '<Mod/Libraries/OWN_TEX.clb>' --dynamic-material-defaults --program-candidates --pass-index 0 --stage 1 --require NOBONES --require NOUV2 --output '<what-if.json>'
```

`--dynamic-material-defaults` evaluates supported dynamic conditions against the stored material values as an explicit **what-if**. It does not observe dynamic renderer overrides. The example evaluates all 24 conditions. An actual edit changing `HasColorize` false→true and `SwayAmountX` 0→1 changes the corresponding native feature predicates. Changing `EnableShadows` does not change an empty CASTSHADOW predicate, so the tool does not infer actual shadow-pass execution from it.

`--program-candidates` joins condition results to the existing exact DXIL/pass/stage table associations. Contradictory or unevaluated duplicate feature conditions are not forced. Conditions absent from the selected table remain visible; explicit require/forbid flags are query assumptions and may not contradict computed conditions. Stage 0 is pixel, 1 is vertex. The first 32 descriptor bytes remain opaque. A tested what-if plus NOBONES/NOUV2 leaves one of 96 vertex table rows; **one candidate still is not proof that the renderer selects it**. Both `renderer_selection_executed` and `shader_variant_certified` stay false.

Reports retain complete material/effect object witnesses, the native typed condition request/results, unsupported/deferred reasons, raw diagnostics and query assumptions. `material_conditions.validate(report)` reconstructs values, conditions, results, input bytes, scope and optional query. It needs those captured artifacts/logs, and the exact effect CLB for a program query. This is a diagnostic report, not a new release protocol: protocol 8's existing texture/model/material gates remain unchanged.

The build-bound CPU interfaces were identified in the installed DLL and checked against literal/operator controls and actual material edits. An internal renderer context and the final descriptor key still require separate work.
