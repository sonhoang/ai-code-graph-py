"""Reading and writing shapes."""

import json

import sample_pkg.shapes

from .. import shapes
from ..shapes import Circle


def dump_circle(circle: Circle) -> str:
    return json.dumps({"radius": circle.radius})


def load_circle(text: str) -> Circle:
    return shapes.Circle(**json.loads(text))


def default_circle() -> Circle:
    return sample_pkg.shapes.Circle()
