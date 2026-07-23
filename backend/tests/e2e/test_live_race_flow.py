"""Playwright stubs for the live circuit map / timing tower UI (Days 25-28).

Skipped until the web frontend exists — see CLAUDE.md's "Planned Feature —
Live Circuit Map" section. Written using playwright's sync_playwright()
context manager directly rather than the pytest-playwright plugin's `page`
fixture, since only the `playwright` package itself is a project dependency
today (pytest-playwright is not) — this keeps these stubs valid, runnable
Playwright tests the day they're un-skipped, without needing a new
dependency added first.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.skip(reason="frontend not built yet — enable Day 25")


@pytest.mark.e2e
def test_live_race_page_loads(base_url: str) -> None:
    """The live race page (circuit map + timing tower) loads without error."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(f"{base_url}/race/live")
        assert page.title() != ""
        browser.close()


@pytest.mark.e2e
def test_circuit_map_renders_driver_dots(base_url: str) -> None:
    """All 20 team-colored driver dots render on the SVG circuit outline."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(f"{base_url}/race/live")
        assert page.locator(".driver-dot").count() == 20
        browser.close()


@pytest.mark.e2e
def test_selecting_driver_syncs_timing_tower_and_circuit_map(base_url: str) -> None:
    """Selecting a driver in the timing tower highlights the same driver's dot."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(f"{base_url}/race/live")
        page.locator(".timing-row").first.click()
        assert page.locator(".driver-dot.selected").count() == 1
        browser.close()
