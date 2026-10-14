"""Geometric shapes."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Circle:
    """A circle with a radius."""

    radius: float = 1.0

    @property
    def diameter(self) -> float:
        """Twice the radius."""
        return self.radius * 2

    @classmethod
    def unit(cls) -> Circle:
        return cls(1.0)

    def scaled(self, factor: float = 2.0, *, clamp: bool = False) -> Circle:
        def helper(x: float) -> float:  # nested function: not indexed
            return min(x, 10.0) if clamp else x

        return Circle(helper(self.radius * factor))

    class Meta:
        def describe(self) -> str:
            return "meta"


def area(shape: Circle, /, precision: int = 2) -> float:
    """Area of a shape, rounded."""
    return round(math.pi * shape.radius**2, precision)


async def fetch_shapes(
    source: str, *sources: str, timeout: float | None = None, **options: object
) -> list[Circle]:
    """Load shapes asynchronously."""
    return []
