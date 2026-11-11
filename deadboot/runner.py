"""The one place every external tool call goes through.

Commands are argument lists, never shell strings. The runner adds the device
serial, streams output to the log pane, writes device commands to the action
log, and supports a dry-run mode that prints instead of executing.

Two ways to run:
  run_blocking(cmd)  for worker threads (QThread jobs) and tests
  start(cmd, ...)    asynchronous, for long-lived streams like logcat and scrcpy
"""

from __future__ import annotations

import os
import shlex
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

from . import hostplatform
from .actionlog import ActionLog

SERIAL_FLAG = {"adb": ["-s"], "fastboot": ["-s"], "scrcpy": ["--serial"]}
DEVICE_TOOLS = {"adb", "fastboot", "heimdall", "scrcpy", "script"}


class ToolMissing(RuntimeError):
    def __init__(self, tool: str):
        super().__init__(f"{tool} was not found. Install it or set its path in Settings.")
        self.tool = tool


@dataclass
class Command:
    tool: str                       # adb, fastboot, heimdall, scrcpy, ssh-keygen, script, ...
    args: list[str]
    serial: str | None = None
    writes: bool = False            # changes the device: skipped in dry-run
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    stdin_file: str | None = None
    timeout_s: float | None = None
    note: str = ""

    def script_path(self) -> str:
        return self.args[0] if self.tool == "script" else ""


@dataclass
class Result:
    code: int
    out: str = ""
    err: str = ""
    dry: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def text(self) -> str:
        return (self.out + ("\n" if self.out and self.err else "") + self.err).strip()


