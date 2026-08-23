"""Real Chromium smoke for the standalone Grile UI against local API/PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:5173")
ARTIFACT_DIR = Path(os.environ.get("E2E_ARTIFACT_DIR", "e2e-artifacts"))


def nav(page: Page, label: str) -> None:
    page.locator("nav.app-nav").get_by_role("button", name=label, exact=True).click()


def assert_no_request_error(page: Page) -> None:
    expect(page.locator('.error[role="alert"]')).to_have_count(0)


def capture_failure(page: Page, name: str) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACT_DIR / f"{name}-failure.png"), full_page=True)


def exercise_desktop(page: Page) -> None:
    page.goto(BASE_URL, wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Situație generală — program și grile", level=2)).to_be_visible()
    expect(page.get_by_role("table", name="Magazine și stare operațională")).to_be_visible()
    expect(page.get_by_role("button", name="Performance Store 000", exact=True).first).to_be_visible()
    assert_no_request_error(page)

    nav(page, "Program")
    grid = page.get_by_role("grid", name="Calendar program lunar")
    expect(grid).to_be_visible()
    editable_cell = page.locator("button.matrix-day:not([disabled])").first
    expect(editable_cell).to_be_visible()
    editable_cell.click()
    editor = page.locator(".program-cell-editor-panel")
    expect(editor).to_be_visible()
    first_editor_control = editor.locator("select").first
    expect(first_editor_control).to_be_focused()
    first_editor_control.press("Escape")
    expect(editor).to_be_hidden()
    expect(editable_cell).to_be_focused()
    assert_no_request_error(page)

    nav(page, "Hub")
    first_store = page.get_by_role("button", name="Performance Store 000", exact=True).first
    first_store.click()
    expect(page.get_by_role("heading", name="Performance Store 000", level=2)).to_be_visible()
    control_tab = page.get_by_role("tab", name="Control")
    expect(control_tab).to_have_attribute("aria-selected", "true")
    expect(page.get_by_text("Verificare Sheet", exact=True)).to_be_visible()
    assert_no_request_error(page)
    control_tab.focus()
    control_tab.press("ArrowRight")
    calendar_tab = page.get_by_role("tab", name="Calendar")
    expect(calendar_tab).to_have_attribute("aria-selected", "true")
    expect(calendar_tab).to_be_focused()
    expect(page.get_by_role("grid", name="Calendar program lunar")).to_be_visible()
    assert_no_request_error(page)

    nav(page, "Excepții")
    expect(page.get_by_role("heading", name="Excepții", level=2)).to_be_visible()
    assert_no_request_error(page)

    nav(page, "Management")
    expect(page.get_by_role("heading", name="Management lună", level=2)).to_be_visible()
    expect(page.get_by_role("region", name="Validări")).to_be_visible()
    assert_no_request_error(page)

    nav(page, "Joburi")
    expect(page.get_by_role("heading", name="Joburi și sincronizări", level=2)).to_be_visible()
    assert_no_request_error(page)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACT_DIR / "desktop-jobs.png"), full_page=True)


def exercise_mobile(page: Page) -> None:
    page.goto(BASE_URL, wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Situație generală — program și grile", level=2)).to_be_visible()
    expect(page.locator(".app-sidebar")).to_be_visible()
    expect(page.locator("nav.app-nav")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

    nav(page, "Program")
    expect(page.get_by_role("grid", name="Calendar program lunar")).to_be_visible()
    # The wide calendar is intentionally locally scrollable; the page itself
    # must remain bounded to the phone viewport.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert page.locator(".program-matrix-scroll").evaluate("el => el.scrollWidth > el.clientWidth")
    assert_no_request_error(page)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(ARTIFACT_DIR / "mobile-program.png"), full_page=True)


def main() -> None:
    browser_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            desktop = browser.new_context(viewport={"width": 1440, "height": 1000})
            desktop_page = desktop.new_page()
            desktop_page.on("pageerror", lambda error: browser_errors.append(f"desktop pageerror: {error}"))
            desktop_page.on(
                "console",
                lambda message: browser_errors.append(f"desktop console: {message.text}")
                if message.type == "error"
                else None,
            )
            try:
                exercise_desktop(desktop_page)
            except Exception:
                capture_failure(desktop_page, "desktop")
                raise
            finally:
                desktop.close()

            mobile = browser.new_context(
                viewport={"width": 390, "height": 844},
                device_scale_factor=1,
                is_mobile=True,
                has_touch=True,
            )
            mobile_page = mobile.new_page()
            mobile_page.on("pageerror", lambda error: browser_errors.append(f"mobile pageerror: {error}"))
            mobile_page.on(
                "console",
                lambda message: browser_errors.append(f"mobile console: {message.text}")
                if message.type == "error"
                else None,
            )
            try:
                exercise_mobile(mobile_page)
            except Exception:
                capture_failure(mobile_page, "mobile")
                raise
            finally:
                mobile.close()
        finally:
            browser.close()

    if browser_errors:
        raise AssertionError("Browser runtime errors:\n" + "\n".join(browser_errors))


if __name__ == "__main__":
    main()
