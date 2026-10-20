import copy
import unittest

from leakage_simulator.lt_validation.comparison import compare_cases


def dataset(flux=None):
    flux = flux if flux is not None else [[1., 2.], [3., 4.]]
    return {"schema": "lt-validation.v1", "kind": "manual", "filename": "test",
            "issues": [], "sources": [{"id": "s", "name": "source", "total_flux_lm": 100.,
                "flux_reference": "emitted", "center_mm": [4.5, 1., 15.],
                "u_axis": [1., 0., 0.], "v_axis": [0., 1., 0.], "normal": [0., 0., 1.],
                "width_mm": 10., "height_mm": 10., "angular_distribution": "lambertian", "aim": None}],
            "receivers": [{"id": "r", "name": "Front", "quantity": "photometric", "raw": True,
                "flux_lm": flux, "total_flux_lm": sum(sum(row) for row in flux),
                "u_edges_mm": [-1., 0., 1.], "v_edges_mm": [-1., 0., 1.],
                "center_mm": [5.7, 9., 17.1], "u_axis": [1., 0., 0.], "v_axis": [0., 1., 0.],
                "normal": [0., 0., -1.], "acceptance_angle_deg": 30., "aperture": "none", "issues": []}]}


def case(sim=None, lt=None, case_id="before"):
    return {"id": case_id, "name": case_id, "sim": sim if sim is not None else dataset(),
            "lt": lt if lt is not None else dataset(), "sim_receiver_id": "r", "lt_receiver_id": "r"}


def compare(sim=None, lt=None):
    return compare_cases({"cases": [case(sim, lt)], "baseline_id": "before"})["cases"][0]


