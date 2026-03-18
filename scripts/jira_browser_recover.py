#!/usr/bin/env python3
import argparse
import json
import os
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recover Jira browser session and print JSON result")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--profile-dir", default="jira_browser_profile")
    parser.add_argument("--reason", default="")
    parser.add_argument("--headful", action="store_true")
    return parser.parse_args()


def fail(details: str, code: int = 1) -> int:
    print(json.dumps({"success": False, "details": details}))
    return code


def main() -> int:
    args = parse_args()

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception:
        return fail("Playwright Python package is not installed")

    if not args.username or not args.password:
        return fail("Username/password are required for browser recovery")

    login_url = args.base_url.rstrip("/") + "/login.jsp"
    profile_dir = os.path.abspath(args.profile_dir)
    os.makedirs(profile_dir, exist_ok=True)

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=not args.headful,
            )
            page = context.new_page()
            page.goto(login_url, wait_until="domcontentloaded", timeout=30000)

            username_selectors = ["#login-form-username", "input[name='os_username']", "input[name='username']"]
            password_selectors = ["#login-form-password", "input[name='os_password']", "input[type='password']"]
            submit_selectors = ["#login", "#login-form-submit", "button[type='submit']", "input[type='submit']"]

            for selector in username_selectors:
                locator = page.locator(selector)
                if locator.count() > 0:
                    locator.first.fill(args.username)
                    break

            for selector in password_selectors:
                locator = page.locator(selector)
                if locator.count() > 0:
                    locator.first.fill(args.password)
                    break

            submitted = False
            for selector in password_selectors:
                locator = page.locator(selector)
                if locator.count() > 0:
                    locator.first.press("Enter")
                    submitted = True
                    break

            if not submitted:
                forms = page.locator("form")
                if forms.count() > 0:
                    forms.first.evaluate("form => form.requestSubmit ? form.requestSubmit() : form.submit()")
                    submitted = True

            if not submitted:
                for selector in submit_selectors:
                    locator = page.locator(selector)
                    if locator.count() == 0:
                        continue
                    for idx in range(locator.count()):
                        candidate = locator.nth(idx)
                        try:
                            if candidate.is_visible(timeout=1000):
                                candidate.click()
                                submitted = True
                                break
                        except Exception:
                            continue
                    if submitted:
                        break

            if not submitted:
                return fail("Could not find Jira login submit button")

            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except PlaywrightTimeoutError:
                pass

            cookies = context.cookies()
            jsession = next((c for c in cookies if c.get("name") == "JSESSIONID" and c.get("value")), None)
            if not jsession:
                return fail("JSESSIONID cookie was not found after browser recovery attempt")

            cookie_value = f"JSESSIONID={jsession['value']}"
            print(json.dumps({"success": True, "details": "Recovered Jira session via browser flow", "cookie": cookie_value}))
            return 0
    except Exception as ex:
        return fail(f"Browser recovery failed: {ex}")


if __name__ == "__main__":
    raise SystemExit(main())
