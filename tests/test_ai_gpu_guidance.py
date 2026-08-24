from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
AGENT_GUIDE = ROOT / "AGENTS.md"
AI_GPU_RUNBOOK = ROOT / "docs" / "ai-gpu-execution-runbook.md"
GPU_USER_GUIDE = ROOT / "docs" / "gpu-cuda-user-guide.md"
WINDOWS_GPU_SETUP = ROOT / "docs" / "WINDOWS_GPU_SETUP.md"
AI_ENTRYPOINTS = (
    ROOT / "CLAUDE.md",
    ROOT / "GEMINI.md",
    ROOT / ".github" / "copilot-instructions.md",
)


def read_lower(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


class AiGpuGuidanceTests(unittest.TestCase):
    def assert_has_pattern(
        self,
        text: str,
        pattern: str,
        message: str,
    ) -> None:
        self.assertRegex(text, re.compile(pattern, re.IGNORECASE | re.DOTALL), message)

    def test_agent_entrypoints_route_to_the_canonical_gpu_guides(self) -> None:
        expected_files = (
            AGENT_GUIDE,
            AI_GPU_RUNBOOK,
            GPU_USER_GUIDE,
            WINDOWS_GPU_SETUP,
            *AI_ENTRYPOINTS,
        )
        for path in expected_files:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertTrue(path.is_file(), f"missing AI guidance file: {path}")

        agent_guide = read_lower(AGENT_GUIDE)
        self.assertIn("docs/ai-gpu-execution-runbook.md", agent_guide)
        self.assertIn("docs/gpu-cuda-user-guide.md", agent_guide)
        self.assertIn("docs/windows_gpu_setup.md", agent_guide)

        for path in AI_ENTRYPOINTS:
            entrypoint = read_lower(path)
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIn("agents.md", entrypoint)
                self.assertIn("docs/ai-gpu-execution-runbook.md", entrypoint)
                self.assertIn("docs/windows_gpu_setup.md", entrypoint)

    def test_canonical_guidance_preserves_the_fail_closed_runtime_contract(self) -> None:
        guidance = "\n".join(
            (read_lower(AGENT_GUIDE), read_lower(AI_GPU_RUNBOOK))
        )
        exact_contract_tokens = (
            "production_ray_bvh",
            "strict_float64_bvh_v1",
            "compute_execution_state",
            "gpu_cuda_gpu_success_count",
        )
        for token in exact_contract_tokens:
            with self.subTest(token=token):
                self.assertIn(token, guidance)

        self.assert_has_pattern(
            guidance,
            r"gpu_cuda.{0,100}brute_force.{0,100}"
            r"(?:invalid|unsupported|reject|must not|금지|유효하지|지원하지)",
            "the guide must say that GPU CUDA plus brute force is invalid",
        )
        self.assert_has_pattern(
            guidance,
            r"bvh.{0,80}rebuilt.{0,160}"
            r"(?:not|isn['’]t|doesn['’]t|아니|없|보장하지).{0,100}"
            r"(?:proof|evidence|증거|입증|판정|보장)",
            "BVH/Rebuilt alone must not be described as proof of GPU execution",
        )
        self.assert_has_pattern(
            guidance,
            r"success.{0,100}(?:greater than zero|> 0).{0,220}"
            r"(?:mixed|partial fallback)",
            "partial CUDA success plus CPU fallback must be reported as mixed",
        )

    def test_guidance_covers_source_zip_and_external_prerequisites(self) -> None:
        runbook = read_lower(AI_GPU_RUNBOOK)
        self.assertIn("run_web_gpu.bat", runbook)
        self.assertIn("run_web_gpu.ps1", runbook)
        self.assert_has_pattern(
            runbook,
            r"(?:source|소스).{0,300}run_web_gpu",
            "the source-checkout workflow must route through run_web_gpu",
        )
        self.assert_has_pattern(
            runbook,
            r"(?:zip|압축).{0,300}check_gpu_cuda\.bat",
            "the extracted-ZIP workflow must route through CHECK_GPU_CUDA.bat",
        )

        prerequisite_patterns = {
            "NVIDIA CUDA-capable GPU": r"nvidia.{0,80}(?:cuda.{0,40})?gpu",
            "NVIDIA driver": r"nvidia.{0,80}(?:driver|드라이버)",
            "CUDA Toolkit 13.1": r"cuda toolkit.{0,20}(?:`?13\.1`?)",
            "Python 3.13 source prerequisite": r"python.{0,20}(?:`?3\.13`?)",
            "Node.js source prerequisite": r"node(?:\.js|js)",
        }
        for name, pattern in prerequisite_patterns.items():
            with self.subTest(prerequisite=name):
                self.assert_has_pattern(runbook, pattern, f"missing {name}")

    def test_readme_has_an_ai_launch_prompt_and_access_boundary(self) -> None:
        readme = read_lower(ROOT / "README.md")
        self.assert_has_pattern(
            readme,
            r"(?:ai.{0,80}(?:launch|run|실행)|(?:launch|run|실행).{0,80}ai)",
            "README needs a visible AI-assisted launch section",
        )
        self.assert_has_pattern(
            readme,
            r"(?:prompt|프롬프트)",
            "README needs a reusable AI launch prompt",
        )
        for token in (
            "agents.md",
            "docs/windows_gpu_setup.md",
            "docs/ai-gpu-execution-runbook.md",
            "docs/gpu-cuda-user-guide.md",
        ):
            with self.subTest(prompt_token=token):
                self.assertIn(token, readme)

        self.assert_has_pattern(
            readme,
            r"(?:repo(?:sitory)?|저장소).{0,160}"
            r"(?:access|permission|open|read|접근|권한|열|읽)",
            "README must disclose that automatic guidance requires repository access",
        )
        self.assert_has_pattern(
            readme,
            r"(?:cannot|can['’]t|not guaranteed|불가능|못|보장되지|자동.{0,30}않)",
            "README must not promise that every AI can automatically read the guide",
        )

    def test_windows_setup_guide_is_actionable_and_approval_gated(self) -> None:
        setup = read_lower(WINDOWS_GPU_SETUP)
        required_tokens = (
            "nvidia rtx a4000",
            "compute capability 8.6",
            "driver 580",
            "cuda toolkit 13.1",
            "python 3.13.15",
            "node.js 24.19.0",
            "setup_windows_gpu.bat",
            "-install",
            "nvidia-smi",
            "nvcc --version",
            "$env:cuda_path",
            "run_web_gpu.ps1 -preflightonly",
            "production_ray_bvh",
            "strict_float64_bvh_v1",
            "gpu_cuda_gpu_success_count",
        )
        for token in required_tokens:
            with self.subTest(token=token):
                self.assertIn(token, setup)

        self.assert_has_pattern(
            setup,
            r"(?:source checkout|git source).{0,300}(?:python|node)",
            "source setup must disclose its Python/Node prerequisites",
        )
        self.assert_has_pattern(
            setup,
            r"gpu zip.{0,300}(?:python|node).{0,120}(?:포함|설치하지)",
            "GPU ZIP setup must say Python/Node are bundled or unnecessary",
        )
        self.assert_has_pattern(
            setup,
            r"(?:driver|드라이버).{0,200}(?:승인|approval)",
            "driver installation must require explicit approval",
        )
        self.assert_has_pattern(
            setup,
            r"(?:재부팅|reboot).{0,120}(?:별도|다시|직전).{0,80}(?:승인|approval)",
            "reboot must require separate just-in-time approval",
        )
        self.assert_has_pattern(
            setup,
            r"(?:uac|사내 보안|company policy).{0,100}(?:우회|bypass)",
            "the guide must forbid bypassing company/UAC controls",
        )

    def test_ai_runbook_routes_prerequisite_setup_through_check_first_mode(self) -> None:
        runbook = read_lower(AI_GPU_RUNBOOK)
        self.assertIn("docs/windows_gpu_setup.md", runbook)
        self.assertIn("setup_windows_gpu.bat", runbook)
        self.assertIn("setup_windows_gpu.bat -install", runbook)
        self.assert_has_pattern(
            runbook,
            r"without arguments.{0,100}read-only inventory",
            "the AI runbook must start with non-mutating inventory",
        )
        self.assert_has_pattern(
            runbook,
            r"reboot.{0,120}(?:ask again|approval)",
            "the AI runbook must not authorize automatic reboot",
        )


if __name__ == "__main__":
    unittest.main()
