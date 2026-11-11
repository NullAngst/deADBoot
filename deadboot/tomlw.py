"""Minimal TOML writer for settings and recipes.

The standard library reads TOML (tomllib) but cannot write it. This covers the
subset deADBoot writes: strings, booleans, integers, floats, lists of those,
plain tables and arrays of tables. Anything else raises TypeError.
"""

from __future__ import annotations

from typing import Any


def _str(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _key(key: str) -> str:
    if key and all(c.isalnum() or c in "-_" for c in key):
        return key
    return _str(key)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _str(value)
    if isinstance(value, (list, tuple)):
        if any(isinstance(v, dict) for v in value):
            raise TypeError("inline arrays of tables are not supported")
        return "[" + ", ".join(_value(v) for v in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def dumps(data: dict, _prefix: str = "") -> str:
    lines: list[str] = []
    tables: list[tuple[str, dict]] = []
    arrays: list[tuple[str, list]] = []
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, dict):
            tables.append((key, value))
        elif _is_table_array(value):
            arrays.append((key, value))
        else:
            lines.append(f"{_key(key)} = {_value(value)}")
    for key, table in tables:
        name = f"{_prefix}{_key(key)}"
        lines.append("")
        lines.append(f"[{name}]")
        body = dumps(table, name + ".")
        if body:
            lines.append(body)
    for key, items in arrays:
        name = f"{_prefix}{_key(key)}"
        for item in items:
            lines.append("")
            lines.append(f"[[{name}]]")
            body = dumps(item, name + ".")
            if body:
                lines.append(body)
    return "\n".join(lines).strip("\n") + ("\n" if not _prefix else "")
