import copy
import math
from dataclasses import replace
import unittest

from test_termination_policy import termination_scene
from leakage_simulator.forward_path_replay import replay_forward_path


class ForwardReplayTests(unittest.TestCase):
    def scene(self,threshold=1e-9):
        trace=termination_scene(32,1,threshold=threshold)
        trace.receivers[0].observer_reference_path=[(0,0,0),(0,0,10),(10,0,10),(10,0,20)]
        return trace

    def test_replays_real_specular_transport_without_changing_input_or_product_hits(self):
        trace=self.scene()
        before=copy.deepcopy((trace.config.to_dict(),trace.emitters[0].to_dict(),trace.receivers[0].to_dict()))
        report=replay_forward_path(trace,trace.receivers[0])
        self.assertEqual(report['state'],'receiver_reached')
        self.assertTrue(report['path_reproduced'])
        self.assertFalse(report['included_in_flux'])
        self.assertEqual(report['matched_surface_count'],2)
        self.assertEqual(report['diagnostics']['primary_ray_count'],1)
        self.assertEqual(before,(trace.config.to_dict(),trace.emitters[0].to_dict(),trace.receivers[0].to_dict()))

    def test_reference_loss_audit_uses_each_material_and_original_packet_threshold(self):
        trace = self.scene(.5)
        audit = replay_forward_path(trace, trace.receivers[0])['reference_attenuation']
        self.assertEqual(audit['state'], 'complete')
        self.assertFalse(audit['included_in_flux'])
        self.assertEqual(len(audit['steps']), 2)
        self.assertAlmostEqual(audit['final_reflectance_ratio'], .36)
        self.assertAlmostEqual(audit['final_log10_ratio'], math.log10(.36))
        self.assertEqual(audit['first_below_min_energy_reflection'], 2)
        self.assertAlmostEqual(audit['steps'][0]['incoming_nominal_packet_lumen'], 1/32)
        self.assertAlmostEqual(audit['steps'][1]['outgoing_nominal_packet_lumen'], .36/32)
        self.assertTrue(all(step['direction_supported'] for step in audit['steps']))

    def test_reference_loss_is_available_when_stochastic_replay_diverges(self):
        from leakage_simulator.types import OpticalProfile
        trace = self.scene()
        trace.optical_profiles = [OpticalProfile(name, .6, scatter_model='lambertian') for name in ('mirror_a','mirror_b')]
        report = replay_forward_path(trace, trace.receivers[0])
        self.assertFalse(report['path_reproduced'])
        self.assertEqual(report['reference_attenuation']['state'], 'complete')
        self.assertAlmostEqual(report['reference_attenuation']['final_reflectance_ratio'], .36)
        self.assertGreater(report['reference_attenuation']['steps'][0]['directional_brdf_per_sr'], 0)

    def test_direct_light_and_single_reflection_have_expected_attenuation(self):
        for path, center, normal, expected in (
            ([(0,0,0),(0,0,5)], (0,0,5), (0,0,-1), 1.),
            ([(0,0,0),(0,0,10),(5,0,10)], (5,0,10), (-1,0,0), .6),
        ):
            trace = self.scene()
            trace.receivers[0] = replace(trace.receivers[0], center=center, normal=normal,
                observer_reference_path=path)
            audit = replay_forward_path(trace, trace.receivers[0])['reference_attenuation']
            self.assertEqual(audit['state'], 'complete')
            self.assertAlmostEqual(audit['final_reflectance_ratio'], expected)
            self.assertEqual(len(audit['steps']), len(path)-2)

    def test_invalid_reference_does_not_publish_a_complete_loss_ratio(self):
        trace = self.scene()
        trace.receivers[0].observer_reference_path[1] = (0,0,8)
        audit = replay_forward_path(trace, trace.receivers[0])['reference_attenuation']
        self.assertEqual(audit['state'], 'invalid')
        self.assertIsNone(audit['final_reflectance_ratio'])

    def test_log_ratio_survives_numeric_packet_underflow(self):
        from leakage_simulator.types import OpticalProfile
        trace = self.scene(0)
        trace.optical_profiles = [OpticalProfile(name, 1e-200, scatter_model='specular') for name in ('mirror_a','mirror_b')]
        audit = replay_forward_path(trace, trace.receivers[0])['reference_attenuation']
        self.assertEqual(audit['state'], 'complete')
        self.assertEqual(audit['final_reflectance_ratio'], 0)
        self.assertAlmostEqual(audit['final_log10_ratio'], -400)

    def test_preserves_nominal_packet_and_reports_original_energy_cutoff(self):
        trace=self.scene(.9)
        report=replay_forward_path(trace,trace.receivers[0])
        self.assertEqual(report['state'],'not_reached')
        self.assertEqual(report['reason'],'energy_cutoff')
        self.assertAlmostEqual(report['nominal_packet_lumen'],1/32)

    def test_source_or_geometry_mismatch_is_not_relabelled_as_sampling_failure(self):
        trace=self.scene()
        trace.receivers[0].observer_reference_path[0]=(100,100,0)
        report=replay_forward_path(trace,trace.receivers[0])
        self.assertEqual(report['state'],'unavailable')
        trace=self.scene()
        trace.receivers[0].observer_reference_path[1]=(0,0,8)
        report=replay_forward_path(trace,trace.receivers[0])
        self.assertFalse(report['path_reproduced'])
        self.assertEqual(report['first_difference']['segment_index'],0)

    def test_stochastic_reflection_is_not_forced_to_saved_direction(self):
        from leakage_simulator.types import OpticalProfile
        trace=self.scene()
        trace.optical_profiles=[OpticalProfile(name,.6,scatter_model='lambertian') for name in ('mirror_a','mirror_b')]
        report=replay_forward_path(trace,trace.receivers[0])
        self.assertFalse(report['reflection_directions_fixed'])
        self.assertEqual(report['reason'],'different_sampled_branch')

    def test_execution_failure_is_explicit_and_not_a_zero_hit_verdict(self):
        from unittest.mock import patch
        trace=self.scene()
        with patch('leakage_simulator.raytracer.run_direct_ray_trace',side_effect=RuntimeError('test-replay-failure')):
            report=replay_forward_path(trace,trace.receivers[0])
        self.assertEqual(report['state'],'failed')
        self.assertEqual(report['reason'],'replay_execution_failed')
        self.assertNotIn('path_reproduced',report)

    def test_overlapping_sources_do_not_invent_a_packet_or_source_identity(self):
        trace=self.scene()
        trace.emitters.append(replace(trace.emitters[0],emitter_id='overlapping',power_lumen=10))
        report=replay_forward_path(trace,trace.receivers[0])
        self.assertEqual(report['state'],'unavailable')
        self.assertEqual(report['reason'],'ambiguous_source')
        self.assertNotIn('nominal_packet_lumen',report)
