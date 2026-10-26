"""Real-money rails end to end on testnet, through the UI and Stellar's SEP-24 test anchor.

A new caller adds money (the anchor's hosted page in a new tab, fake test details), the USDC arrives,
they call a new guide (WhatsApp hand-off), settle and pay the guide, and the guide cashes out to a
(fake) bank account after confirming the amounts. The anchor is testanchor.stellar.org, which pays
Circle's testnet USDC; nothing here touches real money.

    server/.venv/bin/python server/e2e/run_money.py [--base http://localhost:8000]
"""
import argparse
import sys
import time

from playwright.sync_api import Page, expect, sync_playwright

import run_e2e as e2e
from anchor_helpers import anchor_tab, fill_anchor_page
from run_e2e import ARTIFACTS, CHAIN, IPHONE_UA, LAPTOP, PHONE, axe, check, shot, signup, step

def balance(page: Page, selector: str = "#balance") -> float:
    return float(page.locator(selector).inner_text())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    ARTIFACTS.mkdir(exist_ok=True)
    suffix = time.strftime("%H%M%S")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        member_ctx = browser.new_context(viewport=PHONE, has_touch=True, user_agent=IPHONE_UA)
        guide_ctx = browser.new_context(viewport=LAPTOP)
        reviewer_ctx = browser.new_context(viewport=LAPTOP)
        for ctx in (member_ctx, guide_ctx):
            ctx.route("https://wa.me/**", lambda route: route.fulfill(status=200, body="WhatsApp stub"))
        member, guide, reviewer = member_ctx.new_page(), guide_ctx.new_page(), reviewer_ctx.new_page()
        for label, page in (("member", member), ("guide", guide)):
            page.on("pageerror", lambda exc, label=label: (e2e.failures.append(f"{label} page error: {exc}"), print(f"   [{label} PAGE ERROR] {exc}", flush=True)))

        step("New caller joins: the wallet holds real (testnet) USDC and starts empty")
        signup(member, base, "member", "Rosa", f"rosa{suffix}", "+351 914 000 333")
        member.click("#tab-wallet")
        expect(member.locator("#balance")).to_have_text("0.00", timeout=20_000)
        check(True, "new caller starts at 0.00 USDC (money comes in through the anchor)")

        step("Add money: the anchor's page opens in a new tab, the USDC arrives by itself")
        member.locator('.money-btn[data-amount="10"]').click()
        anchor = anchor_tab(member_ctx)
        expect(member.locator("#transfer-panel")).to_be_visible(timeout=20_000)
        check("Finish the steps" in member.locator("#transfer-status").inner_text(), "caller is told to finish on the payment page")
        fill_anchor_page(anchor, "10", bank=False)
        shot(anchor, "20-anchor-deposit-done", full=True)
        anchor.close()
        expect(member.locator("#transfer-status")).to_contain_text("Added 9.00 USDC", timeout=CHAIN)
        expect(member.locator("#balance")).to_have_text("9.00", timeout=20_000)
        check("/tx/" in (member.locator("#transfer-tx").get_attribute("href") or ""), "deposit links to the anchor's Stellar payment")
        axe(member, "member-add-money-done")
        shot(member, "21-member-money-added", full=True)

        step("New guide at 2.00 per minute, verified on-chain by the reviewer")
        signup(guide, base, "guide", "Bruno", f"bruno{suffix}", "+351 915 000 444", rate="2.00")
        reviewer.goto(f"{base}/login/")
        reviewer.locator(".auth-demo summary").click()  # demo accounts sit in a collapsed section
        reviewer.locator("form[action$='/demo-login/reviewer/'] button").click()
        reviewer.get_by_role("button", name="Verify Bruno on Stellar").click()
        expect(reviewer.locator(".message.success")).to_be_visible(timeout=CHAIN)
        expect(guide.locator("#online-btn")).to_be_enabled(timeout=15_000)
        guide.click("#online-btn")
        expect(guide.locator("#online-btn")).to_have_attribute("aria-pressed", "true")

        step("Paid call with the WhatsApp hand-off (about 45 seconds), then pay the guide")
        member.click("#tab-guides")
        member.locator("#guides-list .guide", has_text="Bruno").get_by_role("button", name="Call Bruno").click()
        expect(guide.locator("#incoming-view")).to_be_visible(timeout=15_000)
        guide.click("#accept-btn")
        expect(member.locator("#stage")).to_have_attribute("data-state", "live", timeout=CHAIN)
        guide.evaluate("""() => document.addEventListener('click', (e) => {
            const a = e.target.closest('a[href^="whatsapp:"]'); if (a) e.preventDefault(); }, true)""")
        guide.click("#wa-open")
        expect(member.locator("#wa-title")).to_contain_text("Bruno is calling you on WhatsApp now", timeout=10_000)
        guide.click("#wa-connected")
        time.sleep(45)
        member.click("#ctl-end")
        expect(member.locator("#receipt")).to_be_visible(timeout=CHAIN)
        paid = member.locator("#r-paid").inner_text()
        check(paid.startswith("1.") or paid.startswith("2."), f"caller paid {paid} for the call")
        member.click("#confirm-btn")
        expect(member.locator("#r-payout-tx")).to_be_visible(timeout=CHAIN)
        guide.reload()
        earned = balance(guide)
        check(earned >= 1.0, f"guide wallet holds {earned:.2f} USDC")

        step("Guide cashes out 1.00 USDC: bank details on the anchor's page, then confirm here")
        guide.click("#tab-earnings")
        guide.fill("#cashout-amount", "1")
        guide.locator("#cashout-form button[type=submit]").click()
        anchor = anchor_tab(guide_ctx)
        fill_anchor_page(anchor, "1", bank=True)
        anchor.close()
        expect(guide.locator("#transfer-confirm")).to_be_visible(timeout=CHAIN)
        label = guide.locator("#transfer-status").inner_text()
        check("send 1.00 USDC, you receive 0.90 USD" in label, f"guide sees the amounts before sending: {label}")
        axe(guide, "guide-cash-out-confirm")
        shot(guide, "22-guide-cashout-confirm", full=True)
        guide.click("#transfer-confirm")
        expect(guide.locator("#transfer-status")).to_contain_text("on its way to your bank", timeout=CHAIN)
        after = balance(guide)
        check(abs((earned - after) - 1.0) < 0.01, f"guide balance {earned:.2f} -> {after:.2f}")
        check("/tx/" in (guide.locator("#transfer-tx").get_attribute("href") or ""), "cash-out links to the payment on Stellar")
        shot(guide, "23-guide-cashed-out", full=True)

        browser.close()

    print(f"\n{len(e2e.failures)} failed checks; axe serious/critical: {sum(1 for f in e2e.findings if f['impact'] in ('serious', 'critical'))}")
    for failure in e2e.failures:
        print(f"  FAIL {failure}")
    return 1 if e2e.failures or any(f["impact"] in ("serious", "critical") for f in e2e.findings) else 0


if __name__ == "__main__":
    sys.exit(main())
