from __future__ import annotations

import json
import math
import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_contribution_rt2d import reflected_input

from leakage_simulator.geometry import RayBatch, TriangleMesh, add_box, vec_norm
from leakage_simulator.native_cpu_intersection import (
    NativeCpuCapability,
    NativeCpuExecution,
    NativeCpuProviderError,
    NativeCpuScalarExecution,
    NativeCpuUnavailable,
    probe_native_cpu,
)
from leakage_simulator.raytracer import run_direct_ray_trace


def semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def build_parallel_triangles() -> TriangleMesh:
    mesh = TriangleMesh()
    for z_value, material_id in ((5.0, "near"), (10.0, "far")):
        vertices = [
            mesh.add_vertex((-1.0, -1.0, z_value)),
            mesh.add_vertex((1.0, -1.0, z_value)),
            mesh.add_vertex((0.0, 1.0, z_value)),
        ]
        mesh.add_face(*vertices, material_id)
    return mesh


def build_box_array() -> TriangleMesh:
    mesh = TriangleMesh()
    component_id = 0
    for x_index in range(4):
        for y_index in range(3):
            x0 = x_index * 3.5
            y0 = y_index * 3.25
            z0 = float((x_index + y_index) % 3)
            add_box(
                mesh,
                x0,
                y0,
                z0,
                x0 + 2.0,
                y0 + 1.75,
                z0 + 1.5,
                "test",
                {"component_id": component_id},
            )
            component_id += 1
    return mesh


def build_random_rays(
    count: int,
    seed: int = 20260818,
) -> tuple[np.ndarray, np.ndarray]:
    rng = random.Random(seed)
    origins = []
    directions = []
    for _ in range(count):
        origin = (
            rng.uniform(-5.0, 16.0),
            rng.uniform(-5.0, 12.0),
            -8.0,
        )
        target = (
            rng.uniform(0.2, 12.0),
            rng.uniform(0.2, 8.0),
            rng.uniform(0.2, 3.2),
        )
        origins.append(origin)
        directions.append(
            vec_norm(
                (
                    target[0] - origin[0],
                    target[1] - origin[1],
                    target[2] - origin[2],
                )
            )
        )
    return (
        np.asarray(origins, dtype=np.float64),
        np.asarray(directions, dtype=np.float64),
    )


def reflected_case(ray_count: int = 41):
    trace_input = reflected_input(ray_count=ray_count, with_blocker=True)
    trace_input.config.intersection_backend = "bvh"
    trace_input.config.store_ray_paths = True
    trace_input.config.max_stored_paths = 13
    return trace_input


