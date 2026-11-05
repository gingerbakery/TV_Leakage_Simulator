"""CPU continuation invariants: same samples, no double counting, frozen partials."""
import copy
import tempfile
import threading
import time
from pathlib import Path
from unittest import TestCase

from leakage_simulator.api.runtime import ApiRuntime
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import OpticalProfile, EmitterSpec
from test_observer_probe import setup
from test_escape_collection import receivers
from test_api_fastapi import _scene_loader


def trace_fixture(rays=80):
    trace,_=setup(mirror=True)
    trace.emitters[0].ray_count=rays
    trace.config.store_ray_paths=True
    trace.config.max_stored_paths=100
    return DirectRayTraceInput(mesh=trace.mesh,emitters=trace.emitters,receivers=receivers(),
        optical_profiles=[OpticalProfile(profile_id="mirror",reflectance=.7,scatter_model="lambertian")],config=trace.config)


class CoreResumeTests(TestCase):
    def test_multiple_emitters_and_importance_weights_continue_across_emitter_boundary(self):
        def fixture():
            trace=trace_fixture(40)
            trace.emitters[0].seed=None
            trace.emitters.append(EmitterSpec(emitter_id="face",face_indices=[0,1],normal_flip=True,ray_count=40,seed=19))
            trace.config.store_ray_paths=False
            trace.config.primary_sampling_strategy="receiver_mis"
            trace.config.bounce_sampling_strategy="receiver_mis"
            return trace
        baseline=run_direct_ray_trace(fixture(),intersection_dispatch="batch",intersection_batch_size=8)
        stop=[False]; pauses=[]
        def progress(n,total):
            if (not pauses and n>=16) or (len(pauses)==1 and n>=48): stop[0]=True
        def pause(snapshot):
            pauses.append(snapshot.total_rays); stop[0]=False; return True
        resumed=run_direct_ray_trace(fixture(),intersection_dispatch="batch",intersection_batch_size=8,
            progress_callback=progress,should_stop=lambda:stop[0],pause_callback=pause)
        self.assertEqual(pauses,[16,48])
        self.assertEqual(resumed.total_rays,80)
        self.assertEqual(resumed.receiver_hit_count,baseline.receiver_hit_count)
        for a,b in zip(resumed.receiver_grids,baseline.receiver_grids):
            self.assertAlmostEqual(a.flux_squared_lumen2,b.flux_squared_lumen2,delta=1e-12)
            self.assertEqual(a.flux_lumen,b.flux_lumen)

    def test_repeated_pauses_preserve_primary_stream_grids_moments_and_paths(self):
        for dispatch in ("scalar","batch"):
            with self.subTest(dispatch=dispatch):
                baseline=run_direct_ray_trace(trace_fixture(),intersection_dispatch=dispatch,intersection_batch_size=8)
                stop=[False]; seen=set(); snapshots=[]
                def progress(n,total):
                    if n>=16 and len(seen)<2 and n not in seen:
                        seen.add(n); stop[0]=True
                def pause(snapshot):
                    snapshots.append(snapshot.to_dict())
                    # These are detached snapshots, not live accumulation arrays.
                    snapshot.receiver_grids[0].flux_lumen[0][0]=1e20
                    stop[0]=False
                    return True
                result=run_direct_ray_trace(trace_fixture(),intersection_dispatch=dispatch,intersection_batch_size=8,
                    progress_callback=progress,should_stop=lambda:stop[0],pause_callback=pause)
                self.assertEqual(len(snapshots),2)
                self.assertEqual(result.total_rays,80)
                self.assertEqual(result.receiver_hit_count,baseline.receiver_hit_count)
                self.assertEqual(result.surface_hit_count,baseline.surface_hit_count)
                self.assertEqual(result.terminated_ray_count,baseline.terminated_ray_count)
                self.assertEqual(result.stored_paths,baseline.stored_paths)
                for a,b in zip(result.receiver_grids,baseline.receiver_grids):
                    self.assertEqual(a.hit_count,b.hit_count)
                    self.assertAlmostEqual(a.flux_squared_lumen2,b.flux_squared_lumen2,delta=1e-12)
                    for ar,br in zip(a.flux_lumen,b.flux_lumen):
                        for av,bv in zip(ar,br): self.assertAlmostEqual(av,bv,delta=1e-12)
                    for ar,br in zip(a.flux_squared_lumen2_grid,b.flux_squared_lumen2_grid):
                        for av,bv in zip(ar,br): self.assertAlmostEqual(av,bv,delta=1e-12)
                self.assertTrue(all(s["total_rays"]<80 for s in snapshots))

    def test_discard_ends_the_live_stream_with_a_partial_result(self):
        stop=[False]
        result=run_direct_ray_trace(trace_fixture(),intersection_dispatch="batch",intersection_batch_size=8,
            progress_callback=lambda n,total:stop.__setitem__(0,n>=16),should_stop=lambda:stop[0],pause_callback=lambda _:False)
        self.assertEqual(result.total_rays,16)
        self.assertTrue(result.metrics["_performance_summary"]["stopped_early"])


class ApiResumeTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.builders=0; self.runners=0; self.threads=[]
        self.first_chunk=threading.Event(); self.release=threading.Event(); self.gate_at=8
        def builder(scene,payload):
            self.builders+=1
            result=trace_fixture(payload["emitters"][0]["ray_count"])
            result.config.seed=payload.get("config",{}).get("seed",42)
            return result
        def runner(trace,progress_callback=None,should_stop=None,pause_callback=None):
            self.runners+=1; self.threads.append(threading.get_ident())
            def progress(n,total):
                progress_callback(n,total)
                if n==self.gate_at and not self.release.is_set():
                    self.first_chunk.set(); self.release.wait(3)
            return run_direct_ray_trace(trace,progress_callback=progress,should_stop=should_stop,
                pause_callback=pause_callback,intersection_dispatch="batch",intersection_batch_size=8)
        self.runtime=ApiRuntime(Path(self.temp.name),scene_loader=_scene_loader,trace_input_builder=builder,trace_runner=runner)
        scene=self.runtime.load_scene("resume.step")
        self.payload=dict(scene_token=scene["metadata"]["scene_token"],emitters=[dict(enabled=True,ray_count=80)],config=dict(seed=42))

    def tearDown(self):
        self.release.set()
        with self.runtime._state_lock:
            sessions=list(self.runtime._raytrace_sessions.values())
            for session in sessions:
                session.cancelled=True; session.wake.set()
        for session in sessions: self.assertTrue(session.finished.wait(3))
        self.temp.cleanup()

    def terminal(self,job):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            value=self.runtime.raytrace_job_snapshot(job["job_id"])
            if value["status"] in ("completed","cancelled","failed"): return value
            time.sleep(.005)
        self.fail("job did not reach terminal/paused state")

    def pause_first(self):
        job=self.runtime.start_raytrace_job(self.payload)
        self.assertTrue(self.first_chunk.wait(3))
        self.runtime.stop_raytrace_job(job["job_id"]); self.release.set()
        paused=self.terminal(job)
        self.assertEqual(paused["phase"],"stopped")
        self.assertTrue(paused["resume_available"])
        self.assertEqual(paused["processed_rays"],8)
        return paused

    def test_same_request_resumes_without_builder_or_runner_reentry(self):
        paused=self.pause_first(); frozen=copy.deepcopy(paused["result"])
        resumed=self.runtime.start_raytrace_job(self.payload)
        self.assertEqual(resumed["resumed_from_job_id"],paused["job_id"])
        self.assertEqual(resumed["processed_rays"],8)
        self.assertEqual(resumed["phase"],"tracing")
        final=self.terminal(resumed)
        self.assertEqual(final["phase"],"completed")
        self.assertEqual(final["result"]["total_rays"],80)
        self.assertEqual((self.builders,self.runners),(1,1))
        self.assertEqual(paused["result"],frozen)
        self.assertFalse(self.runtime.raytrace_job_snapshot(paused["job_id"])["resume_available"])

    def test_a_resumed_job_can_be_stopped_and_resumed_again(self):
        first=self.pause_first()
        self.gate_at=16; self.first_chunk.clear(); self.release.clear()
        second=self.runtime.start_raytrace_job(self.payload)
        self.assertTrue(self.first_chunk.wait(3))
        self.runtime.stop_raytrace_job(second["job_id"]); self.release.set()
        paused=self.terminal(second)
        self.assertTrue(paused["resume_available"])
        self.assertEqual(paused["processed_rays"],16)
        third=self.runtime.start_raytrace_job(self.payload)
        self.assertEqual(third["resume_count"],2)
        self.assertEqual(third["resumed_from_job_id"],second["job_id"])
        self.assertEqual(self.terminal(third)["result"]["total_rays"],80)
        self.assertEqual((self.builders,self.runners),(1,1))

    def test_pruning_a_paused_checkpoint_releases_its_worker(self):
        paused=self.pause_first()
        with self.runtime._state_lock:
            session=self.runtime._raytrace_sessions[paused["job_id"]]
            self.runtime._max_jobs=1
            self.runtime._raytrace_jobs["newer"]={"status":"completed","created_at":time.time()+1}
            self.runtime._prune_raytrace_jobs_locked()
        self.assertTrue(session.finished.wait(3))
        self.assertIsNone(self.runtime.raytrace_job_snapshot(paused["job_id"]))

    def test_changed_input_and_explicit_restart_invalidate_the_checkpoint(self):
        for change in ({"config":{"seed":43}}, {"restart_from_beginning":True}, {"resume_scope":"another-case"}):
            with self.subTest(change=change):
                # A fresh fixture makes stop/resume timing deterministic.
                if self.builders:
                    self.tearDown(); self.setUp()
                paused=self.pause_first()
                fresh=self.runtime.start_raytrace_job(dict(self.payload,**change))
                self.assertNotIn("resumed_from_job_id",fresh)
                final=self.terminal(fresh)
                self.assertEqual(final["phase"],"completed")
                self.assertEqual((self.builders,self.runners),(2,2))
                self.assertFalse(self.runtime.raytrace_job_snapshot(paused["job_id"])["resume_available"])
