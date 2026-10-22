from __future__ import annotations

from decimal import Decimal, localcontext
import math
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.receiver_binning import receiver_bin_index, receiver_bin_indices
from leakage_simulator.raytracer import _build_receiver_frame, _find_first_receiver_hit, _find_first_receiver_hits_numeric
from leakage_simulator.types import RayTraceConfig, ReceiverGrid, ReceiverSpec


def bin_cases():
    cases = []
    for cells in (1, 7, 8, 80, 1000):
        for width in (0.002, 2.0, 30.0):
            for scale in (0.0, 100.0, 1e6):
                for boundary in range(1, cells):
                    position = width * (boundary / cells - 0.5)
                    for value in (position, np.nextafter(position, -np.inf), np.nextafter(position, np.inf)):
                        cases.append((value, width / 2.0, 1.0 / width, cells, scale, boundary))
                for column in range(cells):
                    position = width * ((column + 0.5) / cells - 0.5)
                    cases.append((position, width / 2.0, 1.0 / width, cells, scale, column))
                for boundary in range(1, cells):
                    for offset, expected in ((-2e-7, boundary - 1), (2e-7, boundary)):
                        position = width * ((boundary + offset) / cells - 0.5)
                        cases.append((position, width / 2.0, 1.0 / width, cells, scale, expected))
    return cases


class ReceiverBinningTests(unittest.TestCase):
    def test_internal_boundaries_and_adjacent_representable_values(self):
        for value, half, inverse, cells, scale, expected in bin_cases():
            self.assertEqual(receiver_bin_index(value, half, inverse, cells, scale), expected)

    def test_numpy_and_numba_match_scalar(self):
        import numba
        compiled = numba.njit(fastmath=False)(receiver_bin_index)
        cases = bin_cases()
        for value, half, inverse, cells, scale, expected in cases:
            self.assertEqual(compiled(value, half, inverse, cells, scale), expected)
        values = np.array([-1.0, -0.75, -1e-15, 0.0, 1e-15, 0.75, 1.0])
        scales = np.array([0.0, 1.0, 10.0, 100.0, 1e3, 1e6, 1e12])
        vectorized = receiver_bin_indices(values, 1.0, 0.5, 8, scales)
        expected = [receiver_bin_index(value, 1.0, 0.5, 8, scale) for value, scale in zip(values, scales)]
        np.testing.assert_array_equal(vectorized, expected)

    def test_points_outside_band_keep_independent_decimal_cell(self):
        with localcontext() as context:
            context.prec = 80
            for width in (2.0, 3.1, 0.002):
                for cells in (7, 8):
                    for boundary in range(1, cells):
                        for offset in (-2e-7, 2e-7, -0.25, 0.25):
                            value = width * ((boundary + offset) / cells - 0.5)
                            exact_width = Decimal.from_float(width)
                            exact = (Decimal.from_float(value) + exact_width / 2) / exact_width * cells
                            expected = int(exact)
                            for scale in (1.0, 1e6, 1e100):
                                self.assertEqual(receiver_bin_index(value, width / 2.0, 1.0 / width, cells, scale), expected)

    def test_outer_edges_and_single_cell_are_unchanged(self):
        for cells in (1, 7, 8):
            self.assertEqual(receiver_bin_index(-1.0, 1.0, 0.5, cells, 1e12), 0)
            self.assertEqual(receiver_bin_index(1.0, 1.0, 0.5, cells, 1e12), cells - 1)

    def test_rotated_translated_receivers_scalar_batch_and_grazing(self):
        for angle_deg in (0.0, 35.0, 85.0):
            angle = math.radians(angle_deg)
            normal = np.array([math.sin(angle), 0.0, math.cos(angle)])
            axis_u = np.array([math.cos(angle), 0.0, -math.sin(angle)])
            axis_v = np.array([0.0, 1.0, 0.0])
            for translation in (0.0, 1000.0):
                center = np.array([translation, -translation, translation])
                receiver = ReceiverSpec(
                    receiver_id="plane", center=tuple(center), normal=tuple(normal),
                    u_axis=tuple(axis_u), v_axis=tuple(axis_v), width_mm=2.0, height_mm=2.0,
                    resolution=(8, 8), acceptance_angle_deg=90.0,
                )
                frame = _build_receiver_frame(receiver)
                grid = ReceiverGrid("plane", (8, 8), 0.0625, [[0.0] * 8 for _ in range(8)])
                for incidence_deg in (0.0, 85.0):
                    incidence = math.radians(incidence_deg)
                    direction = -normal * math.cos(incidence) + axis_u * math.sin(incidence)
                    positions = [(column, row) for column in range(1, 8) for row in range(1, 8)]
                    origins = np.array([
                        center + (column / 4.0 - 1.0) * axis_u + (row / 4.0 - 1.0) * axis_v - direction * 10.0
                        for column, row in positions
                    ])
                    directions = np.tile(direction, (len(origins), 1))
                    config = RayTraceConfig(ray_count=len(origins))
                    batch = _find_first_receiver_hits_numeric(origins, directions, np.ones(len(origins)), [frame], config)
                    for index, (column, row) in enumerate(positions):
                        hit = _find_first_receiver_hit(tuple(origins[index]), tuple(direction), 1.0, -1, [frame], {"plane": grid}, config)
                        self.assertIsNotNone(hit)
                        self.assertEqual((hit.column, hit.row), (column, row))
                        self.assertEqual((batch.columns[index], batch.rows[index]), (column, row))
                        self.assertEqual(hit.received_power_lumen, 1.0)
                        self.assertEqual(batch.received_power_lumen[index], 1.0)

    def test_nearest_receiver_and_outer_acceptance_not_expanded(self):
        receivers = [ReceiverSpec(
            receiver_id=name, center=(0.0, 0.0, height), normal=(0.0, 0.0, 1.0),
            u_axis=(1.0, 0.0, 0.0), v_axis=(0.0, 1.0, 0.0), width_mm=2.0, height_mm=2.0,
            resolution=(8, 8),
        ) for name, height in (("far", 0.0), ("near", 1.0))]
        origins = np.array([[0.0, 0.0, 2.0], [np.nextafter(1.0, np.inf), 0.0, 2.0], [np.nextafter(-1.0, -np.inf), 0.0, 2.0]])
        result = _find_first_receiver_hits_numeric(
            origins, np.tile((0.0, 0.0, -1.0), (3, 1)), np.ones(3),
            [_build_receiver_frame(receiver) for receiver in receivers], RayTraceConfig(ray_count=3),
        )
        np.testing.assert_array_equal(result.receiver_indices, [1, -1, -1])
        self.assertEqual(result.distances_mm[0], 1.0)
        self.assertEqual((result.columns[0], result.rows[0]), (4, 4))


if __name__ == "__main__":
    unittest.main()