class NativeCpuBackendPerf3B2Tests(unittest.TestCase):
    def require_native_cpu(self) -> None:
        capability = probe_native_cpu()
        if not capability.available:
            self.skipTest(capability.reason_code or "native CPU provider unavailable")

    def assert_native_matches_bvh(
        self,
        mesh: TriangleMesh,
        rays: RayBatch,
    ) -> None:
        reference = mesh.intersect_rays(rays, backend="bvh")
        native, execution = mesh.intersect_rays_native_cpu(rays, backend="bvh")

        np.testing.assert_array_equal(native.face_indices, reference.face_indices)
        np.testing.assert_array_equal(native.t, reference.t)
        self.assertEqual(execution.distances.dtype, np.float64)
        self.assertEqual(execution.face_indices.dtype, np.int64)

    def test_default_auto_does_not_probe_or_call_native_provider(self) -> None:
        trace_input = reflected_case(23)
        reference = run_direct_ray_trace(
            reflected_case(23),
            intersection_dispatch="scalar",
            intersection_provider="python_cpu",
        )

        with (
            patch(
                "leakage_simulator.native_cpu_intersection.probe_native_cpu",
                side_effect=AssertionError("default auto must not probe Numba"),
            ) as probe_mock,
            patch.object(
                trace_input.mesh,
                "intersect_rays_native_cpu",
                side_effect=AssertionError("default auto must not call native batch"),
            ) as batch_mock,
            patch.object(
                trace_input.mesh,
                "intersect_ray_native_cpu",
                side_effect=AssertionError("default auto must not call native scalar"),
            ) as scalar_mock,
        ):
            result = run_direct_ray_trace(trace_input)

        self.assertEqual(semantic_payload(result), semantic_payload(reference))
        probe_mock.assert_not_called()
        batch_mock.assert_not_called()
        scalar_mock.assert_not_called()
        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["requested_intersection_provider"], "auto")
        self.assertEqual(performance["intersection_provider"], "python_cpu")
        self.assertIsNone(performance["native_available"])
        self.assertFalse(performance["native_used"])
        self.assertEqual(performance["native_attempt_count"], 0)
        self.assertEqual(performance["intersection_fallback_count"], 0)

    def test_native_batch_matches_seeded_bvh_rays_exactly(self) -> None:
        self.require_native_cpu()
        mesh = build_box_array()
        origins, directions = build_random_rays(257)
        self.assert_native_matches_bvh(mesh, RayBatch(origins, directions))

    def test_native_preserves_min_max_and_ignore_face_boundaries(self) -> None:
        self.require_native_cpu()
        mesh = build_parallel_triangles()
        rays = RayBatch(
            origins=np.zeros((8, 3), dtype=np.float64),
            directions=np.tile((0.0, 0.0, 1.0), (8, 1)),
            min_t=[1e-8, 1e-8, 1e-8, 1e-8, 5.0, 1e-8, 1e-8, 5.0],
            max_t=[math.inf, math.inf, 4.999, 5.0, math.inf, 9.999, 10.0, 5.0],
            ignore_faces=[-1, 0, -1, -1, -1, 0, 0, -1],
        )

        self.assert_native_matches_bvh(mesh, rays)
        native, _ = mesh.intersect_rays_native_cpu(rays, backend="bvh")
        np.testing.assert_array_equal(
            native.face_indices,
            np.asarray([0, 1, -1, 0, 1, -1, 1, -1]),
        )
        np.testing.assert_array_equal(
            native.t[[0, 1, 3, 4, 6]],
            np.asarray([5.0, 10.0, 5.0, 10.0, 10.0]),
        )

    def test_native_trace_excluded_and_ignore_face_are_transparent(self) -> None:
        self.require_native_cpu()
        mesh = TriangleMesh()
        for z_value, excluded in ((5.0, True), (10.0, False)):
            vertices = [
                mesh.add_vertex((-1.0, -1.0, z_value)),
                mesh.add_vertex((1.0, -1.0, z_value)),
                mesh.add_vertex((0.0, 1.0, z_value)),
            ]
            mesh.add_face(
                *vertices,
                "test",
                {"trace_excluded": excluded},
            )
        rays = RayBatch(
            origins=[(0.0, 0.0, 0.0), (0.0, 0.0, 0.0)],
            directions=[(0.0, 0.0, 1.0), (0.0, 0.0, 1.0)],
            ignore_faces=[-1, 1],
        )

        self.assert_native_matches_bvh(mesh, rays)
        native, _ = mesh.intersect_rays_native_cpu(rays, backend="bvh")
        np.testing.assert_array_equal(native.face_indices, [1, -1])
        self.assertEqual(native.t[0], 10.0)
        self.assertTrue(math.isinf(native.t[1]))

    def test_native_ties_keep_lowest_original_face_index(self) -> None:
        self.require_native_cpu()
        mesh = TriangleMesh()
        delta = 5e-11
        for z_value in (5.0 + delta, 5.0):
            vertices = [
                mesh.add_vertex((-1.0, -1.0, z_value)),
                mesh.add_vertex((1.0, -1.0, z_value)),
                mesh.add_vertex((0.0, 1.0, z_value)),
            ]
            mesh.add_face(*vertices, "tie")
        rays = RayBatch([(0.0, 0.0, 0.0)], [(0.0, 0.0, 1.0)])

        self.assert_native_matches_bvh(mesh, rays)
        native, _ = mesh.intersect_rays_native_cpu(rays, backend="bvh")
        self.assertEqual(native.face_indices.tolist(), [0])
        self.assertEqual(native.t.tolist(), [5.0 + delta])

        edge_mesh = TriangleMesh()
        vertices = [
            edge_mesh.add_vertex((-1.0, -1.0, 5.0)),
            edge_mesh.add_vertex((1.0, -1.0, 5.0)),
            edge_mesh.add_vertex((1.0, 1.0, 5.0)),
            edge_mesh.add_vertex((-1.0, 1.0, 5.0)),
        ]
        edge_mesh.add_face(vertices[0], vertices[1], vertices[2], "first")
        edge_mesh.add_face(vertices[0], vertices[2], vertices[3], "second")
        edge_ray = RayBatch([(0.0, 0.0, 0.0)], [(0.0, 0.0, 1.0)])
        self.assert_native_matches_bvh(edge_mesh, edge_ray)
        edge_hit, _ = edge_mesh.intersect_rays_native_cpu(edge_ray, backend="bvh")
        self.assertEqual(edge_hit.face_indices.tolist(), [0])

    def test_native_matches_aabb_and_determinant_thresholds(self) -> None:
        self.require_native_cpu()
        square = TriangleMesh()
        vertices = [
            square.add_vertex((-1.0, -1.0, 5.0)),
            square.add_vertex((1.0, -1.0, 5.0)),
            square.add_vertex((1.0, 1.0, 5.0)),
            square.add_vertex((-1.0, 1.0, 5.0)),
        ]
        square.add_face(vertices[0], vertices[1], vertices[2], "square")
        square.add_face(vertices[0], vertices[2], vertices[3], "square")
        below_box_eps = np.nextafter(1e-12, 0.0)
        at_box_eps = 1e-12
        above_box_eps = np.nextafter(1e-12, math.inf)
        box_directions = [
            (value, 0.0, math.sqrt(1.0 - value * value))
            for value in (below_box_eps, at_box_eps, above_box_eps)
        ]
        box_rays = RayBatch(
            [(-1.0 - 4e-12, 0.0, 0.0)] * 3,
            box_directions,
        )
        self.assert_native_matches_bvh(square, box_rays)
        box_hits, _ = square.intersect_rays_native_cpu(box_rays, backend="bvh")
        self.assertEqual(box_hits.face_indices[0], -1)
        self.assertGreaterEqual(box_hits.face_indices[1], 0)
        self.assertGreaterEqual(box_hits.face_indices[2], 0)

        triangle = TriangleMesh()
        triangle_vertices = [
            triangle.add_vertex((0.0, 0.0, 0.0)),
            triangle.add_vertex((1.0, 0.0, 0.0)),
            triangle.add_vertex((0.0, 1.0, 0.0)),
        ]
        triangle.add_face(*triangle_vertices, "threshold")
        below_det_eps = np.nextafter(1e-8, 0.0)
        at_det_eps = 1e-8
        above_det_eps = np.nextafter(1e-8, math.inf)
        determinant_directions = [
            (math.sqrt(1.0 - value * value), 0.0, value)
            for value in (below_det_eps, at_det_eps, above_det_eps)
        ]
        determinant_rays = RayBatch(
            [(-0.75, 0.25, -value) for value in (
                below_det_eps,
                at_det_eps,
                above_det_eps,
            )],
            determinant_directions,
        )
        self.assert_native_matches_bvh(triangle, determinant_rays)
        determinant_hits, _ = triangle.intersect_rays_native_cpu(
            determinant_rays,
            backend="bvh",
        )
        self.assertEqual(determinant_hits.face_indices[0], -1)
        self.assertEqual(determinant_hits.face_indices[1:].tolist(), [0, 0])

    def test_native_scene_cache_invalidates_after_mesh_mutation(self) -> None:
        self.require_native_cpu()
        mesh = build_parallel_triangles()
        rays = RayBatch([(0.0, 0.0, 0.0)], [(0.0, 0.0, 1.0)])
        first_hits, _ = mesh.intersect_rays_native_cpu(rays, backend="bvh")
        first_scene = mesh.prepare_native_cpu_scene()
        self.assertEqual(first_hits.face_indices.tolist(), [0])
        self.assertTrue(all(not array.flags.writeable for array in (
            first_scene.triangle_v0,
            first_scene.triangle_edge1,
            first_scene.triangle_edge2,
            first_scene.node_bounds_min,
            first_scene.node_bounds_max,
            first_scene.ordered_faces,
        )))

        new_vertices = [
            mesh.add_vertex((-1.0, -1.0, 2.0)),
            mesh.add_vertex((1.0, -1.0, 2.0)),
            mesh.add_vertex((0.0, 1.0, 2.0)),
        ]
        mesh.add_face(*new_vertices, "new_nearest")
        second_hits, _ = mesh.intersect_rays_native_cpu(rays, backend="bvh")
        second_scene = mesh.prepare_native_cpu_scene()

        self.assertIsNot(first_scene, second_scene)
        self.assertEqual(len(second_scene.triangle_v0), 3)
        self.assertEqual(second_hits.face_indices.tolist(), [2])
        self.assertEqual(second_hits.t.tolist(), [2.0])
        self.assert_native_matches_bvh(mesh, rays)

    def test_native_scalar_handles_large_coordinates(self) -> None:
        self.require_native_cpu()
        mesh = TriangleMesh()
        base = 1_000_000_000.0
        vertices = [
            mesh.add_vertex((base - 10.0, base - 10.0, base + 5.0)),
            mesh.add_vertex((base + 10.0, base - 10.0, base + 5.0)),
            mesh.add_vertex((base, base + 10.0, base + 5.0)),
        ]
        mesh.add_face(*vertices, "large")
        origin = (base, base, base)
        direction = (0.0, 0.0, 1.0)

        reference = mesh.intersect_ray(origin, direction, backend="bvh")
        native, execution = mesh.intersect_ray_native_cpu(
            origin,
            direction,
            backend="bvh",
        )

        self.assertIsNotNone(reference)
        self.assertIsNotNone(native)
        assert reference is not None and native is not None
        self.assertEqual(native.face_index, reference.face_index)
        self.assertEqual(native.t, reference.t)
        self.assertEqual(native.point, reference.point)
        self.assertEqual(native.normal, reference.normal)
        self.assertEqual(native.t, 5.0)
        self.assertIsInstance(execution.numba_version, str)

    def test_native_result_validation_rejects_per_ray_contract_violations(self) -> None:
        capability = NativeCpuCapability(True, None, "test")

        def execution(distance: float, face_index: int = 0) -> NativeCpuExecution:
            return NativeCpuExecution(
                distances=np.asarray([distance], dtype=np.float64),
                face_indices=np.asarray([face_index], dtype=np.int64),
                scene_build_sec=0.0,
                jit_compile_sec=0.0,
                execute_sec=0.0,
                numba_version="test",
            )

        regular_mesh = build_parallel_triangles()
        excluded_mesh = TriangleMesh()
        excluded_vertices = [
            excluded_mesh.add_vertex((-1.0, -1.0, 5.0)),
            excluded_mesh.add_vertex((1.0, -1.0, 5.0)),
            excluded_mesh.add_vertex((0.0, 1.0, 5.0)),
        ]
        excluded_mesh.add_face(
            *excluded_vertices,
            "excluded",
            {"trace_excluded": True},
        )
        cases = (
            (
                "minimum",
                regular_mesh,
                RayBatch([(0.0, 0.0, 0.0)], [(0.0, 0.0, 1.0)], min_t=[5.0]),
                execution(5.0),
                "native_result_distance_out_of_bounds",
            ),
            (
                "maximum",
                regular_mesh,
                RayBatch(
                    [(0.0, 0.0, 0.0)],
                    [(0.0, 0.0, 1.0)],
                    max_t=[4.999],
                ),
                execution(5.0),
                "native_result_distance_out_of_bounds",
            ),
            (
                "ignored_face",
                regular_mesh,
                RayBatch(
                    [(0.0, 0.0, 0.0)],
                    [(0.0, 0.0, 1.0)],
                    ignore_faces=[0],
                ),
                execution(5.0),
                "native_result_ignored_face",
            ),
            (
                "trace_excluded",
                excluded_mesh,
                RayBatch([(0.0, 0.0, 0.0)], [(0.0, 0.0, 1.0)]),
                execution(5.0),
                "native_result_trace_excluded_face",
            ),
        )
        for name, mesh, rays, injected_execution, reason in cases:
            with self.subTest(name=name), patch(
                "leakage_simulator.native_cpu_intersection.probe_native_cpu",
                return_value=capability,
            ), patch(
                "leakage_simulator.native_cpu_intersection.intersect_native_cpu",
                return_value=injected_execution,
            ):
                with self.assertRaisesRegex(NativeCpuProviderError, reason) as caught:
                    mesh.intersect_rays_native_cpu(rays, backend="bvh")
                self.assertEqual(caught.exception.phase, "result_validation")

        def scalar_execution(
            distance: float,
            face_index: int = 0,
        ) -> NativeCpuScalarExecution:
            return NativeCpuScalarExecution(
                distance=distance,
                face_index=face_index,
                scene_build_sec=0.0,
                jit_compile_sec=0.0,
                execute_sec=0.0,
                numba_version="test",
            )

        scalar_cases = (
            (
                "scalar_minimum",
                regular_mesh,
                {"min_t": 5.0},
                scalar_execution(5.0),
                "native_result_distance_out_of_bounds",
            ),
            (
                "scalar_maximum",
                regular_mesh,
                {"max_t": 4.999},
                scalar_execution(5.0),
                "native_result_distance_out_of_bounds",
            ),
            (
                "scalar_ignored_face",
                regular_mesh,
                {"ignore_face": 0},
                scalar_execution(5.0),
                "native_result_ignored_face",
            ),
            (
                "scalar_trace_excluded",
                excluded_mesh,
                {},
                scalar_execution(5.0),
                "native_result_trace_excluded_face",
            ),
        )
        for name, mesh, kwargs, injected_execution, reason in scalar_cases:
            with self.subTest(name=name), patch(
                "leakage_simulator.native_cpu_intersection.probe_native_cpu",
                return_value=capability,
            ), patch(
                "leakage_simulator.native_cpu_intersection.intersect_one_native_cpu",
                return_value=injected_execution,
            ):
                with self.assertRaisesRegex(NativeCpuProviderError, reason) as caught:
                    mesh.intersect_ray_native_cpu(
                        (0.0, 0.0, 0.0),
                        (0.0, 0.0, 1.0),
                        backend="bvh",
                        **kwargs,
                    )
                self.assertEqual(caught.exception.phase, "result_validation")

    def test_invalid_native_output_replays_whole_batch_and_opens_circuit(self) -> None:
        reference = run_direct_ray_trace(
            reflected_case(41),
            intersection_dispatch="batch",
            intersection_batch_size=8,
            intersection_provider="python_cpu",
        )
        trace_input = reflected_case(41)
        original_reference = trace_input.mesh.intersect_rays
        capability = NativeCpuCapability(True, None, "test")

        def invalid_native_output(
            scene,
            origins,
            directions,
            minimum_t,
            maximum_t,
            ignored_faces,
        ) -> NativeCpuExecution:
            return NativeCpuExecution(
                distances=np.array(minimum_t, dtype=np.float64, copy=True),
                face_indices=np.zeros(len(origins), dtype=np.int64),
                scene_build_sec=scene.build_sec,
                jit_compile_sec=0.0,
                execute_sec=0.0,
                numba_version="test",
            )

        with (
            patch(
                "leakage_simulator.native_cpu_intersection.probe_native_cpu",
                return_value=capability,
            ),
            patch(
                "leakage_simulator.native_cpu_intersection.intersect_native_cpu",
                side_effect=invalid_native_output,
            ) as native_mock,
            patch.object(
                trace_input.mesh,
                "intersect_rays",
                wraps=original_reference,
            ) as reference_mock,
        ):
            result = run_direct_ray_trace(
                trace_input,
                intersection_dispatch="batch",
                intersection_batch_size=8,
                intersection_provider="numba_cpu",
            )

        self.assertEqual(semantic_payload(result), semantic_payload(reference))
        native_mock.assert_called_once()
        performance = result.metrics["_performance_summary"]
        self.assertTrue(performance["native_provider_disabled"])
        self.assertEqual(performance["native_attempt_count"], 1)
        self.assertEqual(performance["native_success_count"], 0)
        self.assertEqual(performance["intersection_fallback_count"], 1)
        self.assertEqual(
            performance["intersection_fallback_phase"],
            "result_validation",
        )
        self.assertEqual(
            performance["intersection_fallback_reason"],
            "native_result_distance_out_of_bounds",
        )
        self.assertEqual(
            sum(len(call.args[0]) for call in reference_mock.call_args_list),
            performance["intersection_ray_count"],
        )
        self.assertEqual(
            performance["intersection_ray_count"],
            reference.metrics["_performance_summary"]["intersection_ray_count"],
        )

    def test_native_provider_matches_python_end_to_end_and_reports_metrics(self) -> None:
        self.require_native_cpu()
        reference = run_direct_ray_trace(
            reflected_case(73),
            intersection_dispatch="batch",
            intersection_batch_size=11,
            intersection_provider="python_cpu",
        )
        native = run_direct_ray_trace(
            reflected_case(73),
            intersection_dispatch="batch",
            intersection_batch_size=11,
            intersection_provider="numba_cpu",
        )

        self.assertEqual(semantic_payload(native), semantic_payload(reference))
        performance = native.metrics["_performance_summary"]
        reference_performance = reference.metrics["_performance_summary"]
        self.assertEqual(performance["intersection_provider"], "numba_cpu")
        self.assertTrue(performance["native_available"])
        self.assertTrue(performance["native_used"])
        self.assertTrue(performance["native_batch"])
        self.assertGreater(performance["native_batch_success_count"], 0)
        self.assertEqual(
            performance["native_success_count"],
            performance["intersection_batch_count"],
        )
        self.assertEqual(performance["reference_batch_count"], 0)
        self.assertEqual(performance["intersection_fallback_count"], 0)
        self.assertEqual(
            performance["intersection_ray_count"],
            reference_performance["intersection_ray_count"],
        )
        self.assertGreaterEqual(performance["native_execute_sec"], 0.0)
        json.dumps(native.to_dict(), allow_nan=False)

    def test_native_scalar_provider_matches_python_end_to_end(self) -> None:
        self.require_native_cpu()
        reference = run_direct_ray_trace(
            reflected_case(19),
            intersection_dispatch="scalar",
            intersection_provider="python_cpu",
        )
        native = run_direct_ray_trace(
            reflected_case(19),
            intersection_dispatch="scalar",
            intersection_provider="numba_cpu",
        )

        self.assertEqual(semantic_payload(native), semantic_payload(reference))
        performance = native.metrics["_performance_summary"]
        self.assertEqual(performance["intersection_provider"], "numba_cpu")
        self.assertEqual(
            performance["native_scalar_success_count"],
            performance["intersection_scalar_query_count"],
        )
        self.assertEqual(performance["reference_scalar_query_count"], 0)
        self.assertEqual(performance["intersection_fallback_count"], 0)

    def test_unavailable_provider_selects_reference_once_without_failure_count(self) -> None:
        reference = run_direct_ray_trace(
            reflected_case(41),
            intersection_dispatch="batch",
            intersection_batch_size=8,
            intersection_provider="python_cpu",
        )
        trace_input = reflected_case(41)
        original_reference = trace_input.mesh.intersect_rays
        with (
            patch.object(
                trace_input.mesh,
                "intersect_rays_native_cpu",
                side_effect=NativeCpuUnavailable("injected_unavailable"),
            ) as native_mock,
            patch.object(
                trace_input.mesh,
                "intersect_rays",
                wraps=original_reference,
            ) as reference_mock,
        ):
            result = run_direct_ray_trace(
                trace_input,
                intersection_dispatch="batch",
                intersection_batch_size=8,
                intersection_provider="numba_cpu",
            )

        self.assertEqual(semantic_payload(result), semantic_payload(reference))
        native_mock.assert_called_once()
        performance = result.metrics["_performance_summary"]
        self.assertIs(performance["native_available"], False)
        self.assertTrue(performance["native_provider_disabled"])
        self.assertEqual(performance["intersection_provider"], "python_cpu")
        self.assertEqual(performance["intersection_fallback_count"], 0)
        self.assertEqual(
            performance["intersection_provider_unavailable_reason"],
            "injected_unavailable",
        )
        self.assertEqual(
            sum(len(call.args[0]) for call in reference_mock.call_args_list),
            performance["intersection_ray_count"],
        )
        self.assertEqual(
            performance["intersection_ray_count"],
            reference.metrics["_performance_summary"]["intersection_ray_count"],
        )

    def test_batch_provider_failure_replays_whole_query_and_opens_circuit(self) -> None:
        reference = run_direct_ray_trace(
            reflected_case(41),
            intersection_dispatch="batch",
            intersection_batch_size=8,
            intersection_provider="python_cpu",
        )
        for phase, reason in (
            ("initialize", "injected_initialize_failure"),
            ("execute", "injected_execute_failure"),
            ("result_validation", "injected_invalid_result"),
        ):
            with self.subTest(phase=phase):
                trace_input = reflected_case(41)
                original_reference = trace_input.mesh.intersect_rays
                with (
                    patch.object(
                        trace_input.mesh,
                        "intersect_rays_native_cpu",
                        side_effect=NativeCpuProviderError(phase, reason),
                    ) as native_mock,
                    patch.object(
                        trace_input.mesh,
                        "intersect_rays",
                        wraps=original_reference,
                    ) as reference_mock,
                ):
                    result = run_direct_ray_trace(
                        trace_input,
                        intersection_dispatch="batch",
                        intersection_batch_size=8,
                        intersection_provider="numba_cpu",
                    )

                self.assertEqual(
                    semantic_payload(result),
                    semantic_payload(reference),
                )
                native_mock.assert_called_once()
                self.assertGreater(reference_mock.call_count, 1)
                native_rays = native_mock.call_args.args[0]
                first_reference_rays = reference_mock.call_args_list[0].args[0]
                self.assertIs(native_rays, first_reference_rays)
                performance = result.metrics["_performance_summary"]
                self.assertTrue(performance["native_available"])
                self.assertTrue(performance["native_provider_disabled"])
                self.assertEqual(performance["intersection_provider"], "python_cpu")
                self.assertEqual(performance["native_attempt_count"], 1)
                self.assertEqual(performance["native_attempt_ray_count"], len(native_rays))
                self.assertEqual(performance["native_success_count"], 0)
                self.assertEqual(performance["intersection_fallback_count"], 1)
                self.assertEqual(
                    performance["intersection_fallback_ray_count"],
                    len(native_rays),
                )
                self.assertEqual(performance["intersection_fallback_phase"], phase)
                self.assertEqual(performance["intersection_fallback_reason"], reason)
                self.assertEqual(
                    sum(len(call.args[0]) for call in reference_mock.call_args_list),
                    performance["intersection_ray_count"],
                )
                self.assertEqual(
                    performance["intersection_ray_count"],
                    reference.metrics["_performance_summary"]["intersection_ray_count"],
                )

    def test_scalar_provider_failure_opens_circuit_without_double_counting(self) -> None:
        reference = run_direct_ray_trace(
            reflected_case(19),
            intersection_dispatch="scalar",
            intersection_provider="python_cpu",
        )
        trace_input = reflected_case(19)
        original_reference = trace_input.mesh.intersect_ray
        with (
            patch.object(
                trace_input.mesh,
                "intersect_ray_native_cpu",
                side_effect=NativeCpuProviderError(
                    "execute",
                    "injected_scalar_execute_failure",
                ),
            ) as native_mock,
            patch.object(
                trace_input.mesh,
                "intersect_ray",
                wraps=original_reference,
            ) as reference_mock,
        ):
            result = run_direct_ray_trace(
                trace_input,
                intersection_dispatch="scalar",
                intersection_provider="numba_cpu",
            )

        self.assertEqual(semantic_payload(result), semantic_payload(reference))
        native_mock.assert_called_once()
        performance = result.metrics["_performance_summary"]
        self.assertTrue(performance["native_provider_disabled"])
        self.assertEqual(performance["native_attempt_count"], 1)
        self.assertEqual(performance["native_attempt_ray_count"], 1)
        self.assertEqual(performance["intersection_fallback_count"], 1)
        self.assertEqual(performance["intersection_fallback_ray_count"], 1)
        self.assertEqual(
            reference_mock.call_count,
            performance["intersection_scalar_query_count"],
        )
        self.assertEqual(
            performance["intersection_ray_count"],
            reference.metrics["_performance_summary"]["intersection_ray_count"],
        )


if __name__ == "__main__":
    unittest.main()
