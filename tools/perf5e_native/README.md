# Experimental PERF-5E native probes

These tools are not a production ray-tracing provider. The application does not
import or select them. A successful OptiX launch does not certify the simulator's
CUDA execution contract or establish end-to-end acceleration.

## OptiX candidate probe

- Requires an already installed OptiX SDK 9.1, CUDA 13.1 and Visual Studio C++ tools.
- `scripts/build_perf5e_optix_probe.ps1` builds a local DLL/PTX into ignored
  `outputs/perf5e-native`. It does not install prerequisites or alter system PATH.
- The host builds outward-padded, locally rebased custom-primitive AABBs. OptiX
  enumerates candidate boxes; original double-precision triangles and rays are
  tested in the custom intersection program with contraction disabled.
- Closest-hit reporting/pruning is deliberately not used. Candidate order must
  not replace the existing order-sensitive, nearly-equal-distance tie contract.
- Flags: `1` near-distance cluster, `2` triangle edge, `4` numeric-domain guard.
  Flags are diagnostics, **not a conservative-coverage proof**. Even flag-zero
  results remain `experimental_candidates_unverified_v1` and require a complete
  canonical reference query before use as an authoritative result.
- `query` uses host arrays; its timings include transfers and synchronized launch.
  `query_device` borrows contiguous device arrays without transferring the ray or
  hit arrays. It still synchronizes each query and is not a fused transport loop.
  Scheduling, mandatory canonical replay and optical aggregation are not included.
- CUDA uploads complete before the nonblocking OptiX stream consumes them. This
  explicit ordering is necessary even when a host-to-device API appears
  synchronous for a small input.
- Use `OptixCandidateProbe` as a context manager. Do not call its native handle
  directly or close it concurrently with a native C ABI query.

### Device-buffer experiment

Rebuild the DLL before use: the device-buffer interface requires probe ABI 2.
Inputs use `RAY_DTYPE`, outputs use `HIT_DTYPE`; neither layout is inferred from
byte size alone. CUDA Array Interface v3 descriptors, or the explicit structured
`dtype` for Numba arrays that omit `descr`, must match exactly. Only contiguous,
aligned device allocations in the probe's primary context are accepted.
Overlapping input/output buffers, masked arrays and read-only outputs are rejected.

Pass an explicit CUDA stream handle to `query_device(..., stream_handle=...)`.
The consumer waits for both arrays' advertised producer streams before access.
The call returns only after the chosen execution stream completes, including
cleanup on an enqueue failure, so borrowed arrays can be reused afterwards.
Captured streams are unsupported. Host/device queries share the same per-probe
lock. Other threads must not modify or release the borrowed buffers while a call
is active. Exporters must truthfully advertise pending work through their CUDA
Array Interface stream metadata.

