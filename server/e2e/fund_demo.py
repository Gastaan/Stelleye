"""Top up a demo caller through the SEP-24 test anchor (testnet USDC; fake test details).

    server/.venv/bin/python server/e2e/fund_demo.py [--user maria] [--times 2]
"""
import argparse

from playwright.sync_api import sync_playwright

from anchor_helpers import add_money


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--user", default="maria")
    parser.add_argument("--password", default="sightline")
    parser.add_argument("--times", type=int, default=2, help="each top-up adds 10 USD (9 USDC after the fee)")
    args = parser.parse_args()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(f"{args.base}/login/")
        page.fill("#id_username", args.user)
        page.fill("#id_password", args.password)
        page.click("#signin button[type=submit]")
        page.wait_for_url(f"{args.base}/help/")
        for _ in range(args.times):
            add_money(page, "10")
            print(page.locator("#transfer-status").inner_text(), "Balance:", page.locator("#balance").inner_text(), flush=True)
        browser.close()


if __name__ == "__main__":
    main()
