"""Finite-sensor CPU physical/diagnostic gates. No actual TV or GPU claim."""
import copy
import math
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from leakage_simulator.sensor_comparison import ComparisonOptions, run_sensor_comparison as _run_sensor_comparison, summarize_batches, FiniteSensor
from leakage_simulator.raytracer import DirectRayTraceInput, _build_receiver_frame
from leakage_simulator.types import ReceiverSpec, EmitterAimSpec
from test_gpu_cuda_observer import fixture


def run_sensor_comparison(*args, **kwargs):
    return _run_sensor_comparison(*args, **kwargs, backend="cpu")


def scene(distribution="uniform_solid_angle", mirror=False, blocked=False):
    base, _, _, resolved = fixture(mirror=mirror, blocked=blocked)
    base.emitters[0].aim = EmitterAimSpec(enabled=True, mode="sphere", distribution=distribution,
        sphere_upper_deg=0., sphere_lower_deg=90., sphere_beta_deg=0. if mirror else 180.)
    # 10-degree mirror cone fits both source and mirror; direct cone is 30 degrees.
    receiver = ReceiverSpec("common", center=(0, 0, -10), normal=(0, 0, 1), width_mm=20, height_mm=20,
        acceptance_angle_deg=10 if mirror else 30, resolution=(1, 1))
    return DirectRayTraceInput(mesh=base.mesh, emitters=base.emitters, receivers=[receiver],
        optical_profiles=[resolved[0].profile], config=replace(base.config, max_depth=1 if mirror else 0))


