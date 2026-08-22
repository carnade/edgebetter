"""Upstream value parsing. MLB in particular encodes some numbers unusually."""

from __future__ import annotations


def to_float(value: object) -> float | None:
    """Parse a stat that may arrive as a string, and may be a null sentinel.

    MLB uses '-.--' and '.---' for undefined rate stats (e.g. ERA with 0 IP).
    """
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if not text or set(text) <= {"-", ".", "-"} or text in {"-.--", ".---", "--", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: object) -> int | None:
    f = to_float(value)
    return int(f) if f is not None else None


def innings_to_float(value: object) -> float | None:
    """Convert MLB innings-pitched notation to true innings.

    MLB writes thirds after the decimal point: '5.1' is 5 1/3 innings and '5.2' is
    5 2/3 -- NOT 5.1 and 5.2. Treating the string as a plain float understates
    workload and skews every rate stat derived from it.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "." not in text:
        try:
            return float(text)
        except ValueError:
            return None
    whole, _, frac = text.partition(".")
    try:
        innings = float(whole or 0)
    except ValueError:
        return None
    thirds = {"0": 0.0, "1": 1.0 / 3.0, "2": 2.0 / 3.0}
    if frac not in thirds:
        # Not the thirds notation (already decimal); fall back to a plain parse.
        return to_float(text)
    return innings + thirds[frac]
