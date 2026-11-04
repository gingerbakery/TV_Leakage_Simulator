from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch
import os

from test_observer_probe import setup
from test_escape_collection import receivers
from leakage_simulator.escape_collection import EscapeCollector
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.observer_probe import trace_observer_paths
from leakage_simulator.raytrace_bridge import _append_preview_blockers
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import OpticalProfile, RayTraceConfig, ReceiverGrid


class BlockerTransportTests(TestCase):
    def test_box_and_hole_plug_block_forward_and_reverse_paths_even_with_reflective_default(self):
        for shape in ('box', 'ellipse'):
            with self.subTest(shape=shape):
                trace, baseline = setup()
                self.assertGreater(baseline()['direct_count'], 0)
                detectors = receivers()
                target = next(r for r in detectors if r.receiver_id.endswith('neg_z'))
                profiles = [OpticalProfile(profile_id='black', reflectance=0, scatter_model='none'),
                    OpticalProfile(profile_id='default', reflectance=.9, scatter_model='lambertian')]
                emitter = replace(trace.emitters[0], ray_count=256, seed=42)
                config = RayTraceConfig(max_depth=3, min_energy=0, store_ray_paths=True, max_stored_paths=256)
                forward = DirectRayTraceInput(mesh=trace.mesh, emitters=[emitter], receivers=[target], optical_profiles=profiles, config=config)
                before = run_direct_ray_trace(forward).to_dict()
                self.assertGreater(sum(sum(row) for grid in before['receiver_grids'] for row in grid['flux_lumen']), 0)
                _append_preview_blockers(trace.mesh, [{'blocker_id': 'sealed-hole', 'shape': shape,
                    'center': [0,0,-8], 'u_axis': [1,0,0], 'v_axis': [0,1,0], 'normal': [0,0,1],
                    'width_mm': 60, 'height_mm': 60, 'depth_mm': 1, 'enabled': True}])
                resolver = OpticalPropertyResolver(trace.mesh, profiles, [])
                with patch.dict(os.environ, {'LEAKAGE_OPTICAL_ROUTING_CACHE': 'force'}):
                    resolved_versions = [resolver.resolve_all(), resolver.resolve_all()]
                    self.assertIsNotNone(getattr(trace.mesh, '_optical_routing_cache', None))
                for resolved in resolved_versions:
                    for index in range(len(trace.mesh.faces)):
                        if trace.mesh.metadata(index).get('preview_blocker_id'):
                            self.assertEqual(resolved[index].profile.reflectance, .9)
                            self.assertEqual(resolver.resolve(index).profile.reflectance, .9)
                            self.assertEqual(resolved[index].profile.scatter_model, 'lambertian')
                precision_resolved = OpticalPropertyResolver(trace.mesh, [], []).resolve_all()
                for index in range(len(trace.mesh.faces)):
                    if trace.mesh.metadata(index).get('preview_blocker_id'):
                        self.assertEqual(precision_resolved[index].profile.reflectance, .35)
                        self.assertEqual(precision_resolved[index].profile.scatter_model, 'mixed')
                collector = EscapeCollector({'minimum': [-10,-10,-10], 'maximum': [10,10,10], 'direction': 'neg_z'},
                    detectors, {r.receiver_id: ReceiverGrid.empty(r) for r in detectors})
                reverse = trace_observer_paths(trace, collector, resolver.resolve_all())
                self.assertEqual(reverse['direct_count'] + reverse['reflected_count'], 0)
                after = run_direct_ray_trace(forward).to_dict()
                self.assertEqual(sum(sum(row) for grid in after['receiver_grids'] for row in grid['flux_lumen']), 0)
                self.assertGreater(after['metrics']['_reflection_summary']['lobes']['lambertian']['emitted_flux_lumen'], 0)


    def test_tilted_reversed_box_intercepts_normal_and_oblique_rays(self):
        import math
        from leakage_simulator.geometry import TriangleMesh
        s = math.sqrt(.5)
        u, v, normal = (s,0,s), (0,1,0), (s,0,-s)
        center = (3+1.5*s,4,5-1.5*s)
        mesh = TriangleMesh()
        _append_preview_blockers(mesh, [{'blocker_id':'reverse', 'center':center, 'u_axis':u, 'v_axis':v,
            'normal':normal, 'width_mm':4, 'height_mm':6, 'depth_mm':2}])
        for bias in (0,.2):
            raw = [normal[i]+bias*u[i] for i in range(3)]
            length = math.sqrt(sum(x*x for x in raw))
            direction = tuple(-x/length for x in raw)
            origin = tuple(center[i]-direction[i]*10 for i in range(3))
            hit = mesh.intersect_ray(origin,direction)
            self.assertIsNotNone(hit)
            self.assertEqual(mesh.metadata(hit.face_index)['preview_blocker_id'],'reverse')
            self.assertLess(hit.t,10)
