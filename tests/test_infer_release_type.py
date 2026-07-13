"""`_infer_release_type` always yields a value, so the inferred-RELEASETYPE
path can never fall through to the (now-removed) missing_releasetype review.
"""
import pytest

from dragontag.app.identify.musicbrainz import _release_media, _release_track_total
from dragontag.app.ingest.pipeline import _infer_release_type, prepare_tags
from dragontag.app.tagging.schema import TrackTags


@pytest.mark.parametrize("total", [None, 0, 1, 2, 6, 7, 20, 999])
def test_infer_release_type_always_truthy(total):
    assert _infer_release_type(total)


def test_infer_release_type_buckets():
    assert _infer_release_type(1) == "Single"
    assert _infer_release_type(4) == "EP"
    assert _infer_release_type(12) == "Album"
    assert _infer_release_type(None) == "Album"


def test_prepare_tags_infers_from_release_wide_total():
    # A 4-track disc 2 of a 14-track album must infer "Album", not "EP" —
    # per-disc inference split multi-disc albums by RELEASETYPE.
    tags = TrackTags(track_total=4, release_track_total=14)
    prepare_tags(None, tags)
    assert tags.release_type == "Album"
    # Without the release-wide total the per-disc count is still the fallback.
    tags = TrackTags(track_total=4)
    prepare_tags(None, tags)
    assert tags.release_type == "EP"


def test_release_track_total_sums_all_media():
    rel = {"medium-list": [
        {"track-count": 10},
        {"track-list": [{}, {}, {}, {}]},  # no track-count: falls back to list len
    ]}
    assert _release_track_total(rel) == 14
    assert _release_track_total({}) is None


def test_release_media_normalized_release_wide():
    assert _release_media({"medium-list": [{"format": "CD"}, {"format": "CD"}]}) == "CD"
    assert _release_media({"medium-list": [{"format": "CD"}, {"format": "DVD"}]}) == "CD/DVD"
    assert _release_media({"medium-list": [{"format": None}, {}]}) is None
