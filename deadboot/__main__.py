"""Entry point: python -m deadboot [--self-test]."""

from __future__ import annotations

import argparse
import re
import sys


def self_test(log_path: str = "") -> int:
    """Load Qt, every theme, every ROM profile, every shipped recipe and build the window, then exit.

    Run against a packaged build in CI to catch modules or data files left out of the bundle.
    """
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from . import debloat, downloads, hostplatform, profiles, recipes
    from .actionlog import ActionLog
    from .runner import Runner
    from .settings import Settings
    from .tools import Tools
    from .ui import themes
    from .ui.common import App
    from .ui.jobs import Jobs
    from .ui.mainwindow import MainWindow

    problems: list[str] = []
    qapp = QApplication.instance() or QApplication(sys.argv[:1])
    for key in themes.THEMES:
        try:
            qss = themes.stylesheet(key)
            leftover = re.findall(r"@([a-z_]+)@", qss)
            if leftover:
                problems.append(f"theme {key}: unfilled tokens {sorted(set(leftover))}")
            themes.apply(qapp, key)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"theme {key}: {exc}")
    profs, errs = profiles.load_all()
    problems += errs
    for need in ("stock", "grapheneos", "lineageos", "generic"):
        if need not in profs:
            problems.append(f"ROM profile {need} missing")
    for f in sorted(hostplatform.data_resource("recipes").glob("*.toml")):
        try:
            recipes.load(f)
        except recipes.RecipeError as exc:
            problems.append(str(exc))
    if not debloat.load_protected():
        problems.append("debloat-protected.txt missing or empty")
    try:
        downloads.load_sources()
    except OSError as exc:
        problems.append(f"sources.toml: {exc}")
    settings = Settings()
    tools = Tools(settings)
    runner = Runner(tools, ActionLog())
    ctx = App(settings, tools, runner, Jobs(), profs, debloat.load_protected())
    try:
        win = MainWindow(ctx)
        win.show()
        qapp.processEvents()
        win.close()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"main window: {exc}")
    lines = [f"FAIL: {p}" for p in problems]
    lines.append("self-test: " + ("OK" if not problems else f"{len(problems)} problem(s)"))
    for line in lines:
        print(line)
    if log_path:
        # A windowed Windows build has no console, so CI reads the result from this file.
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deadboot", description="ADB utility, flashing and device recovery tool")
    parser.add_argument("--self-test", action="store_true", help="load every bundled resource and exit")
    parser.add_argument("--self-test-log", default="", help="also write the self-test result to this file")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    from . import APP_NAME, __version__
    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0
    if args.self_test:
        return self_test(args.self_test_log)

    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    from . import debloat, hostplatform, profiles, recipes
    from .actionlog import ActionLog
    from .runner import Runner
    from .settings import Settings
    from .tools import Tools
    from .ui import themes
    from .ui.common import App
    from .ui.jobs import Jobs
    from .ui.mainwindow import MainWindow

    qapp = QApplication(sys.argv[:1])
    qapp.setApplicationName(APP_NAME)
    qapp.setDesktopFileName("deadboot")
    icon = hostplatform.data_resource("deadboot.svg")
    if icon.is_file():
        qapp.setWindowIcon(QIcon(str(icon)))
    settings = Settings.load()
    if not hostplatform.settings_file().is_file():
        settings.save()
    try:
        themes.apply(qapp, settings.theme)
    except (OSError, ValueError):
        themes.apply(qapp, "dark")
    log = ActionLog(retention_days=settings.log_retention_days)
    log.rotate()
    recipes.install_examples()
    profs, errs = profiles.load_all()
    tools = Tools(settings)
    runner = Runner(tools, log)
    ctx = App(settings, tools, runner, Jobs(), profs, debloat.load_protected())
    win = MainWindow(ctx)
    for e in errs:
        runner.line.emit(f"! ROM profile not loaded: {e}")
    win.show()
    QTimer.singleShot(300, win.first_run_tools)
    QTimer.singleShot(400, ctx.devices.start)
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
