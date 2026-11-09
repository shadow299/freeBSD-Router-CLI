"""Exercise installer failure reporting without modifying the host system."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "setup/install.sh").read_text()
HELPERS = SOURCE[SOURCE.index("die() {"):SOURCE.index('[ "$(id -u)"')]
DOAS = SOURCE[SOURCE.index('msg "Configuring doas'):SOURCE.index('msg "Installing rc.d')]
STARTUP = SOURCE[SOURCE.index("set -eu"):SOURCE.index("die() {")]
LOADER_HELPER = SOURCE[SOURCE.index("loader_tunable()"):SOURCE.index("realtek_vendor()")]


class TestInstallDiagnostics(unittest.TestCase):
    def test_loader_tunables_update_remove_and_preserve_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory, "loader.conf")
            config.write_text(
                '# keep comment\nif_rge_load="YES"\n'
                ' net.isr.maxthreads = "2"\nnet.isr.maxthreads="4"\n'
                'netXisrXmaxthreads="keep"\n'
            )
            config.chmod(0o640)
            commands = f"LOADER='{config}'\n" + LOADER_HELPER
            result = self.run_install(commands + """
loader_tunable net.isr.maxthreads 14
loader_tunable net.isr.bindthreads 1
loader_tunable net.isr.maxthreads 14
loader_tunable hw.re.max_rx_mbuf_sz 2048
loader_tunable hw.re.max_rx_mbuf_sz ""
""")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_text(),
                             '# keep comment\nif_rge_load="YES"\n'
                             'netXisrXmaxthreads="keep"\n'
                             'net.isr.bindthreads="1"\nnet.isr.maxthreads="14"\n')
            self.assertEqual(config.stat().st_mode & 0o777, 0o640)
            result = self.run_install(commands + """
loader_tunable net.isr.maxthreads ""
loader_tunable net.isr.bindthreads ""
""")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("net.isr.", config.read_text())
            self.assertEqual(list(Path(directory).iterdir()), [config])

    def test_installer_replaces_base_only_path_with_trusted_paths(self):
        result = subprocess.run(
            ["/bin/sh", "-c", STARTUP + '\nprintf "%s" "$PATH"'],
            env=dict(os.environ, PATH="/sbin:/bin:/usr/sbin:/usr/bin"),
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin",
        )

    def test_ports_executable_lookup_with_base_only_inherited_path(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory, "local")
            (local / "bin").mkdir(parents=True)
            executable = local / "bin/doas"
            executable.write_text('#!/bin/sh\nprintf "doas reached\\n"\n')
            executable.chmod(0o755)
            # Relocate the ports prefix in the fixture, never write to /usr/local.
            startup = STARTUP.replace("/usr/local", str(local))
            result = subprocess.run(
                ["/bin/sh", "-c", startup + '\ndoas -C /unused'],
                env=dict(os.environ, PATH="/sbin:/bin:/usr/sbin:/usr/bin"),
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "doas reached\n")

    def run_install(self, commands):
        return subprocess.run(
            ["sh", "-eu", "-c", HELPERS + commands],
            capture_output=True, text=True, timeout=10,
        )

    def test_unexpected_failure_reports_stage_and_status(self):
        result = self.run_install('msg "Installing launchers"\n(exit 7)')
        self.assertEqual(result.returncode, 7)
        self.assertIn("FAILED during Installing launchers (exit status 7)", result.stderr)

    def test_success_has_no_failure_message(self):
        result = self.run_install('msg "Done"')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def run_doas(self, status):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory, "doas.conf")
            return self.run_install(f"""
DOAS_CONF='{config}'
DOAS_RULE='permit nopass router as root cmd /usr/local/libexec/router-apply'
chown() {{ :; }}
doas() {{ return {status}; }}
""" + DOAS)

    def test_silent_doas_failure_is_explicit(self):
        result = self.run_doas(139)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("doas -C", result.stderr)
        self.assertIn("exit status 139", result.stderr)
        self.assertIn("FAILED during Configuring doas", result.stderr)
        self.assertNotIn("syntax validated", result.stdout)

    def test_valid_doas_configuration_continues(self):
        result = self.run_doas(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("syntax validated", result.stdout)
