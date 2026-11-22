import pytest

from oci_a1_hunter import i18n


@pytest.fixture(autouse=True)
def english():
    """Tests assert on English text; `--lang ru` in one test must not leak into others."""
    i18n.LANG = "en"
    yield
    i18n.LANG = "en"
