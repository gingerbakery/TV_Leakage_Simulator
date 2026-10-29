from __future__ import annotations

import copy
import math
from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from leakage_simulator.raytrace_bridge import build_direct_trace_input, build_prepared_trace_geometry
from leakage_simulator.raytracer import run_direct_ray_trace, _iter_primary_emitter_rays
from leakage_simulator.roi_scope import receiver_roi_mask
from leakage_simulator.types import ReceiverSpec


def plane(emitter_id="source", center=(0,0,0), width=10):
    return {"emitter_id":emitter_id, "emitter_type":"datum_plane", "center":center,
        "u_axis":[1,0,0], "v_axis":[0,1,0], "width_mm":width, "height_mm":width,
        "power_lumen":100, "ray_count":256, "seed":17,
        "aim":{"enabled":True,"mode":"sphere","distribution":"uniform_solid_angle","sphere_upper_deg":0,"sphere_lower_deg":0}}


def setup():
    scene = {"vertices":[[100,100,100],[101,100,100],[100,101,100]], "faces":[[0,1,2]], "face_component_ids":[1]}
    box = {"x_min":-1,"x_max":1,"y_min":-1,"y_max":1,"z_min":-.1,"z_max":.1}
    payload = {"emitters":[plane()], "receivers":[{"receiver_id":"r", "center":[0,0,2], "normal":[0,0,-1],
        "u_axis":[1,0,0],"v_axis":[0,1,0],"width_mm":20,"height_mm":20,"resolution":[20,20]}],
        "config":{"ray_count":256,"max_depth":3,"store_ray_paths":True,"max_stored_paths":512,"min_energy":0},
        "analysis_roi":{"face_indices":[0], "clip_boxes":[box]}}
    return scene, payload


