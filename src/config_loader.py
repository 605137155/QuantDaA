from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:
    tomllib = None

try:
    import tomli
except ModuleNotFoundError:
    tomli = None


def load_toml(path: Path) -> dict:
    if tomllib is not None:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    if tomli is not None:
        with path.open("rb") as handle:
            return tomli.load(handle)
    return _load_simple_toml(path)


def _load_simple_toml(path: Path) -> dict:
    result: Dict[str, dict] = {}
    current_section: Optional[dict] = None

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("[") and line.endswith("]"):
                section_name = line[1:-1].strip()
                current_section = result.setdefault(section_name, {})
                continue

            if "=" not in line or current_section is None:
                continue

            key, value = [part.strip() for part in line.split("=", 1)]
            current_section[key] = _parse_value(value)

    return result


def _parse_value(raw: str):
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw
