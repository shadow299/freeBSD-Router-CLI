"""Exercise the image builder's live-output wrapper without running a build."""

import os
from pathlib import Path
import select
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "image/build-image.sh").read_text()
HELPERS = SOURCE[SOURCE.index("info() {"):SOURCE.index("# Force progress")]


class TestImageOutput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = dict(os.environ, LOGDIR=self.tmp.name, ARCH="amd64")

    def run_step(self, command):
        return subprocess.run(
            ["sh", "-eu", "-c", HELPERS + "\n" + command],
            env=self.env, capture_output=True, text=True, timeout=10,
        )

    def test_stdout_and_stderr_on_screen_and_in_log(self):
        result = self.run_step("run sample sh -c 'echo output; echo warning >&2'")
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("output\n", "warning\n"):
            self.assertIn(text, result.stdout)
        self.assertEqual(
            Path(self.tmp.name, "amd64-sample.log").read_text(),
            "output\nwarning\n",
        )

    def test_command_failure_is_not_masked_by_tee(self):
        result = self.run_step(
            "run sample sh -c 'echo broken >&2; exit 7'\necho UNREACHABLE"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("broken", result.stdout)
        self.assertIn("step 'sample' failed", result.stderr)
        self.assertNotIn("UNREACHABLE", result.stdout)
        self.assertNotIn("done in", result.stdout)

    def test_log_failure_stops_build(self):
        result = self.run_step(
            'LOGDIR="$LOGDIR/missing"\nrun sample echo output\necho UNREACHABLE'
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot stream or save output", result.stderr)
        self.assertNotIn("UNREACHABLE", result.stdout)

    def test_output_is_visible_before_command_finishes(self):
        with subprocess.Popen(
            ["sh", "-eu", "-c", HELPERS + """
run sample sh -c 'echo READY; read answer; echo FINISHED'
"""],
            env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as process:
            try:
                output = b""
                while b"READY\n" not in output:
                    ready, _, _ = select.select([process.stdout], [], [], 5)
                    self.assertTrue(ready, "Output did not arrive while command was running")
                    chunk = os.read(process.stdout.fileno(), 4096)
                    self.assertTrue(chunk, "Command exited before reporting READY")
                    output += chunk
                self.assertIsNone(process.poll())
                rest, errors = process.communicate(b"continue\n", timeout=5)
                self.assertEqual(process.returncode, 0, errors)
                self.assertIn(b"FINISHED\n", rest)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
