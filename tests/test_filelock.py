"""S2: the ingest worker (tag write + move) and a revert/move-back triggered
from an HTTP thread must not touch the same physical file concurrently.
"""
import threading
import time

from dragontag.app.library import filelock


def test_path_lock_serializes_same_path(tmp_path):
    p = tmp_path / "song.flac"
    order: list[str] = []
    start = threading.Event()

    def holder():
        with filelock.path_lock(p):
            start.set()
            time.sleep(0.05)
            order.append("holder-done")

    t = threading.Thread(target=holder)
    t.start()
    start.wait()

    with filelock.path_lock(p):
        order.append("waiter-acquired")

    t.join()
    assert order == ["holder-done", "waiter-acquired"]


def test_path_lock_different_paths_dont_contend(tmp_path):
    a, b = tmp_path / "a.flac", tmp_path / "b.flac"
    acquired_b = threading.Event()

    with filelock.path_lock(a):
        with filelock.path_lock(b):
            acquired_b.set()

    assert acquired_b.is_set()


def test_path_lock_resolves_equivalent_paths(tmp_path):
    direct = tmp_path / "song.flac"
    direct.write_bytes(b"x")
    via_dotdot = tmp_path / "sub" / ".." / "song.flac"

    order: list[str] = []
    start = threading.Event()

    def holder():
        with filelock.path_lock(direct):
            start.set()
            time.sleep(0.05)
            order.append("holder-done")

    t = threading.Thread(target=holder)
    t.start()
    start.wait()

    with filelock.path_lock(via_dotdot):
        order.append("waiter-acquired")

    t.join()
    assert order == ["holder-done", "waiter-acquired"]
