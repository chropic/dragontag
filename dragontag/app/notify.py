"""Discord webhook sender. Fire-and-forget; errors are logged, never raised."""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)


def _send(url: str, payload: dict) -> None:
    try:
        import requests
        r = requests.post(url, json=payload, timeout=5)
        r.raise_for_status()
    except Exception as e:
        log.warning("webhook failed: %s", e)


def post_done(job, tags) -> None:
    from .config import settings
    s = settings()
    if not s.webhook_url or not s.webhook_on_done:
        return
    payload = {
        "embeds": [{
            "title": tags.title or job.original_name,
            "description": f"{tags.artist_display} — {tags.album}",
            "color": 0x44FF44,
            "footer": {"text": f"dragontag · job #{job.id}"},
        }]
    }
    threading.Thread(target=_send, args=(s.webhook_url, payload), daemon=True).start()


def post_error(job) -> None:
    from .config import settings
    s = settings()
    if not s.webhook_url or not s.webhook_on_error:
        return
    payload = {
        "embeds": [{
            "title": f"Error: {job.original_name}",
            "description": job.error or "(no message)",
            "color": 0xFF4444,
            "footer": {"text": f"dragontag · job #{job.id}"},
        }]
    }
    threading.Thread(target=_send, args=(s.webhook_url, payload), daemon=True).start()
