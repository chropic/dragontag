"""Partial (field-only) tag updates using direct mutagen access.

Unlike the full TrackTags writers that replace all tags, these helpers update
a single field while leaving every other tag on the file untouched. Used by
the Library individual-action routes (fetch lyrics only, tag advisories only,
fetch cover art only).

All mutations go through ``atomic_inplace`` so a crash mid-save can never
corrupt the original audio file.
"""
from __future__ import annotations

from pathlib import Path

from ..identify.artist_split import split_multi_artist
from .writers._atomic import atomic_inplace


def _suffix(path: Path) -> str:
    return path.suffix.lower()


def write_lyrics(path: Path, lyrics: str, advisory: int | None = None) -> None:
    """Embed ``lyrics`` (and optionally ``advisory``) into ``path`` in-place."""
    s = _suffix(path)
    with atomic_inplace(path) as tmp:
        if s == ".flac":
            from mutagen.flac import FLAC
            f = FLAC(str(tmp))
            f["LYRICS"] = [lyrics]
            if advisory is not None:
                f["ITUNESADVISORY"] = [str(advisory)]
            f.save()
        elif s in (".mp3", ".wav"):
            import mutagen.id3 as _id3
            from mutagen.mp3 import MP3
            from mutagen.wave import WAVE
            cls = MP3 if s == ".mp3" else WAVE
            f = cls(str(tmp))
            if f.tags is None:
                f.add_tags()
            f.tags.delall("USLT")
            f.tags.add(_id3.USLT(encoding=3, lang="eng", desc="", text=lyrics))
            if advisory is not None:
                f.tags.delall("TXXX:ITUNESADVISORY")
                f.tags.add(_id3.TXXX(encoding=3, desc="ITUNESADVISORY", text=[str(advisory)]))
            f.save()
        elif s in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4
            f = MP4(str(tmp))
            if f.tags is None:
                f.add_tags()
            f.tags["\xa9lyr"] = [lyrics]
            if advisory is not None:
                f.tags["rtng"] = [advisory]
            f.save()


def write_basic_tags(
    path: Path,
    *,
    title: str | None,
    artist: str | None,
    album: str | None,
    album_artist: str | None,
    track: int | None,
    track_total: int | None,
    disc: int | None,
    disc_total: int | None,
) -> None:
    """Update only title/artist/album/album_artist/track/disc numbering.

    Unlike the full ``TrackTags`` writers (which rebuild every tag), this
    touches just these fields and leaves genres, dates, MusicBrainz ids,
    lyrics, etc. untouched — used by the library track-edit menu for quick
    manual corrections where the form doesn't expose the rest of the schema.
    Blank/``None`` fields are left as-is on the file (not cleared).
    """
    s = _suffix(path)
    track_str = (
        f"{track:02d}/{track_total:02d}" if track and track_total
        else (f"{track:02d}" if track else None)
    )
    disc_str = (
        f"{disc:02d}/{disc_total:02d}" if disc and disc_total
        else (f"{disc:02d}" if disc else None)
    )
    with atomic_inplace(path) as tmp:
        if s == ".flac":
            from mutagen.flac import FLAC
            f = FLAC(str(tmp))
            if title:
                f["TITLE"] = [title]
            if artist:
                f["ARTIST"] = split_multi_artist(artist)
            if album:
                f["ALBUM"] = [album]
            if album_artist:
                f["album_artist"] = split_multi_artist(album_artist)
            if track_str:
                f["track"] = [track_str]
            if track_total:
                f["TRACKTOTAL"] = [str(track_total)]
                f["TOTALTRACKS"] = [str(track_total)]
            if disc_str:
                f["disc"] = [disc_str]
            if disc_total:
                f["DISCTOTAL"] = [str(disc_total)]
                f["TOTALDISCS"] = [str(disc_total)]
            f.save()
        elif s in (".mp3", ".wav"):
            import mutagen.id3 as _id3
            from mutagen.mp3 import MP3
            from mutagen.wave import WAVE
            cls = MP3 if s == ".mp3" else WAVE
            f = cls(str(tmp))
            if f.tags is None:
                f.add_tags()
            if title:
                f.tags.setall("TIT2", [_id3.TIT2(encoding=3, text=[title])])
            if artist:
                f.tags.setall("TPE1", [_id3.TPE1(encoding=3, text=split_multi_artist(artist))])
            if album:
                f.tags.setall("TALB", [_id3.TALB(encoding=3, text=[album])])
            if album_artist:
                f.tags.setall("TPE2", [_id3.TPE2(encoding=3, text=split_multi_artist(album_artist))])
            if track_str:
                f.tags.setall("TRCK", [_id3.TRCK(encoding=3, text=[track_str])])
            if disc_str:
                f.tags.setall("TPOS", [_id3.TPOS(encoding=3, text=[disc_str])])
            f.save()
        elif s in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4
            f = MP4(str(tmp))
            if f.tags is None:
                f.add_tags()
            if title:
                f.tags["\xa9nam"] = [title]
            if artist:
                f.tags["\xa9ART"] = split_multi_artist(artist)
            if album:
                f.tags["\xa9alb"] = [album]
            if album_artist:
                f.tags["aART"] = split_multi_artist(album_artist)
            if track:
                f.tags["trkn"] = [(track, track_total or 0)]
            if disc:
                f.tags["disk"] = [(disc, disc_total or 0)]
            f.save()


