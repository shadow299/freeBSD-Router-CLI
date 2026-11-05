"""router-apply: the privileged backend.

This is the ONLY part of router-cli that runs as root. The unprivileged CLI
(running as the 'router' user) invokes it through a single doas(1) rule:

    permit nopass router as root cmd /usr/local/libexec/router-apply

Input is a JSON document on stdin, output is a JSON document on stdout. The
configuration is re-parsed and strictly re-validated here, and only
whitelisted operational commands can be executed.
"""

import json
import os
import pwd
import secrets
import subprocess
import sys
import time

from routercli.backend import apply, paths
from routercli.backend.system import CommandError, Runner, write_atomic
from routercli.config.model import ConfigError, RouterConfig

MAX_INPUT = 8 * 1024 * 1024

# Operational commands the CLI may request. name -> (argv, mutating)
EXEC = {
    "nat-translations": (["pfctl", "-s", "state"], False),
    "pf-rules": (["pfctl", "-v", "-s", "rules"], False),
    "pf-nat": (["pfctl", "-s", "nat"], False),
    "pf-info": (["pfctl", "-s", "info"], False),
    "ospf-neighbor": (["vtysh", "-c", "show ip ospf neighbor"], False),
    "ospf-interface": (["vtysh", "-c", "show ip ospf interface"], False),
    "ospf-database": (["vtysh", "-c", "show ip ospf database"], False),
    "rip-status": (["vtysh", "-c", "show ip rip status"], False),
    "rip-routes": (["vtysh", "-c", "show ip rip"], False),
    "bgp-summary": (["vtysh", "-c", "show bgp summary"], False),
    "bgp-table": (["vtysh", "-c", "show bgp ipv4 unicast"], False),
    "frr-route": (["vtysh", "-c", "show ip route"], False),
    "logging": (["tail", "-n", "200", "/var/log/messages"], False),
    "clear-nat": (["pfctl", "-F", "states"], True),
    "clear-arp": (["arp", "-d", "-a"], True),
    "clear-ospf": (["vtysh", "-c", "clear ip ospf process"], True),
    "clear-bgp": (["vtysh", "-c", "clear bgp *"], True),
    "clear-counters": (["pfctl", "-z"], True),
    "reload": (["shutdown", "-r", "now", "router-cli reload"], True),
}


def _user() -> str:
    return os.environ.get("DOAS_USER") or os.environ.get("SUDO_USER") or pwd.getpwuid(os.getuid()).pw_name


def _read_request() -> dict:
    data = sys.stdin.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        raise ConfigError("request too large")
    return json.loads(data or "{}")


def _syslog(msg: str) -> None:
    Runner().run(["logger", "-t", "router-cli", "-p", "auth.notice", msg], check=False)


