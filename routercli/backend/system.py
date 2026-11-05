"""Safe subprocess execution and atomic file I/O.

* Commands are always executed with an argv list (never a shell string).
* Environment is reset to a minimal, known PATH.
* In sandbox mode (ROUTERCLI_SANDBOX) mutating commands are only logged, and
  validation commands run only if the tool exists on the host.
"""

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass

from routercli.backend import paths

SAFE_ENV = {"PATH": "/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin", "LC_ALL": "C"}


class CommandError(RuntimeError):
    def __init__(self, argv, rc, output):
        super().__init__(f"command failed (rc={rc}): {' '.join(argv)}\n{output.strip()}")
        self.argv, self.rc, self.output = argv, rc, output


@dataclass
class Result:
    rc: int
    output: str
    skipped: bool = False


class Runner:
    def __init__(self, sandbox: bool = bool(paths.SANDBOX)):
        self.sandbox = sandbox
        self.log = []                 # human-readable record of what was executed

    def _log_sandbox(self, argv):
        os.makedirs(os.path.dirname(paths.COMMAND_LOG), exist_ok=True)
        with open(paths.COMMAND_LOG, "a") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {' '.join(argv)}\n")

    def run(self, argv, *, mutating=True, check=True, timeout=120, input_=None) -> Result:
        self.log.append(" ".join(argv))
        if self.sandbox and mutating:
            self._log_sandbox(argv)
            return Result(0, "", skipped=True)
        if self.sandbox and shutil.which(argv[0], path=SAFE_ENV["PATH"]) is None:
            return Result(0, f"(sandbox: {argv[0]} not available, skipped)", skipped=True)
        try:
            cp = subprocess.run(argv, input=input_, capture_output=True, text=True,
                                timeout=timeout, env=SAFE_ENV, stdin=None if input_ else subprocess.DEVNULL)
        except FileNotFoundError:
            if check:
                raise CommandError(argv, 127, f"{argv[0]}: not found")
            return Result(127, f"{argv[0]}: not found")
        except subprocess.TimeoutExpired:
            raise CommandError(argv, -1, f"timed out after {timeout}s")
        out = (cp.stdout or "") + (cp.stderr or "")
        if check and cp.returncode != 0:
            raise CommandError(argv, cp.returncode, out)
        return Result(cp.returncode, out)


def read_file(path: str) -> str:
    try:
        with open(path) as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def write_atomic(path: str, content: str, mode: int = 0o644) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    try:
        mode = os.stat(path).st_mode & 0o7777
    except FileNotFoundError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=".rcli-", dir=d)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    dfd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def write_temp(content: str, suffix: str) -> str:
    fd, tmp = tempfile.mkstemp(prefix="rcli-validate-", suffix=suffix)
    with os.fdopen(fd, "w") as fh:
        fh.write(content)
    return tmp
