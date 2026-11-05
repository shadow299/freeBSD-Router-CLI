"""PPPoE client (interface dialerN + pppoe-client): model, ppp.conf, rc.conf, live steps, CLI.

    python3 -m unittest tests.test_pppoe -v
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

from routercli.auth import type7_decode, type7_encode                       # noqa: E402
from routercli.backend import apply, render_pf, render_ppp, render_rcconf   # noqa: E402
from routercli.config.model import (Acl, ConfigError, Interface, NatRule, RouterConfig,   # noqa: E402
                                    StaticRoute)


def pppoe_config(**dialer):
    c = RouterConfig()
    c.interfaces["re0"] = Interface("re0", ipv4=["192.168.1.1/24"], nat="inside")
    c.interfaces["re1"] = Interface("re1", pppoe_pool=1)
    d = dict(negotiated=True, dialer_pool=1, ppp_username="jane@isp", ppp_password="s3cret", ppp_dns=True,
             nat="outside")
    d.update(dialer)
    c.interfaces["dialer1"] = Interface("dialer1", **d)
    c.routes = [StaticRoute("0.0.0.0/0", interface="dialer1")]
    return c


class TestType7(unittest.TestCase):
    def test_cisco_vector_and_roundtrip(self):
        self.assertEqual(type7_decode("0822455D0A16"), "cisco")
        self.assertEqual(type7_encode("cisco", 8), "0822455D0A16")
        for s in ("a", 'p@ss w"o$rd~!#?\\', "x" * 64):
            self.assertEqual(type7_decode(type7_encode(s)), s)
        for bad in ("", "1", "99AB", "0G12"):
            with self.assertRaises(ValueError):
                type7_decode(bad)


class TestModel(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(pppoe_config().validate(), [])

    def assertInvalid(self, cfg, text):
        with self.assertRaises(ConfigError) as cm:
            cfg.validate()
        self.assertIn(text, str(cm.exception))

    def test_incomplete_dialer(self):
        self.assertInvalid(pppoe_config(ppp_password=""), "ppp chap password")
        self.assertInvalid(pppoe_config(negotiated=False), "ip address negotiated")
        c = pppoe_config()
        c.interfaces["re1"].pppoe_pool = None
        self.assertInvalid(c, "pppoe-client dial-pool-number 1")

    def test_shutdown_dialer_may_be_incomplete(self):
        self.assertIsInstance(pppoe_config(ppp_password="", shutdown=True).validate(), list)

    def test_bad_values(self):
        self.assertInvalid(pppoe_config(ppp_password="!cmd"), "invalid PPP password")
        self.assertInvalid(pppoe_config(ppp_password=" lead"), "invalid PPP password")
        self.assertInvalid(pppoe_config(ppp_username="a b"), "invalid PPP username")
        self.assertInvalid(pppoe_config(ipv4=["1.2.3.4/32"]), "ip address negotiated")
        self.assertInvalid(pppoe_config(mtu=1500), "mtu")
        c = pppoe_config()
        c.interfaces["re0"].ppp_username = "x"
        self.assertInvalid(c, "only valid on dialer")

    def test_shared_pool_and_routes(self):
        c = pppoe_config()
        c.interfaces["dialer2"] = Interface("dialer2", negotiated=True, dialer_pool=1, ppp_username="u",
                                            ppp_password="p")
        self.assertInvalid(c, "same dialer pool")
        c = pppoe_config()
        c.routes.append(StaticRoute("10.0.0.0/8", interface="dialer9"))
        self.assertInvalid(c, "dialer9 is not configured")
        with self.assertRaises(ConfigError):
            StaticRoute("0.0.0.0/0", interface="re1").validate()      # default via a NIC needs a next hop

    def test_dns_warnings(self):
        self.assertTrue(any("upstream DNS" in w for w in pppoe_config(ppp_dns=False).validate()))
        c = pppoe_config()
        c.name_servers = ["1.1.1.1"]
        self.assertTrue(any("ignored" in w for w in c.validate()))

    def test_json_roundtrip(self):
        c = pppoe_config(pppoe_service="")
        self.assertEqual(RouterConfig.from_dict(json.loads(json.dumps(c.to_dict()))).to_dict(), c.to_dict())


class TestRender(unittest.TestCase):
    def test_ppp_conf(self):
        c = pppoe_config(mtu=1480, description="ISP")
        c.interfaces["re1.35"] = Interface("re1.35", pppoe_pool=1, pppoe_service="FTTH")
        c.routes.append(StaticRoute("100.64.0.0/10", interface="dialer1"))
        c.validate()
        txt = render_ppp.render(c)
        self.assertTrue(txt.startswith(render_ppp.HEADER))
        prof = txt.split("\ndialer1:\n", 1)[1]
        for line in (" set device PPPoE:re1 PPPoE:re1_35:FTTH", ' set authname "jane@isp"', ' set authkey "s3cret"',
                     " set mtu max 1480", " set mru max 1480", " set timeout 0", " set redial 10 0",
                     " iface name dialer1", ' iface description "ISP"', " enable dns", " add! default HISADDR",
                     " add! 100.64.0.0/10 HISADDR", " set ifaddr 192.0.2.1/0 192.0.2.2/0 0.0.0.0 0.0.0.0"):
            self.assertIn(line + "\n", prof)

    def test_quoting(self):
        self.assertEqual(render_ppp.quote('a"b\\c$d~e #f'), '"a\\"b\\\\c\\$d\\~e #f"')
        with self.assertRaises(ValueError):
            render_ppp.authkey("!prog")
        with self.assertRaises(ValueError):
            render_ppp.quote("bad\nline")

    def test_no_dns_when_name_servers(self):
        c = pppoe_config()
        c.name_servers = ["9.9.9.9"]
        self.assertNotIn("enable dns", render_ppp.render(c))

    def test_shutdown_drops_credentials(self):
        txt = render_ppp.render(pppoe_config(shutdown=True))
        self.assertNotIn("s3cret", txt)
        self.assertNotIn("set device", txt)

    def test_rcconf(self):
        c = pppoe_config()
        c.routes.append(StaticRoute("10.9.0.0/16", nexthop="192.168.1.254"))
        block = render_rcconf.render_block(c)
        for line in ('ppp_enable="YES"', 'ppp_mode="ddial"', 'ppp_nat="NO"', 'ppp_profile="dialer1"',
                     'ppp_dialer1_unit="1"', 'ifconfig_re1="up"', 'route_rcli0="-net 10.9.0.0/16 192.168.1.254"'):
            self.assertIn(line, block)
        self.assertNotIn("ifconfig_dialer1", block)
        self.assertNotIn("defaultrouter", block)
        self.assertIn('ppp_enable="NO"', render_rcconf.render_block(pppoe_config(shutdown=True)))
        self.assertNotIn("ppp_", render_rcconf.render_block(RouterConfig()))

    def test_pf(self):
        c = pppoe_config()
        c.acls["1"] = Acl.from_dict(
            {"name": "1", "kind": "standard", "entries": [{"seq": 10, "action": "permit", "src": "192.168.1.0/24"}]})
        c.nat_rules = [NatRule(kind="dynamic", acl="1", interface="dialer1", overload=True)]
        c.validate()
        pf = render_pf.render(c)
        self.assertIn('outside = "{ dialer1 }"', pf)
        self.assertIn("nat on dialer1 inet from 192.168.1.0/24 to any -> (dialer1)", pf)


class TestLiveSteps(unittest.TestCase):
    def argvs(self, old, new):
        return [" ".join(s.argv) for s in apply.live_steps(old, new, set())]

    def test_new_dialer_starts_ppp_only(self):
        steps = self.argvs(RouterConfig(), pppoe_config())
        self.assertIn("service ppp onestop dialer1", steps)
        self.assertIn("service ppp start dialer1", steps)
        self.assertLess(steps.index("service ppp onestop dialer1"), steps.index("service ppp start dialer1"))
        self.assertFalse([s for s in steps if "dialer1" in s and s.startswith(("ifconfig", "route"))], steps)

    def test_unrelated_change_keeps_session(self):
        new = pppoe_config()
        new.hostname = "other"
        new.interfaces["re0"].description = "LAN"
        self.assertFalse([s for s in self.argvs(pppoe_config(), new) if "ppp" in s])

    def test_dialer_change_restarts(self):
        self.assertIn("service ppp start dialer1", self.argvs(pppoe_config(), pppoe_config(ppp_password="new")))
        old = pppoe_config()
        new = pppoe_config()
        new.routes.append(StaticRoute("100.64.0.0/10", interface="dialer1"))
        self.assertIn("service ppp start dialer1", self.argvs(old, new))

    def test_shutdown_and_removal_stop(self):
        steps = self.argvs(pppoe_config(), pppoe_config(shutdown=True))
        self.assertIn("service ppp onestop dialer1", steps)
        self.assertNotIn("service ppp start dialer1", steps)
        new = pppoe_config()
        del new.interfaces["dialer1"]
        new.routes = []
        self.assertIn("service ppp onestop dialer1", self.argvs(pppoe_config(), new))

    def test_stop_waits_for_exit(self):
        st = apply.ppp_restart_steps("dialer1")[0]
        self.assertEqual(st.wait_exit, "^/usr/sbin/ppp.*[[:space:]]dialer1$")


class TestCliSandbox(unittest.TestCase):
    def setUp(self):
        self.sb = tempfile.mkdtemp(prefix="rcli-ppp-")
        os.makedirs(os.path.join(self.sb, "etc"))
        open(os.path.join(self.sb, "etc/rc.conf"), "w").close()
        self.env = dict(os.environ, ROUTERCLI_SANDBOX=self.sb, ROUTERCLI_IFACES="re0 re1")
        from routercli.auth import hash_secret
        req = json.dumps({"config": {"hostname": "r1", "enable_secret": hash_secret("Secret123")}})
        out = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "bin/router-apply"), "init"],
                             input=req, capture_output=True, text=True, env=self.env)
        self.assertIn('"ok": true', out.stdout)

    def tearDown(self):
        shutil.rmtree(self.sb, ignore_errors=True)

    def cli(self, *cmds):
        argv = [sys.executable, "-I", os.path.join(ROOT, "bin/router-cli"), "--privileged"]
        for c in cmds:
            argv += ["-c", c]
        return subprocess.run(argv, capture_output=True, text=True, env=self.env, timeout=60).stdout

    def path(self, rel):
        return os.path.join(self.sb, rel)

    SETUP = ("configure terminal",
             "interface re1", "pppoe enable group global", "pppoe-client dial-pool-number 1", "exit",
             "interface Dialer1", "ip address negotiated", "ip nat outside", "encapsulation ppp", "dialer pool 1",
             "ppp chap hostname jane@isp", 'ppp chap password 0 pa ss"$1', "ppp ipcp dns request", "exit",
             "ip route 0.0.0.0 0.0.0.0 Dialer1", "commit", "end")

    def test_configure_show_and_remove(self):
        out = self.cli(*self.SETUP)
        self.assertNotIn("%", out)
        self.assertIn("Commit 2 applied", out)
        self.assertEqual(os.stat(self.path("etc/ppp/ppp.conf")).st_mode & 0o777, 0o600)
        with open(self.path("etc/ppp/ppp.conf")) as f:
            self.assertIn(' set authkey "pa ss\\"\\$1"', f.read())
        run = self.cli("show running-config")
        enc = type7_encode('pa ss"$1')
        for line in ("interface dialer1", " ip address negotiated", " dialer pool 1", " ppp chap hostname jane@isp",
                     f" ppp chap password 7 {enc}", " pppoe-client dial-pool-number 1",
                     "ip route 0.0.0.0 0.0.0.0 dialer1"):
            self.assertIn(line + "\n", run)
        self.assertNotIn("pa ss", run)
        self.assertIn("dialer1", self.cli("show pppoe session"))
        self.assertIn("Restarted PPPoE dialer1", self.cli("clear interface dialer1"))

        out = self.cli("configure terminal", "no ip route 0.0.0.0 0.0.0.0 dialer1", "no interface dialer1",
                       "interface re1", "no pppoe enable", "exit", "commit", "end")
        self.assertNotIn("% Commit failed", out)
        with open(self.path("etc/ppp/ppp.conf")) as f:
            self.assertNotIn("pa ss", f.read())          # credentials scrubbed from ppp.conf
        with open(self.path("var/db/router-cli/sandbox-commands.log")) as f:
            self.assertIn("service ppp onestop dialer1", f.read())

    def test_type7_paste(self):
        enc = type7_encode("Hunter2!")
        out = self.cli("configure terminal", "interface dialer3", f"ppp chap password 7 {enc}",
                       "ppp chap password 7 ZZ", "end")
        self.assertIn("% Invalid encrypted", out)
        self.assertEqual(out.count("%"), 2)        # the bad one + 'Uncommitted changes discarded'

    def test_errors(self):
        out = self.cli("configure terminal", "interface dialer1", "ip address 1.2.3.4 255.255.255.0",
                       "interface re0", "dialer pool 1", "interface dialer1.5", "end")
        self.assertIn("use 'ip address negotiated'", out)
        self.assertIn("Only valid on dialer interfaces", out)
        self.assertIn("Dialer interfaces have no subinterfaces", out)
        out = self.cli("configure terminal", "interface dialer1", "ip address negotiated", "commit", "end")
        self.assertIn("% Commit failed: dialer1 is incomplete", out)


if __name__ == "__main__":
    unittest.main()