def write_album_link_tags(
    path: Path,
    *,
    album: str | None,
    album_artist: str | None,
    disc_total: int | None,
    track_total: int | None,
    mb_album_id: str | None,
    mb_release_group_id: str | None,
) -> None:
    """Inherit album-shared fields from another track onto ``path``.

    Used by the "link to album" picker to fold an orphan/mistagged single
    into an album the user already has in the library. Touches only the
    album-level fields — the track's own title/artist/track number are left
    untouched. Blank/``None`` fields are left as-is on the file.
    """
    s = _suffix(path)
    with atomic_inplace(path) as tmp:
        if s == ".flac":
            from mutagen.flac import FLAC
            f = FLAC(str(tmp))
            if album:
                f["ALBUM"] = [album]
            if album_artist:
                f["album_artist"] = split_multi_artist(album_artist)
            if track_total:
                f["TRACKTOTAL"] = [str(track_total)]
                f["TOTALTRACKS"] = [str(track_total)]
            if disc_total:
                f["DISCTOTAL"] = [str(disc_total)]
                f["TOTALDISCS"] = [str(disc_total)]
            if mb_album_id:
                f["MUSICBRAINZ_ALBUMID"] = [mb_album_id]
            if mb_release_group_id:
                f["MUSICBRAINZ_RELEASEGROUPID"] = [mb_release_group_id]
            f.save()
        elif s in (".mp3", ".wav"):
            import mutagen.id3 as _id3
            from mutagen.mp3 import MP3
            from mutagen.wave import WAVE
            cls = MP3 if s == ".mp3" else WAVE
            f = cls(str(tmp))
            if f.tags is None:
                f.add_tags()
            if album:
                f.tags.setall("TALB", [_id3.TALB(encoding=3, text=[album])])
            if album_artist:
                f.tags.setall("TPE2", [_id3.TPE2(encoding=3, text=split_multi_artist(album_artist))])
            if track_total:
                cur_trck = f.tags.getall("TRCK")
                cur_track = cur_trck[0].text[0].split("/")[0] if cur_trck else "0"
                f.tags.setall("TRCK", [_id3.TRCK(encoding=3, text=[f"{cur_track}/{track_total}"])])
            if disc_total:
                cur_tpos = f.tags.getall("TPOS")
                cur_disc = cur_tpos[0].text[0].split("/")[0] if cur_tpos else "0"
                f.tags.setall("TPOS", [_id3.TPOS(encoding=3, text=[f"{cur_disc}/{disc_total}"])])
            if mb_album_id:
                f.tags.delall("TXXX:MusicBrainz Album Id")
                f.tags.add(_id3.TXXX(encoding=3, desc="MusicBrainz Album Id", text=[mb_album_id]))
            if mb_release_group_id:
                f.tags.delall("TXXX:MusicBrainz Release Group Id")
                f.tags.add(_id3.TXXX(encoding=3, desc="MusicBrainz Release Group Id", text=[mb_release_group_id]))
            f.save()
        elif s in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4
            f = MP4(str(tmp))
            if f.tags is None:
                f.add_tags()
            if album:
                f.tags["\xa9alb"] = [album]
            if album_artist:
                f.tags["aART"] = split_multi_artist(album_artist)
            if track_total is not None:
                cur_trkn = f.tags.get("trkn", [(0, 0)])
                f.tags["trkn"] = [(cur_trkn[0][0], track_total)]
            if disc_total is not None:
                cur_disk = f.tags.get("disk", [(0, 0)])
                f.tags["disk"] = [(cur_disk[0][0], disc_total)]
            if mb_album_id:
                f.tags["----:com.apple.iTunes:MusicBrainz Album Id"] = [mb_album_id.encode("utf-8")]
            if mb_release_group_id:
                f.tags["----:com.apple.iTunes:MusicBrainz Release Group Id"] = [mb_release_group_id.encode("utf-8")]
            f.save()


