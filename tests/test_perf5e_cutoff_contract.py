from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")]

from scripts.verify_perf5e_cutoff_contract import evaluate


class CutoffContractTests(unittest.TestCase):
    def test_candidate_plus_tie_epsilon_is_not_behavior_preserving(self):
        records = evaluate("cpu")
        self.assertEqual(len(records), 18)
        witness = next(record for record in records if record["order"] == [0, 1, 2])
        self.assertEqual(witness["original_face"], 2)
        self.assertEqual(witness["bounded_face"], 1)
        self.assertNotEqual(witness["original_distance_bits"], witness["bounded_distance_bits"])
        self.assertTrue(all(record["repeat_exact"] for record in records))


if __name__ == "__main__":
    unittest.main()
