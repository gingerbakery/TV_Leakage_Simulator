from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.lt_validation.lighttools import read_lts


def receiver(index: int = 0, *, responsivity: str = "Photometric", total: str = "10.5",
             values: str = "1 2 3 4 5 6", interval: str = "0.5", raw: bool = True) -> str:
    raw_field = f"setSumsMesh: [2,3] {{ {values} }};" if raw else ""
    return f'''
    $ORASurfaceReceiverObj create -> $receiver_{index};
    initObject: $receiver_{index};
    $receiver_{index} {{
      setName: "Front {index}";
      setPosition: {{ 1 2 3 }};
      setOrientation: [3,3] {{ 1 0 0 0 1 0 0 0 1 }};
      setResponsivity: "{responsivity}";
      setIlluminanceUnits: "Lux";
      $ForwardSimFunction create -> $forward_{index} {{
        setEnabled: "Yes";
        setRayInfoToUse: "Incident";
        setIntegrationInterval: {interval};
        $ORAIlluminanceDataMeshObj create -> $mesh_{index} {{
          setName: "Illuminance Mesh";
          setPosition: {{ 9 9 9 }};
          setDataBounds: [3,2] {{ -2.5 2.5 -1.5 1.5 0 100 }};
          setFluxUnits: "Lumen";
          setTotalPower: {total};
          setDoNoiseReduction: "Yes";
          setBilateralKernelSizeN: 3;
          setMesh: [2,3] {{ 999 999 999 999 999 999 }};
          setCountsMesh: [2,3] {{ 100 100 100 100 100 100 }};
          {raw_field}
        }} restoreDataSet: $mesh_{index};
        $ORAIntensityDataMeshObj create -> $intensity_{index} {{
          setTotalPower: 9000;
          setSumsMesh: [1,1] {{ 18000 }};
        }} restoreDataSet: $intensity_{index};
      }} restoreFunction: $forward_{index};
    }};
    '''


def database(body: str, version: str = "8.3.2") -> str:
    return f'''#ORACAD Database File - Version 7.1
#LightTools {version}
$ORALensDatabaseManagerObj create -> $root {{
  setUnits: "Millimeters";
  {body}
}}
'''


