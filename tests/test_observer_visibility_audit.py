"""Verify independent analytic fixtures and reproduce documented audit failures.

Passing these tests means the audit is reproducible. It does NOT mean the
production detector passes visible-leak acceptance; its report stays failed.
"""
import math
from pathlib import Path
import sys
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from validate_observer_detection import run_validation


class ObserverVisibilityAuditTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_validation()
        cls.cases = {case['name']:case for case in cls.report['cases']}

    def test_visibility_acceptance_is_explicitly_failed_despite_valid_controls(self):
        self.assertFalse(self.report['visibility_acceptance_passed'])
        self.assertTrue(self.report['controls_passed'])
        self.assertEqual(self.report['totals'], dict(TP=6,FP=4,TN=3,FN=3))
        self.assertTrue(all(case['complete'] for case in self.report['cases']))
        self.assertTrue(all(case['execution_backend']=='cpu' for case in self.report['cases']))

    def test_dim_source_and_weak_mirror_support_do_not_encode_brightness(self):
        low,high = self.cases['direct_1e-06_nit'],self.cases['direct_100.0_nit']
        self.assertEqual(low['direct_count'],high['direct_count'])
        self.assertEqual(low['classification'],'FP')
        self.assertEqual(high['classification'],'TP')
        low,high = self.cases['mirror_R_1e-06'],self.cases['mirror_R_0.9']
        self.assertEqual(low['reflected_count'],high['reflected_count'])
        self.assertEqual(low['classification'],'FP')
        self.assertEqual(high['classification'],'TP')
        self.assertLess(low['analytic_oracle']['radiance_nit'],1)
        self.assertGreater(high['analytic_oracle']['radiance_nit'],1)

    def test_bsdf_rt_and_weak_tails_have_independent_brightness_bounds(self):
        for name in ('bsdf_weak_rt','bsdf_weak_tail'):
            case=self.cases[name]
            self.assertLess(case['analytic_oracle']['radiance_upper_bound_nit'],1.)
            self.assertEqual(case['classification'],'FP')
            self.assertGreater(case['reflected_count'],0)
        self.assertGreater(self.cases['bsdf_bright_flat']['analytic_oracle']['radiance_lower_bound_nit'],1.)
        self.assertEqual(self.cases['bsdf_bright_flat']['classification'],'TP')

    def test_angular_gap_has_an_independent_geometric_visibility_proof(self):
        # Across two equal apertures the displacement error may not exceed
        # their width. Every coarse H sample fails this necessary condition.
        target_slope = math.tan(math.radians(52.5))
        for h in range(-75,76,15):
            self.assertGreater(abs(4*(math.tan(math.radians(h))-target_slope)),.1)
        self.assertEqual(self.cases['angular_coarse_15deg']['view_count'],145)
        self.assertEqual(self.cases['angular_coarse_15deg']['classification'],'FN')
        self.assertEqual(self.cases['angular_known_direction']['classification'],'TP')
        self.assertGreater(self.cases['angular_known_direction']['direct_count'],0)

    def test_spatial_gap_is_visible_to_a_known_fine_grid_ray(self):
        # x=y=.15 is a real 0.1-mm-grid centre for bounds [-1,1].
        # Both masks contain that normal ray and it hits the finite source.
        self.assertLess(abs(.15-.137),.08/2)
        self.assertLess(abs(.15-.173),.08/2)
        self.assertLess(.15,.6/2)
        self.assertEqual(self.cases['spatial_spacing_2.0mm']['classification'],'FN')
        self.assertEqual(self.cases['spatial_spacing_0.1mm']['classification'],'TP')

    def test_exact_two_mirror_path_exists_but_depth_one_cannot_complete_it(self):
        # Independent ideal mirror law applied to the central path.
        d=math.sqrt(.5)
        incoming=(0,0,1)
        for normal, expected in (((d,0,d),(-1,0,0)),((-d,0,d),(0,0,-1))):
            dot=sum(a*b for a,b in zip(incoming,normal))
            outgoing=tuple(incoming[i]-2*dot*normal[i] for i in range(3))
            self.assertLess(max(abs(outgoing[i]-expected[i]) for i in range(3)),1e-14)
            incoming=outgoing
        self.assertEqual(self.cases['two_mirrors_depth_1']['classification'],'FN')
        self.assertEqual(self.cases['two_mirrors_depth_2']['classification'],'TP')

    def test_closed_geometry_controls_do_not_invent_transmitted_paths(self):
        for name in ('opaque_no_gap','accessory_cover','reflective_hole_plug'):
            with self.subTest(case=name):
                case=self.cases[name]
                self.assertEqual(case['classification'],'TN')
                self.assertEqual(case['direct_count']+case['reflected_count'],0)
