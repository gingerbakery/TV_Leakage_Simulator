"""CPU equation/dispatch gates. These tests never establish actual GPU use."""
import copy
import math
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import numpy as np

from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.observer_probe import _source_geometry, sampling_plan
from leakage_simulator.gpu_cuda_observer import pack_scene, reference_chunk, ResidentObserver, ObserverGpuUnsupported, make_kernel, compact_cell_batch, verify_resident_optics
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.types import OpticalProfile, RayTraceConfig, EmitterAimSpec
from test_observer_radiance import plate, source


def fixture(mirror=False, blocked=False):
    mesh = TriangleMesh()
    plate(mesh, 0 if mirror else 9, 20)
    if blocked:
        plate(mesh, -7, 20)
    p = OpticalProfile(profile_id="black", reflectance=.9 if mirror else 0., scatter_model="specular" if mirror else "none")
    trace = SimpleNamespace(mesh=mesh, emitters=[source((0, 0, -5), 40, 100., reverse=mirror)],
        observer_probe=dict(measurement="radiance", horizontal_deg=0., vertical_deg=0., range_deg=0., step_deg=1.,
            resolution=8, samples_per_cell=4, coverage="local", search_mode="preview"),
        config=RayTraceConfig(max_depth=2, seed=42, angle_dependent_reflectance=False))
    resolved = OpticalPropertyResolver(mesh, [p], []).resolve_all()
    host = pack_scene(mesh, _source_geometry(mesh, trace.emitters), resolved)
    plan = sampling_plan(trace.observer_probe, (-10, -10, -10), (10, 10, 10), "neg_z", 0)[0]
    return trace, host, plan, resolved


