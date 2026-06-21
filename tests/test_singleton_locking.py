"""F11: lazy module-level singletons (``db.engine()`` / ``config.store()``) must
not let two racing threads each construct their own instance. Without the
double-checked lock, two threads can both observe the global as ``None``,
each build (and partially initialize) their own object, with the loser's
instance silently discarded after doing real work (DB migration / settings
file read+write).
"""
import threading
import time

from dragontag.app import config, db


def test_engine_singleton_under_concurrent_first_call(monkeypatch):
    db._engine = None
    calls = []
    real_build = db._build_engine

    def _slow_build():
        calls.append(1)
        time.sleep(0.05)  # widen the race window
        return real_build()

    monkeypatch.setattr(db, "_build_engine", _slow_build)

    barrier = threading.Barrier(5)
    results = []

    def _call():
        barrier.wait()
        results.append(db.engine())

    threads = [threading.Thread(target=_call) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1
    assert len({id(r) for r in results}) == 1


def test_store_singleton_under_concurrent_first_call(monkeypatch):
    config._store = None
    calls = []
    real_init = config._Store.__init__

    def _slow_init(self):
        calls.append(1)
        time.sleep(0.05)
        real_init(self)

    monkeypatch.setattr(config._Store, "__init__", _slow_init)

    barrier = threading.Barrier(5)
    results = []

    def _call():
        barrier.wait()
        results.append(config.store())

    threads = [threading.Thread(target=_call) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1
    assert len({id(r) for r in results}) == 1
