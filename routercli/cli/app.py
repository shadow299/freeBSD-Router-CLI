"""Interactive session and REPL."""

import difflib
import getpass
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback

from routercli import __version__
from routercli.auth import verify_secret
from routercli.backend.system import SAFE_ENV
from routercli.cli import modes as M
from routercli.cli.commands import registry
from routercli.cli.parser import ParseError, match, options
from routercli.config import showrun
from routercli.config.model import ConfigError
from routercli.config.store import Store

FILTERS = ("include", "exclude", "begin", "count", "section")
PIPE_RE = re.compile(r"\s\|\s*(\S+)\s*(.*)$")


class CliError(Exception):
    """Raised by handlers to print a '% ...' message without a traceback."""


class Session:
    def __init__(self, store: Store, interactive: bool):
        self.store = store
        self.interactive = interactive
        self.running = store.running()
        self.candidate = None
        self.stack = [(M.EXEC, None)]
        self.term_length = (shutil.get_terminal_size().lines - 1) if interactive and sys.stdout.isatty() else 0
        self.buf = []
        self.history = []
        self.quit = False
        self.user = getpass.getuser()
        self._ifcache = (0.0, [])

    # -- mode handling ------------------------------------------------------
    @property
    def mode(self):
        return self.stack[-1][0]

    @property
    def context(self):
        return self.stack[-1][1]

    def push(self, mode, ctx=None):
        self.stack.append((mode, ctx))

    def to_mode(self, mode):
        while self.mode != mode and len(self.stack) > 1:
            self.stack.pop()

    def prompt(self) -> str:
        return M.prompt(self.candidate_or_running().hostname, self.mode, self.context)

    # -- config access ------------------------------------------------------
    @property
    def cfg(self):
        if self.candidate is None:
            self.candidate = self.running.copy()
        return self.candidate

    def candidate_or_running(self):
        return self.candidate if self.candidate is not None else self.running

    def dirty(self) -> bool:
        return self.candidate is not None and self.candidate.to_dict() != self.running.to_dict()

    def refresh(self):
        self.running = self.store.running()

    # -- output -------------------------------------------------------------
    def print(self, *lines):
        for line in lines:
            self.buf.extend(str(line).split("\n"))

    def error(self, msg):
        raise CliError(msg)

    def confirm(self, question: str, default: bool = True) -> bool:
        self.flush()
        if not self.interactive:
            # scripts (-c / piped stdin) must not consume the next command line
            print(question + ("yes" if default else "no"))
            return default
        try:
            ans = input(question).strip().lower()
        except EOFError:
            return False
        if not ans:
            return default
        return ans in ("y", "yes", "confirm")

    def ask(self, question: str) -> str:
        self.flush()
        try:
            return input(question).strip().lower()
        except EOFError:
            return ""

    def flush(self, filt=None):
        lines, self.buf = self.buf, []
        if filt:
            lines = apply_filter(lines, *filt)
        while lines and lines[-1] == "":
            lines.pop()
        page(lines, self.term_length)

    # -- system helpers -----------------------------------------------------
    def run_local(self, argv, timeout=30) -> str:
        try:
            cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=SAFE_ENV,
                                stdin=subprocess.DEVNULL)
            return (cp.stdout or "") + (cp.stderr or "")
        except FileNotFoundError:
            return f"% {argv[0]}: command not available on this system"
        except subprocess.TimeoutExpired:
            return "% command timed out"

    def run_interactive(self, argv):
        """Stream a long-running command (ping/traceroute) to the terminal; Ctrl-C stops it."""
        self.flush()
        try:
            proc = subprocess.Popen(argv, env=SAFE_ENV, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            self.print(f"% {argv[0]}: command not available on this system")
            return
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.send_signal(signal.SIGINT)
            proc.wait()

    def helper(self, name):
        res = self.store.exec(name)
        out = res.get("output") or res.get("error") or ""
        self.print(out.rstrip() or "%% no output")

    def interface_names(self):
        now = time.time()
        if now - self._ifcache[0] > 30:
            names = []
            if shutil.which("ifconfig", path=SAFE_ENV["PATH"]) and not self.store.sandbox:
                names = self.run_local(["ifconfig", "-l"]).split()
            elif self.store.sandbox:
                names = os.environ.get("ROUTERCLI_IFACES", "em0 em1 em2 lo0").split()
            self._ifcache = (now, names)
        names = set(self._ifcache[1]) | set(self.candidate_or_running().interfaces)
        return sorted(n for n in names if n != "lo0")

    # -- enable / commit -----------------------------------------------------
    def do_enable(self):
        self.refresh()
        secret = self.running.enable_secret
        if not secret:
            self.error("% No password set")
        for _ in range(3):
            self.flush()
            try:
                pw = getpass.getpass("Password: ")
            except EOFError:
                break
            if verify_secret(pw, secret):
                self.push(M.PRIV)
                return
            time.sleep(1.5)
        self.error("% Bad secrets")

    def commit(self, comment="", confirm_minutes=None, force=False):
        cfg = self.candidate_or_running()
        try:
            warnings = cfg.copy().validate()
        except ConfigError as exc:
            self.error(f"% Commit failed: {exc}")
        for w in warnings:
            self.print(f"% Warning: {w}")
        self.flush()
        res = self.store.commit(cfg, comment=comment, confirm_minutes=confirm_minutes, force=force)
        if not res.get("ok"):
            self.error("% Commit failed; configuration NOT applied, system unchanged:\n" + res.get("error", ""))
        for w in res.get("warnings", []):
            if w not in warnings:
                self.print(f"% Warning: {w}")
        if res.get("changed"):
            self.print("Updated: " + ", ".join(res["changed"]))
        self.print(res.get("message", "Commit complete."))
        self.refresh()
        if self.candidate is not None:
            self.candidate = self.running.copy()

    def diff_text(self, old, new, a="running-config", b="candidate-config"):
        d = difflib.unified_diff(showrun.render(old).splitlines(), showrun.render(new).splitlines(),
                                 a, b, lineterm="", n=2)
        return "\n".join(d) or "No differences."

    def leave_config(self) -> bool:
        """Handle uncommitted changes when leaving configuration mode. Returns False to stay."""
        if self.dirty():
            if not self.interactive:
                self.print("% Uncommitted changes discarded (use 'commit' before 'end').")
                self.candidate = None
                return True
            ans = self.ask("Uncommitted changes found, commit them before exiting(yes/no/cancel)? [cancel]: ")
            if ans in ("y", "yes"):
                try:
                    self.commit()
                except CliError as exc:
                    self.print(str(exc))
                    return False
            elif ans not in ("n", "no"):
                return False
        self.candidate = None
        return True

    # -- dispatch -------------------------------------------------------------
    def _resolve(self, tokens, mode):
        negated = False
        if mode in M.CONFIG_MODES and tokens and tokens[0].lower() == "do":
            if len(tokens) == 1:
                raise ParseError("% Incomplete command.", 1)
            cd, args = match(registry.entries(M.PRIV), tokens[1:], self)
            return cd, args, False, M.PRIV, 1
        if tokens and tokens[0].lower() == "no" and mode in M.CONFIG_MODES:
            negated, tokens = True, tokens[1:]
            if not tokens:
                raise ParseError("% Incomplete command.", 1)
        shift = 1 if negated else 0
        try:
            cd, args = match(registry.entries(mode, negated), tokens, self)
            return cd, args, negated, mode, shift
        except ParseError as exc:
            if mode in M.SUB_MODES:
                try:
                    cd, args = match(registry.entries(M.CONFIG, negated), tokens, self)
                    return cd, args, negated, M.CONFIG, shift
                except ParseError:
                    pass
            exc.pos += shift
            raise

    def execute(self, line: str):
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped or stripped.startswith("!"):
            return
        self.history.append(stripped)
        filt = None
        m = PIPE_RE.search(raw)
        if m:
            kind = [f for f in FILTERS if f.startswith(m.group(1).lower())]
            if len(kind) != 1:
                self.print("% Invalid output modifier. Use: include | exclude | begin | section | count")
                self.flush()
                return
            filt = (kind[0], m.group(2))
            raw = raw[:m.start()]
        tokens = raw.split()
        prompt_len = len(self.prompt())
        try:
            cd, args, negated, target_mode, _ = self._resolve(tokens, self.mode)
            if target_mode == M.CONFIG and self.mode in M.SUB_MODES:
                self.to_mode(M.CONFIG)
            cd.handler(self, args, negated)
        except ParseError as exc:
            if "Invalid input" in str(exc) and exc.pos >= 0:
                offs = [mm.start() for mm in re.finditer(r"\S+", raw)]
                col = offs[exc.pos] if exc.pos < len(offs) else len(raw)
                if self.interactive:
                    self.print(" " * (prompt_len + col) + "^")
                else:
                    self.print(raw, " " * col + "^")
            self.print(str(exc))
            filt = None
        except CliError as exc:
            self.print(str(exc))
            filt = None
        except ConfigError as exc:
            self.print(f"% {exc}")
            filt = None
        except KeyboardInterrupt:
            self.print("^C")
        except Exception as exc:                     # never drop the user to a shell or crash
            self.print(f"% Internal error: {exc}")
            if os.environ.get("ROUTERCLI_DEBUG"):
                self.print(traceback.format_exc())
        self.flush(filt)

    # -- completion ------------------------------------------------------------
    def completions(self, text: str):
        """Return (options, cr_ok, partial) for the text before the cursor."""
        tokens = text.split()
        partial = ""
        if tokens and not text.endswith(" "):
            partial = tokens.pop()
        mode = self.mode
        extra = []
        if mode in M.CONFIG_MODES:
            if tokens and tokens[0].lower() == "do":
                opts, cr = options(registry.entries(M.PRIV), tokens[1:], self)
            elif tokens and tokens[0].lower() == "no":
                opts, cr = options(registry.entries(mode, True), tokens[1:], self)
            else:
                opts, cr = options(registry.entries(mode), tokens, self)
                if not tokens:
                    extra = [("do", "To run exec commands in config mode", True),
                             ("no", "Negate a command or set its defaults", True)]
        else:
            opts, cr = options(registry.entries(mode), tokens, self)
        opts = sorted(opts + extra, key=lambda o: (not o[2], o[0].lower()))
        if partial:
            opts = [o for o in opts if o[2] and o[0].lower().startswith(partial.lower())]
            cr = False
        return opts, cr, partial

    def help_text(self, text: str) -> str:
        opts, cr, partial = self.completions(text)
        if partial:
            words = [o[0] for o in opts]
            return "  ".join(words) if words else "% Unrecognized command"
        if not opts and not cr:
            return "% Unrecognized command"
        width = max([len(o[0]) for o in opts] + [4]) + 2
        lines = [f"  {w:<{width}}{h}" for w, h, kw in opts if kw or not w.islower()]
        if cr:
            lines.append("  <cr>")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def apply_filter(lines, kind, pattern):
    try:
        rx = re.compile(pattern)
    except re.error:
        return ["% Invalid regular expression"]
    if kind == "include":
        return [l for l in lines if rx.search(l)]
    if kind == "exclude":
        return [l for l in lines if not rx.search(l)]
    if kind == "begin":
        for i, l in enumerate(lines):
            if rx.search(l):
                return lines[i:]
        return []
    if kind == "count":
        return [f"Number of lines which match regexp = {sum(1 for l in lines if rx.search(l))}"]
    out, keep = [], False
    for l in lines:                                    # section
        if l and not l[0].isspace():
            keep = bool(rx.search(l))
        if keep:
            out.append(l)
    return out


def _getch():
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def page(lines, length):
    if not lines:
        return
    if length <= 0 or not sys.stdout.isatty() or len(lines) <= length:
        print("\n".join(lines), flush=True)
        return
    i, step = 0, length
    while i < len(lines):
        print("\n".join(lines[i:i + step]), flush=True)
        i += step
        if i >= len(lines):
            break
        sys.stdout.write(" --More-- ")
        sys.stdout.flush()
        ch = _getch()
        sys.stdout.write("\r          \r")
        if ch in ("q", "Q", "\x03"):
            break
        step = 1 if ch in ("\r", "\n") else length


# ---------------------------------------------------------------------------
# Input front-ends
# ---------------------------------------------------------------------------

def _history_path():
    # The router account's home is root-owned; setup pre-creates this file owned by 'router'.
    home = os.path.expanduser("~")
    hp = os.path.join(home, ".router_cli_history")
    if os.access(hp, os.W_OK) or (not os.path.exists(hp) and os.access(home, os.W_OK)):
        return hp
    return None


def loop_prompt_toolkit(s: Session):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import run_in_terminal
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings

    class CiscoCompleter(Completer):
        def get_completions(self, document, complete_event):
            opts, _, partial = s.completions(document.text_before_cursor)
            for word, helptext, is_kw in opts:
                if is_kw:
                    yield Completion(word, start_position=-len(partial), display_meta=helptext)

    kb = KeyBindings()

    @kb.add("?")
    def _(event):
        buf = event.app.current_buffer
        text = buf.document.text_before_cursor
        msg = s.help_text(text)
        run_in_terminal(lambda: print(f"{s.prompt()}{text}?\n{msg}"))

    @kb.add("c-z")
    def _(event):
        if s.mode in M.CONFIG_MODES:
            event.app.current_buffer.text = "end"
            event.app.current_buffer.validate_and_handle()

    hp = _history_path()
    ps = PromptSession(history=FileHistory(hp) if hp else InMemoryHistory(), completer=CiscoCompleter(),
                       key_bindings=kb, complete_while_typing=False, enable_suspend=False)
    while not s.quit:
        try:
            line = ps.prompt(s.prompt())
        except KeyboardInterrupt:
            continue
        except EOFError:
            if s.mode in M.EXEC_MODES:
                break
            line = "end"
        s.execute(line)


def loop_readline(s: Session):
    try:
        import readline
    except ImportError:
        readline = None
    if readline:
        def completer(text, state):
            buf = readline.get_line_buffer()[:readline.get_endidx()]
            opts = [o[0] + " " for o in s.completions(buf)[0] if o[2]]
            return opts[state] if state < len(opts) else None
        readline.set_completer_delims(" ")
        readline.set_completer(completer)
        readline.parse_and_bind("tab: complete")
        hp = _history_path()
        if hp and os.path.exists(hp):
            try:
                readline.read_history_file(hp)
            except OSError:
                pass
    while not s.quit:
        try:
            line = input(s.prompt())
        except KeyboardInterrupt:
            print()
            continue
        except EOFError:
            print()
            break
        if line.rstrip().endswith("?"):
            print(s.help_text(line.rstrip()[:-1]))
            continue
        s.execute(line)
    if readline and _history_path():
        try:
            readline.write_history_file(_history_path())
        except OSError:
            pass


def loop_script(s: Session, lines):
    for line in lines:
        if s.quit:
            break
        print(f"{s.prompt()}{line.rstrip()}")
        if line.rstrip().endswith("?"):
            print(s.help_text(line.rstrip()[:-1]))
            continue
        s.execute(line)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="router-cli", add_help=True)
    ap.add_argument("-c", "--command", action="append", help="run command(s) non-interactively")
    ap.add_argument("--privileged", action="store_true", help=argparse.SUPPRESS)
    args, _unknown = ap.parse_known_args(argv)        # sshd may pass '-c <cmd>' for forced shells

    for sig in (signal.SIGTSTP, signal.SIGQUIT):
        signal.signal(sig, signal.SIG_IGN)

    # As a login shell under 'ForceCommand /usr/local/bin/router-cli', sshd runs
    # "router-cli -c /usr/local/bin/router-cli" and puts the client's requested
    # command (ssh router@host 'show run') in SSH_ORIGINAL_COMMAND.
    if args.command:
        self_names = {"router-cli", os.path.basename(sys.argv[0] or "").lstrip("-")}
        args.command = [c for c in args.command if os.path.basename(c.strip()) not in self_names] or None
        if args.command is None and os.environ.get("SSH_ORIGINAL_COMMAND"):
            args.command = [os.environ["SSH_ORIGINAL_COMMAND"]]

    interactive = sys.stdin.isatty() and not args.command
    try:
        s = Session(Store(), interactive)
    except Exception as exc:
        print(f"% router-cli cannot start: {exc}")
        return 1
    if args.privileged and s.store.sandbox:
        s.push(M.PRIV)

    if s.running.banner_motd and interactive:
        print(s.running.banner_motd)
    pending = s.store.pending()
    if pending and interactive:
        left = int(pending.get("deadline", 0) - time.time())
        print(f"% NOTICE: a 'commit confirmed' is pending - automatic rollback in {max(left, 0)}s "
              f"unless confirmed with 'commit'.")

    if args.command:
        loop_script(s, [c for cmd in args.command for c in cmd.split(";")])
    elif interactive:
        print(f"\nrouter-cli {__version__} - {s.running.hostname}. Type '?' for help.\n")
        try:
            import prompt_toolkit  # noqa: F401  (availability probe)
            loop_prompt_toolkit(s)
        except ImportError:
            loop_readline(s)
    else:
        loop_script(s, sys.stdin)
    return 0
