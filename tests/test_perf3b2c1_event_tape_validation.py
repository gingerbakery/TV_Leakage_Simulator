from __future__ import annotations

from dataclasses import fields, replace
import inspect
import math
import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.wavefront_event_tape import (
    EVENT_TAPE_CONTRACT,
    LOBE_LAMBERTIAN,
    LOBE_NONE,
    LOBE_SPECULAR,
    PATH_PAYLOAD_FULL,
    PATH_PAYLOAD_OMITTED,
    RAY_KIND_DIRECT,
    RAY_KIND_LAMBERTIAN,
    RAY_KIND_SPECULAR,
    STATUS_ATTEMPTED,
    STATUS_DISABLED,
    STATUS_EMITTED,
    TERMINAL_BLOCKED,
    TERMINAL_ESCAPED,
    VALIDATION_STRICT,
    VALIDATION_TRUSTED,
    PrimaryMajorEventTape,
    PrimaryMajorEventTapeBuilder,
)


def _builder(*, include_path_payload: bool) -> PrimaryMajorEventTapeBuilder:
    origins = np.asarray(
        [(0.0, 1.0, 2.0), (3.0, 4.0, 5.0), (6.0, 7.0, 8.0)],
        dtype=np.float64,
    )
    directions = np.asarray(
        [(0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        dtype=np.float64,
    )
    return PrimaryMajorEventTapeBuilder(
        origins if include_path_payload else None,
        directions if include_path_payload else None,
        np.asarray([0.25, 0.5, 0.75], dtype=np.float64),
        np.asarray([101, 102, 103], dtype=np.uint64),
        2,
        include_path_payload=include_path_payload,
    )


def _populate_valid(builder: PrimaryMajorEventTapeBuilder) -> None:
    include_path_payload = builder.include_path_payload
    depth_zero_points = np.asarray(
        [(10.0, 0.0, 1.0), (20.0, 0.0, 2.0)],
        dtype=np.float64,
    )
    depth_zero_normals = np.asarray(
        [(0.0, 0.0, 1.0), (0.0, 1.0, 0.0)],
        dtype=np.float64,
    )
    builder.append_surface_events(
        depth=0,
        primary_slots=np.asarray([1, 2], dtype=np.int64),
        face_indices=np.asarray([10, 20], dtype=np.int64),
        points=depth_zero_points if include_path_payload else None,
        normals=depth_zero_normals if include_path_payload else None,
        distances_mm=(
            np.asarray([10.25, 20.25], dtype=np.float64)
            if include_path_payload
            else None
        ),
        incoming_power_lumen=np.asarray([0.5, 0.75], dtype=np.float64),
        reflected_power_lumen=np.asarray([0.4, 0.6], dtype=np.float64),
        emitted_power_lumen=np.asarray([0.0, 0.5], dtype=np.float64),
        status_flags=np.asarray(
            [
                STATUS_ATTEMPTED | STATUS_DISABLED,
                STATUS_ATTEMPTED | STATUS_EMITTED,
            ],
            dtype=np.uint16,
        ),
        lobe_codes=np.asarray([LOBE_NONE, LOBE_SPECULAR], dtype=np.int8),
        incoming_ray_kind_codes=np.asarray(
            [RAY_KIND_DIRECT, RAY_KIND_DIRECT],
            dtype=np.int8,
        ),
    )
    builder.append_surface_events(
        depth=1,
        primary_slots=np.asarray([2], dtype=np.int64),
        face_indices=np.asarray([21], dtype=np.int64),
        points=(
            np.asarray([(21.0, 1.0, 2.0)], dtype=np.float64)
            if include_path_payload
            else None
        ),
        normals=(
            np.asarray([(1.0, 0.0, 0.0)], dtype=np.float64)
            if include_path_payload
            else None
        ),
        distances_mm=(
            np.asarray([21.25], dtype=np.float64)
            if include_path_payload
            else None
        ),
        incoming_power_lumen=np.asarray([0.5], dtype=np.float64),
        reflected_power_lumen=np.asarray([0.4], dtype=np.float64),
        emitted_power_lumen=np.asarray([0.25], dtype=np.float64),
        status_flags=np.asarray(
            [STATUS_ATTEMPTED | STATUS_EMITTED],
            dtype=np.uint16,
        ),
        lobe_codes=np.asarray([LOBE_LAMBERTIAN], dtype=np.int8),
        incoming_ray_kind_codes=np.asarray([RAY_KIND_SPECULAR], dtype=np.int8),
    )
    builder.set_nonreceiver_terminals(
        primary_slots=np.asarray([0], dtype=np.int64),
        terminal_kind=TERMINAL_ESCAPED,
        depth=0,
        current_power_lumen=np.asarray([0.25], dtype=np.float64),
        ray_kind_codes=np.asarray([RAY_KIND_DIRECT], dtype=np.int8),
    )
    builder.set_nonreceiver_terminals(
        primary_slots=np.asarray([1], dtype=np.int64),
        terminal_kind=TERMINAL_BLOCKED,
        depth=0,
        current_power_lumen=np.asarray([0.5], dtype=np.float64),
        ray_kind_codes=np.asarray([RAY_KIND_DIRECT], dtype=np.int8),
    )
    builder.set_receiver_terminals(
        primary_slots=np.asarray([2], dtype=np.int64),
        depth=2,
        current_power_lumen=np.asarray([0.25], dtype=np.float64),
        ray_kind_codes=np.asarray([RAY_KIND_LAMBERTIAN], dtype=np.int8),
        receiver_indices=np.asarray([0], dtype=np.int32),
        rows=np.asarray([1], dtype=np.int32),
        columns=np.asarray([2], dtype=np.int32),
        received_power_lumen=np.asarray([0.125], dtype=np.float64),
        points=(
            np.asarray([(30.0, 1.0, 2.0)], dtype=np.float64)
            if include_path_payload
            else None
        ),
        normals=(
            np.asarray([(0.0, 0.0, -1.0)], dtype=np.float64)
            if include_path_payload
            else None
        ),
        distances_mm=(
            np.asarray([30.25], dtype=np.float64)
            if include_path_payload
            else None
        ),
        incoming_power_lumen=np.asarray([0.25], dtype=np.float64),
    )


def _build_valid(
    *,
    include_path_payload: bool = True,
    trusted: bool = False,
) -> tuple[PrimaryMajorEventTape, PrimaryMajorEventTapeBuilder]:
    builder = _builder(include_path_payload=include_path_payload)
    _populate_valid(builder)
    tape = builder._seal_trusted() if trusted else builder.seal()
    return tape, builder


def _readonly_mutation(array: np.ndarray, index, value) -> np.ndarray:
    mutated = np.array(array, copy=True, order="C")
    mutated[index] = value
    mutated.setflags(write=False)
    return mutated


def _float_bits(array: np.ndarray) -> bytes:
    return np.ascontiguousarray(array).view(np.uint8).tobytes()


class Perf3B2C1EventTapeValidationTests(unittest.TestCase):
    def test_public_strict_and_private_trusted_seals_are_bit_identical(self) -> None:
        self.assertEqual(EVENT_TAPE_CONTRACT, "ordered_primary_event_tape_v3")
        self.assertEqual(list(inspect.signature(PrimaryMajorEventTapeBuilder.seal).parameters), ["self"])

        for include_path_payload in (False, True):
            with self.subTest(include_path_payload=include_path_payload):
                strict_tape, strict_builder = _build_valid(
                    include_path_payload=include_path_payload,
                )
                trusted_tape, trusted_builder = _build_valid(
                    include_path_payload=include_path_payload,
                    trusted=True,
                )
                self.assertEqual(strict_builder.validation_mode, VALIDATION_STRICT)
                self.assertEqual(trusted_builder.validation_mode, VALIDATION_TRUSTED)
                self.assertIs(type(strict_builder.validation_sec), float)
                self.assertIs(type(trusted_builder.validation_sec), float)
                self.assertTrue(math.isfinite(strict_builder.validation_sec))
                self.assertTrue(math.isfinite(trusted_builder.validation_sec))
                self.assertGreaterEqual(strict_builder.validation_sec, 0.0)
                self.assertGreaterEqual(trusted_builder.validation_sec, 0.0)
                self.assertEqual(strict_builder.copy_bytes, trusted_builder.copy_bytes)
                self.assertGreater(strict_builder.copy_bytes, 0)

                for field in fields(strict_tape):
                    strict_value = getattr(strict_tape, field.name)
                    trusted_value = getattr(trusted_tape, field.name)
                    if isinstance(strict_value, np.ndarray):
                        self.assertEqual(strict_value.dtype, trusted_value.dtype, field.name)
                        self.assertEqual(strict_value.shape, trusted_value.shape, field.name)
                        self.assertEqual(_float_bits(strict_value), _float_bits(trusted_value), field.name)
                    else:
                        self.assertEqual(strict_value, trusted_value, field.name)
                trusted_tape.validate()

    def test_full_and_omitted_payload_keep_core_equal_and_arrays_owned_readonly(self) -> None:
        full, full_builder = _build_valid(include_path_payload=True)
        omitted, omitted_builder = _build_valid(include_path_payload=False)

        self.assertEqual(full.path_payload, PATH_PAYLOAD_FULL)
        self.assertEqual(omitted.path_payload, PATH_PAYLOAD_OMITTED)
        self.assertEqual(full.initial_origins.shape, (3, 3))
        self.assertEqual(full.points.shape, (3, 3))
        self.assertEqual(full.terminal_points.shape, (3, 3))
        self.assertEqual(omitted.initial_origins.shape, (0, 3))
        self.assertEqual(omitted.initial_directions.shape, (0, 3))
        self.assertEqual(omitted.initial_source_faces.shape, (0,))
        self.assertEqual(omitted.points.shape, (0, 3))
        self.assertEqual(omitted.normals.shape, (0, 3))
        self.assertEqual(omitted.distances_mm.shape, (0,))
        self.assertEqual(omitted.terminal_points.shape, (0, 3))
        self.assertEqual(omitted.terminal_normals.shape, (0, 3))
        self.assertEqual(omitted.terminal_distances_mm.shape, (0,))

        path_fields = {
            "initial_origins",
            "initial_directions",
            "initial_source_faces",
            "points",
            "normals",
            "distances_mm",
            "terminal_points",
            "terminal_normals",
            "terminal_distances_mm",
        }
        for field in fields(full):
            if field.name in path_fields or field.name in {"path_payload", "peak_bytes"}:
                continue
            full_value = getattr(full, field.name)
            omitted_value = getattr(omitted, field.name)
            if isinstance(full_value, np.ndarray):
                self.assertEqual(_float_bits(full_value), _float_bits(omitted_value), field.name)
            else:
                self.assertEqual(full_value, omitted_value, field.name)

        for tape in (full, omitted):
            owned_arrays = []
            for field in fields(tape):
                value = getattr(tape, field.name)
                if not isinstance(value, np.ndarray):
                    continue
                self.assertFalse(value.flags.writeable, field.name)
                self.assertTrue(value.flags.c_contiguous, field.name)
                self.assertTrue(value.flags.owndata, field.name)
                if value.nbytes:
                    owned_arrays.append((field.name, value))
            for index, (left_name, left) in enumerate(owned_arrays):
                for right_name, right in owned_arrays[index + 1 :]:
                    self.assertFalse(
                        np.shares_memory(left, right),
                        f"{left_name} aliases {right_name}",
                    )

        self.assertLess(omitted.nbytes, full.nbytes)
        self.assertLess(omitted.peak_bytes, full.peak_bytes)
        self.assertLess(omitted_builder.copy_bytes, full_builder.copy_bytes)
        full.validate()
        omitted.validate()

    def test_public_builder_copies_mutable_inputs_before_strict_seal(self) -> None:
        origins = np.asarray([(1.0, 2.0, 3.0)], dtype=np.float64)
        directions = np.asarray([(0.0, 0.0, 1.0)], dtype=np.float64)
        powers = np.asarray([0.5], dtype=np.float64)
        seeds = np.asarray([17], dtype=np.uint64)
        points = np.asarray([(4.0, 5.0, 6.0)], dtype=np.float64)
        normals = np.asarray([(0.0, 1.0, 0.0)], dtype=np.float64)
        distances = np.asarray([7.0], dtype=np.float64)
        builder = PrimaryMajorEventTapeBuilder(
            origins,
            directions,
            powers,
            seeds,
            0,
        )
        builder.append_surface_events(
            depth=0,
            primary_slots=np.asarray([0], dtype=np.int64),
            face_indices=np.asarray([3], dtype=np.int64),
            points=points,
            normals=normals,
            distances_mm=distances,
            incoming_power_lumen=np.asarray([0.5], dtype=np.float64),
            reflected_power_lumen=np.asarray([0.4], dtype=np.float64),
            emitted_power_lumen=np.asarray([0.0], dtype=np.float64),
            status_flags=np.asarray(
                [STATUS_ATTEMPTED | STATUS_DISABLED],
                dtype=np.uint16,
            ),
            lobe_codes=np.asarray([LOBE_NONE], dtype=np.int8),
            incoming_ray_kind_codes=np.asarray([RAY_KIND_DIRECT], dtype=np.int8),
        )
        builder.set_nonreceiver_terminals(
            primary_slots=np.asarray([0], dtype=np.int64),
            terminal_kind=TERMINAL_BLOCKED,
            depth=0,
            current_power_lumen=np.asarray([0.5], dtype=np.float64),
            ray_kind_codes=np.asarray([RAY_KIND_DIRECT], dtype=np.int8),
        )

        for source in (origins, directions, powers, seeds, points, normals, distances):
            source[...] = 99
        tape = builder.seal()

        np.testing.assert_array_equal(tape.initial_origins, [(1.0, 2.0, 3.0)])
        np.testing.assert_array_equal(tape.initial_directions, [(0.0, 0.0, 1.0)])
        np.testing.assert_array_equal(tape.initial_power_lumen, [0.5])
        np.testing.assert_array_equal(tape.reflection_seeds, [17])
        np.testing.assert_array_equal(tape.points, [(4.0, 5.0, 6.0)])
        np.testing.assert_array_equal(tape.normals, [(0.0, 1.0, 0.0)])
        np.testing.assert_array_equal(tape.distances_mm, [7.0])
        for source in (origins, directions, powers, seeds, points, normals, distances):
            for field in fields(tape):
                value = getattr(tape, field.name)
                if isinstance(value, np.ndarray):
                    self.assertFalse(np.shares_memory(source, value), field.name)

    def test_strict_validation_rejects_structural_and_semantic_corruption(self) -> None:
        tape, _ = _build_valid(include_path_payload=True)

        offsets_i32 = tape.offsets.astype(np.int32)
        offsets_i32.setflags(write=False)
        noncontiguous = np.zeros((3, 6), dtype=np.float64)[:, ::2]
        noncontiguous.setflags(write=False)
        writable_offsets = np.array(tape.offsets, copy=True)

        cases = (
            ("contract", replace(tape, contract="ordered_primary_event_tape_v1"), "unsupported event tape contract"),
            ("path_payload", replace(tape, path_payload="unknown"), "unsupported event tape path payload"),
            ("dtype", replace(tape, offsets=offsets_i32), "dtype int64"),
            ("writable", replace(tape, offsets=writable_offsets), "read-only"),
            ("noncontiguous", replace(tape, initial_origins=noncontiguous), "C-contiguous"),
            ("offset_start", replace(tape, offsets=_readonly_mutation(tape.offsets, 0, 1)), "monotonic"),
            ("offset_cover", replace(tape, offsets=_readonly_mutation(tape.offsets, -1, 2)), "cover the event arrays"),
            ("peak_bytes", replace(tape, peak_bytes=tape.nbytes - 1), "cover the sealed tape storage"),
            ("nonfinite", replace(tape, points=_readonly_mutation(tape.points, (1, 0), np.nan)), "must be finite"),
            ("negative_distance", replace(tape, distances_mm=_readonly_mutation(tape.distances_mm, 1, -1.0)), "non-negative"),
            ("negative_face", replace(tape, face_indices=_readonly_mutation(tape.face_indices, 1, -1)), "non-negative face"),
            ("status", replace(tape, status_flags=_readonly_mutation(tape.status_flags, 1, 0)), "valid planner outcome"),
            ("emitted_lobe", replace(tape, lobe_codes=_readonly_mutation(tape.lobe_codes, 1, LOBE_NONE)), "known lobe"),
            ("disabled_lobe", replace(tape, lobe_codes=_readonly_mutation(tape.lobe_codes, 0, LOBE_SPECULAR)), "LOBE_NONE"),
            ("disabled_power", replace(tape, emitted_power_lumen=_readonly_mutation(tape.emitted_power_lumen, 0, 0.1)), "zero emitted power"),
            ("terminal_kind", replace(tape, terminal_kind_codes=_readonly_mutation(tape.terminal_kind_codes, 0, 0)), "one terminal kind"),
            ("incoming_kind", replace(tape, incoming_ray_kind_codes=_readonly_mutation(tape.incoming_ray_kind_codes, 0, 9)), "unknown incoming ray kind"),
            ("terminal_ray_kind", replace(tape, terminal_ray_kind_codes=_readonly_mutation(tape.terminal_ray_kind_codes, 0, 9)), "unknown ray kind"),
            ("negative_depth", replace(tape, terminal_depths=_readonly_mutation(tape.terminal_depths, 0, -1)), "non-negative"),
            ("event_depth_count", replace(tape, terminal_depths=_readonly_mutation(tape.terminal_depths, 2, 1)), "event count and terminal depth"),
            ("first_power", replace(tape, incoming_power_lumen=_readonly_mutation(tape.incoming_power_lumen, 1, 0.7)), "first event power"),
            ("power_chain", replace(tape, incoming_power_lumen=_readonly_mutation(tape.incoming_power_lumen, 2, 0.4)), "powers must form one chain"),
            ("first_kind", replace(tape, incoming_ray_kind_codes=_readonly_mutation(tape.incoming_ray_kind_codes, 1, RAY_KIND_SPECULAR)), "first surface event"),
            ("kind_chain", replace(tape, incoming_ray_kind_codes=_readonly_mutation(tape.incoming_ray_kind_codes, 2, RAY_KIND_DIRECT)), "ray kinds must follow"),
            ("terminal_kind_chain", replace(tape, terminal_ray_kind_codes=_readonly_mutation(tape.terminal_ray_kind_codes, 2, RAY_KIND_SPECULAR)), "terminal ray kind"),
            ("terminal_power", replace(tape, terminal_current_power_lumen=_readonly_mutation(tape.terminal_current_power_lumen, 2, 0.3)), "terminal power"),
            ("receiver_index", replace(tape, terminal_receiver_indices=_readonly_mutation(tape.terminal_receiver_indices, 2, -1)), "receiver index"),
            ("receiver_cell", replace(tape, terminal_rows=_readonly_mutation(tape.terminal_rows, 2, -1)), "grid cell"),
            ("nonreceiver_index", replace(tape, terminal_receiver_indices=_readonly_mutation(tape.terminal_receiver_indices, 0, 0)), "non-receiver terminal"),
            ("receiver_power", replace(tape, terminal_incoming_power_lumen=_readonly_mutation(tape.terminal_incoming_power_lumen, 2, 0.3)), "receiver incoming power"),
        )
        for name, malformed, message in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, message):
                    malformed.validate()

        emitted_status = _readonly_mutation(
            tape.status_flags,
            0,
            STATUS_ATTEMPTED | STATUS_EMITTED,
        )
        emitted_lobes = _readonly_mutation(tape.lobe_codes, 0, LOBE_SPECULAR)
        emitted_power = _readonly_mutation(tape.emitted_power_lumen, 0, 0.4)
        with self.assertRaisesRegex(ValueError, "emission order"):
            replace(
                tape,
                status_flags=emitted_status,
                lobe_codes=emitted_lobes,
                emitted_power_lumen=emitted_power,
            ).validate()

    def test_payload_presence_and_builder_requirements_are_strict(self) -> None:
        full, _ = _build_valid(include_path_payload=True)
        omitted, _ = _build_valid(include_path_payload=False)
        with self.assertRaisesRegex(ValueError, "initial_origins must have shape"):
            replace(omitted, path_payload=PATH_PAYLOAD_FULL).validate()
        with self.assertRaisesRegex(ValueError, "initial_origins must have shape"):
            replace(full, path_payload=PATH_PAYLOAD_OMITTED).validate()
        with self.assertRaisesRegex(ValueError, "initial ray geometry"):
            PrimaryMajorEventTapeBuilder(
                None,
                None,
                np.asarray([1.0], dtype=np.float64),
                np.asarray([1], dtype=np.uint64),
                1,
                include_path_payload=True,
            )

        builder = _builder(include_path_payload=True)
        with self.assertRaisesRegex(ValueError, "surface geometry"):
            builder.append_surface_events(
                depth=0,
                primary_slots=np.asarray([0], dtype=np.int64),
                face_indices=np.asarray([0], dtype=np.int64),
                incoming_power_lumen=np.asarray([0.25], dtype=np.float64),
                reflected_power_lumen=np.asarray([0.2], dtype=np.float64),
                emitted_power_lumen=np.asarray([0.0], dtype=np.float64),
                status_flags=np.asarray(
                    [STATUS_ATTEMPTED | STATUS_DISABLED],
                    dtype=np.uint16,
                ),
                lobe_codes=np.asarray([LOBE_NONE], dtype=np.int8),
                incoming_ray_kind_codes=np.asarray([RAY_KIND_DIRECT], dtype=np.int8),
            )

    def test_trusted_structural_validation_still_rejects_malformed_layout(self) -> None:
        tape, _ = _build_valid(include_path_payload=False, trusted=True)
        writable_offsets = np.array(tape.offsets, copy=True)
        with self.assertRaisesRegex(ValueError, "read-only"):
            replace(tape, offsets=writable_offsets)._validate_trusted_structure()
        with self.assertRaisesRegex(ValueError, "cover the event arrays"):
            replace(
                tape,
                offsets=_readonly_mutation(tape.offsets, -1, tape.event_count - 1),
            )._validate_trusted_structure()

    def test_trusted_skips_only_semantic_scan_and_public_strict_still_rejects(self) -> None:
        def semantically_invalid_builder() -> PrimaryMajorEventTapeBuilder:
            builder = _builder(include_path_payload=False)
            _populate_valid(builder)
            builder._segments[0].status_flags[1] = np.uint16(0)
            return builder

        with self.assertRaisesRegex(ValueError, "valid planner outcome"):
            semantically_invalid_builder().seal()

        trusted_builder = semantically_invalid_builder()
        tape = trusted_builder._seal_trusted()
        self.assertEqual(trusted_builder.validation_mode, VALIDATION_TRUSTED)
        tape._validate_trusted_structure()
        with self.assertRaisesRegex(ValueError, "valid planner outcome"):
            tape.validate()

    def test_strict_and_trusted_reject_nonowned_alias_and_empty_views(self) -> None:
        full, _ = _build_valid(include_path_payload=True)
        offsets_owner = np.array(full.offsets, copy=True)
        offsets_view = offsets_owner.view()
        offsets_view.setflags(write=False)
        self.assertFalse(offsets_view.flags.owndata)
        nonowned = replace(full, offsets=offsets_view)

        aliased = replace(full, initial_directions=full.initial_origins)
        self.assertIs(aliased.initial_directions, aliased.initial_origins)

        omitted, _ = _build_valid(include_path_payload=False)
        empty_owner = np.empty((1, 3), dtype=np.float64)
        empty_view = empty_owner[:0]
        empty_view.setflags(write=False)
        self.assertEqual(empty_view.shape, (0, 3))
        self.assertTrue(empty_view.flags.c_contiguous)
        self.assertFalse(empty_view.flags.owndata)
        nonowned_empty = replace(omitted, initial_origins=empty_view)

        for name, malformed, message in (
            ("nonowned", nonowned, "must own its storage"),
            ("field_alias", aliased, "must not share storage"),
            ("empty_view", nonowned_empty, "must own its storage"),
        ):
            with self.subTest(name=name, validation="strict"):
                with self.assertRaisesRegex(ValueError, message):
                    malformed.validate()
            with self.subTest(name=name, validation="trusted_structural"):
                with self.assertRaisesRegex(ValueError, message):
                    malformed._validate_trusted_structure()


if __name__ == "__main__":
    unittest.main()
