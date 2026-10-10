from __future__ import annotations

from math import cos, radians, sin


def apply_similarity_transform(
    x: float,
    y: float,
    transform: tuple[float, float, float, float],
) -> tuple[float, float]:
    position_x, position_y, rotation, scale = transform
    angle = radians(rotation)
    return (
        position_x + scale * (cos(angle) * x - sin(angle) * y),
        position_y + scale * (sin(angle) * x + cos(angle) * y),
    )