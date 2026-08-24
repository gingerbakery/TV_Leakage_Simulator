from __future__ import annotations

import sys
import unittest
from dataclasses import fields
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.wavefront_event_tape import (
    EVENT_TAPE_CONTRACT,
    LOBE_LAMBERTIAN,
    LOBE_NONE,
    LOBE_SPECULAR,
    RAY_KIND_DIRECT,
    RAY_KIND_LAMBERTIAN,
    RAY_KIND_SPECULAR,
    STATE_LAYOUT,
    STATUS_ATTEMPTED,
    STATUS_DISABLED,
    STATUS_EMITTED,
    STATUS_ROULETTE_SURVIVED,
    PrimaryMajorEventTape,
    PrimaryMajorEventTapeBuilder,
    StableActiveRaySoA,
    TERMINAL_BLOCKED,
    TERMINAL_ESCAPED,
    TERMINAL_RECEIVER,
)


def _append_segment(
    builder: PrimaryMajorEventTapeBuilder,
    *,
    depth: int,
    slots,
    faces,
    emitted,
    lobes,
    incoming_powers=None,
    reflected_powers=None,
    emitted_powers=None,
) -> None:
    slots = np.asarray(slots, dtype=np.int64)
    row_count = len(slots)
    face_values = np.asarray(faces, dtype=np.int64)
    base = np.asarray(
        [[float(face), float(depth), float(slot)] for face, slot in zip(faces, slots)],
        dtype=np.float64,
    ).reshape(row_count, 3)
    emitted_values = np.asarray(emitted, dtype=np.bool_)
    incoming_power_values = np.asarray(
        faces if incoming_powers is None else incoming_powers,
        dtype=np.float64,
    )
    reflected_power_values = np.asarray(
        incoming_power_values
        if reflected_powers is None
        else reflected_powers,
        dtype=np.float64,
    )
    emitted_power_values = np.asarray(
        reflected_power_values
        if emitted_powers is None
        else emitted_powers,
        dtype=np.float64,
    )
    emitted_power_values = np.where(
        emitted_values,
        emitted_power_values,
        0.0,
    )
    status = np.full(row_count, STATUS_ATTEMPTED, dtype=np.uint16)
    status[emitted_values] |= STATUS_EMITTED
    status[~emitted_values] |= STATUS_DISABLED
    builder.append_surface_events(
        depth=depth,
        primary_slots=slots,
        face_indices=face_values,
        points=base,
        normals=base + 0.125,
        distances_mm=np.asarray(faces, dtype=np.float64) + 0.25,
        incoming_power_lumen=incoming_power_values,
        reflected_power_lumen=reflected_power_values,
        emitted_power_lumen=emitted_power_values,
        status_flags=status,
        lobe_codes=np.asarray(lobes, dtype=np.int8),
        incoming_ray_kind_codes=np.full(
            row_count,
            RAY_KIND_DIRECT if depth == 0 else RAY_KIND_SPECULAR,
            dtype=np.int8,
        ),
    )


def _builder(primary_count: int = 3, max_depth: int = 2):
    origins = np.arange(primary_count * 3, dtype=np.float64).reshape(
        primary_count,
        3,
    )
    directions = origins + 0.125
    powers = np.arange(primary_count, dtype=np.float64) + 0.25
    seeds = np.arange(primary_count, dtype=np.uint64) + np.uint64(101)
    return PrimaryMajorEventTapeBuilder(
        origins,
        directions,
        powers,
        seeds,
        max_depth,
    ), (origins, directions, powers, seeds)