def write_advisory(path: Path, advisory: int) -> None:
    """Write only the explicit-content advisory flag."""
    s = _suffix(path)
    with atomic_inplace(path) as tmp:
        if s == ".flac":
            from mutagen.flac import FLAC
            f = FLAC(str(tmp))
            f["ITUNESADVISORY"] = [str(advisory)]
            f.save()
        elif s in (".mp3", ".wav"):
            import mutagen.id3 as _id3
            from mutagen.mp3 import MP3
            from mutagen.wave import WAVE
            cls = MP3 if s == ".mp3" else WAVE
            f = cls(str(tmp))
            if f.tags is None:
                f.add_tags()
            f.tags.delall("TXXX:ITUNESADVISORY")
            f.tags.add(_id3.TXXX(encoding=3, desc="ITUNESADVISORY", text=[str(advisory)]))
            f.save()
        elif s in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4
            f = MP4(str(tmp))
            if f.tags is None:
                f.add_tags()
            f.tags["rtng"] = [advisory]
            f.save()


def read_lyrics(path: Path) -> str | None:
    """Read embedded lyrics from ``path`` without a full tag parse."""
    s = _suffix(path)
    if s == ".flac":
        from mutagen.flac import FLAC
        f = FLAC(str(path))
        v = f.get("LYRICS") or f.get("lyrics")
        return v[0] if v else None
    elif s in (".mp3", ".wav"):
        from mutagen.mp3 import MP3
        from mutagen.wave import WAVE
        cls = MP3 if s == ".mp3" else WAVE
        f = cls(str(path))
        if not f.tags:
            return None
        uslt = f.tags.getall("USLT")
        return uslt[0].text if uslt else None
    elif s in (".m4a", ".mp4"):
        from mutagen.mp4 import MP4
        f = MP4(str(path))
        v = (f.tags or {}).get("\xa9lyr")
        return v[0] if v else None
    return None


def write_cover(path: Path, data: bytes, mime: str = "image/jpeg") -> None:
    """Embed cover art bytes into ``path``."""
    # Resize through the same cap the full writers use so the "Fetch cover art"
    # action doesn't embed full-resolution (often 1500px+) CAA images.
    from .writers._id3common import _cap_cover
    data, mime = _cap_cover(data, mime)
    s = _suffix(path)
    with atomic_inplace(path) as tmp:
        if s == ".flac":
            from mutagen.flac import FLAC, Picture
            f = FLAC(str(tmp))
            f.clear_pictures()
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.data = data
            f.add_picture(pic)
            f.save()
        elif s in (".mp3", ".wav"):
            import mutagen.id3 as _id3
            from mutagen.mp3 import MP3
            from mutagen.wave import WAVE
            cls = MP3 if s == ".mp3" else WAVE
            f = cls(str(tmp))
            if f.tags is None:
                f.add_tags()
            f.tags.delall("APIC")
            f.tags.add(_id3.APIC(encoding=3, mime=mime, type=3, desc="Cover (front)", data=data))
            f.save()
        elif s in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4, MP4Cover
            fmt = MP4Cover.FORMAT_PNG if "png" in mime.lower() else MP4Cover.FORMAT_JPEG
            f = MP4(str(tmp))
            if f.tags is None:
                f.add_tags()
            f.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
            f.save()
