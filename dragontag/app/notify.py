"""Bounded, secret-safe webhook delivery."""
from __future__ import annotations

import logging
import threading
from urllib.parse import urlparse

log = logging.getLogger(__name__)


class WebhookDeliveryError(RuntimeError):
    """A user-safe summary that never includes the webhook URL or response body."""


def deliver(url: str, payload: dict) -> None:
    """Post one payload, accepting only direct 2xx responses."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WebhookDeliveryError("invalid webhook URL")
    try:
        import requests
        from .config import settings

        timeout = max(1.0, min(float(settings().network_timeout_seconds), 30.0))
        response = requests.post(
            url, json=payload, timeout=(timeout, timeout), allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise WebhookDeliveryError("request timed out") from exc
    except requests.ConnectionError as exc:
        raise WebhookDeliveryError("connection failed") from exc
    except requests.RequestException as exc:
        raise WebhookDeliveryError("request failed") from exc
    if not 200 <= response.status_code < 300:
        raise WebhookDeliveryError(f"HTTP {response.status_code}")


def _send(url: str, payload: dict) -> None:
    try:
        deliver(url, payload)
    except WebhookDeliveryError as exc:
        log.warning("webhook failed: %s", exc)


def _post(url: str, payload: dict) -> None:
    threading.Thread(target=_send, args=(url, payload), daemon=True, name="dragontag-webhook").start()


def post_done(job, tags) -> None:
    try:
        from .config import settings
        s = settings()
        if not s.webhook_url or not s.webhook_on_done:
            return
        _post(s.webhook_url, {"embeds": [{
            "title": tags.title or job.original_name,
            "description": f"{tags.artist_display} â€” {tags.album}",
            "color": 0x44FF44,
            "footer": {"text": f"dragontag Â· job #{job.id}"},
        }]})
    except Exception:
        log.warning("webhook (done) failed while preparing payload", exc_info=True)


def post_error(job) -> None:
    try:
        from .config import settings
        s = settings()
        if not s.webhook_url or not s.webhook_on_error:
            return
        _post(s.webhook_url, {"embeds": [{
            "title": f"Error: {job.original_name}",
            "description": (job.error or "(no message)")[:2000],
            "color": 0xFF4444,
            "footer": {"text": f"dragontag Â· job #{job.id}"},
        }]})
    except Exception:
        log.warning("webhook (error) failed while preparing payload", exc_info=True)
