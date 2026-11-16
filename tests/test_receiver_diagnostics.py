from dataclasses import replace
import unittest
from types import SimpleNamespace

import numpy as np

from test_termination_policy import termination_scene
from leakage_simulator.receiver_diagnostics import ReceiverDiagnostics, CATEGORIES
from leakage_simulator.raytracer import _build_receiver_frame, run_direct_ray_trace
from leakage_simulator.types import RayTraceConfig, ReceiverSpec


class ReceiverDiagnosticsTests(unittest.TestCase):
    def test_power_scaling_preserves_hits_and_normalized_flux_without_energy_termination(self):
        from leakage_simulator.types import OpticalProfile
        scene = termination_scene(128, 1, threshold=0)
        scene.config = replace(scene.config, receiver_diagnostics=True, termination_mode='russian_roulette')
        scene.optical_profiles = [OpticalProfile(name, .6, scatter_model='lambertian') for name in ('mirror_a', 'mirror_b')]
        baseline = run_direct_ray_trace(scene)
        scene.emitters[0].power_lumen *= 1000
        brighter = run_direct_ray_trace(scene)
        self.assertEqual(baseline.receiver_hit_count, brighter.receiver_hit_count)
        np.testing.assert_allclose(np.asarray(baseline.receiver_grids[0].flux_lumen)*1000,
            brighter.receiver_grids[0].flux_lumen, rtol=1e-12, atol=0)
        self.assertEqual(baseline.metrics['_receiver_diagnostics']['receivers'][0]['counts'],
            brighter.metrics['_receiver_diagnostics']['receivers'][0]['counts'])

    def test_reference_neighborhood_separates_scene_counts_and_retains_real_obstruction(self):
        receiver = ReceiverSpec(receiver_id="r", center=(0, 0, 10), normal=(0, 0, -1),
            width_mm=4, height_mm=4, observer_reference_path=[(0, 0, -10), (0, 0, 0), (0, 0, 10)])
        frame = _build_receiver_frame(receiver)
        diagnostic = ReceiverDiagnostics([frame])
        mesh = SimpleNamespace(metadata=lambda face: dict(step_component_id=42, step_component_name="Cover"))
        diagnostic.observe([(0, 0, .2), (0, 0, 5), (.1, 0, 0)], [(0, 0, 1), (0, 0, 1), (1, 0, 0)],
            [1, 1, 1], [frame], RayTraceConfig(), [3, 2, np.inf], [-1]*3, [np.inf]*3,
            surface_faces=[4, 5, -1], mesh=mesh)
        row = diagnostic.rows['r']
        local = row['reference_sampling']
        self.assertEqual(row['counts']['occluded'], 2)
        self.assertEqual(local['counts']['occluded'], 1)
        self.assertEqual(local['counts']['parallel'], 1)
        self.assertEqual(local['segments_started'], 2)
        self.assertAlmostEqual(local['min_origin_distance_mm'], .1)
        sample = local['examples']['occluded']
        self.assertEqual(sample['obstruction_component_name'], 'Cover')
        self.assertEqual(sample['obstruction_component_id'], 42)
        self.assertEqual(sample['obstruction_point'], [0, 0, 3.2])
        self.assertEqual(sample['direction'], [0, 0, 1])
        self.assertNotIn('reference_sampling', ReceiverDiagnostics([_build_receiver_frame(
            replace(receiver, observer_reference_path=None))]).rows['r'])

    def test_reference_sampling_preserves_production_results_for_scalar_and_batch(self):
        for dispatch in ('scalar', 'batch'):
            scene = termination_scene(32, 1)
            scene.receivers[0].observer_reference_path = [(0, 0, 0), (0, 0, 10), (10, 0, 10), (10, 0, 20)]
            ordinary = run_direct_ray_trace(scene, intersection_dispatch=dispatch)
            scene.config = replace(scene.config, receiver_diagnostics=True)
            result = run_direct_ray_trace(scene, intersection_dispatch=dispatch)
            row = result.metrics['_receiver_diagnostics']['receivers'][0]
            local = row['reference_sampling']
            self.assertEqual(local['segments_started'], 32)
            self.assertEqual(local['counts']['accepted'], 32)
            self.assertTrue(row['consistent'])
            self.assertEqual(ordinary.receiver_hit_count, result.receiver_hit_count)
            np.testing.assert_array_equal(ordinary.receiver_grids[0].flux_lumen, result.receiver_grids[0].flux_lumen)

    def test_exclusive_reasons_and_occlusion_before_acceptance(self):
        frame = _build_receiver_frame(ReceiverSpec(receiver_id="r", center=(0, 0, 10),
            normal=(0, 0, -1), width_mm=4, height_mm=4, acceptance_angle_deg=30))
        d = ReceiverDiagnostics([frame])
        origins = [(0,0,0), (0,0,0), (10,0,0), (0,0,0), (0,0,20), (-10,0,0), (0,0,0)]
        directions = [(1,0,0), (0,0,-1), (0,0,1), (0,0,1), (0,0,-1), (1,0,1), (0,0,1)]
        directions = np.asarray(directions, float)
        directions /= np.linalg.norm(directions, axis=1)[:,None]
        d.observe(origins, directions, np.ones(7), [frame], RayTraceConfig(),
            [np.inf,np.inf,np.inf,5,np.inf,np.inf,np.inf], [-1,-1,-1,-1,-1,-1,0], [np.inf]*6+[10])
        counts = d.rows['r']['counts']
        for key in ('parallel','behind_origin','outside_area','occluded','back_face','outside_acceptance','accepted'):
            self.assertEqual(counts[key], 1, key)
        self.assertEqual(sum(counts.values()), 7)
        self.assertEqual(sum(d.rows['r']['segment_flux_lumen'].values()), 7)

    def test_real_trace_preserves_flux_and_counts_across_bounces_and_cutoffs(self):
        for depth in (0, 1, 2):
            for threshold in (1e-9, .9):
                scene = termination_scene(32, 1, threshold=threshold, depth=depth)
                ordinary = run_direct_ray_trace(scene)
                scene.config = replace(scene.config, receiver_diagnostics=True)
                diagnostic = run_direct_ray_trace(scene)
                self.assertEqual(ordinary.receiver_hit_count, diagnostic.receiver_hit_count)
                np.testing.assert_allclose(ordinary.receiver_grids[0].flux_lumen,
                                           diagnostic.receiver_grids[0].flux_lumen, rtol=1e-12)
                report = diagnostic.metrics['_receiver_diagnostics']
                self.assertTrue(report['complete'])
                self.assertEqual(report['primary_ray_count'], 32)
                self.assertEqual(report['termination'], ordinary.metrics['_termination_summary'])
                for row in report['receivers']:
                    self.assertTrue(row['consistent'])
                    self.assertEqual(sum(row['counts'].values()), row['segments_tested'])
                    self.assertEqual(set(row['counts']), set(CATEGORIES))

    def test_disabled_has_no_diagnostic_report(self):
        self.assertNotIn('_receiver_diagnostics', run_direct_ray_trace(termination_scene(8)).metrics)

    def test_roi_exclusion_is_a_real_arrival_and_not_a_hit(self):
        scene = termination_scene(32, 1)
        scene.config = replace(scene.config, receiver_diagnostics=True)
        scene.analysis_roi = {'detection_boxes': [(20, 21, -1, 1, 0, 30)]}
        for dispatch in ('scalar', 'batch'):
            result = run_direct_ray_trace(scene, intersection_dispatch=dispatch)
            row = result.metrics['_receiver_diagnostics']['receivers'][0]
            self.assertEqual(row['counts']['outside_roi'], 32)
            self.assertEqual(row['arrived_count'], 32)
            self.assertEqual(row['actual_receiver_hits'], 0)
            self.assertTrue(row['consistent'])

    def test_other_receiver_stops_the_segment_before_the_target(self):
        scene = termination_scene(32, 1)
        scene.config = replace(scene.config, receiver_diagnostics=True)
        nearer = replace(scene.receivers[0], receiver_id='near', center=(10, 0, 15))
        scene.receivers.insert(0, nearer)
        result = run_direct_ray_trace(scene)
        near, far = result.metrics['_receiver_diagnostics']['receivers']
        self.assertEqual(near['actual_receiver_hits'], 32)
        self.assertEqual(far['actual_receiver_hits'], 0)
        self.assertEqual(far['counts']['other_receiver_first'], 32)
        self.assertEqual(far['arrived_count'], 0)
        self.assertTrue(near['consistent'] and far['consistent'])

    def test_stopped_result_is_explicitly_partial(self):
        scene = termination_scene(32)
        scene.config = replace(scene.config, receiver_diagnostics=True)
        result = run_direct_ray_trace(scene, should_stop=lambda: True)
        self.assertFalse(result.metrics['_receiver_diagnostics']['complete'])

    def test_stochastic_transport_preserves_counter_samples(self):
        from leakage_simulator.types import OpticalProfile
        scene = termination_scene(128, 1)
        scene.config = replace(scene.config, termination_mode='russian_roulette')
        scene.optical_profiles = [OpticalProfile(name, .6, scatter_model='lambertian')
                                  for name in ('mirror_a', 'mirror_b')]
        ordinary = run_direct_ray_trace(scene)
        scene.config = replace(scene.config, receiver_diagnostics=True)
        diagnostic = run_direct_ray_trace(scene)
        self.assertEqual(ordinary.receiver_hit_count, diagnostic.receiver_hit_count)
        np.testing.assert_allclose(ordinary.receiver_grids[0].flux_lumen,
                                   diagnostic.receiver_grids[0].flux_lumen, rtol=1e-12)
        self.assertEqual(ordinary.metrics['_termination_summary'], diagnostic.metrics['_termination_summary'])

    def test_config_validates_and_roundtrips(self):
        config = RayTraceConfig(receiver_diagnostics=True)
        self.assertEqual(RayTraceConfig.from_dict(config.to_dict()), config)
        with self.assertRaises(ValueError):
            RayTraceConfig(receiver_diagnostics='false')
