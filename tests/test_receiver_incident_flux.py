from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.raytracer import (
    RECEIVER_FLUX_CONTRACT,
    _build_receiver_frame,
    _find_first_receiver_hit,
    _find_first_receiver_hits_numeric,
)
from leakage_simulator.types import RayTraceConfig, ReceiverGrid, ReceiverSpec


def angled_receiver(angle_deg: float, acceptance_angle_deg: float = 90.0) -> ReceiverSpec:
    angle = math.radians(angle_deg)
    distance_to_plane = 10.0 / math.cos(angle)
    return ReceiverSpec(
        receiver_id="observer",
        center=(
            math.sin(angle) * distance_to_plane,
            0.0,
            10.0,
        ),
        normal=(0.0, 0.0, -1.0),
        width_mm=4.0,
        height_mm=4.0,
        resolution=(2, 2),
        acceptance_angle_deg=acceptance_angle_deg,
    )


class ReceiverIncidentFluxTests(unittest.TestCase):
    def test_scalar_receiver_preserves_flux_packet_at_off_axis_angles(self) -> None:
        power_lumen = 2.0
        for angle_deg in (0.0, 45.0, 80.0):
            with self.subTest(angle_deg=angle_deg):
                angle = math.radians(angle_deg)
                receiver = angled_receiver(angle_deg)
                frame = _build_receiver_frame(receiver)
                grid = ReceiverGrid.empty(receiver)
                candidate = _find_first_receiver_hit(
                    origin=(0.0, 0.0, 0.0),
                    direction=(math.sin(angle), 0.0, math.cos(angle)),
                    power_lumen=power_lumen,
                    source_face=-1,
                    receivers=[frame],
                    grids={receiver.receiver_id: grid},
                    config=RayTraceConfig(),
                )

                self.assertIsNotNone(candidate)
                self.assertAlmostEqual(candidate.received_power_lumen, power_lumen)
                self.assertAlmostEqual(candidate.incoming_power_lumen, power_lumen)

    def test_numeric_receiver_preserves_flux_packet_at_off_axis_angle(self) -> None:
        angle = math.radians(45.0)
        receiver = angled_receiver(45.0)
        result = _find_first_receiver_hits_numeric(
            origins=np.asarray([(0.0, 0.0, 0.0)], dtype=np.float64),
            directions=np.asarray(
                [(math.sin(angle), 0.0, math.cos(angle))],
                dtype=np.float64,
            ),
            powers_lumen=np.asarray([2.0], dtype=np.float64),
            receivers=[_build_receiver_frame(receiver)],
            config=RayTraceConfig(),
        )

        self.assertEqual(int(result.receiver_indices[0]), 0)
        self.assertAlmostEqual(float(result.received_power_lumen[0]), 2.0)

    def test_acceptance_angle_remains_a_hard_gate(self) -> None:
        angle = math.radians(45.0)
        receiver = angled_receiver(45.0, acceptance_angle_deg=30.0)
        frame = _build_receiver_frame(receiver)
        candidate = _find_first_receiver_hit(
            origin=(0.0, 0.0, 0.0),
            direction=(math.sin(angle), 0.0, math.cos(angle)),
            power_lumen=2.0,
            source_face=-1,
            receivers=[frame],
            grids={receiver.receiver_id: ReceiverGrid.empty(receiver)},
            config=RayTraceConfig(),
        )

        self.assertIsNone(candidate)

    def test_result_contract_names_geometric_incident_flux(self) -> None:
        self.assertEqual(RECEIVER_FLUX_CONTRACT, "geometric_incident_flux_v2")


if __name__ == "__main__":
    unittest.main()
