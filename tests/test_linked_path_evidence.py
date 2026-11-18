"""Synthetic CPU evidence gates; no actual CUDA or real-TV accuracy claim."""
import copy
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

import numpy as np

from leakage_simulator.comparison_path_evidence import detected_paths, pair_detected_paths
from leakage_simulator.forward_path_replay import inspect_receiver_last_leg
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.sensor_comparison import ComparisonOptions
from leakage_simulator.reverse_diagnostics import empty_stages, record_stages, stage_record
from test_termination_policy import termination_scene
from test_sensor_comparison import scene, run_sensor_comparison


class LinkedPathEvidenceTests(TestCase):
    def configured(self, threshold=.5):
        trace = termination_scene(32, 1., threshold=threshold)
        trace.emitters[0].seed = 1234
        trace.receivers[0].observer_reference_path = [(0,0,0),(0,0,10),(10,0,10),(10,0,20)]
        comparison = replace(trace, config=replace(trace.config, receiver_diagnostics=True, store_ray_paths=True,
            max_stored_paths=16, min_energy=0., termination_mode='russian_roulette'))
        return trace, comparison

    def test_actual_paths_are_bounded_have_identity_and_do_not_change_transport(self):
        trace, diagnostic = self.configured()
        baseline = run_direct_ray_trace(replace(diagnostic, config=replace(diagnostic.config, store_ray_paths=False)))
        result = run_direct_ray_trace(diagnostic, intersection_batch_size=8)
        self.assertEqual(result.receiver_hit_count, 32)
        np.testing.assert_array_equal(result.receiver_grids[0].flux_lumen, baseline.receiver_grids[0].flux_lumen)
        self.assertEqual(result.metrics['_termination_summary'], baseline.metrics['_termination_summary'])
        capture = detected_paths(result, diagnostic.receivers[0].receiver_id)
        self.assertEqual(capture['recorded_count'], 16)
        self.assertTrue(capture['truncated'])
        self.assertFalse(capture['complete'])
        self.assertEqual(len({row['ray_id'] for row in capture['samples']}), 16)
        for sample in capture['samples']:
            self.assertEqual(sample['reflection_count'], 2)
            expected_distance = min(np.linalg.norm(np.asarray(point)-np.asarray(diagnostic.receivers[0].observer_reference_path[-2])) for point in sample['points'][1:-1])
            self.assertAlmostEqual(sample['selected_point_min_reflection_distance_mm'], expected_distance)
            self.assertLessEqual(sample['selected_point_min_segment_distance_mm'], expected_distance+1e-12)
            self.assertEqual(len(sample['points']), 4)
            self.assertAlmostEqual(sample['initial_packet_lumen'], 1/32)
            self.assertAlmostEqual(sample['flux_lumen'], .36/32)

    def test_paired_ray_proves_specific_energy_stop_and_preserves_input(self):
        trace, diagnostic = self.configured()
        frozen = copy.deepcopy((trace.config.to_dict(), trace.emitters[0].to_dict(), trace.receivers[0].to_dict()))
        capture = detected_paths(run_direct_ray_trace(diagnostic), trace.receivers[0].receiver_id)
        # Nonzero primary IDs must reproduce the same counter stream, not reset to Ray 0.
        capture['samples'] = capture['samples'][5:8]
        pair = pair_detected_paths(trace, diagnostic, capture, 2)
        self.assertEqual(pair['checked_count'], 2)
        for item in pair['records']:
            self.assertEqual((item['state'],item['reason']), ('verified','energy_cutoff'))
            self.assertEqual(item['matched_surface_count'], 2)
            self.assertFalse(item['included_in_flux'])
            self.assertEqual(item['original_hits'], 0)
        self.assertEqual(frozen,(trace.config.to_dict(), trace.emitters[0].to_dict(), trace.receivers[0].to_dict()))

    def test_reproduction_mismatch_cannot_become_a_causal_stop(self):
        trace, diagnostic = self.configured()
        capture = detected_paths(run_direct_ray_trace(diagnostic), trace.receivers[0].receiver_id)
        capture['samples'][0]['reflection_seed'] = 'wrong'
        pair = pair_detected_paths(trace, diagnostic, capture, 1)
        self.assertEqual(pair['records'][0]['state'],'unverified')
        self.assertEqual(pair['records'][0]['reason'],'comparison_ray_reproduction_unverified')

    def test_stochastic_primary_identity_survives_nonzero_index(self):
        from leakage_simulator.types import OpticalProfile
        trace, diagnostic = self.configured(0.)
        diagnostic.optical_profiles = [OpticalProfile(name,.9,scatter_model='lambertian') for name in ('mirror_a','mirror_b')]
        # Large sensor catches a subset of stochastic paths.
        diagnostic.receivers[0] = replace(diagnostic.receivers[0], width_mm=100, height_mm=100)
        diagnostic.emitters[0].ray_count = 256
        trace = replace(diagnostic, config=replace(diagnostic.config, store_ray_paths=False))
        capture = detected_paths(run_direct_ray_trace(diagnostic), diagnostic.receivers[0].receiver_id)
        self.assertGreater(len(capture['samples']),0)
        capture['samples'] = [sample for sample in capture['samples'] if sample['primary_index'] > 0]
        self.assertTrue(capture['samples'])
        pair = pair_detected_paths(trace, diagnostic, capture, 2)
        self.assertTrue(all(item['reason']=='original_also_detected' for item in pair['records']),pair)

    def test_local_leg_separates_area_behind_backface_roi_and_acceptance(self):
        trace, _ = self.configured()
        receiver = trace.receivers[0]
        self.assertEqual(inspect_receiver_last_leg(trace,receiver)['reason'],'accepted')
        for expected, sensor in (
            ('outside_area',replace(receiver,center=(30,0,20))),
            ('behind_origin',replace(receiver,center=(10,0,5))),
            ('back_face',replace(receiver,normal=(0,0,1))),
            ('outside_acceptance',replace(receiver,normal=(.7071067811865476,0,-.7071067811865476),acceptance_angle_deg=1)),
        ):
            self.assertEqual(inspect_receiver_last_leg(trace,sensor)['reason'],expected)
        trace.analysis_roi = {'detection_boxes':[(40,50,40,50,40,50)]}
        self.assertEqual(inspect_receiver_last_leg(trace,receiver)['reason'],'outside_roi')

    def test_last_leg_occlusion_is_local_and_excluded_from_flux(self):
        trace = scene(blocked=True)
        receiver = trace.receivers[0]
        receiver.observer_reference_path = [(0,0,0),(0,0,-10)]
        result = inspect_receiver_last_leg(trace,receiver)
        self.assertEqual(result['reason'],'occluded')
        self.assertIsNotNone(result['obstruction_point'])
        self.assertFalse(result['included_in_flux'])

    def test_comparison_records_paths_pairs_reverse_stages_and_stop(self):
        trace = scene()
        report = run_sensor_comparison(trace,ComparisonOptions('common',batches=4,forward_samples=32,reverse_samples=32))
        self.assertEqual(len(report['path_evidence']),4)
        self.assertLessEqual(sum(row.get('policy_pair',{}).get('checked_count',0) for row in report['path_evidence']),16)
        self.assertEqual(sum(row['detected_paths']['actual_hit_count'] for row in report['path_evidence']),report['forward_hits'])
        for row in report['reverse_diagnostics']:
            stage = row['stages']
            self.assertTrue(stage['available'])
            self.assertEqual(sum(stage['counts'].values()),32)
            self.assertEqual(stage['counts']['positive'],32)
        self.assertFalse(trace.config.receiver_diagnostics)

    def test_forward_sampling_records_packet_factors_and_paired_budget(self):
        trace = scene("lambertian", mirror=True)
        trace.optical_profiles[0].scatter_model = "lambertian"
        report = run_sensor_comparison(trace, ComparisonOptions("common", batches=4, forward_samples=2048, reverse_samples=32))
        source = report["forward_sampling"]["source"]
        mis = report["forward_sampling"]["receiver_mis"]
        self.assertEqual(source["sample_count"], 8192)
        self.assertEqual(mis["sample_count"], 8192)
        self.assertEqual([row["seed"] for row in source["batches"]], [row["seed"] for row in mis["batches"]])
        self.assertGreater(mis["hits"], 16)
        captured_bounces = 0
        for batch in report["path_evidence"]:
            paths = batch["detected_paths"]
            self.assertEqual(paths["max_single_ray_flux_lumen"], max((p["flux_lumen"] for p in paths["samples"]), default=0.0))
            for path in paths["samples"]:
                for bounce in path["reflections"]:
                    captured_bounces += 1
                    self.assertEqual(bounce["sampling_pdf_capture_status"], "captured", bounce)
                    self.assertTrue(bounce["sampled_pdfs_match_reconstruction"], bounce)
                    self.assertTrue(bounce["sampled_weight_matches_packet"], bounce)
                    self.assertTrue(bounce["pdf_matches_packet_weight"], bounce)
                    self.assertAlmostEqual(bounce["effective_reflectance"], .9)
                    self.assertAlmostEqual(bounce["outgoing_packet_lumen"],
                        bounce["incoming_packet_lumen"] * bounce["effective_reflectance"]
                        * bounce["applied_sampling_weight"] / bounce["roulette_survival_probability"])
        self.assertGreater(captured_bounces, 0)

    def test_reverse_connection_counters_partition_attempts(self):
        summary = empty_stages()
        record_stages(summary, [
            (0, 0., None, 0, None, 0., 0., stage_record(False, False, 1, (0, 0, 0, 0, 0))),
            (1, 0., None, 0, None, 0., 0., stage_record(False, False, 1, (1, 2, 3, 0, 0))),
            (2, 0., None, 0, None, 0., 0., stage_record(True, False, 1, (0, 0, 0, 1, 0))),
        ])
        missing = summary["source_not_encountered_connections"]
        self.assertEqual(missing["no_attempt_samples"], 1)
        self.assertEqual(missing["attempted_samples"], 1)
        self.assertEqual(missing["attempts"], 6)
        self.assertEqual(missing["occluded"], 3)
        self.assertEqual(missing["direction_rejected"], 2)
        self.assertEqual(summary["source_connections"]["visible_emission_zero"], 1)

    def test_reverse_missing_records_and_zero_have_distinct_meanings(self):
        summary = empty_stages()
        items = [(0, 0.,None,0,None,0.,0.,stage_record(False,False,1)),
                 (1, 0.,None,0,None,0.,0.,stage_record(True,False,1)),
                 (2, 0.,None,0,None,0.,0.,stage_record(True,True,4)),
                 (3, 1.,None,0,None,0.,0.,stage_record(True,True,2))]
        record_stages(summary,items)
        self.assertEqual(summary['counts']['positive'],1)
        self.assertEqual(summary['counts']['source_not_encountered'],1)
        self.assertEqual(summary['counts']['source_emission_zero'],1)
        self.assertEqual(summary['counts']['contribution_zero'],1)
        record_stages(summary,[(4,0.,None)])
        self.assertFalse(summary['available'])
        self.assertEqual(summary['missing_records'],1)

    def test_resident_equation_counters_cover_same_committed_samples(self):
        from leakage_simulator.gpu_cuda_observer import reference_chunk, pack_scene
        from leakage_simulator.observer_probe import _source_geometry
        from leakage_simulator.optics import OpticalPropertyResolver
        from leakage_simulator.raytracer import _build_receiver_frame
        from leakage_simulator.sensor_comparison import FiniteSensor
        for blocked in (False,True):
            trace = scene(blocked=blocked)
            trace = replace(trace,observer_probe={'samples_per_cell':32})
            resolved = OpticalPropertyResolver(trace.mesh,trace.optical_profiles,[]).resolve_all()
            sensor = FiniteSensor(_build_receiver_frame(trace.receivers[0]),32)
            values,_ = reference_chunk(trace,pack_scene(trace.mesh,_source_geometry(trace.mesh,trace.emitters),resolved),sensor.plans()[0],trace.receivers[0].center,0,0,32,sensor=sensor)
            self.assertEqual(values.shape,(32,16))
            self.assertTrue(np.all(values[:,11:] == 0))
            self.assertTrue(np.all(values[:,8] == (0 if blocked else 1)))
            self.assertTrue(np.all(values[:,9] == (0 if blocked else 1)))
            self.assertTrue(np.all(values[:,0] == 0) if blocked else np.all(values[:,0] > 0))

    def test_resident_source_seen_but_back_emission_zero_is_not_no_source(self):
        from leakage_simulator.gpu_cuda_observer import reference_chunk, pack_scene
        from leakage_simulator.observer_probe import _source_geometry
        from leakage_simulator.optics import OpticalPropertyResolver
        from leakage_simulator.raytracer import _build_receiver_frame
        from leakage_simulator.sensor_comparison import FiniteSensor
        trace = scene()
        trace.emitters[0].aim = None
        trace.emitters[0].direction_distribution = 'lambertian'
        trace.emitters[0].normal_flip = True
        trace = replace(trace,observer_probe={'samples_per_cell':32})
        resolved = OpticalPropertyResolver(trace.mesh,trace.optical_profiles,[]).resolve_all()
        sensor = FiniteSensor(_build_receiver_frame(trace.receivers[0]),32)
        values,_ = reference_chunk(trace,pack_scene(trace.mesh,_source_geometry(trace.mesh,trace.emitters),resolved),sensor.plans()[0],trace.receivers[0].center,0,0,32,sensor=sensor)
        self.assertTrue(np.all(values[:,8] == 1))
        self.assertTrue(np.all(values[:,9] == 0))
        self.assertTrue(np.all(values[:,0] == 0))

    def test_roulette_pair_reports_only_verified_stopped_or_surviving_rays(self):
        trace, diagnostic = self.configured(.5)
        trace = replace(trace,config=replace(trace.config,termination_mode='russian_roulette'))
        capture = detected_paths(run_direct_ray_trace(diagnostic),trace.receivers[0].receiver_id)
        pair = pair_detected_paths(trace,diagnostic,capture,16)
        reasons = [row['reason'] for row in pair['records']]
        self.assertIn('roulette_terminated',reasons)
        self.assertIn('original_also_detected',reasons)
        self.assertTrue(all(row['state']=='verified' for row in pair['records']),pair)

    def test_near_segment_is_not_a_near_reflection(self):
        trace, diagnostic = self.configured()
        diagnostic.receivers[0].observer_reference_path = [(0,0,0),(5,0,10),(10,0,20)]
        capture = detected_paths(run_direct_ray_trace(diagnostic),trace.receivers[0].receiver_id)
        for sample in capture['samples']:
            self.assertLess(sample['selected_point_min_segment_distance_mm'],1.)
            self.assertGreater(sample['selected_point_min_reflection_distance_mm'],4.)

    def test_cad_face_source_has_host_reverse_stage_records(self):
        from test_gpu_cuda_observer import plate
        trace = scene()
        first = len(trace.mesh.faces)
        plate(trace.mesh,-5,20)
        emitter = trace.emitters[0]
        emitter.emitter_type = 'face'
        emitter.face_indices = list(range(first,len(trace.mesh.faces)))
        emitter.normal_mode,emitter.custom_normal = 'custom',(0.,0.,-1.)
        emitter.aim = None
        emitter.direction_distribution = 'lambertian'
        report = run_sensor_comparison(trace,ComparisonOptions('common',batches=4,forward_samples=32,reverse_samples=32))
        self.assertTrue(report['complete'])
        for row in report['reverse_diagnostics']:
            self.assertEqual(row['stages']['counts']['positive'],32)
            self.assertTrue(row['stages']['available'])