class ResidentObserverEquationsTests(TestCase):
    def test_optical_preflight_never_accepts_cpu_fallback(self):
        class Fallback:
            validated = True
            def __init__(self, *args, **kwargs):
                pass
            def run(self, *args):
                return [], SimpleNamespace(cpu_fallback=True), 0
        with self.assertRaisesRegex(ObserverGpuUnsupported, "optical_preflight_failed:direct"):
            verify_resident_optics(Fallback)

    def test_face_emitter_is_not_lost_in_resident_transport(self):
        trace, _, plan, resolved = fixture()
        first_face = len(trace.mesh.faces)
        plate(trace.mesh, -5, 20)
        emitter = trace.emitters[0]
        emitter.emitter_type = "face"
        emitter.face_indices = list(range(first_face, len(trace.mesh.faces)))
        emitter.normal_mode, emitter.custom_normal = "custom", (0., 0., -1.)
        profiles = [OpticalProfile(profile_id="black", reflectance=0., scatter_model="none")]
        resolved = OpticalPropertyResolver(trace.mesh, profiles, []).resolve_all()
        host = pack_scene(trace.mesh, _source_geometry(trace.mesh, trace.emitters), resolved)
        values, _ = reference_chunk(trace, host, plan, (0, 0, 0), 0, 0, 16)
        np.testing.assert_allclose(values[:, 0], 100., atol=1e-10, rtol=1e-12)

    def test_sphere_uniform_and_lambertian_match_reference_emission(self):
        from leakage_simulator.observer_radiance import emission_radiance
        for distribution in ("uniform_solid_angle", "lambertian"):
            trace, _, plan, resolved = fixture()
            trace.emitters[0].aim = EmitterAimSpec(enabled=True, mode="sphere", distribution=distribution,
                sphere_upper_deg=0., sphere_lower_deg=90., sphere_beta_deg=180.)
            sources = _source_geometry(trace.mesh, trace.emitters)
            host = pack_scene(trace.mesh, sources, resolved)
            expected = emission_radiance(sources[0], (0, 0, -5), sources[0][1][0][1], (0, 0, -1))
            self.assertGreater(expected, 0.)
            values, _ = reference_chunk(trace, host, plan, (0, 0, 0), 0, 0, 16)
            np.testing.assert_allclose(values[:, 0], expected, atol=1e-10, rtol=1e-12)

    def test_direct_and_specular_analytic_luminance_and_true_paths(self):
        for mirror in (False, True):
            with self.subTest(mirror=mirror):
                trace, host, plan, _ = fixture(mirror)
                values, paths = reference_chunk(trace, host, plan, (0, 0, 0), 0, 0, 64)
                np.testing.assert_allclose(values[:, 0], 90. if mirror else 100., rtol=0., atol=1e-10)
                np.testing.assert_array_equal(values[:, 2], 1 if mirror else 0)
                np.testing.assert_array_equal(values[:, 3], 3 if mirror else 2)
                np.testing.assert_array_equal(values[:, 6], 0)
                np.testing.assert_allclose(paths[:, 0, 2], -5.)
                if mirror:
                    np.testing.assert_allclose(paths[:, 1, 2], 0.)

    def test_solid_occluder_blocks_sources_and_no_path_is_fabricated(self):
        trace, host, plan, _ = fixture(blocked=True)
        values, _ = reference_chunk(trace, host, plan, (0, 0, 0), 0, 0, 64)
        np.testing.assert_array_equal(values[:, :4], 0)

    def test_counter_samples_are_chunk_and_resume_invariant(self):
        trace, host, plan, _ = fixture(True)
        whole, paths = reference_chunk(trace, host, plan, (0, 0, 0), 3, 0, 32)
        pieces = [reference_chunk(trace, host, plan, (0, 0, 0), 3, start, n) for start, n in ((0, 7), (7, 9), (16, 16))]
        np.testing.assert_array_equal(whole, np.concatenate([p[0] for p in pieces]))
        np.testing.assert_array_equal(paths, np.concatenate([p[1] for p in pieces]))

    def test_compiled_numeric_helpers_match_python_equations(self):
        trace, host, plan, _ = fixture(True)
        # Include continuous NEE/shadow and continuation arithmetic, not just
        # straight mirror rays, in the Numba typing/numerical gate.
        host["materials"][:, 2:4] = (1., 0.)
        plain, paths = reference_chunk(trace, host, plan, (0, 0, 0), 9, 100, 12)
        compiled, compiled_paths = reference_chunk(trace, host, plan, (0, 0, 0), 9, 100, 12, compiled_helpers=True)
        np.testing.assert_allclose(plain, compiled, atol=1e-12, rtol=1e-12)
        np.testing.assert_allclose(paths, compiled_paths, atol=1e-12, rtol=1e-12)
        host["materials"][:, 2] = .3
        host["materials"][:, 5] = .7
        plain, paths = reference_chunk(trace, host, plan, (0, 0, 0), 9, 100, 12)
        compiled, compiled_paths = reference_chunk(trace, host, plan, (0, 0, 0), 9, 100, 12, compiled_helpers=True)
        np.testing.assert_allclose(plain, compiled, atol=1e-10, rtol=1e-10)
        np.testing.assert_allclose(paths, compiled_paths, atol=1e-10, rtol=1e-10)

    def test_compact_partial_cells_keep_counts_sums_and_first_best_path(self):
        trace, host, plan, _ = fixture(True)
        host["materials"][:, 2:4] = (1., 0.)
        host["materials"][:, 5] = .4
        host["materials"][:, 2] = .6
        values, paths = reference_chunk(trace, host, plan, (0, 0, 0), 0, 3, 31)
        path_map = {i: [tuple(p) for p in paths[i, :int(row[3])]] for i, row in enumerate(values) if row[3] > 0}
        batch = compact_cell_batch(trace, values, 3, 0, plan["columns"], path_map)
        self.assertEqual(batch.sample_count, 31)
        for cell in batch:
            pixel = cell["row"]*plan["columns"]+cell["column"]
            indices = [i for i in range(31) if (3+i)//4 == pixel]
            self.assertEqual(cell["sample_count"], len(indices))
            self.assertAlmostEqual(cell["sum"], sum(values[i, 0] for i in indices), delta=1e-10)
            self.assertEqual(cell["direct_count"], sum(values[i, 3] > 0 and values[i, 2] == 0 for i in indices))
            self.assertEqual(cell["reflected_count"], sum(values[i, 3] > 0 and values[i, 2] > 0 for i in indices))
            best = max(indices, key=lambda i: values[i, 1])
            self.assertEqual(cell["representative_path"], path_map.get(best))

    def test_diffuse_uniform_hemisphere_preserves_r_times_l(self):
        trace, _, plan, resolved = fixture(True)
        trace.emitters = [source((0, 0, -5), 100000., 100., reverse=True)]
        host = pack_scene(trace.mesh, _source_geometry(trace.mesh, trace.emitters), resolved)
        host["materials"][:, 2:4] = (1., 0.)
        values, _ = reference_chunk(trace, host, plan, (0, 0, 0), 0, 0, 256)
        self.assertAlmostEqual(float(values[:, 0].mean()), 90., delta=.5)
        self.assertTrue(np.all(values[:, 0] >= 0))

    def test_resident_host_integration_preserves_pause_and_reports_cpu_oracle_honestly(self):
        from leakage_simulator.observer_probe import trace_observer_paths
        from test_preview_radiance_resume import preview_fixture, physical
        from leakage_simulator.escape_collection import EscapeCollector
        from leakage_simulator.types import ReceiverGrid
        trace = preview_fixture()
        trace.config.compute_backend = "gpu_cuda"
        resolved = OpticalPropertyResolver(trace.mesh, trace.optical_profiles, []).resolve_all()
        raw_mode = [False]
        def create(*args):
            session = ResidentObserver.__new__(ResidentObserver)
            session.trace, session.failure_reason = trace, "test_cpu_oracle"
            session.host = pack_scene(trace.mesh, _source_geometry(trace.mesh, trace.emitters), resolved)
            if raw_mode[0]:
                original = session.run
                def raw_run(*args):
                    session.compact_cells = False
                    return original(*args)
                session.run = raw_run
            return session
        def render(stop=None, pause=None):
            collector = EscapeCollector(trace.escape_collection, trace.receivers,
                {r.receiver_id: ReceiverGrid.empty(r) for r in trace.receivers})
            with patch("leakage_simulator.gpu_cuda_observer.ResidentObserver", side_effect=create):
                return trace_observer_paths(trace, collector, resolved, stop, batch_size=7, pause_callback=pause)
        baseline = render()
        raw_mode[0] = True
        raw = render()
        raw_mode[0] = False
        # NumPy's grouped FP64 summation can differ by a few ULPs from the
        # scalar ordered reducer; all discrete counts and paths remain exact.
        compact_physical, raw_physical = copy.deepcopy(physical(baseline)), copy.deepcopy(physical(raw))
        for key, field in (("cells", "luminance_nit"), ("cells", "direct_luminance_nit"),
                           ("cells", "reflected_luminance_nit"), ("views", "peak_luminance_nit")):
            for a, b in zip(compact_physical[key], raw_physical[key]):
                av, bv = a.pop(field), b.pop(field)
                self.assertAlmostEqual(av, bv, delta=max(1e-12, abs(bv)*1e-12))
        self.assertEqual(compact_physical, raw_physical)
        stop = [True]
        snapshots = []
        def pause(summary):
            snapshots.append(copy.deepcopy(summary))
            stop[0] = False
            return True
        resumed = render(lambda: stop[0], pause)
        self.assertEqual(physical(resumed), physical(baseline))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(resumed["performance"]["gpu_cuda_gpu_success_count"], 0)
        self.assertEqual(resumed["performance"]["observer_resident_fallback_reason"], "test_cpu_oracle")
        self.assertEqual(resumed["execution_backend"], "cpu")
        self.assertEqual(resumed["sample_contract"], "observer_counter_latin_v1")

    def test_unsupported_optics_are_rejected_instead_of_approximated(self):
        trace, _, _, resolved = fixture()
        changed = copy.deepcopy(resolved)
        for item in changed:
            item.profile.bsdf_asset_id = "measured"
        with self.assertRaisesRegex(ObserverGpuUnsupported, "bsdf"):
            pack_scene(trace.mesh, _source_geometry(trace.mesh, trace.emitters), changed)

    def test_gaussian_equations_match_independent_reference_at_grazing_angles(self):
        from leakage_simulator.observer_radiance import gaussian_acceptance, brdf, continuation_pdf, delta_weight
        class Cpu:
            def jit(self, **kwargs):
                return lambda fn: fn
        helpers = {}
        make_kernel(Cpu(), None, helpers)
        trace, _, _, resolved = fixture(True)
        for sigma in (.01, 1., 18., 80.):
            profile = resolved[0].profile
            profile.scatter_model = "gaussian"
            profile.gaussian_sigma_deg = sigma
            host = pack_scene(trace.mesh, _source_geometry(trace.mesh, trace.emitters), resolved)
            for degrees in (0., 45., 89.9):
                ci = math.cos(math.radians(degrees))
                wi = (math.sin(math.radians(degrees)), 0., ci)
                wo = (-wi[0], 0., ci)
                normal = (0., 0., 1.)
                self.assertAlmostEqual(helpers["acceptance"](host["quadrature"], math.radians(sigma), ci), gaussian_acceptance(sigma, ci), delta=1e-12)
                for outgoing in (wo, (0., 0., 1.)):
                    expected = brdf(profile, normal, wi, outgoing, False)
                    actual = helpers["brdf"](host["materials"], 0, normal, wi, outgoing, False, host["quadrature"])
                    self.assertAlmostEqual(actual, expected, delta=max(1e-9, abs(expected)*1e-11))
                    self.assertAlmostEqual(helpers["pdf"](host["materials"], 0, normal, outgoing, wi, host["quadrature"]),
                        continuation_pdf(profile, normal, outgoing, wi, False), delta=max(1e-9, abs(expected)*1e-11))

    def test_kernel_failure_replays_exact_counter_chunk_and_disables_gpu(self):
        trace, host, plan, _ = fixture(True)
        session = ResidentObserver.__new__(ResidentObserver)
        session.trace, session.host, session.failure_reason = trace, host, None
        expected, _ = reference_chunk(trace, host, plan, (0, 0, 0), 0, 7, 13)
        with patch.object(session, "_run_gpu", side_effect=RuntimeError("test_device_error")) as launch:
            for _ in range(2):
                completed, execution, _ = session.run(plan, (0, 0, 0), 0, 7, 13)
                self.assertTrue(execution.cpu_fallback)
                self.assertEqual(execution.reason, "test_device_error")
                self.assertEqual([p[0] for p in completed], list(range(7, 20)))
                np.testing.assert_array_equal([p[1] for p in completed], expected[:, 0])
            self.assertEqual(launch.call_count, 1)
