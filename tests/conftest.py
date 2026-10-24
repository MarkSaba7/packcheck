"""Fixtures shared by all test files.

The demo product is generated once per test session into a temporary folder, so the tests
never touch products/ and never depend on files that a developer might have regenerated.
"""

import pytest

from packcheck import demo
from packcheck.product import load_product


@pytest.fixture(scope="session")
def products_dir(tmp_path_factory):
    folder = tmp_path_factory.mktemp("products")
    demo.write_demo_product(folder)
    return folder


@pytest.fixture(scope="session")
def product(products_dir):
    """The demo FRONT face."""
    return load_product(products_dir / "demo-dog-food-front" / "product.json")


def boxes_overlap(a, b):
    """Two (x, y, w, h) boxes in the same units: do they touch?"""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah
