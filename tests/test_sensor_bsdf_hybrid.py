"""BSDF hybrid routing with CPU geometry doubles; never actual CUDA proof."""
from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from leakage_simulator.bsdf_transport import BsdfTransport, PolarTable
from leakage_simulator.gpu_cuda_observer import ObserverGpuUnsupported
from leakage_simulator.observer_radiance import trace_observer_radiance
from leakage_simulator.raytracer import _IntersectionDispatchStats, _build_receiver_frame
from leakage_simulator.sensor_comparison import FiniteSensor, gpu_proven
from leakage_simulator.types import ReceiverSpec
from test_gpu_cuda_observer import fixture


class SensorBsdfHybridTests(TestCase):
    def setup_scene(self):
        trace, _, _, resolved = fixture(True)
        table = PolarTable.build(dict(latitudesDeg=[90.,45.,0.], longitudesDeg=[0.,90.,180.,270.,360.], values=[[1.]*5]*3))
        for item in resolved:
            item.profile.bsdf_asset_id = "measured"
            item.profile._bsdf_transport = BsdfTransport("measured",550.,(550.,),((550.,),),((table,),),(((0.,90.),(.9,.9)),))
        receiver = ReceiverSpec("sensor", center=(0,0,-10), normal=(0,0,1), width_mm=2, height_mm=2, acceptance_angle_deg=10)
        sensor = FiniteSensor(_build_receiver_frame(receiver), 64)
        collector = SimpleNamespace(minimum=receiver.center, maximum=receiver.center, direction="neg_z", regions=[])
        trace.observer_probe["samples_per_cell"] = 64
        return trace, resolved, sensor, collector

    def test_bsdf_preserved_and_all_queries_route_to_forced_cuda_batches(self):
        trace, resolved, sensor, collector = self.setup_scene()
        baseline = trace_observer_radiance(trace, collector, resolved, sensor=sensor)
        trace.config = replace(trace.config, compute_backend="gpu_cuda")
        queries = []
        def fake_cuda(stats, mesh, rays, *, allow_hybrid_cpu=True):
            queries.append(allow_hybrid_cpu)
            stats.gpu_cuda_gpu_attempt_count += 1
            stats.gpu_cuda_gpu_success_count += 1  # Mock evidence only, no GPU execution.
            return mesh.intersect_rays(rays, backend="bvh")
        with patch("leakage_simulator.gpu_cuda_observer.ResidentObserver", side_effect=ObserverGpuUnsupported("observer_resident_bsdf_requires_host")), patch.object(_IntersectionDispatchStats,"intersect_batch",fake_cuda):
            result = trace_observer_radiance(trace, collector, resolved, sensor=sensor)
        self.assertTrue(queries)
        self.assertTrue(all(value is False for value in queries))
        self.assertEqual(result["flux_lumen"], baseline["flux_lumen"])
        self.assertEqual(result["sample_second_moment"], baseline["sample_second_moment"])
        self.assertGreater(result["flux_lumen"], 0.)
        self.assertEqual(result["performance"]["compute_execution_state"], "gpu_mixed")
        self.assertEqual(result["performance"]["observer_bsdf_evaluation_backend"], "cpu")
        self.assertTrue(all(item.profile.bsdf_asset_id == "measured" for item in resolved))

    def test_cpu_only_batches_cannot_pass_gpu_execution_gate(self):
        trace, resolved, sensor, collector = self.setup_scene()
        trace.config = replace(trace.config, compute_backend="gpu_cuda")
        def cpu_only(stats, mesh, rays, **kwargs):
            stats.reference_batch_count += 1
            return mesh.intersect_rays(rays, backend="bvh")
        with patch("leakage_simulator.gpu_cuda_observer.ResidentObserver", side_effect=ObserverGpuUnsupported("observer_resident_bsdf_requires_host")), patch.object(_IntersectionDispatchStats,"intersect_batch",cpu_only):
            result = trace_observer_radiance(trace, collector, resolved, sensor=sensor)
        self.assertFalse(gpu_proven(result["performance"]))
        self.assertEqual(result["performance"]["compute_execution_state"], "gpu_requested_cpu_only")

    def test_driver_or_other_resident_failure_does_not_enable_bsdf_hybrid(self):
        trace, resolved, sensor, collector = self.setup_scene()
        trace.config = replace(trace.config, compute_backend="gpu_cuda")
        with patch("leakage_simulator.gpu_cuda_observer.ResidentObserver", side_effect=RuntimeError("device_error")), patch.object(_IntersectionDispatchStats,"intersect_batch") as batch:
            with self.assertRaisesRegex(ValueError, "device_error"):
                trace_observer_radiance(trace, collector, resolved, sensor=sensor)
            batch.assert_not_called()
