"""One-off diagnostics must never target the real backup set or publish test photos."""
from pathlib import Path


def test_dangerous_one_offs_are_not_shipped():
    backend = Path(__file__).parents[1]
    assert not (backend / '_rotation_test.py').exists()
    assert not (backend / '_diag_upload.py').exists()
    # These diagnostics only read remote/local records and should remain available.
    assert (backend / '_check_recent.py').exists()
    assert (backend / '_diag_engagement.py').exists()
