"""Themes: one QSS template in data/themes/base.qss, filled from a palette file per theme.

Each palette defines its own danger colors, so the Flashing tab and destructive
buttons stay readable and obviously red in all five themes.
"""

from __future__ import annotations

import tomllib

from PyQt6.QtGui import QColor, QPalette

from .. import hostplatform

THEMES = {
    "light": ("Normal light", "light.toml"),
    "dark": ("Normal dark", "dark.toml"),
    "verdigris": ("Verdigris", "verdigris.toml"),
    "kawaii-light": ("Kawaii Light", "kawaii-light.toml"),
    "kawaii-dark": ("Kawaii Dark", "kawaii-dark.toml"),
}
REQUIRED = {"bg", "bg_alt", "fg", "fg_dim", "accent", "accent_fg", "border", "input", "select", "danger",
            "danger_fg", "danger_bg", "ok", "radius", "font"}


def palette_for(key: str) -> dict:
    _label, fname = THEMES.get(key, THEMES["dark"])
    with hostplatform.data_resource("themes", fname).open("rb") as fh:
        pal = tomllib.load(fh)
    missing = REQUIRED - set(pal)
    if missing:
        raise ValueError(f"theme {fname} is missing {', '.join(sorted(missing))}")
    return pal


def stylesheet(key: str) -> str:
    pal = palette_for(key)
    qss = hostplatform.data_resource("themes", "base.qss").read_text(encoding="utf-8")
    for k, v in pal.items():
        qss = qss.replace("@" + k + "@", str(v))
    return qss


def apply(app, key: str) -> None:
    pal = palette_for(key)
    qp = QPalette()
    qp.setColor(QPalette.ColorRole.Window, QColor(pal["bg"]))
    qp.setColor(QPalette.ColorRole.WindowText, QColor(pal["fg"]))
    qp.setColor(QPalette.ColorRole.Base, QColor(pal["input"]))
    qp.setColor(QPalette.ColorRole.AlternateBase, QColor(pal["bg_alt"]))
    qp.setColor(QPalette.ColorRole.Text, QColor(pal["fg"]))
    qp.setColor(QPalette.ColorRole.Button, QColor(pal["bg_alt"]))
    qp.setColor(QPalette.ColorRole.ButtonText, QColor(pal["fg"]))
    qp.setColor(QPalette.ColorRole.Highlight, QColor(pal["select"]))
    qp.setColor(QPalette.ColorRole.HighlightedText, QColor(pal["fg"]))
    qp.setColor(QPalette.ColorRole.Link, QColor(pal["accent"]))
    qp.setColor(QPalette.ColorRole.PlaceholderText, QColor(pal["fg_dim"]))
    qp.setColor(QPalette.ColorRole.ToolTipBase, QColor(pal["bg_alt"]))
    qp.setColor(QPalette.ColorRole.ToolTipText, QColor(pal["fg"]))
    app.setPalette(qp)
    app.setStyleSheet(stylesheet(key))
