"""`_infer_release_type` always yields a value, so the inferred-RELEASETYPE
path can never fall through to the (now-removed) missing_releasetype review.
"""
import pytest

from dragontag.app.ingest.pipeline import _infer_release_type


@pytest.mark.parametrize("total", [None, 0, 1, 2, 6, 7, 20, 999])
def test_infer_release_type_always_truthy(total):
    assert _infer_release_type(total)


def test_infer_release_type_buckets():
    assert _infer_release_type(1) == "Single"
    assert _infer_release_type(4) == "EP"
    assert _infer_release_type(12) == "Album"
    assert _infer_release_type(None) == "Album"
