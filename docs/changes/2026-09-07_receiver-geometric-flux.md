# Receiver geometric incident-flux contract

## Scope

The live React + FastAPI ray-tracing pipeline now treats a ray's
`power_lumen` as a luminous-flux packet when that ray intersects a finite
Receiver plane.

## Contract

- `acceptance_angle_deg` remains a hard angular pass/reject gate.
- A ray that passes the gate contributes its full surviving `power_lumen` to
  the intersected Receiver bin.
- The incidence cosine is not multiplied into the packet a second time.
- Receiver irradiance is still computed by dividing each bin's accumulated
  flux by the physical bin area.

For position-based Monte Carlo accumulation, oblique incidence is already
represented by the lower geometric probability of a ray intersecting a fixed
Receiver area. Multiplying the accepted packet by incidence cosine again
would therefore under-estimate off-axis incident flux.

## Compatibility

New results record
`_performance_summary.receiver_flux_contract = geometric_incident_flux_v2`.
Results without that field are treated as the previous
`cosine_weighted_incident_flux_v1` contract. Compare Cases does not calculate
an improvement score across the two contracts; affected Cases must be traced
again with one common version.

## Regression coverage

- Scalar Receiver intersection at 0, 45, and 80 degrees.
- NumPy batch Receiver intersection at 45 degrees.
- Acceptance-angle rejection remains active.
- Existing RT2C reflection and PERF4E Receiver-MIS tests.