class Runner(QObject):
    line = pyqtSignal(str)          # one line of output or a command echo
    command_started = pyqtSignal(str)
    command_finished = pyqtSignal(str, int)

    def __init__(self, tools, log: ActionLog | None = None, parent=None):
        super().__init__(parent)
        self.tools = tools
        self.log = log or ActionLog()
        self.dry_run = False
        self._streams: set[QProcess] = set()

    # ------------------------------------------------------------ building

    def argv(self, cmd: Command) -> list[str]:
        if cmd.tool == "script":
            return hostplatform.script_command(Path(cmd.args[0])) + list(cmd.args[1:])
        base = self.tools.argv(cmd.tool)
        if base is None:
            raise ToolMissing(cmd.tool)
        serial = []
        if cmd.serial and cmd.tool in SERIAL_FLAG:
            serial = SERIAL_FLAG[cmd.tool] + [cmd.serial]
        return list(base) + serial + list(cmd.args)

    def display(self, cmd: Command) -> str:
        """The command as a person would read it (tool name, not full path)."""
        if cmd.tool == "script":
            argv = hostplatform.script_command(Path(cmd.args[0])) + list(cmd.args[1:])
        else:
            serial = SERIAL_FLAG[cmd.tool] + [cmd.serial] if cmd.serial and cmd.tool in SERIAL_FLAG else []
            argv = [cmd.tool] + serial + list(cmd.args)
        text = shlex.join(argv)
        if cmd.tool == "script" and cmd.serial:
            text = f"ANDROID_SERIAL={cmd.serial} {text}"
        if cmd.stdin_file:
            text += f" < {shlex.quote(cmd.stdin_file)}"
        return text

    def environment(self, cmd: Command) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        # Make vendor scripts use the same adb/fastboot deADBoot resolved.
        tool_dirs = []
        for t in ("fastboot", "adb"):
            p = self.tools.path(t)
            if p:
                d = str(Path(p).parent)
                if d not in tool_dirs:
                    tool_dirs.append(d)
        if tool_dirs:
            env.insert("PATH", hostplatform.path_sep().join(tool_dirs + [env.value("PATH", "")]))
        if cmd.serial and cmd.tool in ("script", "fastboot", "adb"):
            env.insert("ANDROID_SERIAL", cmd.serial)
        env.insert("LC_ALL", "C")
        for k, v in cmd.env.items():
            env.insert(k, v)
        return env

    # ------------------------------------------------------------ running

    def _record(self, cmd: Command, shown: str, result: str) -> None:
        if cmd.tool in DEVICE_TOOLS or cmd.serial:
            self.log.write(cmd.serial or "", shown, result)

    def run_blocking(self, cmd: Command, on_line: Callable[[str], None] | None = None,
                     cancel: threading.Event | None = None, quiet: bool = False) -> Result:
        shown = self.display(cmd)
        if self.dry_run and cmd.writes:
            self.line.emit(f"[dry-run] $ {shown}")
            self._record(cmd, "[dry-run] " + shown, "not executed")
            return Result(0, "", "", dry=True)
        try:
            argv = self.argv(cmd)
        except ToolMissing as exc:
            self.line.emit(f"! {exc}")
            return Result(127, "", str(exc))
        if not quiet:
            self.line.emit(f"$ {shown}")
        self.command_started.emit(shown)

        proc = QProcess()
        proc.setProgram(argv[0])
        proc.setArguments(argv[1:])
        proc.setProcessEnvironment(self.environment(cmd))
        if cmd.cwd:
            proc.setWorkingDirectory(cmd.cwd)
        if cmd.stdin_file:
            proc.setStandardInputFile(cmd.stdin_file)
        proc.start()
        if not proc.waitForStarted(15000):
            msg = f"could not start {argv[0]}: {proc.errorString()}"
            self.line.emit(f"! {msg}")
            self._record(cmd, shown, "failed to start")
            return Result(126, "", msg)
        if not cmd.stdin_file:
            proc.closeWriteChannel()

        out_parts: list[str] = []
        err_parts: list[str] = []
        buffers = {"out": "", "err": ""}
        elapsed = 0.0

        def drain(final: bool = False) -> None:
            for key, data in (("out", proc.readAllStandardOutput()), ("err", proc.readAllStandardError())):
                chunk = bytes(data).decode("utf-8", errors="replace")
                if not chunk and not final:
                    continue
                (out_parts if key == "out" else err_parts).append(chunk)
                buf = buffers[key] + chunk
                pieces = buf.replace("\r", "\n").split("\n")
                buffers[key] = "" if final else pieces.pop()
                for piece in pieces:
                    if piece.strip():
                        if not quiet:
                            self.line.emit(piece)
                        if on_line:
                            on_line(piece)

        while proc.state() != QProcess.ProcessState.NotRunning:
            proc.waitForReadyRead(200)
            drain()
            elapsed += 0.2
            if cancel is not None and cancel.is_set():
                proc.kill()
                proc.waitForFinished(3000)
                break
            if cmd.timeout_s and elapsed > cmd.timeout_s:
                proc.kill()
                proc.waitForFinished(3000)
                err_parts.append("\n[timed out]")
                break
        proc.waitForFinished(1000)
        drain(final=True)
        if proc.exitStatus() == QProcess.ExitStatus.CrashExit:
            code = -2 if not (cancel and cancel.is_set()) else -15
        else:
            code = proc.exitCode()
        self.command_finished.emit(shown, code)
        self._record(cmd, shown, f"exit {code}")
        if not quiet and code != 0:
            self.line.emit(f"! exit code {code}")
        return Result(code, "".join(out_parts), "".join(err_parts))

    def start(self, cmd: Command, on_line: Callable[[str], None] | None = None,
              on_finished: Callable[[int], None] | None = None, quiet: bool = False) -> QProcess | None:
        """Start a long-running process (logcat, scrcpy). Must be called from the GUI thread."""
        shown = self.display(cmd)
        if self.dry_run and cmd.writes:
            self.line.emit(f"[dry-run] $ {shown}")
            if on_finished:
                on_finished(0)
            return None
        try:
            argv = self.argv(cmd)
        except ToolMissing as exc:
            self.line.emit(f"! {exc}")
            if on_finished:
                on_finished(127)
            return None
        self.line.emit(f"$ {shown}")
        proc = QProcess(self)
        proc.setProgram(argv[0])
        proc.setArguments(argv[1:])
        proc.setProcessEnvironment(self.environment(cmd))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        buf = {"s": ""}

        def ready() -> None:
            chunk = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
            text = buf["s"] + chunk
            pieces = text.split("\n")
            buf["s"] = pieces.pop()
            for piece in pieces:
                piece = piece.rstrip("\r")
                if on_line:
                    on_line(piece)
                elif not quiet:
                    self.line.emit(piece)

        def done(code: int, _status) -> None:
            self._streams.discard(proc)
            self._record(cmd, shown, f"exit {code}")
            if on_finished:
                on_finished(code)
            proc.deleteLater()

        proc.readyReadStandardOutput.connect(ready)
        proc.finished.connect(done)
        self._streams.add(proc)
        proc.start()
        self._record(cmd, shown, "started")
        return proc

    def stop_all(self) -> None:
        for proc in list(self._streams):
            proc.kill()
            proc.waitForFinished(2000)


def remote_quote(*parts: str) -> str:
    """Join arguments into one string for the *device's* shell (adb shell takes one string)."""
    return " ".join(shlex.quote(p) for p in parts)


def env_path_list() -> list[str]:
    return os.environ.get("PATH", "").split(os.pathsep)
