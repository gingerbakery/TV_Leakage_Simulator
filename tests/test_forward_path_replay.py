import copy
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
