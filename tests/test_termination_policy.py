from __future__ import annotations

from dataclasses import replace
import unittest

from test_multibounce_rt3 import two_bounce_input
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.types import OpticalProfile, RayTraceConfig


def termination_scene(count=4096, power=1e-5, basis="initial_ray_fraction", threshold=1e-9, depth=2):
    scene = two_bounce_input(depth, count, min_energy=threshold, store_paths=False)
    scene.config = replace(scene.config, min_energy_basis=basis, angle_dependent_reflectance=False)
    scene.optical_profiles = [OpticalProfile(name, 0.6, scatter_model="specular") for name in ("mirror_a", "mirror_b")]
    scene.emitters[0].power_lumen = power
    return scene


class TerminationPolicyTests(unittest.TestCase):
    def test_legacy_config_keeps_absolute_units_and_new_field_round_trips(self):
        self.assertEqual(RayTraceConfig.from_dict({}).min_energy_basis, "absolute_lumen")
        config = RayTraceConfig(min_energy_basis="initial_ray_fraction", min_energy=0.01)
        self.assertEqual(RayTraceConfig.from_dict(config.to_dict()), config)
        for options in ({"min_energy_basis": "wrong"}, {"min_energy_basis": "initial_ray_fraction", "min_energy": 1.1}):
            with self.assertRaises(ValueError):
                RayTraceConfig(**options)

    def test_relative_threshold_is_power_and_ray_count_invariant(self):
        for dispatch in ("scalar", "batch"):
            for count in (32, 64):
                for power in (1.0, 1e-12):
                    with self.subTest(dispatch=dispatch, count=count, power=power):
                        scene = termination_scene(count, power)
                        result = run_direct_ray_trace(scene, intersection_dispatch=dispatch)
                        self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"] / power, 0.36, places=11)
                        self.assertEqual(result.config.min_energy, 1e-9)
                        self.assertEqual(scene.config.min_energy_basis, "initial_ray_fraction")

    def test_absolute_legacy_cutoff_reproduces_and_reports_lost_power(self):
        result = run_direct_ray_trace(termination_scene(basis="absolute_lumen"))
        self.assertEqual(result.metrics["observer"]["total_flux_lumen"], 0)
        summary = result.metrics["_termination_summary"]
        self.assertAlmostEqual(summary["unpropagated_surface_flux_lumen"], 3.6e-6, places=14)
        self.assertGreaterEqual(summary["energy_cutoff_upper_bound_lumen"], 3.6e-6)

    def test_depth_cap_remains_separate_from_energy_cutoff(self):
        for depth, expected in ((0, 0.6), (1, 0.36), (2, 0.0)):
            with self.subTest(depth=depth):
                result = run_direct_ray_trace(termination_scene(64, 1.0, depth=depth))
                summary = result.metrics["_termination_summary"]
                self.assertAlmostEqual(summary["unpropagated_surface_flux_lumen"], expected, places=12)
                self.assertEqual(summary["energy_cutoff_upper_bound_lumen"], 0)
                self.assertEqual(summary["depth_limit_count"], 64 if depth < 2 else 0)

    def test_threshold_equality_is_not_discarded(self):
        for threshold, expected in ((0.36, 0.36), (0.37, 0.0)):
            result = run_direct_ray_trace(termination_scene(64, 1, threshold=threshold))
            self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], expected, places=12)

    def test_multiple_emitters_have_separate_nominal_packets(self):
        scene = termination_scene(128, 1.0, threshold=0.01)
        scene.emitters.append(replace(scene.emitters[0], emitter_id="weak", ray_count=256, power_lumen=1e-10))
        result = run_direct_ray_trace(scene)
        self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 0.36 * (1 + 1e-10), places=12)
        self.assertEqual(result.metrics["_termination_summary"]["energy_cutoff_upper_bound_lumen"], 0)

    def test_segments_and_batch_sizes_do_not_change_deterministic_flux(self):
        for count in (64, 128, 256):
            for batch in (16, 256):
                result = run_direct_ray_trace(termination_scene(count, 1e-12), intersection_batch_size=batch)
                self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"] / 1e-12, 0.36, places=12)

    def test_weighted_modes_do_not_claim_exact_discarded_flux(self):
        for mode in ("roulette", "primary_mis", "bounce_mis"):
            scene = termination_scene(64, 1.0, threshold=0.5)
            if mode == "roulette":
                scene.config.termination_mode = "russian_roulette"
            elif mode == "primary_mis":
                scene.config.primary_sampling_strategy = "receiver_mis"
                scene.emitters[0].direction_distribution = "lambertian"
            else:
                scene.config.bounce_sampling_strategy = "receiver_mis"
                scene.optical_profiles = [OpticalProfile(name, 0.6, scatter_model="lambertian") for name in ("mirror_a", "mirror_b")]
            summary = run_direct_ray_trace(scene).metrics["_termination_summary"]
            self.assertIsNone(summary["unpropagated_surface_flux_lumen"])
            if mode == "roulette":
                self.assertIsNone(summary["energy_cutoff_upper_bound_lumen"])

    def test_zero_weight_mis_termination_is_valid_in_reference_and_native_tapes(self):
        for planner in ("python_cpu", "numba_cpu"):
            scene = termination_scene(256, 1.0, threshold=0.5)
            scene.config.bounce_sampling_strategy = "receiver_mis"
            scene.optical_profiles = [OpticalProfile(name, 0.6, scatter_model="lambertian") for name in ("mirror_a", "mirror_b")]
            result = run_direct_ray_trace(scene, wavefront_planner=planner, wavefront_rng="counter_rng_v2", wavefront_pipeline="soa_event_tape", intersection_dispatch="batch")
            self.assertGreater(result.metrics["_performance_summary"]["bounce_sampling_zero_weight_count"], 0)
            self.assertIsNone(result.metrics["_termination_summary"]["unpropagated_surface_flux_lumen"])

    def test_relative_policy_round_trips_through_api(self):
        from leakage_simulator.raytrace_bridge import build_direct_trace_input
        from test_optical_transport_audit import override_payload, override_scene

        request = override_payload()
        request["config"].update({"min_energy_basis": "initial_ray_fraction", "min_energy": 1e-9})
        request["emitters"][0]["power_lumen"] = 1e-12
        trace_input = build_direct_trace_input(override_scene(), request)
        result = run_direct_ray_trace(trace_input)
        self.assertAlmostEqual(result.metrics["receiver"]["total_flux_lumen"] / 1e-12, 0.9, places=12)
        self.assertEqual(result.to_dict()["config"]["min_energy_basis"], "initial_ray_fraction")


if __name__ == "__main__":
    unittest.main()