class ComparisonTests(unittest.TestCase):
    def test_extracted_lts_provenance_cannot_be_bypassed_by_manual_or_json_input(self):
        lt = dataset()
        receiver = lt["receivers"][0]
        receiver["provenance"] = {
            "raw_dimensions": [2, 2], "raw_flux_lm_flat": [1., 2., 3., 4.],
            "user_confirmed_extracted_order": "fast=U;U=ascending;V=ascending",
        }
        self.assertEqual(compare(lt=lt)["status"], "ready")
        receiver["flux_lm"] = [[2., 1.], [3., 4.]]
        self.assertEqual(compare(lt=lt)["status"], "blocked")
        receiver.update(flux_lm=[[1., 2., 3., 4.]], u_edges_mm=[-1., -.5, 0., .5, 1.], v_edges_mm=[-1., 1.])
        self.assertEqual(compare(lt, copy.deepcopy(lt))["status"], "blocked")
        receiver.update(flux_lm=[[1., 2.], [3., 4.]], u_edges_mm=[-1., 0., 1.], v_edges_mm=[-1., 0., 1.])
        del receiver["provenance"]["user_confirmed_extracted_order"]
        self.assertEqual(compare(lt=lt)["status"], "blocked")
        receiver["provenance"]["user_confirmed_extracted_order"] = "fast=U;U=ascending;V=ascending"
        del receiver["provenance"]["raw_flux_lm_flat"]
        receiver["flux_lm"] = [[2., 1.], [3., 4.]]
        result = compare(lt=lt)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("extracted_mesh_mismatch", [item["code"] for item in result["issues"]])

    def test_absolute_flux_is_separate_from_distribution(self):
        result = compare(dataset([[2., 4.], [6., 8.]]), dataset())
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["flux_deviation_pct"], 100.)
        self.assertAlmostEqual(result["pearson"], 1.)
        self.assertEqual(result["overlap_pct"], 100.)
        for actual, expected in zip(result["plots"]["column_sim_pct"], [40., 60.]):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(result["plots"]["row_sim_pct"], [30., 70.]):
            self.assertAlmostEqual(actual, expected)

    def test_known_overlap_difference_and_negative_correlation(self):
        result = compare(dataset([[4., 3.], [2., 1.]]), dataset())
        self.assertAlmostEqual(result["pearson"], -1.)
        self.assertAlmostEqual(result["overlap_pct"], 60.)
        self.assertAlmostEqual(result["plots"]["difference_pp"][0][0], 30.)
        self.assertAlmostEqual(sum(result["plots"]["column_difference_pp"]), 0.)
        self.assertAlmostEqual(sum(result["plots"]["row_difference_pp"]), 0.)

    def test_illuminance_uses_square_metres(self):
        result = compare()
        self.assertEqual(result["plots"]["sim_flux_lm"][0][0], 1.)
        self.assertEqual(result["plots"]["sim_illuminance_lx"][0][0], 1_000_000.)

    def test_finite_extremes_never_return_infinity(self):
        import json
        result = compare(dataset([[1e300, 1e300], [1e300, 1e300]]), dataset([[1e-300, 1e-300], [1e-300, 1e-300]]))
        self.assertIsNone(result["flux_deviation_pct"])
        json.dumps(result, allow_nan=False)

    def test_aim_distribution_must_be_consistent_and_supported(self):
        sim = dataset()
        source = sim["sources"][0]
        source["flux_reference"] = "aim_region"
        source["aim"] = {"center_mm": [4.5, 2.7, 9.5], "u_axis": [1., 0., 0.],
                         "v_axis": [0., 1., 0.], "width_mm": 10., "height_mm": 3.,
                         "distribution": "uniform_target_area"}
        self.assertEqual(compare(sim, copy.deepcopy(sim))["status"], "blocked")
        source["angular_distribution"] = "uniform_target_area"
        self.assertEqual(compare(sim, copy.deepcopy(sim))["status"], "ready")
        source["angular_distribution"] = source["aim"]["distribution"] = "uniform_solid_angle"
        self.assertEqual(compare(sim, copy.deepcopy(sim))["status"], "blocked")

    def test_coordinate_proven_axis_reversal(self):
        lt = dataset([[2., 1.], [4., 3.]])
        lt["receivers"][0]["u_axis"] = [-1., 0., 0.]
        result = compare(lt=lt)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["overlap_pct"], 100.)
        # Without coordinate evidence the same reversed numbers must not be fit.
        lt["receivers"][0]["u_axis"] = [1., 0., 0.]
        self.assertLess(compare(lt=lt)["overlap_pct"], 100.)

    def test_coordinate_proven_square_axis_swap(self):
        lt = dataset([[1., 3.], [2., 4.]])
        r = lt["receivers"][0]
        r["u_axis"], r["v_axis"] = r["v_axis"], r["u_axis"]
        self.assertEqual(compare(lt=lt)["overlap_pct"], 100.)

    def test_incompatible_grid_is_never_resampled(self):
        lt = dataset([[1., 2., 1.], [1., 2., 3.]])
        lt["receivers"][0]["u_edges_mm"] = [-1., -.5, .5, 1.]
        result = compare(lt=lt)
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["plots"])
        self.assertIsNone(result["pearson"])

    def test_shifted_edge_or_pose_or_angle_blocks(self):
        for field, value in [("u_edges_mm", [-1.1, 0., 1.]), ("center_mm", [5.7, 9., 17.2]),
                             ("normal", [0., 0., 1.]), ("acceptance_angle_deg", 90.)]:
            with self.subTest(field=field):
                lt = dataset()
                lt["receivers"][0][field] = value
                self.assertEqual(compare(lt=lt)["status"], "blocked")

    def test_smoothed_radiometric_missing_metadata_blocks(self):
        for field, value in [("raw", False), ("raw", None), ("quantity", "radiometric"),
                             ("u_axis", None), ("aperture", None), ("acceptance_angle_deg", None)]:
            with self.subTest(field=field):
                lt = dataset()
                lt["receivers"][0][field] = value
                self.assertEqual(compare(lt=lt)["status"], "blocked")

    def test_source_shift_and_power_basis_mismatch_blocks(self):
        lt = dataset()
        lt["sources"][0]["center_mm"][0] = 4.
        self.assertEqual(compare(lt=lt)["status"], "blocked")
        lt = dataset()
        lt["sources"][0]["flux_reference"] = "aim_region"
        self.assertEqual(compare(lt=lt)["status"], "blocked")

    def test_total_power_disagreement_is_not_rescaled(self):
        lt = dataset()
        lt["receivers"][0]["total_flux_lm"] = 11.
        result = compare(lt=lt)
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["flux_deviation_pct"])
        self.assertTrue(any(x["code"] == "total_mismatch" for x in result["issues"]))

    def test_invalid_values_and_ragged_arrays_block(self):
        for value in (float("nan"), float("inf"), -1., True, "1"):
            lt = dataset()
            lt["receivers"][0]["flux_lm"][0][0] = value
            self.assertEqual(compare(lt=lt)["status"], "blocked")
        lt = dataset()
        lt["receivers"][0]["flux_lm"][0].pop()
        self.assertEqual(compare(lt=lt)["status"], "blocked")

    def test_zero_and_constant_are_not_fake_perfect_correlations(self):
        zero = compare(dataset([[0., 0.], [0., 0.]]))
        self.assertEqual(zero["status"], "ready")
        self.assertEqual(zero["flux_deviation_pct"], -100.)
        self.assertIsNone(zero["pearson"])
        self.assertIsNone(zero["overlap_pct"])
        self.assertIsNone(zero["plots"])
        zero_reference = compare(lt=dataset([[0., 0.], [0., 0.]]))
        self.assertIsNone(zero_reference["flux_deviation_pct"])
        uniform = compare(dataset([[1., 1.], [1., 1.]]), dataset([[2., 2.], [2., 2.]]))
        self.assertEqual(uniform["status"], "ready")
        self.assertIsNone(uniform["pearson"])
        self.assertEqual(uniform["overlap_pct"], 100.)

    def test_arbitrary_cases_use_own_baseline_and_check_conditions(self):
        before = case()
        after = case(dataset([[.5, 1.], [1.5, 2.]]), dataset([[.25, .5], [.75, 1.]]), "custom-case")
        results = compare_cases({"cases": [before, after], "baseline_id": "before"})["cases"]
        self.assertEqual(results[1]["sim_relative_pct"], 50.)
        self.assertEqual(results[1]["lt_relative_pct"], 25.)
        self.assertEqual(results[1]["lt_change_pct"], -75.)
        after["sim"]["sources"][0]["center_mm"][0] = 4.
        results = compare_cases({"cases": [before, after], "baseline_id": "before"})["cases"]
        self.assertIsNone(results[1]["sim_relative_pct"])
        self.assertEqual(results[1]["lt_relative_pct"], 25.)

    def test_no_results_is_not_zero_and_inputs_are_not_mutated(self):
        sim = dataset()
        saved = copy.deepcopy(sim)
        compare(sim=sim)
        self.assertEqual(sim, saved)
        sim["receivers"] = []
        result = compare(sim=sim)
        self.assertIsNone(result["sim_total_lm"])
        self.assertEqual(result["status"], "blocked")

    def test_required_baseline_and_unique_case_ids(self):
        with self.assertRaises(ValueError):
            compare_cases({"cases": [case(), case()], "baseline_id": "before"})
        with self.assertRaises(ValueError):
            compare_cases({"cases": [case()], "baseline_id": "missing"})


if __name__ == "__main__":
    unittest.main()
