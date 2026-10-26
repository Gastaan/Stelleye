"""A real call on a running deployment, with the demo accounts: the guide on a laptop, the caller on an iPhone.

Ana (desktop Chrome) goes online, Maria (iPhone, Safari's engine) sees her appear live and calls her,
Ana accepts, the budget is locked on Stellar, the WhatsApp hand-off runs, Maria hangs up and gets the
receipt at once, and Ana is paid automatically. Spends a few cents of Maria's testnet USDC.

    server/.venv/bin/python server/e2e/live_check.py --base https://<workspace>--stelleye.us-east.modal.run
"""
import argparse
import time

from playwright.sync_api import Page, expect, sync_playwright

CHAIN = 90_000  # Soroban transactions on testnet: allow for slowness
failures: list[str] = []


def check(ok: bool, label: str) -> None:
    print(f"   {'ok  ' if ok else 'FAIL'} {label}", flush=True)
    if not ok:
        failures.append(label)


def sign_in(page: Page, base: str, username: str, home: str) -> None:
    page.goto(f"{base}/login/")
    page.click(".auth-demo summary")
    page.locator(f"form[action$='/demo-login/{username}/'] button").click()
    page.wait_for_url(f"**{home}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    base = parser.parse_args().base.rstrip("/")

    with sync_playwright() as p:
        chrome, webkit = p.chromium.launch(), p.webkit.launch()
        guide = chrome.new_context(viewport={"width": 1440, "height": 900}).new_page()
        phone = dict(p.devices["iPhone 14"])
        phone.pop("default_browser_type", None)
        member = webkit.new_context(**phone).new_page()
        errors: list[str] = []
        for page, who in ((guide, "Ana"), (member, "Maria")):
            page.on("pageerror", lambda e, who=who: errors.append(f"{who}: {e}"))

        print("== Ana goes online on a laptop; Maria sees her live on an iPhone", flush=True)
        sign_in(guide, base, "ana", "/guide/")
        if guide.locator("#online-btn").get_attribute("aria-pressed") != "true":
            guide.click("#online-btn")
        expect(guide.locator("#online-btn")).to_have_attribute("aria-pressed", "true", timeout=15_000)
        check(True, "live socket through the deployment: Ana is online")
        sign_in(member, base, "maria", "/help/")
        member.click("#tab-guides")
        member.click("#online-records summary")  # sections start folded
        card = member.locator("#guides-list .guide", has_text="Ana")
        expect(card).to_be_visible(timeout=15_000)
        check(True, "Maria sees Ana in Online now without reloading")

        print("== Maria calls Ana; Ana accepts; the budget is locked on Stellar", flush=True)
        started = time.monotonic()
        card.get_by_role("button", name="Call Ana").click()
        expect(guide.locator("#incoming-view")).to_be_visible(timeout=15_000)
        guide.click("#accept-btn")
        expect(member.locator("#stage")).to_have_attribute("data-state", "live", timeout=CHAIN)
        expect(guide.locator("#stage")).to_have_attribute("data-state", "live", timeout=CHAIN)
        lock = member.locator("#stage-badge a").get_attribute("href") or ""
        check("/tx/" in lock, f"budget locked on Stellar in {time.monotonic() - started:.0f}s: {lock[-20:]}")

        print("== WhatsApp hand-off", flush=True)
        guide.evaluate("""() => document.addEventListener('click', (e) => {
            const a = e.target.closest('a[href^="whatsapp:"]');
            if (a) { e.preventDefault(); window.__waOpened = a.href; } }, true)""")
        guide.click("#wa-open")
        check((guide.evaluate("() => window.__waOpened") or "").startswith("whatsapp://send?phone="), "Ana's laptop opens the WhatsApp app")
        expect(member.locator("#wa-title")).to_contain_text("Ana is calling you on WhatsApp now", timeout=15_000)
        guide.click("#wa-connected")
        expect(member.locator("#wa-title")).to_contain_text("On WhatsApp video with Ana", timeout=15_000)
        check(True, "Maria's iPhone shows the WhatsApp video call as connected")
        member.wait_for_timeout(6000)  # a few billable seconds

        print("== Maria hangs up: settled on Stellar, receipt on both sides", flush=True)
        hung_up = time.monotonic()
        member.click("#ctl-end")
        try:
            expect(member.locator("#receipt")).to_be_visible(timeout=5_000)
        except AssertionError:
            print("   script errors so far:", errors or "none", flush=True)
            raise
        check(time.monotonic() - hung_up < 3, f"receipt opened {time.monotonic() - hung_up:.1f}s after hanging up")
        expect(member.locator("#r-paid")).not_to_have_text("Settling…", timeout=CHAIN)
        paid, back = member.locator("#r-paid").inner_text(), member.locator("#r-refund").inner_text()
        check(paid != "0.00 USDC", f"Maria paid {paid} and got {back} back")
        expect(member.locator("#r-payout-tx")).to_be_visible(timeout=CHAIN)
        check(True, "Ana was paid automatically when the call ended")
        expect(guide.locator("#ended-text")).to_contain_text("is in your wallet", timeout=15_000)
        check(guide.locator("#online-btn").is_hidden(), "Ana's call summary sits in the online button's place")
        check(not errors, f"no script errors on either page {errors[:2] if errors else ''}")
        guide.click("#ended-ok")
        guide.click("#online-btn")  # leave Ana offline again
        chrome.close()
        webkit.close()

    print(f"\n{len(failures)} failed checks")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
