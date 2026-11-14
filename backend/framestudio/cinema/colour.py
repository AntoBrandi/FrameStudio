"""Colour conversions for the cinema tools (M5.1 adherence, M5.4 palettes):
sRGB (0–255) ↔ CIELAB (D65), hex strings, and hue."""

from __future__ import annotations

import math

Lab = tuple[float, float, float]
RGB = tuple[int, int, int]

_WHITE = (0.95047, 1.0, 1.08883)


def _linear(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c: float) -> float:
    c = c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return min(255.0, max(0.0, c * 255))


def to_lab(rgb: RGB | tuple[float, float, float]) -> Lab:
    r, g, b = (_linear(v) for v in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / _WHITE[0]
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / _WHITE[1]
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / _WHITE[2]

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def to_rgb(lab: Lab) -> tuple[float, float, float]:
    """sRGB (0–255, unrounded, clamped) of a CIELAB colour."""
    lightness, a, b = lab
    fy = (lightness + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200

    def inv(t: float) -> float:
        return t**3 if t**3 > 0.008856 else (t - 16 / 116) / 7.787

    x, y, z = inv(fx) * _WHITE[0], inv(fy) * _WHITE[1], inv(fz) * _WHITE[2]
    r = 3.2406 * x - 1.5372 * y - 0.4986 * z
    g = -0.9689 * x + 1.8758 * y + 0.0415 * z
    bl = 0.0557 * x - 0.2040 * y + 1.0570 * z
    return _gamma(r), _gamma(g), _gamma(bl)


def hex_to_rgb(value: str) -> RGB:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(min(255, max(0, c))):02X}" for c in rgb)


def hex_to_lab(value: str) -> Lab:
    return to_lab(hex_to_rgb(value))


def distance(a: Lab, b: Lab) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def chroma(lab: Lab) -> float:
    return math.hypot(lab[1], lab[2])


def hue_degrees(lab: Lab) -> float:
    return math.degrees(math.atan2(lab[2], lab[1])) % 360
