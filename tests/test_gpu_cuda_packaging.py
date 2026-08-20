from __future__ import annotations

import json
from importlib import util
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "scripts" / "verify_gpu_cuda_runtime.py"
GPU_REQUIREMENTS = ROOT / "requirements-gpu-cuda.txt"
GPU_USER_GUIDE = ROOT / "docs" / "gpu-cuda-user-guide.md"


class GpuCudaPackagingTests(unittest.TestCase):
    def test_optional_requirements_are_version_pinned(self) -> None:
        requirements = {
            line.strip()
            for line in GPU_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertEqual(
            requirements,
            {"numba==0.66.0", "llvmlite==0.48.0"},
        )

    def test_import_smoke_does_not_require_a_cuda_device(self) -> None:
        if util.find_spec("numba") is None or util.find_spec("llvmlite") is None:
            self.skipTest("optional GPU packaging dependencies are not installed")
        process = subprocess.run(
            [sys.executable, str(SMOKE_SCRIPT), "--mode", "imports"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["mode"], "imports")
        self.assertEqual(payload["numba"], "0.66.0")
        self.assertEqual(payload["llvmlite"], "0.48.0")
        self.assertTrue(payload["llvmlite_native"])
        self.assertFalse(payload["cuda_device_executed"])

    def test_default_build_edition_remains_lite(self) -> None:
        script = (ROOT / "build_lightweight_desktop.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('[string]$Edition = "lite"', script)
        self.assertIn('$IsGpuCudaEdition = $Edition -eq "gpu_cuda"', script)
        self.assertIn('if ($IsGpuCudaEdition) {', script)

    def test_user_gpu_check_uses_the_bundled_runtime(self) -> None:
        script = (ROOT / "CHECK_GPU_CUDA.bat").read_text(encoding="utf-8")
        self.assertIn("_tools\\python313\\python.exe", script)
        self.assertIn("verify_gpu_cuda_runtime.py", script)
        self.assertIn("--mode device", script)

    def test_gpu_user_guide_is_bundled_with_both_editions(self) -> None:
        guide = GPU_USER_GUIDE.read_text(encoding="utf-8")
        self.assertIn("CHECK_GPU_CUDA.bat", guide)
        self.assertIn("CUDA Toolkit `13.1`", guide)
        self.assertIn("NVIDIA CUDA GPU", guide)

        script = (ROOT / "build_lightweight_desktop.ps1").read_text(
            encoding="utf-8"
        )
        guide_copy = script.index(
            'docs\\gpu-cuda-user-guide.md") -Destination '
            '(Join-Path $OutputDir "docs")',
        )
        gpu_block_start = script.index("if ($IsGpuCudaEdition) {", guide_copy)
        self.assertLess(guide_copy, gpu_block_start)
        self.assertIn(
            '"$OutputName/docs/gpu-cuda-user-guide.md"',
            script,
        )


if __name__ == "__main__":
    unittest.main()
