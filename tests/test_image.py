"""Tests for the installer-image pieces that can run on any OS.

* image/installer/quick-setup.cli must run cleanly through the real CLI (sandbox),
  exactly the way postinstall.sh does it, and 'router-apply init' with
  write_files must render rc.conf / pf.conf / dnsmasq.conf from the result.
* image/metalog.sh must list every directory and file of the payload.
* the shell scripts must at least parse.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "image", "installer", "quick-setup.cli")


def fill(template, **kv):
    with open(template, encoding="utf-8") as f:
        text = f.read()
    for k, v in kv.items():
        text = text.replace(f"@{k}@", v)
    return text


class TestQuickSetup(unittest.TestCase):
    def setUp(self):
        self.real = tempfile.mkdtemp(prefix="rcli-real-")      # the "installed system"
        self.sb = tempfile.mkdtemp(prefix="rcli-quick-")       # postinstall's scratch sandbox
        for d in (self.real, self.sb):
            os.makedirs(os.path.join(d, "etc"))
            with open(os.path.join(d, "etc/rc.conf"), "w") as f:
                f.write('hostname="gw1.example.net"\nsshd_enable="YES"\nifconfig_re0="DHCP"\n')
        sys.path.insert(0, ROOT)
        from routercli.auth import hash_secret
        req = {"config": {"hostname": "gw1", "enable_secret": hash_secret("Secret123")}}
        self.assertTrue(self.helper(self.real, "init", req)["ok"])
        os.makedirs(os.path.join(self.sb, "var/db/router-cli"))
        shutil.copy(os.path.join(self.real, "var/db/router-cli/running-config.json"),
                    os.path.join(self.sb, "var/db/router-cli/"))

    def tearDown(self):
        shutil.rmtree(self.real, ignore_errors=True)
        shutil.rmtree(self.sb, ignore_errors=True)

    def helper(self, sandbox, op, req):
        env = dict(os.environ, ROUTERCLI_SANDBOX=sandbox)
        cp = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "bin/router-apply"), op],
                            input=json.dumps(req), capture_output=True, text=True, env=env)
        return json.loads(cp.stdout.strip().splitlines()[-1])

    def read(self, path):
        with open(os.path.join(self.real, path.lstrip("/")), encoding="utf-8") as f:
            return f.read()

    def test_template_and_install(self):
        script = fill(TEMPLATE, HOST="gw1", WAN="rge0", LAN="re0", LANIP="10.20.30.1", NET="10.20.30")
        self.assertNotIn("@", script.replace("! postinstall.sh fills in the @...@", ""))
        env = dict(os.environ, ROUTERCLI_SANDBOX=self.sb, ROUTERCLI_IFACES="re0 rge0")
        out = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "bin/router-cli"), "--privileged"],
                             input=script, capture_output=True, text=True, env=env, timeout=120).stdout
        errors = [l for l in out.splitlines() if l.lstrip().startswith("%")]
        self.assertEqual(errors, [], out)
        self.assertIn("Commit 1 applied", out)

        with open(os.path.join(self.sb, "var/db/router-cli/running-config.json")) as f:
            cfg = json.load(f)
        res = self.helper(self.real, "init", {"force": True, "write_files": True, "config": cfg})
        self.assertTrue(res["ok"], res)

        rc = self.read("/etc/rc.conf")
        self.assertTrue(rc.startswith('hostname="gw1.example.net"\nsshd_enable="YES"'))   # bsdinstall's part kept
        block = rc.split("router-cli managed block", 1)[1]
        for line in ('ifconfig_re0="inet 10.20.30.1/24 up"', 'ifconfig_rge0="DHCP up"',
                     'gateway_enable="YES"', 'pf_enable="YES"', 'dnsmasq_enable="YES"', 'hostname="gw1"'):
            self.assertIn(line, block)
        self.assertIn("nat on rge0 inet from 10.20.30.0/24 to any -> (rge0)", self.read("/etc/pf.conf"))
        dm = self.read("/usr/local/etc/dnsmasq.conf")
        self.assertIn("dhcp-range=set:LAN,10.20.30.50,10.20.30.254,255.255.255.0", dm)
        self.assertIn("option:router,10.20.30.1", dm)
        running = json.loads(self.read("/var/db/router-cli/running-config.json"))
        self.assertTrue(running["enable_secret"])                                         # secret kept
        self.assertTrue(os.path.exists(os.path.join(self.real, "var/db/router-cli/startup-config.json")))

    def test_init_refuses_bad_config_without_writing(self):
        res = self.helper(self.real, "init", {"force": True, "write_files": True,
                                              "config": {"hostname": "bad host!"}})
        self.assertFalse(res["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.real, "etc/pf.conf")))


class TestMetalog(unittest.TestCase):
    def test_metalog_lists_everything(self):
        root = tempfile.mkdtemp(prefix="rcli-meta-")
        try:
            pay = os.path.join(root, "usr/freebsd-router")
            os.makedirs(os.path.join(pay, "packages/All"))
            os.makedirs(os.path.join(pay, "router-cli/bin"))
            for f in ("packages/All/pkg-2.1.pkg", "packages/meta.conf", "router-cli/bin/router-cli",
                      "router-cli/setup.sh", "build.env"):
                open(os.path.join(pay, f), "w").close()
            out = subprocess.run(["sh", os.path.join(ROOT, "image/metalog.sh"), root, "usr/freebsd-router"],
                                 capture_output=True, text=True, check=True).stdout.splitlines()
            entries = {l.split()[0]: l for l in out}
            for d in ("", "/packages", "/packages/All", "/router-cli", "/router-cli/bin"):
                self.assertIn("type=dir", entries[f"./usr/freebsd-router{d}"])
            self.assertIn("mode=0644", entries["./usr/freebsd-router/packages/All/pkg-2.1.pkg"])
            self.assertIn("mode=0755", entries["./usr/freebsd-router/router-cli/bin/router-cli"])
            self.assertIn("mode=0755", entries["./usr/freebsd-router/router-cli/setup.sh"])
            self.assertEqual(len(out), 10)
            # directories must come before their contents
            self.assertLess(out.index(entries["./usr/freebsd-router/packages"]),
                            out.index(entries["./usr/freebsd-router/packages/meta.conf"]))
            open(os.path.join(pay, "bad name.txt"), "w").close()
            cp = subprocess.run(["sh", os.path.join(ROOT, "image/metalog.sh"), root, "usr/freebsd-router"],
                                capture_output=True, text=True)
            self.assertNotEqual(cp.returncode, 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestScriptsParse(unittest.TestCase):
    def test_sh_syntax(self):
        scripts = ["image/build-image.sh", "image/metalog.sh", "image/installer/postinstall.sh",
                   "image/installer/local.post-configure", "setup/install.sh", "setup/setup_accounts.sh",
                   "setup/harden_sshd.sh"]
        for s in scripts:
            with self.subTest(script=s):
                cp = subprocess.run(["sh", "-n", os.path.join(ROOT, s)], capture_output=True, text=True)
                self.assertEqual(cp.returncode, 0, cp.stderr)


if __name__ == "__main__":
    unittest.main()
