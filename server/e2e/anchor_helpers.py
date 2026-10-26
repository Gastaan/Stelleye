"""Driving the SEP-24 test anchor's hosted page in end-to-end runs (fake test details only)."""
from playwright.sync_api import Page

FAKE_PERSON = {"name": "Test", "surname": "User", "email": "test@example.com"}
FAKE_BANK = {"bank": "Test Bank", "account": "12345678"}


def anchor_tab(ctx, timeout_ms: int = 30_000) -> Page:
    """The tab showing the anchor's page. Stelleye sends COOP same-origin, so the browser may swap the
    blank tab for a fresh page when it moves to the anchor; find it by address instead of by handle."""
    waited = 0
    while waited < timeout_ms:
        for page in ctx.pages:
            if page.url.startswith("https://") and "localhost" not in page.url:
                return page
        ctx.pages[0].wait_for_timeout(250)  # a Playwright wait, so new-tab events get processed
        waited += 250
    raise AssertionError("the anchor's page never opened")


def fill_anchor_page(page: Page, amount: str, bank: bool = False) -> None:
    page.wait_for_selector("input[name=amount]", timeout=30_000)
    for name, value in {"amount": amount, **FAKE_PERSON, **(FAKE_BANK if bank else {})}.items():
        page.fill(f"input[name={name}]", value)
    page.get_by_role("button", name="Submit").click()
    page.wait_for_selector("text=Transaction information", timeout=60_000)  # an external test service: allow for slowness


def add_money(page: Page, amount: str = "10") -> None:
    """On a caller's home page: Add money through the anchor and wait until it arrives."""
    from playwright.sync_api import expect

    if page.locator("#tab-wallet").count():
        page.click("#tab-wallet")  # the caller's home is tabbed
    page.locator(f'.money-btn[data-amount="{amount}"]').click()
    tab = anchor_tab(page.context)
    fill_anchor_page(tab, amount)
    tab.close()
    expect(page.locator("#transfer-status")).to_contain_text("Added", timeout=90_000)
