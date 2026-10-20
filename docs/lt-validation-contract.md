# LT validation internal contract

All lengths are mm, photometric flux lm, illuminance lx. No luminance/nit inputs.
Missing or unverified fields are null, never inferred from the other dataset.
All HTTP bodies/results are JSON except file upload bodies (raw file bytes).

## Dataset (schema `lt-validation.v1`)

```ts
type Issue = {code: string; severity: 'error'|'warning'; message: string}
type Vec3 = [number,number,number]
type Source = {
  id: string; name: string;
  total_flux_lm: number|null;
  flux_reference: 'aim_region'|'emitted'|null;
  center_mm: Vec3|null; u_axis: Vec3|null; v_axis: Vec3|null;
  width_mm: number|null; height_mm: number|null;
  // Effective outward direction. Unknown if both sides / convention unverified.
  normal: Vec3|null;
  angular_distribution: string|null;
  aim: null|{
    center_mm: Vec3|null; u_axis: Vec3|null; v_axis: Vec3|null;
    width_mm: number|null; height_mm: number|null; distribution: string|null
  };
}
type Receiver = {
  id: string; name: string;
  quantity: 'photometric'|'radiometric'|'unknown';
  raw: boolean|null;
  // flux_lm[vIndex][uIndex], edges strictly increasing, only VERIFIED order.
  flux_lm: number[][]|null;
  u_edges_mm: number[]|null; v_edges_mm: number[]|null;
  center_mm: Vec3|null; u_axis: Vec3|null; v_axis: Vec3|null;
  normal: Vec3|null; // Effective receiving normal, already applies normal_flip.
  acceptance_angle_deg: number|null;
  aperture: 'none'|null;
  total_flux_lm: number|null; // Stored TotalPower or independently summed BitSam grid.
  // Default 1e-12 absolute + 1e-6 relative; photographed rounded totals may
  // explicitly declare up to 5e-8 lm rounding tolerance (not arbitrary scaling).
  total_flux_tolerance_lm?: number;
  issues: Issue[];
  provenance?: Record<string,unknown>;
}
type Dataset = {
  schema: 'lt-validation.v1';
  kind: 'bitsam'|'lighttools'|'manual';
  filename: string;
  receivers: Receiver[];
  sources: Source[];
  issues: Issue[];
  provenance?: Record<string,unknown>;
}
```

Reader functions: `read_bitsam(path: Path, filename: str | None = None)` and
`read_lts(path: Path, filename: str | None = None)` return Dataset dictionaries.
Unreadable/invalid container raises ValueError. Unavailable result/metadata returns
diagnostic Dataset; receivers can retain valid totals while flux grid stays null.
Use issue severity error only for unrecoverable data errors; missing metadata is
detected by comparison, so a user can explicitly supplement unknown LT conditions.
Any unverified mesh ordering must leave flux_lm null (can retain raw flat values
in provenance for explicit coordinate mapping). Never silently fix negatives/NaN.
When extracted LTS provenance is present, supplemented grids must retain its
original dimensions, flux array, and explicitly confirmed storage order.
Missing original arrays, reshaped grids, or edited cells block comparison.
Cell equality allows only unit-conversion roundoff (1e-12 relative / 1e-18 lm
absolute); it does not rescale the grid to match TotalPower. Provenance is
consistency metadata, not cryptographic proof of an external file's origin.

## HTTP

- `POST /api/lt-validation/import?filename=...` raw file body. `.bitsam`, `.lts`,
  or `.json` (canonical Dataset for manually supplied/previously saved LT data).
  Returns Dataset. Does not import CAD or modify simulator scene/job state.
- `POST /api/lt-validation/compare` body:
  `{cases: [{id, name, sim: Dataset|null, lt: Dataset|null,
  sim_receiver_id: string|null, lt_receiver_id: string|null}], baseline_id: string}`.
- Response `{cases: CaseResult[], baseline_id: string}`.
- `CaseResult`: `{id, name, status:'ready'|'blocked', issues:Issue[],
  sim_total_lm:number|null, lt_total_lm:number|null,
  flux_deviation_pct:number|null, pearson:number|null, overlap_pct:number|null,
  sim_relative_pct:number|null, lt_relative_pct:number|null,
  sim_change_pct:number|null, lt_change_pct:number|null,
  plots: PlotData|null}`.
- `PlotData`: `{u_mm:number[], v_mm:number[], u_edges_mm:number[], v_edges_mm:number[],
  sim_flux_lm:number[][], lt_flux_lm:number[][],
  sim_illuminance_lx:number[][], lt_illuminance_lx:number[][],
  sim_pct:number[][], lt_pct:number[][], difference_pp:number[][],
  column_sim_pct:number[], column_lt_pct:number[], column_difference_pp:number[],
  row_sim_pct:number[], row_lt_pct:number[], row_difference_pp:number[]}`.
  `u_mm/v_mm` are centers; edges have N+1 values. Illuminance divides flux by
  each cell area in m², without luminance factors. All returned numbers are finite.

Only ready pairs get deviation/Pearson/overlap/plots. Total-only rows remain useful
for incomplete inputs. Baseline ratios require verified comparable conditions
within the same tool across cases; do not require cross-tool pair to be ready.
No resampling/interpolation, guessed flips, or similarity optimization. Input
axes can differ in storage order only when actual coordinates prove a bijection.
Grid dimensions/cell boundaries, receiver pose/effective normal/acceptance,
source power basis/geometry/aim are required matching conditions. Position
tolerance is 1e-6 mm, unit-vector tolerance 1e-6, number relative tolerance 1e-6.
Pearson over all cells; constant/zero cases return undefined null with reason.
U/V counts must match before axis mapping (non-square swaps are blocked).
V1 supports lambertian/isotropic emitted sources and uniform_target_area
rectangular Aim sources; other distributions/geometries are blocked explicitly.
No pass/fail performance rating. Raw validity/comparability states only.

UI: Result panel always accessible LT 검증 button. Dialog workspace persists
while closed; no mutation to live simulator. Add/rename/remove cases, baseline,
file upload, receiver choice, raw manual LT data with explicit geometry/conditions.
Explanations live behind HelpTooltip, mandatory warnings stay visible. Plots use
physical local U/V, normalized %/cell, difference pp; each marginal sums 100%.
