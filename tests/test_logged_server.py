from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


WRAPPER = Path(__file__).resolve().parents[1] / 'scripts' / 'run_logged_server.py'


class LoggedServerTests(unittest.TestCase):
    def run_fixture(self, root, source, *args, log_name='server.log'):
        entrypoint = root / 'fake_server.py'
        entrypoint.write_text(source, encoding='utf-8')
        log = root / 'logs' / log_name
        process = subprocess.run(
            [sys.executable, '-X', 'faulthandler', str(WRAPPER),
             '--log-file', str(log), '--entrypoint', str(entrypoint), *args],
            cwd=root, env={**os.environ, 'PYTHONUTF8': '1'},
            capture_output=True, text=True, encoding='utf-8', timeout=10,
        )
        return process, log

    def test_progress_errors_and_entrypoint_arguments_reach_console_and_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'server_sibling.py').write_text("VALUE = 'sibling import OK'", encoding='utf-8')
            process, log = self.run_fixture(root,
                "import sys\nfrom server_sibling import VALUE\n"
                "print(VALUE)\nprint(sys.argv[1:])\n"
                "print('[RAY] 진행 중', flush=True)\nprint('stderr evidence', file=sys.stderr)\n",
                '--port', '12345', '--strict-port')
            self.assertEqual(process.returncode, 0, process.stderr)
            content = log.read_text(encoding='utf-8')
            for token in ('sibling import OK', "['--port', '12345', '--strict-port']", '[RAY] 진행 중'):
                self.assertIn(token, process.stdout)
                self.assertIn(token, content)
            self.assertIn('stderr evidence', process.stderr)
            self.assertIn('stderr evidence', content)
            self.assertIn('[SERVER LOG] UTC=', content)

    def test_unhandled_exception_is_saved_before_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            process, log = self.run_fixture(Path(directory), "raise RuntimeError('precise failure evidence')")
            self.assertEqual(process.returncode, 1)
            for output in (process.stderr, log.read_text(encoding='utf-8')):
                self.assertIn('Traceback (most recent call last)', output)
                self.assertIn('RuntimeError: precise failure evidence', output)

    def test_fault_handler_is_active_and_has_a_writable_persistent_stack_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            process, log = self.run_fixture(Path(directory),
                "import faulthandler, sys\nassert faulthandler.is_enabled()\n"
                "faulthandler.dump_traceback(file=sys.stderr.log, all_threads=True)\n")
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn('fake_server.py', log.read_text(encoding='utf-8'))

    def test_nonzero_exit_code_is_preserved_and_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            process, log = self.run_fixture(Path(directory), 'raise SystemExit(7)')
            self.assertEqual(process.returncode, 7)
            self.assertIn('[SERVER EXIT] 7', log.read_text(encoding='utf-8'))

    def test_existing_evidence_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, log = self.run_fixture(root, "print('original evidence')")
            original = log.read_bytes()
            second, _ = self.run_fixture(root, "print('replacement')")
            self.assertEqual(first.returncode, 0)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(log.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
