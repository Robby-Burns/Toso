"""Initial smoke test verifying environment and package accessibility."""

import toso


def test_toso_package_version():
    assert toso.__version__ == "0.1.0"