class LightToolsSavedFileTests(unittest.TestCase):
    def read(self, text: str) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.lts"
            path.write_text(text, encoding="utf-8")
            before = path.read_bytes()
            result = read_lts(path, "uploaded.lts")
            self.assertEqual(before, path.read_bytes())
            # Output must be JSON-safe, with null for unavailable data.
            json.dumps(result, allow_nan=False)
            return result

    def test_raw_sum_interval_and_total_agree_without_using_smoothed_values(self):
        result = self.read(database(receiver()))
        item = result["receivers"][0]
        self.assertEqual(result["filename"], "uploaded.lts")
        self.assertEqual(item["total_flux_lm"], 10.5)
        self.assertEqual(item["provenance"]["raw_flux_lm_flat"], [.5, 1, 1.5, 2, 2.5, 3])
        self.assertTrue(item["provenance"]["total_power_verified"])
        self.assertTrue(item["provenance"]["display_mesh_ignored"])
        self.assertTrue(item["raw"])

    def test_unknown_serialization_order_and_coordinate_convention_stay_unknown(self):
        item = self.read(database(receiver()))["receivers"][0]
        for key in ("flux_lm", "u_edges_mm", "v_edges_mm", "center_mm", "normal", "u_axis", "v_axis"):
            self.assertIsNone(item[key])
        self.assertEqual(item["provenance"]["raw_dimensions"], [2, 3])
        self.assertEqual(item["provenance"]["receiver_position_unverified"][1], [1, 2, 3])
        self.assertFalse(item["provenance"]["raw_order_verified"])

    def test_multiple_receivers_keep_their_own_interval_and_totals(self):
        items = self.read(database(receiver() + receiver(1, total="42", interval="2")))["receivers"]
        self.assertEqual([item["name"] for item in items], ["Front 0", "Front 1"])
        self.assertEqual([item["total_flux_lm"] for item in items], [10.5, 42])
        self.assertEqual(len(items), 2)  # no intensity mesh

    def test_radiometric_receiver_is_not_lumens_despite_flux_units_setting(self):
        item = self.read(database(receiver(responsivity="Radiometric")))["receivers"][0]
        self.assertEqual(item["quantity"], "radiometric")
        self.assertIsNone(item["total_flux_lm"])
        self.assertEqual(item["provenance"]["stored_total_power"], 10.5)
        self.assertNotIn("raw_flux_lm_flat", item["provenance"])

    def test_missing_raw_does_not_substitute_display_or_ray_count_grid(self):
        item = self.read(database(receiver(raw=False)))["receivers"][0]
        self.assertIsNone(item["flux_lm"])
        self.assertIsNone(item["raw"])
        self.assertEqual(item["total_flux_lm"], 10.5)
        self.assertIn("lts_raw_mesh_missing", [issue["code"] for issue in item["issues"]])

    def test_wrong_total_is_not_fixed_by_rescaling(self):
        item = self.read(database(receiver(total="100")))["receivers"][0]
        self.assertIsNone(item["total_flux_lm"])
        self.assertEqual(item["provenance"]["raw_sum_times_interval"], 10.5)
        self.assertIn("lts_total_power_mismatch", [issue["code"] for issue in item["issues"]])
        self.assertTrue(any(issue["severity"] == "error" for issue in item["issues"]))

    def test_bad_values_and_shapes_are_explicit_data_errors(self):
        for body in (receiver(values="-1 2 3 4 5 6"), receiver(values="NaN 2 3 4 5 6"),
                     receiver(values="1 2 3"), receiver(interval="0"), receiver(interval="1e999")):
            with self.subTest(body=body):
                item = self.read(database(body))["receivers"][0]
                self.assertIsNone(item["total_flux_lm"])
                self.assertTrue(any(issue["severity"] == "error" for issue in item["issues"]))

    def test_quoted_commands_braces_and_comments_cannot_create_fake_objects(self):
        malicious_label = r'''setNotes: "$ORAIlluminanceDataMeshObj create -> $fake { setTotalPower: 999; }";
        # $ORAIlluminanceDataMeshObj create -> $fake2 { }
        '''
        result = self.read(database(malicious_label + receiver()))
        self.assertEqual(len(result["receivers"]), 1)
        self.assertEqual(result["receivers"][0]["total_flux_lm"], 10.5)

    def test_target_version_is_not_claimed_verified(self):
        result = self.read(database(receiver(), "2025.03"))
        self.assertFalse(result["provenance"]["observed_example_version"])
        self.assertIn("lts_version_unverified", [issue["code"] for issue in result["issues"]])

    def test_backward_illuminance_is_not_forward_incident_result(self):
        result = self.read(database(receiver().replace("$ForwardSimFunction", "$BackwardSimFunction")))
        self.assertEqual(result["receivers"], [])

    def test_nonincident_results_are_not_reported_as_received_flux(self):
        body = receiver().replace('setRayInfoToUse: "Incident"', 'setRayInfoToUse: "Exiting"')
        item = self.read(database(body))["receivers"][0]
        self.assertIsNone(item["total_flux_lm"])
        self.assertNotIn("raw_flux_lm_flat", item["provenance"])
        self.assertIn("lts_incident_basis_unverified", [issue["code"] for issue in item["issues"]])

    def test_missing_interval_is_not_replaced_with_smoothing_width(self):
        body = receiver().replace('setIntegrationInterval: 0.5;', '')
        item = self.read(database(body))["receivers"][0]
        self.assertEqual(item["provenance"]["smoothing_width_bins"], 3)
        self.assertIsNone(item["provenance"]["integration_interval"])
        self.assertNotIn("raw_flux_lm_flat", item["provenance"])
        self.assertIn("lts_interval_missing", [issue["code"] for issue in item["issues"]])

    def test_invalid_container_and_truncated_strings_are_rejected(self):
        for text in ("not an LTS file", database(receiver())[:-2], database('setNotes: "truncated;')):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    self.read(text)

    def test_duplicate_mesh_ids_are_not_silently_merged(self):
        with self.assertRaises(ValueError):
            self.read(database(receiver() + receiver()))


if __name__ == "__main__":
    unittest.main()
