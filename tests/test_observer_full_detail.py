from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from leakage_simulator.escape_collection import EscapeCollector
from leakage_simulator.observer_probe import sampling_plan, trace_observer_paths, validate_settings
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import OpticalProfile, ReceiverGrid
from test_observer_probe import setup
from test_escape_collection import receivers


class FullDetailObserverTests(TestCase):
    def context(self):
        trace, _ = setup()
        trace.observer_probe.update(search_mode="full_detail", spacing_mm=1., samples_per_cell=2)
        detectors = receivers()
        bounds = {"minimum": [-10,-10,-10], "maximum": [10,10,10], "direction": "neg_z"}
        collector = EscapeCollector(bounds, detectors, {r.receiver_id: ReceiverGrid.empty(r) for r in detectors})
        profiles = [OpticalProfile(profile_id="black", reflectance=0, absorption=1, scatter_model="none")]
        resolved = OpticalPropertyResolver(trace.mesh, profiles, []).resolve_all()
        return trace, collector, resolved, profiles, bounds

    def test_mm_plan_is_rectangular_and_keeps_spacing_when_angles_increase(self):
        trace, _, _, _, _ = self.context()
        plan = sampling_plan(trace.observer_probe, (-50,-20,-5), (50,20,5), "neg_z", 1)
        self.assertEqual((plan[0]["columns"], plan[0]["rows"]), (100,40))
        self.assertEqual(plan[0]["grid_samples"], 8000)
        trace.observer_probe.update(range_deg=2, step_deg=1)
        many = sampling_plan(trace.observer_probe, (-50,-20,-5), (50,20,5), "neg_z", 1)
        self.assertEqual(len(many), 25)
        for view in many:
            self.assertLessEqual(view["width"]/view["columns"], 1)
            self.assertLessEqual(view["height"]/view["rows"], 1)

    def test_large_plans_exceed_the_preview_cap_without_allocating_grid_arrays(self):
        trace, _, _, _, _ = self.context()
        trace.observer_probe.update(horizontal_deg=85, vertical_deg=20, range_deg=10, step_deg=2, spacing_mm=2)
        plans = sampling_plan(trace.observer_probe, (-500,-300,-5), (500,300,5), "neg_z", 1)
        self.assertEqual(len(plans), 99)
        self.assertGreater(sum(p["grid_samples"]+p["targeted_samples"] for p in plans), 500_000)
        trace.observer_probe["spacing_mm"] = .1
        with self.assertRaisesRegex(ValueError, "budget exceeded"):
            sampling_plan(trace.observer_probe, (-500,-300,-5), (500,300,5), "neg_z", 1)

    def test_all_cells_are_sampled_and_chunk_size_does_not_change_detections(self):
        trace, collector, resolved, _, _ = self.context()
        progress = []
        first = trace_observer_paths(trace, collector, resolved, batch_size=7, progress_callback=lambda n,total: progress.append((n,total)))
        second = trace_observer_paths(trace, collector, resolved, batch_size=8192)
        self.assertEqual(first["cells"], second["cells"])
        self.assertEqual(first["direct_count"], second["direct_count"])
        self.assertGreater(first["direct_count"], 0)
        self.assertEqual(first["search_contract"], "observer_full_spacing_v1")
        self.assertEqual(first["attempted_count"], 1600)
        self.assertEqual(first["planned_count"], 1600)
        self.assertEqual(first["views"][0]["sampling_resolution"], [20,20])
        self.assertEqual(first["views"][0]["sample_spacing_mm"], [1,1])
        self.assertEqual(progress[0], (0,1600))
        self.assertEqual(progress[-1], (1600,1600))

    def test_display_merging_is_bounded_without_discarding_path_counts(self):
        trace, collector, resolved, _, _ = self.context()
        full = trace_observer_paths(trace, collector, resolved)
        with patch("leakage_simulator.observer_probe.FULL_DETAIL_CELL_LIMIT", 8):
            merged = trace_observer_paths(trace, collector, resolved)
        self.assertLessEqual(len(merged["cells"]), 8)
        self.assertGreater(merged["views"][0]["display_stride"], 1)
        self.assertEqual(merged["direct_count"], full["direct_count"])
        self.assertEqual(sum(c["direct_count"] for c in merged["cells"]), full["direct_count"])
        self.assertEqual(merged["views"][0]["sample_spacing_mm"], [1,1])

    def test_stop_preserves_a_partial_result(self):
        trace, collector, resolved, _, _ = self.context()
        progress = []
        result = trace_observer_paths(trace, collector, resolved, lambda: bool(progress), batch_size=7,
            progress_callback=lambda n,total: progress.append((n,total)) if n else None)
        self.assertTrue(result["stopped"])
        self.assertFalse(result["complete"])
        self.assertGreater(result["attempted_count"], 0)
        self.assertLess(result["attempted_count"], result["planned_count"])

    def test_engine_progress_covers_forward_and_observer_work_without_changing_flux_counts(self):
        trace, _, _, profiles, bounds = self.context()
        direct = DirectRayTraceInput(mesh=trace.mesh, emitters=trace.emitters, receivers=receivers(),
            optical_profiles=profiles, config=trace.config, observer_probe=trace.observer_probe, escape_collection=bounds)
        progress = []
        result = run_direct_ray_trace(direct, progress_callback=lambda n,total: progress.append((n,total)))
        probe = result.metrics["_observer_probe_summary"]
        self.assertEqual(result.total_rays, 16)
        self.assertEqual(progress[-1], (16+probe["planned_count"], 16+probe["planned_count"]))
        fractions = [n/total for n,total in progress]
        self.assertEqual(fractions, sorted(fractions))
        self.assertLessEqual(result.metrics["_escape_direction_summary"]["selected_count"], 16)

    def test_invalid_modes_and_spacing_fail_closed(self):
        trace, _, _, _, _ = self.context()
        for value in (0, True, float("nan"), float("inf"), 101):
            spec = dict(trace.observer_probe, spacing_mm=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_settings(spec)
        with self.assertRaises(ValueError):
            validate_settings(dict(trace.observer_probe, search_mode="unknown"))

    def test_bsdf_paths_use_measured_support_and_zero_reflectance_stays_zero(self):
        from test_bsdf_transport import synthetic_asset
        for reflectance in (.3, 0):
            trace, _ = setup(mirror=True)
            trace.observer_probe.update(search_mode="full_detail", spacing_mm=2., samples_per_cell=2)
            asset = synthetic_asset(reflectance=reflectance)
            direct = DirectRayTraceInput(mesh=trace.mesh, emitters=trace.emitters, receivers=receivers(),
                optical_profiles=[OpticalProfile(profile_id="mirror", reflectance=0, scatter_model="none", bsdf_asset_id=asset["id"])],
                bsdf_assets=[asset], config=trace.config, observer_probe=trace.observer_probe,
                escape_collection={"minimum": [-10,-10,-10], "maximum": [10,10,10], "direction": "neg_z"})
            result = run_direct_ray_trace(direct)
            count = result.metrics["_observer_probe_summary"]["reflected_count"]
            if reflectance:
                self.assertGreater(count, 0)
            else:
                self.assertEqual(count, 0)
