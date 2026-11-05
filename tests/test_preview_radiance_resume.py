"""CPU Preview checkpoint invariants, including reverse waves in flight."""
import copy
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from leakage_simulator.api.runtime import ApiRuntime
from leakage_simulator.escape_collection import EscapeCollector
from leakage_simulator.observer_probe import trace_observer_paths
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.raytracer import run_direct_ray_trace, _IntersectionDispatchStats
from leakage_simulator.types import ReceiverGrid
from test_raytrace_resume import trace_fixture
from test_api_fastapi import _scene_loader


def preview_fixture():
    trace=trace_fixture(16)
    trace.escape_collection=dict(minimum=[-10,-10,-10],maximum=[10,10,10],direction="neg_z")
    trace.observer_probe=dict(measurement="radiance",horizontal_deg=0.,vertical_deg=0.,range_deg=1.,
        step_deg=1.,resolution=8,samples_per_cell=4,search_mode="full_detail",spacing_mm=5.,coverage="local",min_luminance_nit=0.)
    trace.config.contribution_mode="detailed"
    return trace


def observer(trace,dispatch,pause_callback=None,should_stop=None):
    collector=EscapeCollector(trace.escape_collection,trace.receivers,{r.receiver_id:ReceiverGrid.empty(r) for r in trace.receivers})
    resolved=OpticalPropertyResolver(trace.mesh,trace.optical_profiles,[]).resolve_all()
    return trace_observer_paths(trace,collector,resolved,should_stop,dispatch=dispatch,batch_size=7,pause_callback=pause_callback)


def physical(summary):
    return {key:summary[key] for key in ("cells","views","direct_count","reflected_count","excluded_count",
        "filtered_count","depth_limited_count","attempted_count","planned_count","complete","stopped")}


class ObserverWaveResumeTests(TestCase):
    def test_preview_grid_hemisphere_resumes_across_view_boundary(self):
        trace = preview_fixture()
        trace.observer_probe.update(search_mode="preview", coverage="hemisphere", step_deg=90., samples_per_cell=1)
        baseline = run_direct_ray_trace(trace, intersection_batch_size=8)
        stop = [False]
        snapshots = []
        def progress(completed, total):
            if completed >= 16+64 and not snapshots:
                stop[0] = True
        def pause(snapshot):
            snapshots.append(snapshot.to_dict())
            stop[0] = False
            return True
        resumed = run_direct_ray_trace(trace, intersection_batch_size=8,
            progress_callback=progress, should_stop=lambda: stop[0], pause_callback=pause)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(physical(resumed.metrics["_observer_probe_summary"]), physical(baseline.metrics["_observer_probe_summary"]))
        self.assertGreater(len(resumed.metrics["_observer_probe_summary"]["views"]), 1)

    def test_reverse_generators_and_partial_cells_resume_without_repeated_intersections(self):
        for dispatch in ("scalar","batch"):
            with self.subTest(dispatch=dispatch):
                baseline=observer(preview_fixture(),dispatch)
                stop=[False]; calls=[0]; snapshots=[]
                method="intersect_batch" if dispatch=="batch" else "intersect_scalar"
                original=getattr(_IntersectionDispatchStats,method)
                def intersect(stats,*args,**kwargs):
                    result=original(stats,*args,**kwargs)
                    calls[0]+=1
                    if calls[0] in (1,45): stop[0]=True
                    return result
                def pause(summary):
                    snapshots.append(copy.deepcopy(summary))
                    summary["settings"]["min_luminance_nit"]=1e20
                    summary["cells"].clear()
                    stop[0]=False
                    return True
                with patch.object(_IntersectionDispatchStats,method,intersect):
                    resumed=observer(preview_fixture(),dispatch,pause,lambda:stop[0])
                self.assertEqual(physical(resumed),physical(baseline))
                self.assertEqual(len(snapshots),2)
                self.assertTrue(all(s["stopped"] and not s["complete"] for s in snapshots))
                self.assertEqual(snapshots[0]["attempted_count"],0)
                self.assertGreater(snapshots[0]["in_flight_sample_count"],0)
                self.assertEqual(resumed["in_flight_sample_count"],0)
                for key in ("intersection_scalar_query_count","intersection_batch_count","intersection_ray_count"):
                    self.assertEqual(resumed["performance"][key],baseline["performance"][key])

    def test_full_preview_keeps_forward_grids_and_detailed_contributions_while_resuming_angles(self):
        baseline=run_direct_ray_trace(preview_fixture(),intersection_batch_size=8)
        stop=[False]; snapshots=[]
        def progress(n,total):
            if (not snapshots and n>=8) or (len(snapshots)==1 and n>16): stop[0]=True
        def pause(snapshot):
            snapshots.append(snapshot.to_dict()); stop[0]=False; return True
        resumed=run_direct_ray_trace(preview_fixture(),intersection_batch_size=8,
            progress_callback=progress,should_stop=lambda:stop[0],pause_callback=pause)
        self.assertEqual(len(snapshots),2)
        self.assertEqual(snapshots[0]["metrics"]["_resume_state_summary"]["stage"],"forward")
        self.assertEqual(snapshots[1]["metrics"]["_resume_state_summary"]["stage"],"observer_radiance")
        self.assertEqual(physical(resumed.metrics["_observer_probe_summary"]),physical(baseline.metrics["_observer_probe_summary"]))
        self.assertEqual(resumed.receiver_grids,baseline.receiver_grids)
        self.assertEqual(resumed.stored_paths,baseline.stored_paths)
        self.assertEqual(resumed.contribution_summary,baseline.contribution_summary)
        self.assertEqual(resumed.metrics["_escape_direction_summary"],baseline.metrics["_escape_direction_summary"])


