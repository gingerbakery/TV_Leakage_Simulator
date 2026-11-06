# In-app sensor comparison batch diagnostics

The sensor comparison dialog now includes a collapsed-by-default 회차별 결과 section. It uses existing report batches and exposes forward/reverse lumen estimates, positive/hit counts and effective sample sizes for each completed batch. A compact distribution row shows positive batches and the largest batch share for each direction. Explanations are in the existing keyboard-focusable (?) help. Missing historical fields display — rather than a fabricated zero. The input labels now explicitly say 표본 / 회; defaults and analysis settings are unchanged.

The forward effective sample calculation uses squared weighted sums; reverse uses the mean, second moment and launch count. These diagnostics identify concentration, not its cause. They do not prove a forward/reverse match or physical correctness and do not fix a transport estimator. No source geometry or materials leave the application. Existing JSON export remains optional. A retained server report can be inspected without tracing again; restarting the server can lose that report.

Validation: five frontend tests passed across sensor-batch-diagnostics and sensor-comparison-dialog, including known contribution moments, missing historical values, default collapse and unchanged source data. Production frontend build passed. No actual NVIDIA GPU test or user-scene reproduction was performed.
