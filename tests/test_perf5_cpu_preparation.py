from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]

from test_perf3c_counter_wavefront_planner import _counter_batch
from leakage_simulator import native_cpu_ordered_reducer as reducer
from leakage_simulator import raytracer
from leakage_simulator.native_cpu_wavefront import SCATTER_BSDF


def readonly(values, dtype):
    result = np.array(values, dtype=dtype, copy=True, order='C')
    result.setflags(write=False)
    return result


def terminal_batch():
    empty, _ = reducer._empty_compile_inputs()
    powers = [float.fromhex('0x1.9d0dc7ce2b7ecp+15'), 1e-160, 0.0, -0.0]
    return replace(
        empty,
        offsets=readonly([0, 0, 0, 0, 0], np.int64),
        terminal_kind_codes=readonly([1, 1, 2, 1], np.int8),
        terminal_depths=readonly([0, 0, 0, 0], np.int16),
        terminal_current_power_lumen=readonly(powers, np.float64),
        terminal_receiver_indices=readonly([0, 1, -1, 0], np.int32),
        terminal_rows=readonly([1, 2, -99, 0], np.int32),
        terminal_columns=readonly([2, 1, -99, 0], np.int32),
        terminal_received_power_lumen=readonly(powers, np.float64),
        terminal_received_power_squared_lumen2=readonly(
            [power ** 2 for power in powers], np.float64,
        ),
        receiver_columns=readonly([3, 2], np.int32),
        grid_offsets=readonly([0, 6, 12], np.int64),
    )


class CpuPreparationTests(unittest.TestCase):
    def test_receiver_bounds_use_each_receivers_own_dimensions(self):
        batch = terminal_batch()
        self.assertEqual(batch.receiver_count, 2)
        for name, slot, invalid in (
            ('terminal_rows', 0, -1),
            ('terminal_rows', 0, 2),
            ('terminal_rows', 1, -1),
            ('terminal_rows', 1, 3),
            ('terminal_columns', 0, -1),
            ('terminal_columns', 0, 3),
            ('terminal_columns', 1, -1),
            ('terminal_columns', 1, 2),
        ):
            with self.subTest(name=name, slot=slot, invalid=invalid):
                values = np.array(getattr(batch, name), copy=True)
                values[slot] = invalid
                values.setflags(write=False)
                with self.assertRaisesRegex(ValueError, 'invalid grid cell'):
                    replace(batch, **{name: values})

    def test_unknown_receiver_is_rejected_before_dimension_lookup(self):
        batch = terminal_batch()
        for invalid in (-1, 2):
            with self.subTest(invalid=invalid):
                indices = readonly([invalid, 1, -1, 0], np.int32)
                with self.assertRaisesRegex(ValueError, 'unknown receiver'):
                    replace(batch, terminal_receiver_indices=indices)

    def test_squared_power_keeps_python_bits_and_rejects_one_ulp_drift(self):
        batch = terminal_batch()
        power = float(batch.terminal_received_power_lumen[0])
        self.assertNotEqual(power ** 2, power * power)
        for slot, value in ((0, power * power), (2, -0.0)):
            with self.subTest(slot=slot):
                squared = np.array(batch.terminal_received_power_squared_lumen2, copy=True)
                squared[slot] = value
                squared.setflags(write=False)
                with self.assertRaisesRegex(ValueError, 'squared power'):
                    replace(batch, terminal_received_power_squared_lumen2=squared)

    def test_empty_and_nonreceiver_batches_do_not_divide_by_zero(self):
        with np.errstate(divide='raise', invalid='raise'):
            empty, _ = reducer._empty_compile_inputs()
            self.assertEqual(empty.primary_count, 0)
            batch = replace(
                empty,
                offsets=readonly([0, 0], np.int64),
                terminal_kind_codes=readonly([2], np.int8),
                terminal_depths=readonly([0], np.int16),
                terminal_current_power_lumen=readonly([1.0], np.float64),
                terminal_receiver_indices=readonly([-1], np.int32),
                terminal_rows=readonly([-99], np.int32),
                terminal_columns=readonly([-99], np.int32),
                terminal_received_power_lumen=readonly([0.0], np.float64),
                terminal_received_power_squared_lumen2=readonly([0.0], np.float64),
            )
            self.assertEqual(batch.receiver_count, 0)

    def test_prepared_squared_power_keeps_python_contract_and_owned_storage(self):
        batch = terminal_batch()

        class Tape(SimpleNamespace):
            def __len__(self):
                return batch.primary_count

        tape = Tape(**{
            name: getattr(batch, name)
            for name in batch.__dataclass_fields__
        })
        bindings = SimpleNamespace(**{
            name: getattr(batch, name)
            for name in ('face_profile_slots', 'profile_unassigned',
                         'receiver_columns', 'grid_offsets')
        })
        actual = raytracer._prepare_ordered_summary_batch(
            tape, SimpleNamespace(max_depth=0), bindings,
        )
        squared = actual.terminal_received_power_squared_lumen2
        np.testing.assert_array_equal(
            squared.view(np.uint64),
            batch.terminal_received_power_squared_lumen2.view(np.uint64),
        )
        self.assertTrue(squared.flags.owndata)
        self.assertFalse(squared.flags.writeable)

    def test_scatter_membership_still_accepts_all_models_and_rejects_unknowns(self):
        batch = _counter_batch(65)
        for code in range(5):
            actual = replace(batch, scatter_models=np.full(65, code, dtype=np.int8))
            self.assertTrue(np.all(actual.scatter_models == code))
        with self.assertRaisesRegex(ValueError, 'BSDF material indices'):
            replace(batch, scatter_models=np.full(65, SCATTER_BSDF, dtype=np.int8))
        for code in (-128, -1, SCATTER_BSDF + 1, 127):
            values = np.array(batch.scatter_models, copy=True)
            values[-1] = code
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, 'unknown value'):
                replace(batch, scatter_models=values)

    def test_integer_pair_counts_match_ordered_updates_with_empty_and_large_depths(self):
        generator = np.random.default_rng(4112)
        for shape, count in (((3, 5), 17003), ((1001, 7), 100003), ((0, 6), 0)):
            rows = generator.integers(shape[0], size=count, dtype=np.int16) if count else np.empty(0, dtype=np.int16)
            columns = generator.integers(shape[1], size=count, dtype=np.int16)
            for selected_columns in (columns, 1):
                expected = np.zeros(shape, dtype=np.int64)
                np.add.at(expected, (rows, selected_columns), 1)
                actual = reducer._pair_event_counts(rows, selected_columns, shape)
                np.testing.assert_array_equal(actual, expected)
                self.assertEqual(actual.dtype, expected.dtype)

    def test_disabled_paths_do_not_scan_terminal_codes(self):
        class Tape:
            path_payload = 'unused'

            def __len__(self):
                return 100000

            @property
            def terminal_kind_codes(self):
                raise AssertionError('disabled path storage scanned terminals')

        for store_paths, quota in ((False, 500), (True, 0)):
            config = SimpleNamespace(store_ray_paths=store_paths, max_stored_paths=quota)
            actual = raytracer._stage_ordered_paths_only(Tape(), None, config, [], {}, [], [])
            self.assertEqual(actual.stored_paths, [])
            self.assertEqual(actual.materialized_count, 0)
            self.assertEqual(actual.skipped_count, 0)


if __name__ == '__main__':
    unittest.main()
