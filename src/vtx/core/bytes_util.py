import re

_UNITS = ["B", "KB", "MB", "GB", "TB", "PB", "EB"]
_UNIT_FACTORS = {unit: 1024**i for i, unit in enumerate(_UNITS)}


def format_bytes(size: int) -> str:
    if size < 0:
        raise ValueError(f"size must be non-negative, got {size}")
    for unit in reversed(_UNITS[1:]):
        value = size / _UNIT_FACTORS[unit]
        if value >= 1:
            formatted = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{formatted} {unit}"
    return f"{size} B"


def parse_bytes(value: str) -> int:
    value = value.strip()
    if not value:
        raise ValueError("empty string is not a valid byte size")
    match = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*([a-zA-Z]+)", value)
    if not match:
        raise ValueError(f"invalid byte size: {value}")
    number_str, unit = match.groups()
    unit = unit.upper()
    if unit not in _UNIT_FACTORS:
        raise ValueError(f"unknown unit: {unit}")
    number = float(number_str)
    if number < 0:
        raise ValueError(f"size must be non-negative, got {number}")
    return int(number * _UNIT_FACTORS[unit])
