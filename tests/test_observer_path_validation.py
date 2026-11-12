"""Independent geometry evidence and isolated CPU replay, no product hit injection."""
import copy
import tempfile
from pathlib import Path
from unittest import TestCase

from leakage_simulator.observer_path_validation import validate_observer_paths, validate_receiver_evidence
from leakage_simulator.raytracer import _build_receiver_frame
from leakage_simulator.types import ReceiverSpec, ReceiverGrid, EmitterAimSpec
from test_gpu_cuda_observer import fixture


class ObserverPathValidationTests(TestCase):
    def test_blocker_immediately_before_camera_is_not_skipped_by_endpoint_tolerance(self):
        from test_observer_radiance import plate
        trace, _, _, _ = fixture()
        epsilon = trace.config.epsilon_mm
        # The reverse check starts at epsilon and cannot see this near-camera
        # blocker; the forward finite segment must cover it all the way.
        plate(trace.mesh, -11+epsilon*.5, 20)
        cell, report = self.audit(trace, [(0, 0, -5), (0, 0, -11)], None)
        self.assertEqual(report["invalid"], 1)
        self.assertEqual(cell["path_validation"]["reason"], "occluded_segment")

    def test_path_validation_replay_never_starts_a_product_tracing_job(self):
        from fastapi.testclient import TestClient
        from leakage_simulator.api import ApiRuntime, create_app
        from leakage_simulator.raytracer import DirectRayTraceInput
        from test_api_fastapi import _scene_loader
        trace, _, _, resolved = fixture()
        trace.config.max_depth = 10
        trace.emitters[0].ray_count = 1_000_000
        trace.emitters[0].aim = EmitterAimSpec(enabled=True, mode="sphere", distribution="uniform_solid_angle",
            sphere_upper_deg=0., sphere_lower_deg=90., sphere_beta_deg=180.)
        receiver = ReceiverSpec(receiver_id="witness", center=(0, 0, -12), normal=(0, 0, 1), width_mm=2, height_mm=2,
            observer_reference_path=[(0, 0, -5), (0, 0, -11)])
        prepared = DirectRayTraceInput(mesh=trace.mesh, emitters=trace.emitters, receivers=[receiver],
            optical_profiles=[resolved[0].profile], config=trace.config)
        def no_tracing(*args, **kwargs):
            self.fail("Path validation must not launch the product tracing runner")
        with tempfile.TemporaryDirectory() as directory:
            runtime = ApiRuntime(Path(directory), scene_loader=_scene_loader,
                trace_input_builder=lambda *args: prepared, trace_runner=no_tracing)
            scene = runtime.load_scene("witness.step")
            client = TestClient(create_app(runtime))
            response = client.post("/api/raytrace/validate-observer", json={"scene_token": scene["metadata"]["scene_token"]})
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertFalse(payload["simulation_run_started"])
            self.assertTrue(payload['diagnostic_forward_replay_started'])
            self.assertFalse(payload['receiver_checks'][0]['forward_replay']['included_in_flux'])
            self.assertTrue(payload["receiver_checks"][0]["path_valid"])
            self.assertEqual(payload["receiver_checks"][0]["diagnosis"], "reference_path_valid")
            self.assertIsNone(payload["receiver_checks"][0]["forward_hit_count"])
            self.assertFalse(runtime._raytrace_jobs)

    def audit(self, trace, path, resolved):
        cell = dict(position=[999, 999, 999], representative_path=path)
        report = validate_observer_paths(trace, dict(cells=[cell], measure="luminance_cd_m2"), resolved)
        return cell, report

    def test_visible_virtual_source_is_not_claimed_as_cad_or_external_hole(self):
        trace, _, _, resolved = fixture()
        cell, report = self.audit(trace, [(0, 0, -5), (0, 0, -11)], resolved)
        self.assertEqual(report["valid"], 1)
        self.assertEqual(cell["position"], [0, 0, -5])
        self.assertEqual(cell["path_validation"]["endpoint_kind"], "virtual_emitter")
        self.assertFalse(cell["path_validation"]["external_aperture_confirmed"])

    def test_solid_wall_rejects_a_path_even_if_renderer_reported_it(self):
        trace, _, _, resolved = fixture(blocked=True)
        cell, report = self.audit(trace, [(0, 0, -5), (0, 0, -11)], resolved)
        self.assertEqual(report["invalid"], 1)
        self.assertEqual(cell["path_validation"]["reason"], "occluded_segment")

    def test_floating_reflection_vertex_is_rejected(self):
        trace, _, _, resolved = fixture(True)
        cell, _ = self.audit(trace, [(0, 0, -5), (0, 0, -1), (0, 0, -11)], resolved)
        self.assertEqual(cell["path_validation"]["status"], "invalid")
        self.assertIn("off_surface", cell["path_validation"]["reason"])

    def test_actual_mirror_endpoint_passes_but_wrong_source_depth_and_material_do_not(self):
        trace, _, _, resolved = fixture(True)
        path = [(0, 0, -5), (0, 0, 0), (0, 0, -11)]
        cell, _ = self.audit(trace, path, resolved)
        self.assertEqual(cell["path_validation"]["endpoint_kind"], "cad_surface")
        trace.config.max_depth = 0
        cell, _ = self.audit(trace, path, resolved)
        self.assertEqual(cell["path_validation"]["reason"], "reflection_depth_mismatch")
        trace.config.max_depth = 2
        trace.emitters[0].normal_flip = True
        cell, _ = self.audit(trace, path, resolved)
        self.assertEqual(cell["path_validation"]["reason"], "source_position_or_emission_mismatch")
        trace.emitters[0].normal_flip = False
        for face in resolved:
            face.profile.scatter_model = "none"
        cell, _ = self.audit(trace, path, resolved)
        self.assertEqual(cell["path_validation"]["reason"], "reflection_material_mismatch")

    def test_receiver_geometry_and_zero_forward_samples_are_separate_and_grids_untouched(self):
        trace, _, _, resolved = fixture(True)
        receiver = ReceiverSpec(receiver_id="witness", center=(0, 0, -12), normal=(0, 0, 1),
            width_mm=2, height_mm=2, resolution=(1, 1), observer_reference_path=[(0, 0, -5), (0, 0, 0), (0, 0, -11)])
        grids = {receiver.receiver_id: ReceiverGrid.empty(receiver)}
        frozen = copy.deepcopy(grids)
        result = validate_receiver_evidence(trace, [_build_receiver_frame(receiver)], grids, resolved)
        self.assertTrue(result[0]["path_valid"])
        self.assertEqual(result[0]["diagnosis"], "valid_reference_path_zero_forward_samples")
        self.assertEqual(result[0]["injected_hits"], 0)
        self.assertEqual(grids, frozen)
        receiver.center = (10, 0, -12)
        result = validate_receiver_evidence(trace, [_build_receiver_frame(receiver)], grids, resolved)
        self.assertFalse(result[0]["path_valid"])
        self.assertIn("receiver_position", result[0]["diagnosis"])