def _spawn_timer(token: str, seconds: int) -> None:
    subprocess.Popen([sys.executable, "-I", os.path.abspath(sys.argv[0]), "confirm-timer", token, str(seconds)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)


def _rollback_pending(reason: str) -> dict:
    pending = apply.load_json(paths.PENDING_CONFIRM)
    prev = RouterConfig.from_dict(pending["previous"])
    rep = apply.commit(prev, "system", reason)
    os.unlink(paths.PENDING_CONFIRM)
    _syslog(reason)
    return {"ok": True, "message": reason, "warnings": rep.warnings}


def cmd_commit(req: dict) -> dict:
    cfg = RouterConfig.from_dict(req.get("config"))
    minutes = req.get("confirm_minutes")
    comment = str(req.get("comment", ""))[:200]
    with apply.state_lock():
        pending = apply.load_json(paths.PENDING_CONFIRM)
        running = apply.load_config(paths.RUNNING)
        if running and running.to_dict() == cfg.to_dict() and not req.get("force"):
            if pending and not minutes:
                os.unlink(paths.PENDING_CONFIRM)
                _syslog(f"commit confirmed by {_user()}")
                return {"ok": True, "message": "Commit confirmed; automatic rollback cancelled."}
            return {"ok": True, "message": "No configuration changes to commit."}
        prev = running or RouterConfig()
        rep = apply.commit(cfg, _user(), comment, full=bool(req.get("force")))
        msg = f"Commit {rep.archive_id} applied."
        if minutes:
            minutes = max(1, min(int(minutes), 60))
            token = secrets.token_hex(8)
            rec = {"token": token, "deadline": time.time() + minutes * 60, "user": _user(),
                   "previous": pending["previous"] if pending else prev.to_dict()}
            write_atomic(paths.PENDING_CONFIRM, json.dumps(rec), 0o640)
            _spawn_timer(token, minutes * 60)
            msg += f" Automatic rollback in {minutes} minute(s) unless confirmed with 'commit'."
        elif pending:
            os.unlink(paths.PENDING_CONFIRM)
            msg += " Pending commit-confirmed rollback cancelled."
        _syslog(f"commit {rep.archive_id} by {_user()}: {comment}")
        return {"ok": True, "message": msg, "warnings": rep.warnings,
                "changed": rep.changed_files, "executed": rep.executed}


def cmd_confirm_timer(token: str, seconds: str) -> dict:
    time.sleep(int(seconds))
    with apply.state_lock():
        pending = apply.load_json(paths.PENDING_CONFIRM)
        if not pending or pending.get("token") != token:
            return {"ok": True, "message": "already confirmed"}
        return _rollback_pending("automatic rollback: commit confirmed was not confirmed in time")


def cmd_boot() -> dict:
    with apply.state_lock():
        if apply.load_json(paths.PENDING_CONFIRM):
            return _rollback_pending("boot-time rollback of unconfirmed commit")
    return {"ok": True, "message": "nothing to do"}


def cmd_save_startup() -> dict:
    with apply.state_lock():
        running = apply.load_config(paths.RUNNING)
        if running is None:
            return {"ok": False, "error": "no running configuration has been committed yet"}
        apply.save_config(paths.STARTUP, running)
    _syslog(f"startup-config saved by {_user()}")
    return {"ok": True, "message": "[OK]"}


def cmd_erase_startup() -> dict:
    with apply.state_lock():
        if os.path.exists(paths.STARTUP):
            os.unlink(paths.STARTUP)
    return {"ok": True, "message": "Erase of startup-config: complete"}


def cmd_init(req: dict) -> dict:
    """Create the initial configuration (setup script only)."""
    with apply.state_lock():
        if os.path.exists(paths.RUNNING) and not req.get("force"):
            return {"ok": False, "error": "already initialised (running-config exists)"}
        cfg = RouterConfig.from_dict(req.get("config", {}))
        cfg.validate()
        os.makedirs(paths.ARCHIVE_DIR, exist_ok=True)
        apply.save_config(paths.RUNNING, cfg)
        apply.save_config(paths.STARTUP, cfg)
        apply.archive(cfg, _user(), "initial configuration")
    return {"ok": True, "message": "initialised"}


def cmd_exec(name: str) -> dict:
    if name not in EXEC:
        return {"ok": False, "error": f"operation {name!r} not permitted"}
    argv, mutating = EXEC[name]
    if mutating:
        _syslog(f"{_user()} executed {name}")
    res = Runner().run(argv, mutating=mutating, check=False, timeout=60)
    return {"ok": res.rc == 0, "output": res.output, "error": "" if res.rc == 0 else res.output}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    os.umask(0o027)
    if not paths.SANDBOX and os.geteuid() != 0:
        print(json.dumps({"ok": False, "error": "router-apply must run as root (via doas)"}))
        return 1
    if not args:
        print("usage: router-apply {commit|save-startup|erase-startup|exec NAME|boot|init}", file=sys.stderr)
        return 2
    cmd = args[0]
    try:
        if cmd == "commit":
            out = cmd_commit(_read_request())
        elif cmd == "save-startup":
            out = cmd_save_startup()
        elif cmd == "erase-startup":
            out = cmd_erase_startup()
        elif cmd == "exec" and len(args) == 2:
            out = cmd_exec(args[1])
        elif cmd == "confirm-timer" and len(args) == 3:
            out = cmd_confirm_timer(args[1], args[2])
        elif cmd == "boot":
            out = cmd_boot()
        elif cmd == "init":
            out = cmd_init(_read_request())
        else:
            out = {"ok": False, "error": f"unknown operation {cmd!r}"}
    except (ConfigError, apply.ApplyError, CommandError) as exc:
        out = {"ok": False, "error": str(exc)}
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        out = {"ok": False, "error": f"bad request: {exc}"}
    print(json.dumps(out))
    if cmd == "boot":
        print(out.get("message") or out.get("error"), file=sys.stderr)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