This follows the producer/consumer ordering in the
[CUDA Array Interface v3 specification](https://numba.readthedocs.io/en/stable/cuda/cuda_array_interface.html).
Small launch parameters are still uploaded; “no array transfers” does not mean
zero host interaction. Device output is not an authoritative optical result.

The current harness is `scripts/verify_perf5e_transport.py`. It observes batch
queries during canonical CPU/CUDA multi-bounce transport, including stored paths
and Receiver aggregation, without using any OptiX result as an authoritative hit.
It runs in a separate command-line process, never in a running app server.

Before production promotion, conservative miss coverage, canonical tie replay,
geometry lifecycle, full optical transport, saved-project roundtrips and the
predeclared representative/small-scene performance gates remain required.

The SDK and third-party binary dependencies are not redistributed here.

### Candidate visitation diagnostics

Coverage ABI 1 adds `query_coverage(rays, expected_faces)`, with two expected
face IDs per ray and a two-bit visited mask. It records custom-primitive visits
before ignored-face and exact-hit rejection. A visited face is not necessarily
the selected hit. Numeric guard rows do not traverse and require canonical replay.
The ordinary ABI-2 queries and output layouts remain unchanged; older builds
explicitly reject the new diagnostic method.

Bounds are now constructed after rebasing, with outward rounding for local
vertices/edges, padding and radius. Constructing global endpoints first lost
small rotated edges at large offsets; the failed diagnostic remains recorded.
Passing finite coverage fixtures does not prove conservative coverage for
arbitrary rays or geometry.

### Transport validation on the Workspace and Batch baseline

Use the already prepared `run_web_gpu` source environment. The harness checks
fresh production preflight, binary/build hashes and source identity. No SDK,
driver or Toolkit is installed by these commands.

```powershell
powershell -NoProfile -File scripts/build_perf5e_optix_probe.ps1
.\.venv-gpu\Scripts\python.exe scripts/verify_perf5e_transport.py `
  --native-directory outputs/perf5e-native --output outputs/perf5e-transport-run
.\.venv-gpu\Scripts\python.exe scripts/audit_perf5e_transport.py `
  outputs/perf5e-transport-run/report.json --output outputs/perf5e-transport-audit.json
```

Output directories must be new so failed evidence is never replaced. The optional
`--saved-case` loader currently accepts the earlier single-Case portable format
with a cached trace mesh and no transforms; it is not a Workspace importer.
Workspace compatibility is tested separately with
`tests/test_perf5_workspace_compatibility.py`. Opt in to its real-device test with
`PERF5_GPU_COMPATIBILITY=1` after preparing the official source environment.

`passed` in the transport report means observation preserved canonical results,
not that OptiX is a validated or faster production provider. Coverage is separately
reported, including reference-miss controls for which coverage is not evaluated.
Near-tie/edge mismatches remain visible. Every query still uses canonical results.
Timings include observation, uploads, synchronization and raw evidence writing;
they are not performance acceptance measurements. Normal fused transport is not
replaced by this host-roundtrip diagnostic.

Scene identity changes close the old native probe; repeated batches reuse the same
probe. Exceptions and cancellation restore dispatch and release the probe. Nested
observation and other-thread dispatch are rejected. No API field, project schema,
Batch scheduling or Ray Path format changes are made.

## Oracle-checked selective replay experiment

`verify_perf5e_transport.py --selective-replay` uses the isolated
`selective_replay.py` candidate instead of observation-only mode. Ambiguous,
guarded and missed candidates replay the original BVH. Other hits recompute only
the chosen triangle's distance in the original batch's actual CPU/CUDA backend.
Small replay subsets never change that backend. A rejected distance refinement
also replays the BVH; it never discards a ray.

Every diagnostic batch still runs a separate full canonical oracle and compares
face ID, cardinality and distance bits before letting candidate results enter
transport. A mismatch throws instead of silently substituting oracle values.
The second normal OptiX query receives no oracle face IDs. The coverage query
and raw evidence capture are outside `selective_seconds`.

This is **not production acceleration**. In particular, an unflagged candidate
could still omit a nearer triangle: per-face refinement proves that the selected
triangle was hit, not that it is the nearest one. Oracle-free candidate coverage
remains unproven, and diagnostics retain all full-oracle queries. Do not remove
the oracle, enable a product provider, or report fewer replay rows as a measured
end-to-end speedup. Host copies, allocations, synchronization and small launches
remain in this prototype.

Use `verify_perf5e_selective_boundaries.py` for synthetic overlap, positive gap,
rotation/translation, exclusions, ignored faces, distance limits and guarded-ray
checks. `audit_perf5e_transport.py` and `audit_perf5e_selective_boundaries.py`
recalculate raw artifacts and record their own source SHA-256. Each output path
must be new. These checks preserve existing behavior; they are not physical
oracles and do not establish LT agreement or complete ROI/transform coverage.

## Device selective replay adapter

`device_replay.py` keeps candidate classification, FP64 winner refinement,
canonical replay, and mandatory full-oracle comparison on the GPU. It reuses a
fixed-capacity workspace. Version 2 runs the original canonical kernel once over
the full batch. Ambiguous/missed rows retain that result; other rows compare the
refined candidate against it before returning. This removes the duplicate
canonical pass on replay rows and four scratch arrays (25 bytes per capacity row),
not the mandatory full oracle. The original ordering and arithmetic are unchanged.

The adapter accepts typed, contiguous Numba device arrays of `RAY_DTYPE` only.
OptiX waits on the advertised producer stream, then classification, the shared
replay/oracle pass and bitwise certification execute on the adapter's explicit stream.
Only a 56-byte status vector is downloaded per query; launch parameters and
synchronization still involve the host. Invalid candidates, stack overflow,
cardinality errors or oracle differences raise before returning results.
The whole oracle is mandatory: omitted nearer candidates remain an unresolved
production-promotion risk, not something finite tests can prove absent.

Use the adapter as a context manager on its creating thread/context. It owns its
scene snapshot and must be recreated after geometry changes. Output device views
are borrowed until the next query or close; consumers must finish their work
before reuse. Caller-owned ray buffers must remain alive and unmodified during
the synchronous query. No product dispatch, API, file format or default changes.

`verify_perf5e_device_replay.py` tests boundary fixtures, first/two warm queries,
size changes, empty batches, producer streams, failure/recovery and fault
injection. It downloads evidence outside the adapter. `verify_perf5e_transport.py
--device-replay` additionally uses the adapter on actual CUDA batches during
isolated diagnostic transport, retaining the original CPU hybrid path. This
transport harness still uploads/downloads rays and hits at its boundary; it is
not a fully resident optical loop. Neither harness is a speed-acceptance test.
