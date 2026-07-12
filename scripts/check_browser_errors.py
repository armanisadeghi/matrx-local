from pathlib import Path

from playwright.sync_api import sync_playwright
import time

REPO_ROOT = Path(__file__).resolve().parent.parent


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def handle_console(msg):
            print(f"[Console] {msg.type}: {msg.text}")

        def handle_pageerror(err):
            print(f"[PageError] {err}")

        page.on("console", handle_console)
        page.on("pageerror", handle_pageerror)

        print("Navigating to localhost:1420...")
        try:
            page.goto("http://localhost:1420")
            time.sleep(5)
            # Take a screenshot
            screenshot_path = REPO_ROOT / "system/logs/blank_screen_test.png"
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=screenshot_path)
            print(f"Screenshot saved to {screenshot_path}")
        except Exception as e:
            print(f"Error: {e}")

        browser.close()


if __name__ == "__main__":
    run()