class Perf3B2CEventTapeContractTests(unittest.TestCase):
    def test_stable_active_soa_owns_inputs_and_compacts_in_stable_order(self) -> None:
        origins = np.asarray(
            [(0.0, 1.0, 2.0), (3.0, 4.0, 5.0), (6.0, 7.0, 8.0)],
            dtype=np.float64,
        )
        directions = origins + 0.5
        seeds = np.asarray([11, 12, 13], dtype=np.uint64)
        state = StableActiveRaySoA.initialize(
            origins,
            directions,
            ray_power_lumen=0.25,
            primary_start_index=41,
            reflection_seeds=seeds,
        )

        origins[:] = -1.0
        directions[:] = -2.0
        seeds[:] = 99
        self.assertEqual(STATE_LAYOUT, "stable_active_soa_v1")
        self.assertEqual(state.primary_slots.tolist(), [0, 1, 2])
        self.assertEqual(state.primary_indices.tolist(), [41, 42, 43])
        self.assertEqual(state.reflection_seeds.tolist(), [11, 12, 13])
        self.assertEqual(state.powers_lumen.tolist(), [0.25, 0.25, 0.25])
        self.assertEqual(state.source_faces.tolist(), [-1, -1, -1])
        self.assertEqual(state.ray_kind_codes.tolist(), [RAY_KIND_DIRECT] * 3)
        self.assertTrue(state.origins.flags.c_contiguous)
        self.assertFalse(np.shares_memory(state.origins, origins))
        self.assertFalse(np.shares_memory(state.directions, directions))
        self.assertFalse(np.shares_memory(state.reflection_seeds, seeds))

        continuation_origins = np.asarray(
            [(10.0, 11.0, 12.0), (20.0, 21.0, 22.0)],
            dtype=np.float64,
        )
        continuation_directions = continuation_origins + 0.5
        compacted = state.compact_continuations(
            np.asarray([0, 2], dtype=np.int64),
            continuation_origins,
            continuation_directions,
            np.asarray([0.2, 0.1], dtype=np.float64),
            np.asarray([7, 8], dtype=np.int64),
            np.asarray([RAY_KIND_SPECULAR, RAY_KIND_SPECULAR], dtype=np.int8),
        )
        continuation_origins[:] = -10.0
        self.assertEqual(compacted.primary_slots.tolist(), [0, 2])
        self.assertEqual(compacted.primary_indices.tolist(), [41, 43])
        self.assertEqual(compacted.reflection_seeds.tolist(), [11, 13])
        self.assertEqual(compacted.source_faces.tolist(), [7, 8])
        np.testing.assert_array_equal(
            compacted.origins,
            np.asarray([(10.0, 11.0, 12.0), (20.0, 21.0, 22.0)]),
        )
        self.assertFalse(np.shares_memory(compacted.origins, continuation_origins))
        self.assertGreater(compacted.nbytes, 0)

        face_state = StableActiveRaySoA.initialize(
            np.zeros((3, 3), dtype=np.float64),
            np.asarray([(0.0, 0.0, 1.0)] * 3, dtype=np.float64),
            ray_power_lumen=0.25,
            primary_start_index=0,
            reflection_seeds=np.asarray([21, 22, 23], dtype=np.uint64),
            source_faces=np.asarray([4, -1, 9], dtype=np.int64),
        )
        self.assertEqual(face_state.source_faces.tolist(), [4, -1, 9])

        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            state.compact_continuations(
                np.asarray([1, 1]),
                np.zeros((2, 3)),
                np.zeros((2, 3)),
                np.zeros(2),
                np.zeros(2),
                np.zeros(2),
            )
        with self.assertRaisesRegex(ValueError, "one row per row_index"):
            state.compact_continuations(
                np.asarray([0, 2]),
                np.zeros((1, 3)),
                np.zeros((2, 3)),
                np.zeros(2),
                np.zeros(2),
                np.zeros(2),
            )

    def test_seal_transposes_actual_events_to_readonly_primary_major_csr(self) -> None:
        builder, source_arrays = _builder()
        source_snapshots = tuple(array.copy() for array in source_arrays)

        faces_depth_zero = np.asarray([10, 20], dtype=np.int64)
        _append_segment(
            builder,
            depth=0,
            slots=[1, 2],
            faces=faces_depth_zero,
            emitted=[False, True],
            lobes=[LOBE_NONE, LOBE_SPECULAR],
            incoming_powers=[1.25, 2.25],
            reflected_powers=[1.0, 2.0],
            emitted_powers=[0.0, 1.5],
        )
        faces_depth_zero[:] = -1
        _append_segment(
            builder,
            depth=1,
            slots=[2],
            faces=[21],
            emitted=[True],
            lobes=[LOBE_LAMBERTIAN],
            incoming_powers=[1.5],
            reflected_powers=[1.25],
            emitted_powers=[1.0],
        )
        builder.set_nonreceiver_terminals(
            primary_slots=np.asarray([0]),
            terminal_kind=TERMINAL_ESCAPED,
            depth=0,
            current_power_lumen=np.asarray([0.25]),
            ray_kind_codes=np.asarray([RAY_KIND_DIRECT]),
        )
        builder.set_nonreceiver_terminals(
            primary_slots=np.asarray([1]),
            terminal_kind=TERMINAL_BLOCKED,
            depth=0,
            current_power_lumen=np.asarray([1.25]),
            ray_kind_codes=np.asarray([RAY_KIND_DIRECT]),
        )
        builder.set_receiver_terminals(
            primary_slots=np.asarray([2]),
            depth=2,
            current_power_lumen=np.asarray([1.0]),
            ray_kind_codes=np.asarray([RAY_KIND_LAMBERTIAN]),
            receiver_indices=np.asarray([4]),
            rows=np.asarray([5]),
            columns=np.asarray([6]),
            received_power_lumen=np.asarray([0.75]),
            points=np.asarray([(1.0, 2.0, 3.0)]),
            normals=np.asarray([(0.0, 0.0, -1.0)]),
            distances_mm=np.asarray([9.5]),
            incoming_power_lumen=np.asarray([1.0]),
        )

        for source in source_arrays:
            source[:] = 999

        self.assertEqual(builder.event_count, 3)
        tape = builder.seal()
        self.assertIsInstance(tape, PrimaryMajorEventTape)
        self.assertEqual(tape.contract, EVENT_TAPE_CONTRACT)
        self.assertEqual(tape.primary_count, 3)
        self.assertEqual(tape.event_count, 3)
        self.assertEqual(tape.offsets.tolist(), [0, 0, 1, 3])
        self.assertEqual(tape.face_indices.tolist(), [10, 20, 21])
        self.assertEqual(tape.initial_source_faces.tolist(), [-1, -1, -1])
        self.assertEqual(tape.primary_event_bounds(0), (0, 0))
        self.assertEqual(tape.primary_event_bounds(1), (0, 1))
        self.assertEqual(tape.primary_event_bounds(2), (1, 3))
        self.assertEqual(
            tape.terminal_kind_codes.tolist(),
            [TERMINAL_ESCAPED, TERMINAL_BLOCKED, TERMINAL_RECEIVER],
        )
        self.assertEqual(tape.terminal_depths.tolist(), [0, 0, 2])
        self.assertEqual(tape.terminal_receiver_indices.tolist(), [-1, -1, 4])
        for stored, expected in zip(
            (
                tape.initial_origins,
                tape.initial_directions,
                tape.initial_power_lumen,
                tape.reflection_seeds,
            ),
            source_snapshots,
        ):
            np.testing.assert_array_equal(stored, expected)
        for field in fields(tape):
            value = getattr(tape, field.name)
            if isinstance(value, np.ndarray):
                self.assertFalse(value.flags.writeable, field.name)
        with self.assertRaises(ValueError):
            tape.face_indices[0] = 999
        with self.assertRaises(IndexError):
            tape.primary_event_bounds(3)
        with self.assertRaises(RuntimeError):
            builder.seal()
        self.assertGreater(tape.nbytes, 0)
        self.assertGreater(tape.peak_bytes, tape.nbytes)

    def test_empty_tape_is_valid_and_uses_no_event_storage(self) -> None:
        builder, _ = _builder(primary_count=0, max_depth=20)
        tape = builder.seal()
        self.assertEqual(tape.offsets.tolist(), [0])
        self.assertEqual(tape.event_count, 0)
        self.assertEqual(tape.primary_count, 0)
        tape.validate()

    def test_roulette_survival_can_end_in_disabled_scatter(self) -> None:
        builder, _ = _builder(primary_count=1)
        builder.append_surface_events(
            depth=0,
            primary_slots=np.asarray([0], dtype=np.int64),
            face_indices=np.asarray([7], dtype=np.int64),
            points=np.asarray([(1.0, 2.0, 3.0)]),
            normals=np.asarray([(0.0, 0.0, 1.0)]),
            distances_mm=np.asarray([4.0]),
            incoming_power_lumen=np.asarray([0.25]),
            reflected_power_lumen=np.asarray([0.2]),
            emitted_power_lumen=np.asarray([0.0]),
            status_flags=np.asarray(
                [STATUS_ATTEMPTED | STATUS_ROULETTE_SURVIVED | STATUS_DISABLED],
                dtype=np.uint16,
            ),
            lobe_codes=np.asarray([LOBE_NONE], dtype=np.int8),
            incoming_ray_kind_codes=np.asarray([RAY_KIND_DIRECT], dtype=np.int8),
        )
        builder.set_nonreceiver_terminals(
            primary_slots=np.asarray([0], dtype=np.int64),
            terminal_kind=TERMINAL_BLOCKED,
            depth=0,
            current_power_lumen=np.asarray([0.25]),
            ray_kind_codes=np.asarray([RAY_KIND_DIRECT], dtype=np.int8),
        )

        tape = builder.seal()

        self.assertEqual(
            int(tape.status_flags[0]),
            STATUS_ATTEMPTED | STATUS_ROULETTE_SURVIVED | STATUS_DISABLED,
        )
        self.assertEqual(tape.terminal_kind_codes.tolist(), [TERMINAL_BLOCKED])

    def test_builder_rejects_order_terminal_and_validation_violations(self) -> None:
        builder, _ = _builder()
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            _append_segment(
                builder,
                depth=0,
                slots=[1, 1],
                faces=[10, 11],
                emitted=[True, True],
                lobes=[LOBE_SPECULAR, LOBE_SPECULAR],
            )

        builder, _ = _builder(primary_count=1)
        builder.set_nonreceiver_terminals(
            primary_slots=np.asarray([0]),
            terminal_kind=TERMINAL_ESCAPED,
            depth=0,
            current_power_lumen=np.asarray([1.0]),
            ray_kind_codes=np.asarray([RAY_KIND_DIRECT]),
        )
        with self.assertRaisesRegex(ValueError, "multiple terminals"):
            builder.set_nonreceiver_terminals(
                primary_slots=np.asarray([0]),
                terminal_kind=TERMINAL_BLOCKED,
                depth=0,
                current_power_lumen=np.asarray([1.0]),
                ray_kind_codes=np.asarray([RAY_KIND_DIRECT]),
            )

        missing_terminal, _ = _builder(primary_count=1)
        with self.assertRaisesRegex(ValueError, "terminal kind"):
            missing_terminal.seal()

        missing_depth, _ = _builder(primary_count=1)
        _append_segment(
            missing_depth,
            depth=1,
            slots=[0],
            faces=[10],
            emitted=[True],
            lobes=[LOBE_SPECULAR],
        )
        missing_depth.set_nonreceiver_terminals(
            primary_slots=np.asarray([0]),
            terminal_kind=TERMINAL_ESCAPED,
            depth=1,
            current_power_lumen=np.asarray([1.0]),
            ray_kind_codes=np.asarray([RAY_KIND_SPECULAR]),
        )
        with self.assertRaisesRegex(ValueError, "depth-contiguous"):
            missing_depth.seal()

        invalid_lobe, _ = _builder(primary_count=1)
        _append_segment(
            invalid_lobe,
            depth=0,
            slots=[0],
            faces=[10],
            emitted=[True],
            lobes=[LOBE_NONE],
            incoming_powers=[0.25],
            reflected_powers=[0.2],
            emitted_powers=[0.2],
        )
        invalid_lobe.set_nonreceiver_terminals(
            primary_slots=np.asarray([0]),
            terminal_kind=TERMINAL_ESCAPED,
            depth=1,
            current_power_lumen=np.asarray([0.2]),
            ray_kind_codes=np.asarray([RAY_KIND_SPECULAR]),
        )
        with self.assertRaisesRegex(ValueError, "known lobe"):
            invalid_lobe.seal()


if __name__ == "__main__":
    unittest.main()
