"""Optional real-browser queue smoke; run with DRAGONTAG_BROWSER_URL set."""
import os

import pytest


@pytest.mark.skipif(not os.environ.get("DRAGONTAG_BROWSER_URL"), reason="optional local browser smoke")
def test_checkbox_title_and_reload_state_are_independent():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        page.route("**/coverartarchive.org/**", lambda route: route.abort())
        page.goto(os.environ["DRAGONTAG_BROWSER_URL"].rstrip("/") + "/queue")
        card = page.locator(".dt-review-item").first
        checkbox = card.locator('input[name="job_ids"]')
        title = card.locator('a[href^="/jobs/"]').first
        checkbox.click()
        assert page.url.endswith("/queue")
        assert checkbox.is_checked()
        page.reload()
        assert page.locator(".dt-review-item").first.locator('input[name="job_ids"]').is_checked()
        title.click()
        assert "/jobs/" in page.url
        browser.close()
