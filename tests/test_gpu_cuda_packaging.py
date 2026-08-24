from __future__ import annotations

import json
from importlib import util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "scripts" / "verify_gpu_cuda_runtime.py"
GPU_REQUIREMENTS = ROOT / "requirements-gpu-cuda.txt"
GPU_USER_GUIDE = ROOT / "docs" / "gpu-cuda-user-guide.md"
WINDOWS_GPU_SETUP = ROOT / "docs" / "WINDOWS_GPU_SETUP.md"


def _load_cuda_verifier():
    spec = util.spec_from_file_location(
        "gpu_cuda_runtime_verifier_contract",
        SMOKE_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load GPU CUDA verifier")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_device_verifier_uses_production_bvh_preflight_contract(self) -> None:
        source = SMOKE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("preflight_gpu_cuda(refresh=True)", source)
        self.assertIn("production Ray/BVH CUDA path", source)
        self.assertNotIn("def add_one", source)

        source_root = str(ROOT / "src")
        if source_root not in sys.path:
            sys.path.insert(0, source_root)
        from leakage_simulator import gpu_cuda_intersection as gpu_cuda

        verifier = _load_cuda_verifier()
        preflight = gpu_cuda.GpuCudaPreflight(
            available=True,
            reason_code=None,
            numba_version="0.66.0",
            device_name="Contract GPU",
            compute_capability="9.1",
            device_id=2,
            strict_float64=True,
            toolkit_layout="test-layout",
            kernel_executed=True,
            kernel_verified=True,
        )
        import_payload = {
            "status": "ok",
            "mode": "imports",
            "python": "3.13.test",
            "architecture": "AMD64",
            "numpy": "2.4.6",
            "numba": "0.66.0",
            "llvmlite": "0.48.0",
            "llvmlite_native": True,
            "cuda_device_executed": False,
        }
        with (
            patch.object(
                verifier,
                "verify_imports",
                return_value=import_payload,
            ),
            patch.object(
                gpu_cuda,
                "preflight_gpu_cuda",
                return_value=preflight,
            ) as production_preflight,
        ):
            result = verifier.verify_device()

        production_preflight.assert_called_once_with(refresh=True)
        self.assertEqual(result["mode"], "device")
        self.assertTrue(result["cuda_device_executed"])
        self.assertTrue(result["kernel_verified"])
        self.assertEqual(result["preflight_scope"], "production_ray_bvh")
        self.assertEqual(result["provider_contract"], "strict_float64_bvh_v1")
        self.assertEqual(result["kernel_result_sha"], 33153)
        self.assertIn(
            "Real Ray/BVH CUDA kernel: PASS",
            verifier.format_human_success(result),
        )

    def test_device_verifier_preserves_production_failure_reason(self) -> None:
        source_root = str(ROOT / "src")
        if source_root not in sys.path:
            sys.path.insert(0, source_root)
        from leakage_simulator import gpu_cuda_intersection as gpu_cuda

        verifier = _load_cuda_verifier()
        preflight = gpu_cuda.GpuCudaPreflight(
            available=False,
            reason_code="gpu_cuda_scene_upload_failed",
            numba_version="0.66.0",
            device_name="Contract GPU",
            compute_capability="9.1",
            device_id=2,
            strict_float64=False,
            toolkit_layout="test-layout",
            kernel_executed=False,
            kernel_verified=False,
        )
        with (
            patch.object(verifier, "verify_imports", return_value={}),
            patch.object(
                gpu_cuda,
                "preflight_gpu_cuda",
                return_value=preflight,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "gpu_cuda_scene_upload_failed",
            ):
                verifier.verify_device()

    def test_default_build_edition_remains_lite(self) -> None:
        script = (ROOT / "build_lightweight_desktop.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('[string]$Edition = "lite"', script)
        self.assertIn('$IsGpuCudaEdition = $Edition -eq "gpu_cuda"', script)
        self.assertIn('if ($IsGpuCudaEdition) {', script)
        self.assertIn("Ray/BVH kernel PASS", script)
        self.assertIn("Compute row", script)
        self.assertIn("BVH/Rebuilt", script)
        self.assertIn("git pull does not update this extracted EXE", script)

    def test_user_gpu_check_uses_the_bundled_runtime(self) -> None:
        script = (ROOT / "CHECK_GPU_CUDA.bat").read_text(encoding="utf-8")
        self.assertIn("_tools\\python313\\python.exe", script)
        self.assertIn("verify_gpu_cuda_runtime.py", script)
        self.assertIn("--mode device", script)
        self.assertIn("--human", script)
        self.assertIn("run_web_gpu.bat", script)
        self.assertIn("Do not copy only the EXE", script)
        self.assertIn("working on THIS PC", script)

    def test_gpu_user_guide_is_bundled_with_both_editions(self) -> None:
        guide = GPU_USER_GUIDE.read_text(encoding="utf-8")
        self.assertIn("CHECK_GPU_CUDA.bat", guide)
        self.assertIn("run_web_gpu.bat", guide)
        self.assertIn("CUDA Toolkit `13.1`", guide)
        self.assertIn("NVIDIA CUDA GPU", guide)
        self.assertIn("git pull", guide)
        self.assertIn("Compute", guide)
        self.assertIn("CPU와 GPU를 직접 비교한 수치가 아니다", guide)

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

        setup_guide = WINDOWS_GPU_SETUP.read_text(encoding="utf-8")
        self.assertIn("NVIDIA RTX A4000", setup_guide)
        setup_copy = script.index(
            'docs\\WINDOWS_GPU_SETUP.md") -Destination '
            '(Join-Path $OutputDir "docs")',
        )
        gpu_block_start = script.index("if ($IsGpuCudaEdition) {", setup_copy)
        self.assertLess(setup_copy, gpu_block_start)
        self.assertIn(
            '"$OutputName/docs/WINDOWS_GPU_SETUP.md"',
            script,
        )

    def test_ai_gpu_guidance_is_bundled_with_both_editions(self) -> None:
        script = (ROOT / "build_lightweight_desktop.ps1").read_text(
            encoding="utf-8"
        )
        copy_tokens = (
            'Join-Path $Root "AGENTS.md") -Destination $OutputDir',
            'Join-Path $Root "CLAUDE.md") -Destination $OutputDir',
            'Join-Path $Root "GEMINI.md") -Destination $OutputDir',
            'Join-Path $Root ".github\\copilot-instructions.md")',
            'Join-Path $Root "docs\\ai-gpu-execution-runbook.md")',
            'Join-Path $Root "docs\\WINDOWS_GPU_SETUP.md")',
        )
        for token in copy_tokens:
            with self.subTest(copy_token=token):
                copy_index = script.index(token)
                gpu_block_start = script.index(
                    "if ($IsGpuCudaEdition) {",
                    copy_index,
                )
                self.assertLess(copy_index, gpu_block_start)

        required_entries = (
            '"$OutputName/AGENTS.md"',
            '"$OutputName/CLAUDE.md"',
            '"$OutputName/GEMINI.md"',
            '"$OutputName/.github/copilot-instructions.md"',
            '"$OutputName/docs/ai-gpu-execution-runbook.md"',
        )
        for entry in required_entries:
            with self.subTest(required_entry=entry):
                self.assertIn(entry, script)

        self.assertIn(
            "make it read AGENTS.md, docs/WINDOWS_GPU_SETUP.md and docs/ai-gpu-execution-runbook.md",
            script,
        )
        self.assertIn(
            "without access to this folder cannot read those instructions automatically",
            script,
        )

    def test_windows_setup_helper_is_bundled_with_gpu_edition(self) -> None:
        script = (ROOT / "build_lightweight_desktop.ps1").read_text(
            encoding="utf-8"
        )
        gpu_block_start = script.index("if ($IsGpuCudaEdition) {", script.index("[4/9]"))
        for filename in ("setup_windows_gpu.bat", "setup_windows_gpu.ps1"):
            with self.subTest(filename=filename):
                copy_index = script.index(
                    f'Join-Path $Root "{filename}") -Destination $OutputDir',
                    gpu_block_start,
                )
                self.assertGreater(copy_index, gpu_block_start)
                self.assertIn(f'"$OutputName/{filename}"', script)

        self.assertIn(
            "run setup_windows_gpu.bat in its default read-only mode",
            script,
        )


if __name__ == "__main__":
    unittest.main()
