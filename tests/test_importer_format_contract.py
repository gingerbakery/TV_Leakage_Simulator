from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.importers import import_geometry


class ImporterFormatContractTests(unittest.TestCase):
    def test_xt_never_falls_back_to_synthetic_test_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "assembly.x_t"
            path.write_bytes(b"PARASOLID-DATA")

            with self.assertRaisesRegex(ValueError, "STEP AP214"):
                import_geometry(str(path))


if __name__ == "__main__":
    unittest.main()