class RoiEmissionDetectionTests(unittest.TestCase):
    def test_virtual_emitter_is_really_cut_and_total_power_does_not_concentrate(self):
        scene, payload = setup()
        original = copy.deepcopy(payload)
        trace = build_direct_trace_input(scene, payload)
        self.assertEqual(payload, original)
        emitter = trace.emitters[0]
        self.assertEqual(emitter.emitter_id, "source")
        self.assertAlmostEqual(sum(trace.mesh.area(i) for i in emitter.face_indices), 4.)
        self.assertAlmostEqual(emitter.power_lumen, 4.)
        self.assertTrue(all(trace.mesh.metadata(i)["trace_excluded"] for i in emitter.face_indices))
        result = run_direct_ray_trace(trace)
        origins = [path[0].point for path in result.stored_paths]
        self.assertGreater(len(origins), 30)
        self.assertTrue(all(-1 <= p[0] <= 1 and -1 <= p[1] <= 1 and abs(p[2]) <= .001 for p in origins))
        self.assertAlmostEqual(sum(sum(row) for row in result.receiver_grids[0].flux_lumen), 4., places=8)

    def test_luminance_and_power_density_stay_constant_after_crop(self):
        scene, payload = setup()
        for mode in ("set_luminance", "power_per_area"):
            with self.subTest(mode=mode):
                payload["emitters"][0].update(power_mode=mode, luminance_nit=500, power_density_lm_per_m2=200)
                trace = build_direct_trace_input(scene, payload)
                emitter = trace.emitters[0]
                self.assertEqual(emitter.luminance_nit, 500)
                self.assertEqual(emitter.power_density_lm_per_m2, 200)
                expected = math.pi*500*4e-6 if mode == "set_luminance" else 200*4e-6
                self.assertAlmostEqual(emitter.effective_power_lumen(sum(trace.mesh.area(i) for i in emitter.face_indices)), expected)

    def test_overlapping_and_disjoint_rois_do_not_duplicate_energy_or_bridge_gaps(self):
        scene, payload = setup()
        a = payload["analysis_roi"]["clip_boxes"][0]
        payload["analysis_roi"]["clip_boxes"] = [a, dict(a,x_min=0,x_max=2), dict(a,x_min=3,x_max=4)]
        trace = build_direct_trace_input(scene,payload)
        emitter = trace.emitters[0]
        self.assertAlmostEqual(sum(trace.mesh.area(i) for i in emitter.face_indices), 8.)
        self.assertAlmostEqual(emitter.power_lumen, 8.)
        result = run_direct_ray_trace(trace)
        self.assertTrue(all(not 2+1e-8 < path[0].point[0] < 3-1e-8 for path in result.stored_paths))

    def test_outside_sources_are_removed_and_all_outside_fails_closed(self):
        scene, payload = setup()
        payload["emitters"].append(plane("outside", (20,0,0), 1))
        trace = build_direct_trace_input(scene,payload)
        self.assertEqual([e.emitter_id for e in trace.emitters], ["source"])
        payload["emitters"] = [payload["emitters"][1]]
        with self.assertRaisesRegex(ValueError, "ROI 안에 발광면이 없습니다"):
            build_direct_trace_input(scene,payload)

    def test_outside_source_can_reflect_into_receiver_without_roi_but_not_with_roi(self):
        d = math.sqrt(.5)
        # +Z from x=5 hits this 45-degree mirror and reflects along -X.
        points = [[5+su*2*d, sv*2, 1-su*2*d] for su,sv in ((-1,-1),(1,-1),(1,1),(-1,1))]
        scene = {"vertices":points,"faces":[[0,1,2],[0,2,3]],"face_component_ids":[1,1],"face_material_ids":["mirror","mirror"]}
        _, payload = setup()
        payload["emitters"] = [plane("inside",(0,0,.1),.1), plane("outside",(5,0,0),.1)]
        payload["receivers"] = [{"receiver_id":"r","center":[0,0,1],"normal":[1,0,0],"u_axis":[0,1,0],"v_axis":[0,0,1],"width_mm":2,"height_mm":2}]
        payload["optical_profiles"] = [{"profile_id":"mirror","reflectance":1,"absorption":0,"scatter_model":"specular"}]
        payload["analysis_roi"]["clip_boxes"][0].update(x_min=-.5,x_max=.5,y_min=-1,y_max=1,z_min=0,z_max=2)
        baseline = copy.deepcopy(payload); baseline.pop("analysis_roi")
        self.assertGreater(run_direct_ray_trace(build_direct_trace_input(scene,baseline)).receiver_hit_count, 30)
        trace = build_direct_trace_input(scene,payload)
        self.assertEqual([e.emitter_id for e in trace.emitters], ["inside"])
        self.assertEqual(run_direct_ray_trace(trace).receiver_hit_count, 0)
        self.assertEqual(len([i for i in range(len(trace.mesh.faces)) if not trace.mesh.metadata(i).get("trace_excluded")]), 2)

    def test_receiver_roi_rejects_outside_hits_before_they_terminate_rays(self):
        from leakage_simulator.raytracer import _build_receiver_frame, _find_first_receiver_hit, _find_first_receiver_hits_numeric
        from leakage_simulator.types import ReceiverGrid, RayTraceConfig
        receiver = ReceiverSpec(receiver_id="r",center=(0,0,10),normal=(0,0,-1),u_axis=(1,0,0),v_axis=(0,1,0),width_mm=20,height_mm=20)
        frame = _build_receiver_frame(receiver); frame.roi_boxes = ((-1,1,-1,1,-.1,.1),)
        config = RayTraceConfig()
        grid = {"r":ReceiverGrid.empty(receiver)}
        self.assertIsNotNone(_find_first_receiver_hit((0,0,0),(0,0,1),1,-1,[frame],grid,config))
        self.assertIsNone(_find_first_receiver_hit((3,0,0),(0,0,1),1,-1,[frame],grid,config))
        hits = _find_first_receiver_hits_numeric(np.array([[0.,0,0],[3.,0,0]]),np.array([[0.,0,1],[0.,0,1]]),np.ones(2),[frame],config)
        self.assertEqual(hits.receiver_indices.tolist(), [0,-1])
        # Oblique projection: x-z silhouette, not an enlarged bounding rectangle.
        self.assertEqual(receiver_roi_mask([[10,0,10],[10,3,10]],(d:=math.sqrt(.5),0,d),frame.roi_boxes).tolist(), [True,False])

    def test_prepared_cache_does_not_grow_sampling_faces_between_runs(self):
        scene, payload = setup()
        prepared = build_prepared_trace_geometry(scene,payload)
        count = len(prepared.mesh.faces)
        first = build_direct_trace_input(scene,payload,prepared_geometry=prepared)
        second_payload = copy.deepcopy(payload); second_payload["emitters"][0]["power_lumen"] = 200
        second = build_direct_trace_input(scene,second_payload,prepared_geometry=prepared)
        self.assertEqual(len(prepared.mesh.faces),count)
        self.assertAlmostEqual(second.emitters[0].power_lumen, first.emitters[0].power_lumen*2)

    def test_retained_face_source_is_cut_independently_of_context_cad(self):
        scene, payload = setup()
        scene.update(vertices=[[-5,-5,0],[5,-5,0],[5,5,0],[-5,5,0]],faces=[[0,1,2],[0,2,3]],face_component_ids=[1,1])
        payload["emitters"][0].update(emitter_type="face",face_indices=[0,1])
        trace = build_direct_trace_input(scene,payload)
        self.assertEqual(len([i for i in range(len(trace.mesh.faces)) if not trace.mesh.metadata(i).get("trace_excluded")]), 2)
        self.assertAlmostEqual(sum(trace.mesh.area(i) for i in trace.emitters[0].face_indices),4.)
        self.assertAlmostEqual(trace.emitters[0].power_lumen,4.)
        result = run_direct_ray_trace(trace)
        self.assertGreater(result.receiver_hit_count,30)
        self.assertTrue(all(abs(path[0].point[0]) <= 1 and abs(path[0].point[1]) <= 1 for path in result.stored_paths))

    def test_legacy_virtual_roi_and_rotated_polygon_sources_are_not_exempt(self):
        scene, payload = setup()
        scene["vertices"] = [[-1,-1,-.05],[-.9,-1,-.05],[-1,-.9,-.05]]
        scope = payload.pop("analysis_roi")
        payload.update(roi_faces=[0],roi_clip_boxes=scope["clip_boxes"])
        trace = build_direct_trace_input(scene,payload)
        self.assertAlmostEqual(trace.emitters[0].power_lumen,4.)
        # A rotated diamond (reference polygon) must keep only its ROI subset.
        item = payload["emitters"][0]
        item.update(emitter_type="reference_plane",surface_construction="polygon_auto",
            polygon_vertices=[[-3,0,0],[0,-3,0],[3,0,0],[0,3,0]])
        trace = build_direct_trace_input(scene,payload)
        self.assertAlmostEqual(sum(trace.mesh.area(i) for i in trace.emitters[0].face_indices),4.)
        self.assertAlmostEqual(trace.emitters[0].power_lumen,100*4/18)

    def test_cache_key_tracks_roi_emitter_geometry_but_not_power_or_ray_budget(self):
        import tempfile
        from leakage_simulator.api import ApiRuntime
        scene, payload = setup()
        with tempfile.TemporaryDirectory() as folder:
            runtime = ApiRuntime(Path(folder))
            key = runtime._trace_geometry_cache_key(scene,payload)
            altered = copy.deepcopy(payload); altered["emitters"][0]["center"] = [1,0,0]
            self.assertNotEqual(key,runtime._trace_geometry_cache_key(scene,altered))
            altered = copy.deepcopy(payload); altered["analysis_roi"]["clip_boxes"][0]["x_max"] = 2
            self.assertNotEqual(key,runtime._trace_geometry_cache_key(scene,altered))
            altered = copy.deepcopy(payload); altered["emitters"][0].update(power_lumen=200,ray_count=512)
            self.assertEqual(key,runtime._trace_geometry_cache_key(scene,altered))

    def test_invalid_or_empty_roi_never_silently_uses_the_full_source(self):
        scene, payload = setup()
        for scope in ({"face_indices":[],"clip_boxes":[]}, {"face_indices":[0],"clip_boxes":[{}]}):
            with self.subTest(scope=scope),self.assertRaises(ValueError):
                build_direct_trace_input(scene,{**payload,"analysis_roi":scope})


if __name__ == "__main__":
    unittest.main()