class SensorComparisonTests(TestCase):
    def test_replay_execution_failure_cannot_produce_a_passing_comparison(self):
        with patch('leakage_simulator.forward_path_replay.replay_forward_path',return_value={'state':'failed','reason':'replay_execution_failed'}):
            report=run_sensor_comparison(scene(),ComparisonOptions('common',batches=4,forward_samples=32,reverse_samples=32))
        self.assertFalse(report['complete'])
        self.assertEqual(report['verdict'],'inconclusive')
        self.assertEqual(report['reason'],'reference_replay_failed')
        self.assertEqual(report['forward_diagnostics'],[])
    def test_comparison_keeps_forward_diagnostics_and_original_policy_report(self):
        trace=scene(blocked=True)
        report=run_sensor_comparison(trace,ComparisonOptions('common',batches=4,forward_samples=32,reverse_samples=32))
        self.assertEqual(len(report['forward_diagnostics']),4)
        for batch in report['forward_diagnostics']:
            row=batch['report']['receivers'][0]
            self.assertTrue(row['consistent'])
            self.assertEqual(row['actual_receiver_hits'],0)
        self.assertIn('diagnostics',report['original_termination_probe'])
        self.assertEqual(report['reference_replay']['reason'],'missing_representative_path')
        self.assertFalse(trace.config.receiver_diagnostics)
    def test_same_roi_masks_half_sensor_without_changing_area_or_sample_denominator(self):
        trace = replace(scene(), analysis_roi=dict(detection_boxes=[(-10, 0, -10, 10, -8, -4)]))
        report = run_sensor_comparison(trace, ComparisonOptions("common", batches=4, forward_samples=65536, reverse_samples=2048))
        expected = .5*100*math.pi*400e-6*(1-math.cos(math.radians(30)))
        self.assertAlmostEqual(report["forward_flux_lumen"], expected, delta=expected*.1)
        self.assertAlmostEqual(report["reverse_flux_lumen"], expected, delta=expected*.05)
        self.assertEqual(report["batches"][0]["reverse_sample_count"], 2048)
        self.assertAlmostEqual(report["sensor"]["area_m2"], 400e-6)

    def test_gpu_sensor_camera_equations_match_analytic_flux_with_same_launches(self):
        from leakage_simulator.gpu_cuda_observer import reference_chunk, pack_scene
        from leakage_simulator.observer_probe import _source_geometry
        from leakage_simulator.optics import OpticalPropertyResolver
        for mirror in (False, True):
            trace = scene("lambertian", mirror=mirror)
            trace = replace(trace, observer_probe=dict(samples_per_cell=256))
            resolved = OpticalPropertyResolver(trace.mesh, trace.optical_profiles, []).resolve_all()
            sensor = FiniteSensor(_build_receiver_frame(trace.receivers[0]), 256)
            host = pack_scene(trace.mesh, _source_geometry(trace.mesh, trace.emitters), resolved)
            values, _ = reference_chunk(trace, host, sensor.plans()[0], trace.receivers[0].center, 0, 0, 256, sensor=sensor)
            alpha = math.radians(10 if mirror else 30)
            expected = (.9 if mirror else 1)*100*math.pi*400e-6*math.sin(alpha)**2
            self.assertAlmostEqual(float(values[:, 0].mean()), expected, delta=expected*.01)
            np_rays = sensor.rays(trace.config.seed, 0, 256)
            import numpy as np
            np.testing.assert_array_equal(np_rays[128:], sensor.rays(trace.config.seed, 128, 128))
            self.assertTrue(np.all(np_rays[:, 5] >= math.cos(alpha)-1e-12))

    def test_gpu_request_fails_closed_on_missing_preflight(self):
        from types import SimpleNamespace
        preflight = SimpleNamespace(available=False, reason_code="test_no_gpu")
        with patch("leakage_simulator.gpu_cuda_intersection.preflight_gpu_cuda", return_value=preflight), patch("leakage_simulator.raytracer.run_direct_ray_trace") as forward:
            with self.assertRaisesRegex(ValueError, "test_no_gpu"):
                _run_sensor_comparison(scene(), ComparisonOptions("common"))
            forward.assert_not_called()

    def test_uniform_and_lambertian_match_independent_analytic_flux(self):
        for distribution in ("uniform_solid_angle", "lambertian"):
            with self.subTest(distribution=distribution):
                trace = scene(distribution)
                frozen = copy.deepcopy((trace.emitters, trace.receivers, trace.config))
                report = run_sensor_comparison(trace, ComparisonOptions("common", batches=8, forward_samples=65536, reverse_samples=512))
                alpha = math.radians(30)
                expected = 100*math.pi*400e-6*((1-math.cos(alpha)) if distribution == "uniform_solid_angle" else math.sin(alpha)**2)
                self.assertAlmostEqual(report["reverse_flux_lumen"], expected, delta=expected*.01)
                self.assertAlmostEqual(report["forward_flux_lumen"], expected, delta=expected*.04)
                self.assertEqual(report["verdict"], "match", report)
                self.assertTrue(report["complete"])
                self.assertEqual((trace.emitters, trace.receivers, trace.config), frozen)
                self.assertNotEqual(report["batches"][0]["forward_seed"], report["batches"][0]["reverse_seed"])
                self.assertEqual(len(report["physical_input_sha256"]), 64)
                self.assertIn("original_termination_probe", report)

    def test_specular_mirror_matches_reflectance_times_analytic_lambertian_flux(self):
        trace = scene("lambertian", mirror=True)
        report = run_sensor_comparison(trace, ComparisonOptions("common", batches=4, forward_samples=16384, reverse_samples=512))
        expected = .9*100*math.pi*400e-6*math.sin(math.radians(10))**2
        self.assertAlmostEqual(report["reverse_flux_lumen"], expected, delta=expected*.015)
        self.assertAlmostEqual(report["forward_flux_lumen"], expected, delta=expected*.15)

    def test_closed_wall_zero_is_inconclusive_and_stop_never_passes(self):
        report = run_sensor_comparison(scene(blocked=True), ComparisonOptions("common", batches=4, forward_samples=128, reverse_samples=64))
        self.assertEqual(report["forward_flux_lumen"], 0.)
        self.assertEqual(report["reverse_flux_lumen"], 0.)
        self.assertEqual(report["verdict"], "inconclusive")
        self.assertEqual(report["reason"], "zero_samples_not_zero_light_proof")
        stopped = run_sensor_comparison(scene(), ComparisonOptions("common", batches=4, forward_samples=128, reverse_samples=64), lambda: True)
        self.assertFalse(stopped["complete"])
        self.assertEqual(stopped["reason"], "stopped")

    def test_finite_cone_required_and_actual_flipped_normal_used(self):
        trace = scene()
        trace.receivers[0].acceptance_angle_deg = 0
        with self.assertRaisesRegex(ValueError, "수용각"):
            run_sensor_comparison(trace, ComparisonOptions("common"))
        trace.receivers[0].acceptance_angle_deg = 30
        trace.receivers[0].normal_flip = True
        frame = _build_receiver_frame(trace.receivers[0])
        self.assertEqual(frame.normal, (0, 0, -1))
        self.assertEqual(FiniteSensor(frame, 64).frame.normal, frame.normal)

    def test_diagnostics_require_equivalence_and_effective_samples(self):
        options = ComparisonOptions("common", batches=4)
        def rows(f, r, hits=100):
            return [dict(forward_flux_lumen=f, reverse_flux_lumen=r, forward_hits=hits, reverse_positive_samples=100,
                forward_flux_square_sum=f*f/100, reverse_sample_count=100, reverse_second_moment=r*r) for _ in range(4)]
        self.assertEqual(summarize_batches(rows(1, 1), options)["verdict"], "match")
        self.assertEqual(summarize_batches(rows(1, 2), options)["verdict"], "mismatch")
        self.assertEqual(summarize_batches(rows(1, 1, 0), options)["verdict"], "inconclusive")
        self.assertEqual(summarize_batches(rows(1, 1, 2), options)["reason"], "insufficient_effective_samples")
        self.assertEqual(summarize_batches([], options)["reason"], "incomplete_batches")

    def test_api_async_start_status_stop_and_serialization(self):
        import threading
        from fastapi.testclient import TestClient
        from leakage_simulator.api import ApiRuntime, create_app
        from test_api_fastapi import _scene_loader
        entered, release = threading.Event(), threading.Event()
        def compare(trace, options, stop, progress):
            entered.set()
            release.wait(5)
            return dict(reason="stopped" if stop() else "test", complete=False, verdict="inconclusive")
        with tempfile.TemporaryDirectory() as directory:
            runtime = ApiRuntime(Path(directory), scene_loader=_scene_loader, trace_input_builder=lambda *args: scene())
            loaded = runtime.load_scene("test.step")
            request = dict(scene_token=loaded["metadata"]["scene_token"])
            client = TestClient(create_app(runtime))
            with patch("leakage_simulator.sensor_comparison.run_sensor_comparison", compare):
                response = client.post("/api/raytrace/compare-sensor/start", json=dict(request=request, options=dict(receiver_id="common")))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['input_request'],request)
                self.assertEqual(len(response.json()['input_request_sha256']),64)
                self.assertTrue(entered.wait(5))
                jid = response.json()["job_id"]
                self.assertEqual(client.post("/api/raytrace/compare-sensor/start", json=dict(request=request, options=dict(receiver_id="common"))).status_code, 400)
                self.assertEqual(client.post("/api/raytrace/start", json=request).status_code, 400)
                stopped = client.post("/api/raytrace/compare-sensor/stop", params=dict(job_id=jid))
                self.assertTrue(stopped.json()["stop_requested"])
                release.set()
                for _ in range(100):
                    status = client.get("/api/raytrace/compare-sensor/status").json()
                    if status["status"] == "stopped": break
                    time.sleep(.01)
                self.assertEqual(status["status"], "stopped")
                self.assertFalse(runtime._raytrace_jobs)
