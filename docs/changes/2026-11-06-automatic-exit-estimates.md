# Automatic directional exit estimates without manual exterior tags

The user chose automatic estimation with uncertainty instead of manually assigning exterior faces/regions. The result is explicitly an estimate, never global exterior-shell certification.

## Geometry method

After the independent central optical path audit, inspect first CAD hits along opposed parallel rays around its last surface/source-to-observer segment. Sixteen angular samples at each of up to seven geometric ring scales provide local surface-depth support. At least four opposed pairs and agreement at two adjacent scales are required. A more outward conflicting opposed pair vetoes a deeper internal-layer estimate even if the outward layer is not itself sufficiently supported. Candidates on an illuminated exterior sheet, one-sided support, unstable depths and invalid central paths remain unresolved.

This can estimate the local exit membrane on curved covers and open seams between separate parts, without requiring a planar closed rim or authored face tags. It uses the already loaded/transformed physical mesh, including active Blocks and accessories; it does not reread CAD files or alter Hide semantics. If the local estimate agrees with a recognized planar aperture, retain that aperture's identity and actual crossing. Otherwise use local spatial estimated-region identities; these groups are not a proof of one topological aperture. The estimator never changes source distributions, optical energies, sample weights, materials, raw paths or sensor-comparison verdicts.

The contract is automatic_exit_estimate_v1, exterior_shell_certified=false. uncertainty_mm is observed multiscale/depth spread, not a confidence interval or guaranteed physical position-error bound. The UI accordingly labels it 깊이 변동. The visible automatic-estimate status is retained even when support is strong. Missing/ambiguous results stay 미확정 and do not create floating fallback markers at internal reflections or bounding boxes.

## Resources and execution

Rings are selected automatically from the mesh extent (radius at most 25 mm). Very wide gaps and geometries without opposed support can remain unresolved. Bounds reduction uses chunks of 65,536 vertices; query batches hold at most 32 cell neighborhoods. A soft ten-second budget checked between batches and a two-million-neighbor-query cap retain unresolved evidence rather than coarsen/alter optical sampling. A blocking provider call/JIT cannot be interrupted by that soft deadline. Planar recognition has a separate three-second budget.

GPU-selected runs route neighborhood intersection through the strict CUDA mesh/BVH method, reusing its scene. GPU errors do not silently fall back to CPU: attribution remains unresolved with a visible warning. CPU-selected runs use native CPU BVH with recorded reference fallback if necessary. Separate aperture_summary records backend, query count, successful batches, GPU-successful batches and CPU fallback batches; these are not a replacement for the optical run's production GPU preflight/execution proof.

## Validation and limitations

Related CPU tests: 83 passed, including open seams, curved tessellated covers, one-sided support, partial outer-cover veto, illuminated exterior sheets, query-budget failure, and a mocked CUDA failure proving no CPU substitution. Related frontend tests: 117 passed, including estimate metadata validation, rejection of malformed variation, marker provenance and explicit estimate labels. The geometry tests are synthetic CPU checks; mocked CUDA routing is not actual device execution proof. User-scene physical accuracy and browser pixel rendering remain unverified. Full global exterior topology certification remains intentionally unsupported; no automatic heuristic is relabeled as exact shell truth. No commit/push or user server restart was performed.
