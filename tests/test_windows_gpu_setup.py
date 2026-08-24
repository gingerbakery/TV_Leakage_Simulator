from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SETUP_PS1 = ROOT / "setup_windows_gpu.ps1"
SETUP_BAT = ROOT / "setup_windows_gpu.bat"
WINDOWS_POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)


class WindowsGpuSetupTests(unittest.TestCase):
    def test_batch_defaults_to_check_and_requires_explicit_install_mode(self) -> None:
        script = SETUP_BAT.read_text(encoding="utf-8")
        self.assertIn('if "%MODE%"=="" set "MODE=check"', script)
        self.assertIn('if /I "%MODE%"=="check"', script)
        self.assertIn('if /I "%MODE%"=="install"', script)
        self.assertIn('if /I "%MODE%"=="runtime-check"', script)
        self.assertIn('if /I "%MODE%"=="runtime-install"', script)
        self.assertIn('if /I "%MODE%"=="-Install" set "MODE=install"', script)
        self.assertIn("-Install", script)
        self.assertIn("-RuntimeOnly", script)
        self.assertIn("-ApprovedDriverInstallerPath", script)
        self.assertNotIn("runas", script.lower())
        self.assertNotIn("-Verb RunAs", script)
        self.assertIn("docs\\WINDOWS_GPU_SETUP.md", script)

    def test_delivery_path_separates_source_and_bundled_gpu_runtime(self) -> None:
        script = SETUP_PS1.read_text(encoding="utf-8")
        self.assertIn("function Get-DeliveryPath", script)
        self.assertIn('"requirements-dev.txt"', script)
        self.assertIn('"requirements-gpu-cuda.txt"', script)
        self.assertIn('"frontend"', script)
        self.assertIn('"LeakageSimulator.exe"', script)
        self.assertIn('"_tools"', script)
        self.assertIn('"CHECK_GPU_CUDA.bat"', script)
        self.assertIn("RequiresSourceTools = $true", script)
        self.assertIn("RequiresSourceTools = $false", script)
        self.assertIn("[DELIVERY]", script)
        self.assertIn("[switch]$RuntimeOnly", script)

        self.assertIn(
            "Python and Node.js: SKIPPED (bundled GPU runtime; not an install target)",
            script,
        )
        self.assertIn(
            "if ($Delivery.RequiresSourceTools -and -not $Status.Python.Ready)",
            script,
        )
        self.assertIn(
            "if ($Delivery.RequiresSourceTools -and -not $Status.Node.Ready)",
            script,
        )

    def test_default_powershell_mode_is_read_only_and_fail_closed(self) -> None:
        script = SETUP_PS1.read_text(encoding="utf-8")
        self.assertIn("[switch]$Install", script)
        self.assertIn("docs\\WINDOWS_GPU_SETUP.md", script)
        self.assertNotIn("[switch]$Install = $true", script)
        self.assertIn("if (-not $Install)", script)
        self.assertIn("[MODE] CHECK ONLY", script)
        self.assertIn("[GPU SETUP NOT READY]", script)
        self.assertIn("exit 1", script)
        self.assertIn("No CPU fallback is treated as GPU success", script)
        self.assertNotIn("run_web.bat", script)

        check_only = script.index("if (-not $Install) {", script.index("try {"))
        install_guard = script.index("Assert-Administrator", check_only)
        first_main_install = script.index(
            'Invoke-WingetPackage -Id "Nvidia.CUDA"',
            install_guard,
        )
        self.assertLess(check_only, install_guard)
        self.assertLess(install_guard, first_main_install)

    def test_preflight_checks_every_external_prerequisite(self) -> None:
        script = SETUP_PS1.read_text(encoding="utf-8")
        expected_tokens = (
            "[Environment]::Is64BitOperatingSystem",
            "Win32_VideoController",
            "nvidia-smi.exe",
            "--query-gpu=name,driver_version",
            "--query-gpu=compute_cap",
            '[version]"580.0"',
            "CUDA_PATH_V13_1",
            "nvcc.exe",
            "--version",
            '"cudart64_*.dll"',
            '"nvvm*.dll"',
            '"libdevice*.bc"',
            '"3.13.15"',
            "sys.maxsize > 2**32",
            '"v24.19.0"',
            '"process.arch"',
            '$NpmVersion = (& $NpmCommand.Source --version',
            "npm.cmd",
        )
        for token in expected_tokens:
            with self.subTest(token=token):
                self.assertIn(token, script)

    def test_install_mode_pins_exact_winget_packages_and_versions(self) -> None:
        script = SETUP_PS1.read_text(encoding="utf-8")
        expected_invocations = (
            'Invoke-WingetPackage -Id "Nvidia.CUDA" '
            "-Version $RequiredCudaWingetVersion",
            'Invoke-WingetPackage -Id "Python.Python.3.13" '
            "-Version $RequiredPythonVersion",
            'Invoke-WingetPackage -Id "OpenJS.NodeJS.LTS" '
            '-Version ($RequiredNodeVersion.TrimStart("v"))',
        )
        for invocation in expected_invocations:
            with self.subTest(invocation=invocation):
                self.assertIn(invocation, script)

        for version in ('"13.1"', '"3.13.15"', '"v24.19.0"'):
            with self.subTest(version=version):
                self.assertIn(version, script)

        for argument in (
            '"--exact"',
            '"--source", "winget"',
            '"--architecture", "x64"',
            '"--scope", $Scope',
            '"--silent"',
            '"--accept-source-agreements"',
            '"--accept-package-agreements"',
            '"--disable-interactivity"',
        ):
            with self.subTest(argument=argument):
                self.assertIn(argument, script)

        show = script.index('$WingetShowArguments = @(')
        install = script.index('$WingetArguments = @(', show)
        invoke_show = script.index('& $Winget @WingetShowArguments', show)
        invoke_install = script.index('& $Winget @WingetArguments', install)
        self.assertLess(show, invoke_show)
        self.assertLess(invoke_show, install)
        self.assertLess(install, invoke_install)
        self.assertIn("Installation was not attempted", script)
        show_block = script[show:install]
        self.assertIn('"--architecture", "x64"', show_block)
        self.assertIn('"--scope", $Scope', show_block)

    def test_existing_compatible_patch_versions_are_not_downgraded(self) -> None:
        script = SETUP_PS1.read_text(encoding="utf-8")
        self.assertIn("$ParsedVersion.Major -eq 3", script)
        self.assertIn("$ParsedVersion.Minor -eq 13", script)
        self.assertIn("$ParsedNodeVersion.Major -eq 24", script)
        self.assertIn("$ParsedNodeVersion -ge $MinimumAcceptedNodeVersion", script)
        self.assertIn('$NodeArchitecture -eq "x64"', script)
        self.assertIn('$null -ne $NpmVersion', script)

    def test_driver_is_it_supplied_signed_and_never_downloaded(self) -> None:
        script = SETUP_PS1.read_text(encoding="utf-8")
        self.assertIn("$ApprovedDriverInstallerPath", script)
        self.assertIn("Resolve-ApprovedDriverInstaller", script)
        self.assertIn("function Test-IsFullyQualifiedWindowsPath", script)
        self.assertIn("Test-IsFullyQualifiedWindowsPath $Path", script)
        self.assertIn("Get-AuthenticodeSignature", script)
        self.assertIn("SignatureStatus]::Valid", script)
        self.assertIn('Subject -notmatch "NVIDIA"', script)
        self.assertIn("& $ResolvedInstaller -s -n Display.Driver", script)
        self.assertIn("This script never downloads a driver", script)
        driver_function = script[
            script.index("function Invoke-ApprovedDriverInstaller") :
            script.index("function Get-WingetCommand")
        ]
        self.assertIn("Assert-InstallMode", driver_function)

        lowered = script.lower()
        for token in (
            "invoke-webrequest",
            "start-bitstransfer",
            "curl.exe",
            "downloadfile(",
            "downloadstring(",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, lowered)

    def test_reboot_and_unsafe_winget_overrides_are_forbidden(self) -> None:
        script = SETUP_PS1.read_text(encoding="utf-8").lower()
        for token in (
            "restart-computer",
            "shutdown.exe",
            "--allow-reboot",
            "--ignore-security-hash",
            "--force",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, script)

        self.assertIn("rebootrequired", script)
        self.assertIn("[reboot required]", script)
        self.assertIn("restart manually", script)
        self.assertIn("exit 2", script)
        winget_function = script[
            script.index("function invoke-wingetpackage") :
            script.index("function refresh-processenvironment")
        ]
        self.assertIn("assert-installmode", winget_function)

    def test_success_requires_full_delivery_specific_contract(self) -> None:
        script = SETUP_PS1.read_text(encoding="utf-8")
        all_ready = script.index("AllReady = (")
        contract_end = script.index(")", all_ready)
        contract = script[all_ready:contract_end]
        for token in (
            "$Windows64Bit",
            "$Nvidia.GpuDetected",
            "$Nvidia.DriverCompatible",
            "$Cuda.Ready",
            "$SourceToolsReady",
        ):
            with self.subTest(token=token):
                self.assertIn(token, contract)

        self.assertIn(
            "[GPU PREREQUISITES READY] External prerequisites are present; "
            "GPU execution is not verified yet",
            script,
        )
        self.assertIn("Run run_web_gpu.bat", script)
        self.assertIn("Run CHECK_GPU_CUDA.bat", script)
        self.assertIn("Runtime-only prerequisites passed", script)
        self.assertIn(
            "Only its production Ray/BVH CUDA preflight can verify GPU readiness",
            script,
        )

    @unittest.skipUnless(sys.platform == "win32", "PowerShell parser is Windows-only")
    def test_powershell_script_parses_without_errors(self) -> None:
        escaped_path = str(SETUP_PS1).replace("'", "''")
        parser = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{escaped_path}', [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count -gt 0) { $errors | ForEach-Object { "
            "[Console]::Error.WriteLine($_.Message) }; exit 1 }"
        )
        process = subprocess.run(
            [
                str(WINDOWS_POWERSHELL),
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

    @unittest.skipUnless(sys.platform == "win32", "PowerShell check is Windows-only")
    def test_default_source_check_executes_without_mutating_install_path(self) -> None:
        process = subprocess.run(
            [
                str(WINDOWS_POWERSHELL),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SETUP_PS1),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        output = process.stdout + process.stderr
        self.assertIn("[DELIVERY] Git source checkout", output)
        self.assertIn("[MODE] CHECK ONLY", output)
        self.assertNotIn("[INSTALL]", output)
        self.assertNotIn("property 'Count'", output)
        self.assertIn(process.returncode, (0, 1))

    @unittest.skipUnless(sys.platform == "win32", "batch forwarding is Windows-only")
    def test_batch_install_alias_forwards_the_install_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            batch_path = temporary_root / SETUP_BAT.name
            stub_path = temporary_root / SETUP_PS1.name
            capture_path = temporary_root / "capture.txt"
            batch_path.write_text(SETUP_BAT.read_text(encoding="utf-8"), encoding="utf-8")
            stub_path.write_text(
                "param([switch]$Install)\n"
                "if (-not $Install) { exit 23 }\n"
                "Set-Content -LiteralPath $env:TVLS_SETUP_CAPTURE "
                "-Value 'install=true' -Encoding ascii\n"
                "exit 0\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["TVLS_SETUP_CAPTURE"] = str(capture_path)
            process = subprocess.run(
                ["cmd.exe", "/d", "/c", str(batch_path), "-Install"],
                cwd=temporary_root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertEqual(capture_path.read_text(encoding="ascii").strip(), "install=true")

    @unittest.skipUnless(sys.platform == "win32", "GPU ZIP check is Windows-only")
    def test_gpu_zip_check_skips_source_only_python_and_node(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            script_path = temporary_root / SETUP_PS1.name
            script_path.write_text(SETUP_PS1.read_text(encoding="utf-8"), encoding="utf-8")
            (temporary_root / "LeakageSimulator.exe").touch()
            (temporary_root / "CHECK_GPU_CUDA.bat").touch()
            (temporary_root / "_tools").mkdir()
            process = subprocess.run(
                [
                    str(WINDOWS_POWERSHELL),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                ],
                cwd=temporary_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
            output = process.stdout + process.stderr
            self.assertIn("[DELIVERY] Extracted GPU CUDA ZIP", output)
            self.assertIn("Python and Node.js: SKIPPED", output)
            self.assertNotIn("[INSTALL]", output)
            self.assertIn(process.returncode, (0, 1))


if __name__ == "__main__":
    unittest.main()
