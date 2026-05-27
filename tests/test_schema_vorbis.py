"""Verify the canonical schema produces the exact Vorbis Comment shape from the reference doc."""
from dragontag.app.config import Separators
from dragontag.app.tagging.schema import TrackTags


def test_vorbis_render_matches_reference():
    sep = Separators()
    t = TrackTags(
        title="deletee (intro)",
        artist_display="Bladee//Thaiboy Digital",
        artists=["Bladee", "Thaiboy Digital"],
        artist_sort=["Bladee feat. Thaiboy Digital"],
        album="gluee",
        album_artist_display="Bladee",
        album_artist_sort=["Bladee"],
        composers=["Some Composer"],
        conductor=["Some Conductor"],
        lyricist=["Some Lyricist"],
        arranger=["Some Arranger"],
        date="2014-01-27",
        original_date="2014-01-27",
        original_year="2014",
        track=1, track_total=9,
        disc=1, disc_total=1,
        genres=["Hip-Hop/Rap"],
        labels=["Revenue"],
        media="Digital Media",
        barcode="7071245142858",
        isrcs=["SE4LC1400201"],
        catalog_number="REV-001",
        language="eng",
        compilation=True,
        release_country="XW",
        release_status="official",
        release_type="Album",
        script="Latn",
        acoustid_id="e4829e35-5b3e-4c68-9b9b-d4d202765f15",
        mb_track_id="b8cf993a-d3e0-4efd-adfa-8d93574c6eb1",
        mb_releasetrack_id="f9e68e57-5997-4352-bcd7-5ffc07ad21bc",
        mb_album_id="dc23e008-e934-469b-a210-fbfbabc57019",
        mb_album_artist_ids=["cd689e77-dfdd-4f81-b50c-5e5a3f5e38a4"],
        mb_artist_ids=[
            "cd689e77-dfdd-4f81-b50c-5e5a3f5e38a4",
            "68d311c0-525f-4f72-a044-84e54565d02d",
        ],
        mb_release_group_id="c3669980-9a4b-4cb5-89e5-e4efb144972e",
    )
    out = t.to_vorbis(sep)

    # --- core fields ---
    assert out["TITLE"] == "deletee (intro)"
    assert out["ARTIST"] == "Bladee//Thaiboy Digital"
    assert out["ARTISTS"] == "Bladee;Thaiboy Digital"
    assert out["ARTISTSORT"] == "Bladee feat. Thaiboy Digital"
    assert out["ALBUM"] == "gluee"
    assert out["album_artist"] == "Bladee"
    assert out["ALBUMARTISTSORT"] == "Bladee"
    assert out["DATE"] == "2014-01-27"
    assert out["ORIGINALDATE"] == "2014-01-27"
    assert out["ORIGINALYEAR"] == "2014"
    assert out["track"] == "01/09"
    assert out["TRACKTOTAL"] == "9"
    assert out["TOTALTRACKS"] == "9"
    assert out["disc"] == "01/01"
    assert out["DISCTOTAL"] == "1"
    assert out["TOTALDISCS"] == "1"
    assert out["GENRE"] == "Hip-Hop/Rap"
    assert out["LABEL"] == "Revenue"
    assert out["MEDIA"] == "Digital Media"
    assert out["BARCODE"] == "7071245142858"
    assert out["ISRC"] == "SE4LC1400201"
    assert out["RELEASECOUNTRY"] == "XW"
    assert out["RELEASESTATUS"] == "official"
    assert out["RELEASETYPE"] == "Album"
    assert out["SCRIPT"] == "Latn"
    assert out["ACOUSTID_ID"] == "e4829e35-5b3e-4c68-9b9b-d4d202765f15"
    assert out["MUSICBRAINZ_TRACKID"] == "b8cf993a-d3e0-4efd-adfa-8d93574c6eb1"
    assert out["MUSICBRAINZ_RELEASETRACKID"] == "f9e68e57-5997-4352-bcd7-5ffc07ad21bc"
    assert out["MUSICBRAINZ_ALBUMID"] == "dc23e008-e934-469b-a210-fbfbabc57019"
    assert out["MUSICBRAINZ_ALBUMARTISTID"] == "cd689e77-dfdd-4f81-b50c-5e5a3f5e38a4"
    assert out["MUSICBRAINZ_ARTISTID"] == (
        "cd689e77-dfdd-4f81-b50c-5e5a3f5e38a4;68d311c0-525f-4f72-a044-84e54565d02d"
    )
    assert out["MUSICBRAINZ_RELEASEGROUPID"] == "c3669980-9a4b-4cb5-89e5-e4efb144972e"

    # --- new fields ---
    assert out["COMPOSER"] == "Some Composer"
    assert out["CONDUCTOR"] == "Some Conductor"
    assert out["LYRICIST"] == "Some Lyricist"
    assert out["ARRANGER"] == "Some Arranger"
    assert out["CATALOGNUMBER"] == "REV-001"
    assert out["LANGUAGE"] == "eng"
    assert out["COMPILATION"] == "1"


def test_new_fields_omitted_when_empty():
    sep = Separators()
    t = TrackTags(title="test")
    out = t.to_vorbis(sep)
    assert "CONDUCTOR" not in out
    assert "LYRICIST" not in out
    assert "ARRANGER" not in out
    assert "CATALOGNUMBER" not in out
    assert "LANGUAGE" not in out
    assert "COMPILATION" not in out


def test_compilation_false_not_written():
    sep = Separators()
    t = TrackTags(title="test", compilation=False)
    out = t.to_vorbis(sep)
    assert "COMPILATION" not in out
