from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import cos, degrees, hypot, isclose, isfinite, radians, sin, atan2
from typing import Iterable

Coordinate = tuple[float, float]
Matrix = tuple[float, float, float, float, float, float, float, float, float]
Similarity = tuple[float, float, float, float]


@dataclass(frozen=True)
class MatrixTransform:
    coefficients: Matrix
    model: str = "projective"

    def __post_init__(self) -> None:
        if self.model not in ("similarity", "affine", "projective"):
            raise ValueError("Unsupported matrix transform model.")
        if len(self.coefficients) != 9 or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
            for value in self.coefficients
        ):
            raise ValueError("A transform requires nine finite numeric coefficients.")
        coefficients = tuple(float(value) for value in self.coefficients)
        divisor = coefficients[8] or max(abs(value) for value in coefficients)
        if divisor == 0:
            raise ValueError("A transform must be invertible.")
        normalized = tuple(value / divisor for value in coefficients)
        if not all(isfinite(value) for value in normalized):
            raise ValueError("Transform normalization exceeds numeric range.")
        object.__setattr__(self, "coefficients", normalized)
        if self.determinant == 0 or not isfinite(self.determinant):
            raise ValueError("A transform must be invertible with a finite determinant.")
        if self.model != "projective" and not self.is_affine:
            raise ValueError("An affine or similarity transform cannot contain perspective coefficients.")
        if self.model == "similarity" and not self._is_similarity():
            raise ValueError("A similarity transform must have orthogonal axes of equal length.")

    @property
    def determinant(self) -> float:
        first, second, third, fourth, fifth, sixth, seventh, eighth, ninth = self.coefficients
        return (
            first * (fifth * ninth - sixth * eighth)
            - second * (fourth * ninth - sixth * seventh)
            + third * (fourth * eighth - fifth * seventh)
        )

    @property
    def is_affine(self) -> bool:
        return self.coefficients[6:] == (0.0, 0.0, 1.0)

    def _is_similarity(self) -> bool:
        first, second, _, fourth, fifth, *_ = self.coefficients
        first_length, second_length = hypot(first, fourth), hypot(second, fifth)
        return self.is_affine and isclose(first_length, second_length, rel_tol=1e-12) and (
            abs((first / first_length) * (second / second_length)
                + (fourth / first_length) * (fifth / second_length)) <= 1e-12
        )

    @classmethod
    def identity(cls) -> MatrixTransform:
        return cls((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0), "similarity")

    @classmethod
    def from_similarity(cls, transform: Similarity, *, pixel_y_down: bool = False) -> MatrixTransform:
        position_x, position_y, rotation, scale = transform
        if not all(isfinite(value) for value in transform) or scale <= 0:
            raise ValueError("A similarity pose must be finite with positive scale.")
        angle = radians(rotation)
        cosine, sine = scale * cos(angle), scale * sin(angle)
        direction = -1.0 if pixel_y_down else 1.0
        return cls((cosine, -sine * direction, position_x,
                    sine, cosine * direction, position_y, 0.0, 0.0, 1.0), "similarity")

    @classmethod
    def from_affine(cls, coefficients: tuple[float, float, float, float, float, float]) -> MatrixTransform:
        return cls((*coefficients, 0.0, 0.0, 1.0), "affine")

    def to_affine(self) -> tuple[float, float, float, float, float, float]:
        if not self.is_affine:
            raise ValueError("A projective transform cannot be represented by affine coefficients.")
        return self.coefficients[:6]

    def to_similarity(self, *, pixel_y_down: bool = False) -> Similarity:
        if not self._is_similarity():
            raise ValueError("This mapping cannot be represented by a similarity pose.")
        first, second, position_x, fourth, fifth, position_y, *_ = self.coefficients
        direction = -1.0 if pixel_y_down else 1.0
        if (first * fifth - second * fourth) * direction <= 0:
            raise ValueError("The mapping's orientation does not match the requested pixel convention.")
        return position_x, position_y, degrees(atan2(fourth, first)), hypot(first, fourth)

    def map_point(self, x: float, y: float) -> Coordinate:
        if not isfinite(x) or not isfinite(y):
            raise ValueError("Point coordinates must be finite.")
        first, second, third, fourth, fifth, sixth, seventh, eighth, ninth = self.coefficients
        denominator = seventh * x + eighth * y + ninth
        magnitude = abs(seventh * x) + abs(eighth * y) + abs(ninth)
        if abs(denominator) <= 1e-12 * magnitude:
            raise ValueError("Point is on or too close to the projective horizon.")
        mapped = ((first * x + second * y + third) / denominator,
                  (fourth * x + fifth * y + sixth) / denominator)
        if not all(isfinite(value) for value in mapped):
            raise ValueError("Mapped coordinates exceed numeric range.")
        return mapped

    def map_points(self, points: Iterable[Coordinate]) -> tuple[Coordinate | None, ...]:
        mapped: list[Coordinate | None] = []
        for x, y in points:
            try:
                mapped.append(self.map_point(x, y))
            except ValueError:
                mapped.append(None)
        return tuple(mapped)

    def inverse(self) -> MatrixTransform:
        first, second, third, fourth, fifth, sixth, seventh, eighth, ninth = self.coefficients
        determinant = self.determinant
        adjugate = (
            fifth * ninth - sixth * eighth, third * eighth - second * ninth, second * sixth - third * fifth,
            sixth * seventh - fourth * ninth, first * ninth - third * seventh, third * fourth - first * sixth,
            fourth * eighth - fifth * seventh, second * seventh - first * eighth, first * fifth - second * fourth,
        )
        return MatrixTransform(tuple(value / determinant for value in adjugate), self.model)

    def compose(self, inner: MatrixTransform) -> MatrixTransform:
        outer_values, inner_values = self.coefficients, inner.coefficients
        values = tuple(
            sum(outer_values[row * 3 + offset] * inner_values[offset * 3 + column] for offset in range(3))
            for row in range(3) for column in range(3)
        )
        model = "projective"
        if self.is_affine and inner.is_affine:
            model = "similarity" if self.model == inner.model == "similarity" else "affine"
        return MatrixTransform(values, model)

    def jacobian(self, x: float, y: float) -> tuple[float, float, float, float]:
        mapped_x, mapped_y = self.map_point(x, y)
        first, second, _, fourth, fifth, _, seventh, eighth, ninth = self.coefficients
        denominator = seventh * x + eighth * y + ninth
        return ((first - seventh * mapped_x) / denominator, (second - eighth * mapped_x) / denominator,
                (fourth - seventh * mapped_y) / denominator, (fifth - eighth * mapped_y) / denominator)

    def domain_bounds(self, width: float, height: float) -> tuple[float, float, float, float]:
        if not all(isfinite(value) and value > 0 for value in (width, height)):
            raise ValueError("Source dimensions must be finite and positive.")
        corners = ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height))
        seventh, eighth, ninth = self.coefficients[6:]
        denominators = [seventh * x + eighth * y + ninth for x, y in corners]
        if min(denominators) <= 0 <= max(denominators):
            raise ValueError("A projective horizon crosses the source domain.")
        mapped = [self.map_point(x, y) for x, y in corners]
        return (min(point[0] for point in mapped), min(point[1] for point in mapped),
                max(point[0] for point in mapped), max(point[1] for point in mapped))

    def to_dict(self) -> dict:
        return {"version": 1, "model": self.model, "coefficients": list(self.coefficients)}

    @classmethod
    def from_dict(cls, data: dict) -> MatrixTransform:
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Unsupported matrix mapping format.")
        coefficients = data.get("coefficients")
        if not isinstance(coefficients, (list, tuple)) or len(coefficients) != 9:
            raise ValueError("A transform requires nine coefficients.")
        return cls(tuple(coefficients), data.get("model", "projective"))


@lru_cache(maxsize=2048)
def similarity_mapping(transform: Similarity, pixel_y_down: bool = False) -> MatrixTransform:
    return MatrixTransform.from_similarity(transform, pixel_y_down=pixel_y_down)


def apply_similarity_transform(
    x: float,
    y: float,
    transform: tuple[float, float, float, float],
) -> tuple[float, float]:
    return similarity_mapping(transform).map_point(x, y)