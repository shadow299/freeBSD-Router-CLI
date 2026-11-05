"""Run every example in docs/EXAMPLES.md against the sandbox.

Fenced blocks tagged ```cli are typed into router-cli (starting at 'router#') and must not
produce any '%' message. Blocks tagged ```cli-error must produce one (the "common mistakes"
section). All blocks under the same '## ' heading share one sandbox, so a section can build on
its earlier blocks; each new section starts from a fresh, empty configuration. A line
`<!-- fresh -->` also starts a fresh configuration (used for "the second router" examples).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "EXAMPLES.md")
IFACES = "re0 re1"


def parse_examples(path=DOC):
    """Yield (section, line_no, kind, [lines]) for every cli / cli-error block."""
    section, block, kind, start = "", None, None, 0
    with open(path, encoding="utf-8") as f:
        for no, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if block is None:
                if line.startswith("## "):
                    section = line[3:].strip()
                elif line.strip() == "<!-- fresh -->":
                    section = f"{section} @{no}"
                m = re.fullmatch(r"```(cli|cli-error)\s*", line)
                if m:
                    block, kind, start = [], m.group(1), no
            elif line.startswith("```"):
                yield section, start, kind, block
                block = None
            else:
                block.append(line)


class SectionSandbox:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="rcli-ex-")
        os.makedirs(os.path.join(self.dir, "etc"))
        open(os.path.join(self.dir, "etc/rc.conf"), "w").close()
        self.env = dict(os.environ, ROUTERCLI_SANDBOX=self.dir, ROUTERCLI_IFACES=IFACES)
        sys.path.insert(0, ROOT)
        from routercli.auth import hash_secret
        req = json.dumps({"config": {"hostname": "router", "enable_secret": hash_secret("Example123")}})
        out = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "bin/router-apply"), "init"],
                             input=req, capture_output=True, text=True, env=self.env)
        assert '"ok": true' in out.stdout, out.stdout + out.stderr

    def run(self, lines):
        argv = [sys.executable, "-I", os.path.join(ROOT, "bin/router-cli"), "--privileged"]
        for l in lines:
            argv += ["-c", l]
        return subprocess.run(argv, capture_output=True, text=True, env=self.env, timeout=120).stdout

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestExamples(unittest.TestCase):
    def test_examples_doc(self):
        examples = list(parse_examples())
        self.assertGreater(len(examples), 20)
        sandboxes = {}
        try:
            for section, line_no, kind, lines in examples:
                with self.subTest(section=section, line=line_no):
                    sb = sandboxes.get(section)
                    if sb is None:
                        sb = sandboxes[section] = SectionSandbox()
                    out = sb.run(lines)
                    errors = [l for l in out.splitlines() if l.lstrip().startswith("%")]
                    if kind == "cli":
                        self.assertEqual(errors, [], f"EXAMPLES.md:{line_no}\n{out}")
                    else:
                        self.assertNotEqual(errors, [], f"EXAMPLES.md:{line_no} should fail\n{out}")
        finally:
            for sb in sandboxes.values():
                sb.close()


if __name__ == "__main__":
    unittest.main()
