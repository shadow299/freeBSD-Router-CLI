"""Client side of the configuration store.

The CLI runs unprivileged. It *reads* state files directly (they are group
readable by 'router') and asks the privileged helper to *change* anything.
"""

import json
import os
import subprocess
import sys

from routercli.backend import paths
from routercli.config.model import RouterConfig

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except PermissionError:
        raise RuntimeError(f"permission denied reading {path} (is this user in the 'router' group?)")


class Store:
    def __init__(self):
        self.sandbox = bool(paths.SANDBOX)

    # -- privileged operations ---------------------------------------------
    def _helper_argv(self) -> list:
        if self.sandbox:
            return [sys.executable, "-I", os.path.join(REPO_ROOT, "bin", "router-apply")]
        if os.geteuid() == 0:                       # root on the console: no doas rule needed
            return [sys.executable, "-I", paths.HELPER]
        return [paths.DOAS, "-n", paths.HELPER]

    def call(self, op: str, *args, payload=None, timeout=300) -> dict:
        try:
            cp = subprocess.run(self._helper_argv() + [op, *args], input=json.dumps(payload or {}),
                                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "backend timed out"}
        except FileNotFoundError as exc:
            return {"ok": False, "error": f"backend not installed: {exc}"}
        try:
            return json.loads(cp.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return {"ok": False, "error": (cp.stderr or cp.stdout or "backend failed").strip()}

    def commit(self, cfg: RouterConfig, comment="", confirm_minutes=None, force=False) -> dict:
        return self.call("commit", payload={"config": cfg.to_dict(), "comment": comment,
                                            "confirm_minutes": confirm_minutes, "force": force})

    def exec(self, name: str) -> dict:
        return self.call("exec", name)

    # -- read-only ---------------------------------------------------------
    def running(self) -> RouterConfig:
        d = _load(paths.RUNNING)
        return RouterConfig.from_dict(d) if d else RouterConfig()

    def startup(self):
        d = _load(paths.STARTUP)
        return RouterConfig.from_dict(d) if d else None

    def pending(self):
        return _load(paths.PENDING_CONFIRM)

    def archive_list(self) -> list:
        out = []
        try:
            names = sorted(os.listdir(paths.ARCHIVE_DIR))
        except FileNotFoundError:
            return out
        for name in names:
            rec = _load(os.path.join(paths.ARCHIVE_DIR, name))
            if rec:
                out.append({k: rec.get(k) for k in ("id", "time", "user", "comment")})
        return out

    def archive_get(self, ident: int):
        rec = _load(os.path.join(paths.ARCHIVE_DIR, f"{ident:06d}.json"))
        return RouterConfig.from_dict(rec["config"]) if rec else None
