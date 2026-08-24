from __future__ import annotations

from importlib import util
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_BAT = ROOT / "run_web_gpu.bat"
BOOTSTRAP_PS1 = ROOT / "run_web_gpu.ps1"
RELEASE_PS1 = ROOT / "prepare_gpu_cuda_test_release.ps1"
PACKAGING_PS1 = ROOT / "build_lightweight_desktop.ps1"
SOURCE_VERIFIER = ROOT / "scripts" / "verify_source_requirements.py"
CUDA_VERIFIER = ROOT / "scripts" / "verify_gpu_cuda_runtime.py"
OPENER_SCRIPT = ROOT / "scripts" / "open_web_when_ready.py"
WINDOWS_POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)


def load_script_module(path: Path, name: str):
    spec = util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GpuSourceBootstrapTests(unittest.TestCase):
    def test_batch_file_routes_to_dedicated_gpu_bootstrap(self) -> None:
        script = BOOTSTRAP_BAT.read_text(encoding="utf-8")
        self.assertIn("run_web_gpu.ps1", script)
        self.assertIn('-Port "%PORT%"', script)
        self.assertIn("The GPU server was not started", script)
        self.assertIn("run_web.bat", script)
        self.assertIn("docs\\WINDOWS_GPU_SETUP.md", script)

    def test_bootstrap_synchronizes_every_pulled_runtime_layer(self) -> None:
        script = BOOTSTRAP_PS1.read_text(encoding="utf-8")
        expected_tokens = (
            'Join-Path $Root ".venv-gpu"',
            '"requirements-dev.txt"',
            '"requirements-gpu-cuda.txt"',
            "Get-RequirementsFingerprint",
            "Get-FileHash",
            "Remove-GeneratedGpuVenv",
            "-m pip install",
            "-m pip check",
            "verify_source_requirements.py",
            "npm.cmd",
            "ci --no-audit --no-fund",
            "run build",
            "verify_gpu_cuda_runtime.py",
            "--mode device --human",
            "PreflightOnly",
            "run_web.py",
        )
        for token in expected_tokens:
            with self.subTest(token=token):
                self.assertIn(token, script)

        self.assertIn("docs\\WINDOWS_GPU_SETUP.md", script)

        preflight = script.index("verify_gpu_cuda_runtime.py")
        server = script.index('Join-Path $Root "run_web.py"')
        self.assertLess(preflight, server)
        self.assertIn('Invoke-Checked "GPU PREFLIGHT"', script)
        self.assertIn("$BasePython = @(Find-Python313)", script)
        setup_start = script.index(
            "try {\n    if (-not $PreflightOnly) {",
        )
        first_port_check = script.index(
            "Assert-LoopbackPortAvailable $Port",
            setup_start,
        )
        python_setup = script.index("foreach ($RequiredFile", setup_start)
        final_port_check = script.rindex("Assert-LoopbackPortAvailable $Port")
        opener = script.index("Start-Process -FilePath", setup_start)
        self.assertLess(first_port_check, python_setup)
        self.assertLess(final_port_check, opener)

        token_creation = script.index(
            '$BootToken = [System.Guid]::NewGuid().ToString("N")'
        )
        token_environment = script.index(
            "$env:LEAKAGE_BOOT_TOKEN = $BootToken"
        )
        token_argument = script.index('"--expected-boot-token"')
        self.assertLess(token_creation, token_environment)
        self.assertLess(token_environment, token_argument)
        self.assertLess(token_argument, opener)

    def test_boot_token_readiness_requires_exact_generation(self) -> None:
        opener = load_script_module(OPENER_SCRIPT, "boot_token_opener_check")

        class FakeResponse:
            status = 200

            def __init__(self, payload: bytes):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return self.payload

        requested_urls: list[str] = []

        def mismatch_open(url: str, timeout: float):
            requested_urls.append(url)
            self.assertEqual(timeout, 1.0)
            return FakeResponse(b'{"boot_token":"older-server"}')

        self.assertFalse(
            opener.check_server_ready(
                "http://127.0.0.1:8788/",
                "new-server",
                open_url=mismatch_open,
            )
        )
        self.assertEqual(
            requested_urls,
            ["http://127.0.0.1:8788/dev-status"],
        )

        def match_open(url: str, timeout: float):
            return FakeResponse(b'{"boot_token":"new-server"}')

        self.assertTrue(
            opener.check_server_ready(
                "http://127.0.0.1:8788/",
                "new-server",
                open_url=match_open,
            )
        )

        def legacy_open(url: str, timeout: float):
            self.assertTrue(url.endswith("/health"))
            return FakeResponse(b"legacy health body")

        self.assertTrue(
            opener.check_server_ready(
                "http://127.0.0.1:8788/",
                open_url=legacy_open,
            )
        )

    def test_legacy_cpu_launcher_keeps_tokenless_health_check(self) -> None:
        script = (ROOT / "run_web.bat").read_text(encoding="utf-8")
        opener_call = next(
            line
            for line in script.splitlines()
            if "open_web_when_ready.py" in line
        )
        self.assertNotIn("expected-boot-token", opener_call)

    @unittest.skipUnless(sys.platform == "win32", "PowerShell scripts are Windows-only")
    def test_occupied_port_aborts_before_setup_or_browser_opener(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_EXCLUSIVEADDRUSE,
                    1,
                )
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            process = subprocess.run(
                [
                    str(WINDOWS_POWERSHELL),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(BOOTSTRAP_PS1),
                    "-Port",
                    str(port),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        output = process.stdout + process.stderr
        self.assertNotEqual(process.returncode, 0)
        self.assertIn(f"Port {port} is already occupied", output)
        self.assertIn("No browser or GPU server was started", output)
        self.assertNotIn("[PYTHON PIP]", output)
        self.assertNotIn("Start-Process", output)

    @unittest.skipUnless(sys.platform == "win32", "PowerShell scripts are Windows-only")
    def test_python_discovery_fallback_preserves_full_executable_path(self) -> None:
        script = BOOTSTRAP_PS1.read_text(encoding="utf-8")
        function_start = script.index("function Find-Python313 {")
        function_end = script.index(
            "function Get-RequirementsFingerprint {",
            function_start,
        )
        discovery_function = script[function_start:function_end]
        command = (
            discovery_function
            + "\n"
            + "if (Get-Command 'py.exe' -ErrorAction SilentlyContinue) { "
            + "throw 'test PATH unexpectedly contains py.exe' }\n"
            + "$BasePython = @(Find-Python313)\n"
            + "if ($BasePython.Count -ne 1) { "
            + "throw \"expected one fallback token, got $($BasePython.Count)\" }\n"
            + "$Expected = (Get-Command 'python.exe' -ErrorAction Stop).Source\n"
            + "if ($BasePython[0] -ne $Expected) { "
            + "throw \"truncated fallback: $($BasePython[0]) != $Expected\" }\n"
            + "Write-Output $BasePython[0]"
        )
        environment = os.environ.copy()
        environment["PATH"] = str(Path(sys.executable).resolve().parent)
        process = subprocess.run(
            [str(WINDOWS_POWERSHELL), "-NoProfile", "-Command", command],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(
            Path(process.stdout.strip()).resolve(),
            Path(sys.executable).resolve(),
        )

    def test_generated_gpu_venv_is_ignored(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".venv/", gitignore.splitlines())
        self.assertIn(".venv-gpu/", gitignore.splitlines())

    def test_requirement_parser_rejects_unpinned_dependencies(self) -> None:
        verifier = load_script_module(SOURCE_VERIFIER, "source_verifier_unpinned")
        with tempfile.TemporaryDirectory() as temporary_directory:
            requirements = Path(temporary_directory) / "requirements.txt"
            requirements.write_text("numpy>=2\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unpinned_requirement"):
                verifier.read_exact_pins([requirements])

    def test_all_source_requirement_files_use_exact_non_conflicting_pins(self) -> None:
        verifier = load_script_module(SOURCE_VERIFIER, "source_verifier_repo")
        pins = verifier.read_exact_pins(
            [
                ROOT / "requirements-dev.txt",
                ROOT / "requirements-gpu-cuda.txt",
            ]
        )
        self.assertEqual(pins["numba"][1], "0.66.0")
        self.assertEqual(pins["llvmlite"][1], "0.48.0")
        self.assertEqual(pins["numpy"][1], "2.4.6")
        self.assertGreaterEqual(len(pins), 10)

    def test_cuda_human_diagnostics_are_actionable(self) -> None:
        verifier = load_script_module(CUDA_VERIFIER, "cuda_human_diagnostics")
        toolkit = verifier.format_human_error(
            "RuntimeError:gpu_cuda_unavailable:cuda_toolkit_not_found"
        )
        self.assertIn("CUDA Toolkit 13.1", toolkit)
        self.assertIn("[ACTION]", toolkit)
        self.assertIn("must not be reported as working", toolkit)

        driver = verifier.format_human_error(
            "RuntimeError:gpu_cuda_unavailable:cuda_driver_unavailable"
        )
        self.assertIn("NVIDIA display driver", driver)
        self.assertIn("reboot", driver)

    def test_release_handoff_is_commit_and_checksum_identified(self) -> None:
        script = RELEASE_PS1.read_text(encoding="utf-8")
        expected_tokens = (
            "status --porcelain --untracked-files=all",
            "build_gpu_cuda_desktop.ps1",
            "Get-FileHash",
            '"$ZipPath.sha256"',
            '"$ZipPath.handoff.json"',
            "git_branch",
            "git_commit",
            "ai_instruction_entrypoint",
            "ai_gpu_runbook",
            "windows_gpu_setup_guide",
            "windows_gpu_setup_entrypoint",
            "windows_gpu_setup_script",
            "ai_requires_package_file_access",
            "[System.IO.Compression.ZipFile]::OpenRead",
            "$ExpectedArchiveEntry",
            "$RoundTrip.ai_instruction_entrypoint",
            "$RoundTrip.ai_gpu_runbook",
            "$RoundTrip.windows_gpu_setup_guide",
            "$RoundTrip.windows_gpu_setup_entrypoint",
            "$RoundTrip.windows_gpu_setup_script",
            "source_pull_does_not_update_extracted_zip",
            "tester_must_run_real_cuda_preflight",
        )
        for token in expected_tokens:
            with self.subTest(token=token):
                self.assertIn(token, script)
        self.assertNotIn("gh release", script.lower())
        self.assertNotIn("invoke-webrequest", script.lower())

    @unittest.skipUnless(sys.platform == "win32", "PowerShell scripts are Windows-only")
    def test_packaging_rejects_unsafe_output_names_without_deleting_files(self) -> None:
        unsafe_names = (
            ".",
            "..",
            r"..\release-sibling",
            r"nested\package",
            "nested/package",
            "CON",
            "trailing.",
            " leading",
            Path.cwd().anchor,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            fake_python = temporary_root / "fake-python"
            fake_python.mkdir()
            for script_path in (RELEASE_PS1, PACKAGING_PS1):
                for case_index, unsafe_name in enumerate(unsafe_names):
                    with self.subTest(
                        script=script_path.name,
                        output_name=unsafe_name,
                    ):
                        case_root = temporary_root / f"case-{script_path.stem}-{case_index}"
                        release_root = case_root / "release"
                        sibling_root = case_root / "release-sibling"
                        release_root.mkdir(parents=True)
                        sibling_root.mkdir(parents=True)
                        release_sentinel = release_root / "keep.txt"
                        sibling_sentinel = sibling_root / "keep.txt"
                        release_sentinel.write_text("release-safe", encoding="utf-8")
                        sibling_sentinel.write_text("sibling-safe", encoding="utf-8")
                        process = subprocess.run(
                            [
                                str(WINDOWS_POWERSHELL),
                                "-NoProfile",
                                "-ExecutionPolicy",
                                "Bypass",
                                "-File",
                                str(script_path),
                                "-OutputName",
                                unsafe_name,
                                "-ReleaseDirectory",
                                str(release_root),
                                "-SourcePythonDirectory",
                                str(fake_python),
                            ],
                            cwd=ROOT,
                            check=False,
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        output = process.stdout + process.stderr
                        self.assertNotEqual(process.returncode, 0)
                        self.assertIn("[PATH SAFETY]", output)
                        self.assertEqual(
                            release_sentinel.read_text(encoding="utf-8"),
                            "release-safe",
                        )
                        self.assertEqual(
                            sibling_sentinel.read_text(encoding="utf-8"),
                            "sibling-safe",
                        )

    def test_packaging_validates_exact_direct_children_before_recursive_delete(self) -> None:
        for path in (RELEASE_PS1, PACKAGING_PS1):
            with self.subTest(path=path.name):
                script = path.read_text(encoding="utf-8")
                self.assertIn("Assert-SafeOutputName $OutputName", script)
                self.assertIn("Assert-SafeDirectChildPath", script)
                self.assertIn("ReleaseDirectory cannot be a filesystem root", script)
        packaging = PACKAGING_PS1.read_text(encoding="utf-8")
        self.assertIn("before recursive deletion", packaging)
        self.assertIn("Assert-NotReparsePoint", packaging)
        self.assertNotIn(
            "$resolvedOutput.StartsWith($resolvedRelease",
            packaging,
        )

    @unittest.skipUnless(sys.platform == "win32", "PowerShell scripts are Windows-only")
    def test_powershell_scripts_parse_without_errors(self) -> None:
        for path in (BOOTSTRAP_PS1, RELEASE_PS1, PACKAGING_PS1):
            with self.subTest(path=path.name):
                escaped_path = str(path).replace("'", "''")
                parser = (
                    "$tokens=$null; $errors=$null; "
                    "[System.Management.Automation.Language.Parser]::ParseFile("
                    f"'{escaped_path}', [ref]$tokens, [ref]$errors) | Out-Null; "
                    "if ($errors.Count -gt 0) { $errors | ForEach-Object { "
                    "[Console]::Error.WriteLine($_.Message) }; exit 1 }"
                )
                process = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-Command",
                        parser,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == "__main__":
    unittest.main()