class PreviewApiResumeTests(TestCase):
    def test_same_preview_request_keeps_forward_work_and_combined_progress(self):
        gate=threading.Event(); release=threading.Event(); counts=dict(builder=0,runner=0)
        def builder(scene,payload):
            counts["builder"]+=1
            trace=preview_fixture(); trace.observer_probe=payload["observer_probe"]
            return trace
        def runner(trace,progress_callback=None,should_stop=None,pause_callback=None):
            counts["runner"]+=1
            def progress(n,total):
                progress_callback(n,total)
                if n>16 and not release.is_set(): gate.set(); release.wait(3)
            return run_direct_ray_trace(trace,intersection_batch_size=8,progress_callback=progress,
                should_stop=should_stop,pause_callback=pause_callback)
        with tempfile.TemporaryDirectory() as directory:
            runtime=ApiRuntime(Path(directory),scene_loader=_scene_loader,trace_input_builder=builder,trace_runner=runner)
            scene=runtime.load_scene("preview.step")
            payload=dict(scene_token=scene["metadata"]["scene_token"],emitters=[dict(ray_count=16,enabled=True)],
                observer_probe=preview_fixture().observer_probe,resume_scope="preview:case")
            def terminal(job):
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    value=runtime.raytrace_job_snapshot(job["job_id"])
                    if value["status"] in ("completed","failed","cancelled"): return value
                    time.sleep(.005)
                self.fail("Preview did not finish/pause")
            try:
                job=runtime.start_raytrace_job(payload)
                self.assertTrue(gate.wait(3)); runtime.stop_raytrace_job(job["job_id"]); release.set()
                paused=terminal(job)
                self.assertTrue(paused["resume_available"])
                summary=paused["result"]["metrics"]["_observer_probe_summary"]
                self.assertEqual(paused["processed_rays"],16+summary["attempted_count"])
                self.assertEqual(paused["total_rays"],16+summary["planned_count"])
                self.assertGreater(summary["attempted_count"],0)
                frozen=copy.deepcopy(paused["result"])
                next_job=runtime.start_raytrace_job(payload)
                self.assertEqual(next_job["resumed_from_job_id"],job["job_id"])
                final=terminal(next_job)
                self.assertEqual(final["phase"],"completed")
                self.assertEqual(final["processed_rays"],final["total_rays"])
                self.assertEqual(counts,dict(builder=1,runner=1))
                self.assertEqual(paused["result"],frozen)
            finally:
                release.set()
                with runtime._state_lock:
                    sessions=list(runtime._raytrace_sessions.values())
                    for session in sessions: session.cancelled=True; session.wake.set()
                for session in sessions: self.assertTrue(session.finished.wait(3))
