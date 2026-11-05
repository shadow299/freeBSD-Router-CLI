"""End-to-end tests for router-cli in sandbox mode (runs on any OS).

    python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _env(sb):
    return dict(os.environ, ROUTERCLI_SANDBOX=sb, ROUTERCLI_IFACES="em0 em1 em2 re0 re1 lo0")


class SandboxCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sb = tempfile.mkdtemp(prefix="rcli-test-")
        os.makedirs(os.path.join(cls.sb, "etc"))
        with open(os.path.join(cls.sb, "etc/rc.conf"), "w") as f:
            f.write('hostname="freebsd"\nsshd_enable="YES"\n')
        os.environ["ROUTERCLI_SANDBOX"] = cls.sb
        from routercli.auth import hash_secret
        req = json.dumps({"config": {"hostname": "r1", "enable_secret": hash_secret("Secret123")}})
        out = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "bin/router-apply"), "init"],
                             input=req, capture_output=True, text=True, env=_env(cls.sb))
        assert '"ok": true' in out.stdout, out.stdout + out.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.sb, ignore_errors=True)

    def cli(self, *cmds, privileged=True):
        argv = [sys.executable, "-I", os.path.join(ROOT, "bin/router-cli")]
        if privileged:
            argv.append("--privileged")
        for c in cmds:
            argv += ["-c", c]
        return subprocess.run(argv, capture_output=True, text=True, env=_env(self.sb), timeout=60).stdout

    def read(self, rel):
        with open(os.path.join(self.sb, rel)) as f:
            return f.read()


class TestEndToEnd(SandboxCase):
    def test_1_lan_wan_nat_dhcp(self):
        out = self.cli(
            "configure terminal",
            "interface em0", "ip address dhcp", "ip nat outside", "no shutdown",
            "interface em1", "ip address 192.168.1.1 255.255.255.0", "ip nat inside",
            "interface em1.20", "ip address 192.168.20.1 255.255.255.0", "ip nat inside",
            "exit",
            "access-list 1 permit 192.168.0.0 0.0.255.255",
            "ip nat inside source list 1 interface em0 overload",
            "ip dhcp pool LAN", "network 192.168.1.0 255.255.255.0", "default-router 192.168.1.1",
            "exit",
            "ip route 10.0.0.0 255.0.0.0 192.168.1.254",
            "commit comment test1", "end")
        self.assertIn("applied", out)
        rc = self.read("etc/rc.conf")
        self.assertIn('vlans_em1="20"', rc)
        self.assertIn('ifconfig_em1_20="inet 192.168.20.1/24 up"', rc)
        self.assertIn('ifconfig_em0="DHCP up"', rc)
        self.assertIn('gateway_enable="YES"', rc)
        self.assertIn('route_rcli0="-net 10.0.0.0/8 192.168.1.254"', rc)
        self.assertIn('sshd_enable="YES"', rc)            # unmanaged lines are preserved
        pf = self.read("etc/pf.conf")
        self.assertIn("nat on em0 inet from 192.168.0.0/16 to any -> (em0)", pf)
        dm = self.read("usr/local/etc/dnsmasq.conf")
        self.assertIn("dhcp-range=set:LAN,192.168.1.2,192.168.1.254,255.255.255.0,86400", dm)
        self.assertIn("dhcp-option=tag:LAN,option:router,192.168.1.1", dm)

    def test_2_show_run_roundtrip(self):
        out = self.cli("show running-config")
        self.assertIn("interface em1.20", out)
        self.assertIn(" encapsulation dot1Q 20", out)
        self.assertIn("ip nat inside source list 1 interface em0 overload", out)

    def test_3_invalid_input(self):
        out = self.cli("configure terminal", "interface em1", "ip address 300.1.1.1 255.255.255.0")
        self.assertIn("% Invalid input", out)
        out = self.cli("configure terminal", "interface em1", "ip address 10.1.1.1 255.0.255.0")
        self.assertIn("% Invalid input", out)
        self.assertIn("% Incomplete command", self.cli("show ip"))

    def test_4_unprivileged_cannot_configure(self):
        out = self.cli("configure terminal", privileged=False)
        self.assertIn("% Invalid input", out)

    def test_5_negation_and_rollback(self):
        self.cli("configure terminal", "no ip route 10.0.0.0 255.0.0.0 192.168.1.254", "commit", "end")
        self.assertNotIn("route_rcli0", self.read("etc/rc.conf"))
        out = self.cli("rollback 2")
        self.assertIn("applied", out)
        self.assertIn("route_rcli0", self.read("etc/rc.conf"))

    def test_6_write_memory(self):
        self.assertIn("[OK]", self.cli("write memory"))
        with open(os.path.join(self.sb, "var/db/router-cli/startup-config.json")) as f:
            self.assertEqual(json.load(f)["hostname"], "r1")

    def test_7_dynamic_routing(self):
        self.cli("configure terminal", "router ospf 1", "network 192.168.1.0 0.0.0.255 area 0",
                 "router bgp 65000", "neighbor 192.0.2.1 remote-as 65001", "commit", "end")
        frr = self.read("usr/local/etc/frr/frr.conf")
        self.assertIn("network 192.168.1.0/24 area 0", frr)
        self.assertIn("neighbor 192.0.2.1 remote-as 65001", frr)
        self.assertIn('frr_daemons="zebra staticd ospfd bgpd"', self.read("etc/rc.conf"))

    def test_8_realtek_offload(self):
        out = self.cli("configure terminal",
                       "interface re1", "description WAN 2.5G", "ip address dhcp", "no hardware-offload",
                       "interface re0.10", "ip address 10.10.10.1 255.255.255.0", "commit", "end",
                       "show running-config interface re1")
        self.assertIn(" no hardware-offload", out)
        rc = self.read("etc/rc.conf")
        self.assertIn('ifconfig_re1="DHCP -rxcsum -txcsum -rxcsum6 -txcsum6 -tso -lro up"', rc)
        self.assertIn('vlans_re0="10"', rc)
        self.assertIn('ifconfig_re0_10="inet 10.10.10.1/24 up"', rc)
        log = self.read("var/db/router-cli/sandbox-commands.log")
        self.assertIn("ifconfig re1 -rxcsum -txcsum -rxcsum6 -txcsum6 -tso -lro", log)
        self.assertIn("ifconfig re0.10 create vlan 10 vlandev re0", log)
        self.cli("configure terminal", "interface re1", "hardware-offload", "commit", "end")
        self.assertIn('ifconfig_re1="DHCP up"', self.read("etc/rc.conf"))
        self.assertIn("ifconfig re1 rxcsum txcsum rxcsum6 txcsum6 tso",
                      self.read("var/db/router-cli/sandbox-commands.log"))


class TestHelperSecurity(SandboxCase):
    def helper(self, *args, data=""):
        return subprocess.run([sys.executable, "-I", os.path.join(ROOT, "bin/router-apply"), *args],
                              input=data, capture_output=True, text=True, env=_env(self.sb)).stdout

    def test_rejects_injection(self):
        bad = {"config": {"hostname": "r1", "interfaces": {"em0": {"description": 'x"; reboot; "'}}}}
        out = json.loads(self.helper("commit", data=json.dumps(bad)))
        self.assertFalse(out["ok"])
        bad = {"config": {"hostname": "r1$(id)"}}
        self.assertFalse(json.loads(self.helper("commit", data=json.dumps(bad)))["ok"])

    def test_rejects_unknown_keys_and_exec(self):
        out = json.loads(self.helper("commit", data=json.dumps({"config": {"evil": 1}})))
        self.assertFalse(out["ok"])
        out = json.loads(self.helper("exec", "sh"))
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
