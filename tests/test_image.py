"""Tests for the installer-image pieces that can run on any OS.

* image/installer/quick_setup.py + the quick-setup templates (DHCP and PPPoE WAN)
  must run cleanly through the real CLI (sandbox), exactly the way postinstall.sh
  does it, and 'router-apply init' with write_files must render rc.conf / pf.conf /
  dnsmasq.conf (and ppp.conf for PPPoE) from the result.
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
QUICK = os.path.join(ROOT, "image", "installer", "quick_setup.py")


def quick_script(**env):
    """Run quick_setup.py exactly like postinstall.sh does (values via QS_* environment)."""
    e = dict(os.environ, **{f"QS_{k}": v for k, v in env.items()})
    cp = subprocess.run([sys.executable, "-I", QUICK], capture_output=True, text=True, env=e)
    if cp.returncode:
        raise AssertionError(cp.stderr)
    return cp.stdout


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

    def install(self, script):
        """postinstall.sh: CLI in a sandbox, then 'router-apply init' with write_files."""
        self.assertNotRegex("\n".join(l for l in script.splitlines() if not l.startswith("!")), r"@[A-Z0-9]+@")
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
        return cfg

    def test_template_and_install(self):
        self.install(quick_script(TYPE="dhcp", HOST="gw1", WAN="rge0", LAN="re0", LANIP="10.20.30.1"))

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

    def test_pppoe_quick_setup(self):
        password = 'Pa ss"w$rd\\~#?'
        cfg = self.install(quick_script(TYPE="pppoe", HOST="gw1", WAN="rge0", LAN="re0", LANIP="192.168.7.1",
                                        VLAN="35", USER="jane@isp.example", PASS=password))
        d = cfg["interfaces"]["dialer1"]
        self.assertEqual((d["ppp_username"], d["ppp_password"], d["dialer_pool"]), ("jane@isp.example", password, 1))
        self.assertEqual(cfg["interfaces"]["rge0.35"]["pppoe_pool"], 1)
        ppp_path = os.path.join(self.real, "etc/ppp/ppp.conf")
        self.assertEqual(os.stat(ppp_path).st_mode & 0o777, 0o600)
        ppp = self.read("/etc/ppp/ppp.conf")
        for line in (" set device PPPoE:rge0_35", ' set authname "jane@isp.example"',
                     ' set authkey "Pa ss\\"w\\$rd\\\\\\~#?"', " iface name dialer1", " enable dns",
                     " add! default HISADDR"):
            self.assertIn(line, ppp)
        block = self.read("/etc/rc.conf").split("router-cli managed block", 1)[1]
        for line in ('ppp_enable="YES"', 'ppp_profile="dialer1"', 'ppp_dialer1_unit="1"', 'vlans_rge0="35"',
                     'ifconfig_rge0_35="up"'):
            self.assertIn(line, block)
        self.assertNotIn("defaultrouter", block)
        self.assertIn("nat on dialer1 inet from 192.168.7.0/24 to any -> (dialer1)", self.read("/etc/pf.conf"))

    def test_quick_setup_rejects_bad_ppp_values(self):
        for user, pw, vlan in (("jane", "!run-me", ""), ("", "secret", ""), ("jane", "secret", "5000"),
                               ("ja ne", "secret", "")):
            env = dict(os.environ, QS_USER=user, QS_PASS=pw, QS_VLAN=vlan)
            cp = subprocess.run([sys.executable, "-I", QUICK, "check"], capture_output=True, text=True, env=env)
            self.assertEqual(cp.returncode, 1, (user, pw, vlan))

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
