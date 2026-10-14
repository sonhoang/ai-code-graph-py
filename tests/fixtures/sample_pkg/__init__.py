"""Sample package used by the test suite."""

from .shapes import Circle, area

__all__ = ["Circle", "area"]


def _private_helper() -> None:
    pass


def not_exported() -> None:
    pass
