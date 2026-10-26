"""Every page and tab on the laptops and phones a jury is likely to use, in light and dark.

Checks: no sideways scrolling, nothing sticking out past the screen edge, a one-line header,
tap targets of at least 44 px on touch screens (24 px is the hard floor), no script errors and
no axe findings; plus the account menu, the theme switch (remembered across reloads) and the
guide's online button. The live socket is faked, so this never takes over a real session.

    server/.venv/bin/python server/e2e/responsive_sweep.py [--base http://localhost:8000] [--quick]

Screenshots land in server/e2e/artifacts/sweep/; index.html there shows them side by side.
"""
import argparse
import json
import pathlib

from playwright.sync_api import Page, sync_playwright

HERE = pathlib.Path(__file__).parent
AXE = (HERE / "vendor" / "axe-4.10.3.min.js").read_text()
OUT = HERE / "artifacts" / "sweep"

DESKTOPS = [("laptop-1280", 1280, 800), ("laptop-1440", 1440, 900), ("desktop-1920", 1920, 1080), ("ipad-820", 820, 1180)]
PHONES = [("iphone-14", "iPhone 14"), ("pixel-7", "Pixel 7"), ("android-360", None)]

MEMBER_TABS = ("help", "guides", "wallet", "account")
GUIDE_TABS = ("calls", "earnings", "account")

PROBE = """() => {
  const vw = document.documentElement.clientWidth;
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    const cs = getComputedStyle(el);
    const folded = el.closest("details:not([open])");
    if (folded && !el.closest("summary")) return false;
    return cs.visibility !== "hidden" && cs.display !== "none" && !el.closest(".visually-hidden,[hidden]");
  };
  const name = (el) => el.id ? `#${el.id}` : `${el.tagName.toLowerCase()}.${[...el.classList].join(".")}`;
  const sticking = [...document.querySelectorAll("body *")]
    .filter((el) => visible(el) && !el.closest(".stage,[aria-hidden=true],svg") && el.getBoundingClientRect().right > vw + 1)
    .slice(0, 4).map(name);
  const header = document.querySelector(".app-header").getBoundingClientRect();
  const small = [...document.querySelectorAll("a[href], button, input:not([type=hidden]), select, summary, [role=tab]")]
    .filter((el) => visible(el) && !el.closest("p, li .list-item__meta, .site-footer, .tip__panel"))
    // A radio or checkbox wrapped in its label is tapped through the whole label.
    .map((el) => (el.matches("input[type=radio], input[type=checkbox]") && el.closest("label")) || el)
    .map((el) => { const r = el.getBoundingClientRect(); return { n: name(el), w: Math.round(r.width), h: Math.round(r.height) }; })
    .filter((t) => t.w < 44 || t.h < 44);
  return { scroll: document.documentElement.scrollWidth - vw, sticking, header: Math.round(header.height), small };
}"""


def fake_live(ws):
    """Stands in for the live socket: answers go online / go offline, ignores everything else."""
    def on_message(message):
        kind = json.loads(message).get("type")
        if kind in ("go_online", "go_offline"):
            ws.send(json.dumps({"type": "presence", "online": kind == "go_online"}))
    ws.on_message(on_message)


class Sweep:
    def __init__(self, base: str, full_axe: bool):
        self.base, self.full_axe = base, full_axe
        self.problems: list[str] = []
        self.shots: list[tuple[str, str]] = []
        self.states = 0

    def check(self, page: Page, label: str, touch: bool, axe: bool) -> None:
        self.states += 1
        page.wait_for_timeout(150)
        probe = page.evaluate(PROBE)
        if probe["scroll"] > 1:
            self.problems.append(f"{label}: scrolls sideways by {probe['scroll']}px")
        if probe["sticking"]:
            self.problems.append(f"{label}: sticks out past the screen edge: {', '.join(probe['sticking'])}")
        if probe["header"] > 80:
            self.problems.append(f"{label}: header wraps ({probe['header']}px tall)")
        floor = [t for t in probe["small"] if t["w"] < 24 or t["h"] < 24]
        if floor:
            self.problems.append(f"{label}: tap targets under 24px: {floor[:4]}")
        elif touch and probe["small"]:
            self.problems.append(f"{label}: tap targets under 44px: {probe['small'][:4]}")
        if axe:
            page.add_script_tag(content=AXE)
            found = page.evaluate("async () => (await axe.run(document, {resultTypes: ['violations']})).violations"
                                  ".map(v => `${v.id} (${v.impact}) ${v.nodes.slice(0, 2).map(n => n.target.join(' ')).join(' | ')}`)")
            for v in found:
                self.problems.append(f"{label}: axe {v}")
        name = label.replace(" ", "_").replace("/", "-")
        page.screenshot(path=str(OUT / f"{name}.png"), full_page=True, scale="css")
        self.shots.append((label, f"{name}.png"))

    def run_context(self, browser, device: str, theme: str, ctx_options: dict, touch: bool) -> None:
        tag = f"{browser.browser_type.name}/{device}/{theme}"
        axe = self.full_axe or device in ("laptop-1440", "iphone-14", "pixel-7")
        ctx = browser.new_context(color_scheme=theme, **ctx_options)
        ctx.route_web_socket("**/ws/live/", fake_live)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{self.base}/")
        self.check(page, f"{tag} landing", touch, axe)
        page.click(".about-link")
        page.wait_for_url("**/about/")
        self.check(page, f"{tag} about", touch, axe)
        page.click("#clear-label")
        if page.locator("#med-label.is-blurred").count() or page.get_attribute("#clear-label", "aria-pressed") != "true":
            self.problems.append(f"{tag} about: the label did not come clear")
        page.goto(f"{self.base}/login/")
        page.click(".auth-demo summary")
        self.check(page, f"{tag} sign-in", touch, axe)
        page.goto(f"{self.base}/join/")
        page.click("label:has(#id_role_guide)")
        self.check(page, f"{tag} sign-up", touch, axe)

        for who, path, tabs in (("maria", "/help/", MEMBER_TABS), ("ana", "/guide/", GUIDE_TABS)):
            page.goto(f"{self.base}/login/")
            page.click(".auth-demo summary")
            page.locator(f"form[action$='/demo-login/{who}/'] button").click()
            page.wait_for_url(f"**{path}")
            for t in tabs:
                page.click(f"#tab-{t}")
                if not page.locator(f"#panel-{t}").is_visible():
                    self.problems.append(f"{tag} {who}: tab {t} did not open")
                self.check(page, f"{tag} {who}-{t}", touch, axe)
            if who == "maria":
                page.click("#tab-help")
                page.click(".help-meta .tip__btn")
                self.check(page, f"{tag} maria-help-tip", touch, False)
                page.keyboard.press("Escape")
                page.click("#call-options summary")
                self.check(page, f"{tag} maria-help-options", touch, False)
                page.keyboard.press("Escape")
            else:
                page.click("#tab-calls")
                page.click("#online-btn")
                if page.locator("#online-btn").get_attribute("aria-pressed") != "true":
                    self.problems.append(f"{tag} ana: online button did not switch on")
                self.check(page, f"{tag} ana-online", touch, axe)
                page.click("#online-btn")
            # Account menu, then sign out.
            page.click("#account-btn")
            if not page.locator("#account-panel").is_visible():
                self.problems.append(f"{tag} {who}: account menu did not open")
            self.check(page, f"{tag} {who}-menu", touch, False)
            page.keyboard.press("Escape")
            if page.locator("#account-panel").is_visible():
                self.problems.append(f"{tag} {who}: Escape did not close the account menu")
            page.click("#account-btn")
            page.locator("#account-panel button[type=submit]").click()
            page.wait_for_url(f"{self.base}/")

        page.goto(f"{self.base}/login/")
        page.click(".auth-demo summary")
        page.locator("form[action$='/demo-login/reviewer/'] button").click()
        page.wait_for_url("**/review/")
        self.check(page, f"{tag} review", touch, axe)

        if errors:
            self.problems.append(f"{tag}: script errors {errors[:3]}")
        ctx.close()

    def theme_switch(self, browser) -> None:
        """The switch flips the theme, is remembered across reloads, and beats the system setting."""
        ctx = browser.new_context(color_scheme="light", viewport={"width": 1280, "height": 800})
        ctx.route_web_socket("**/ws/live/", fake_live)
        page = ctx.new_page()
        page.goto(f"{self.base}/")
        theme = lambda: page.evaluate("document.documentElement.dataset.theme")
        ok = theme() == "light"
        page.click(".theme-btn")
        ok &= theme() == "dark" and page.get_attribute(".theme-btn", "aria-pressed") == "true"
        page.reload()
        ok &= theme() == "dark"
        page.emulate_media(color_scheme="light")
        ok &= theme() == "dark"  # a saved choice wins over the system
        page.goto(f"{self.base}/login/")
        page.click(".auth-demo summary")
        page.locator("form[action$='/demo-login/maria/'] button").click()
        page.wait_for_url("**/help/")
        ok &= page.get_attribute(".theme-btn", "aria-pressed") == "true"
        page.click(".theme-btn")
        ok &= theme() == "light"
        page.click("#account-btn")
        page.click("#account-panel a.menu-item")
        page.wait_for_function("location.hash === '#account'")
        page.locator("#panel-account").wait_for(state="visible", timeout=3000)
        ok &= page.locator("#account-panel").is_hidden()
        print(f"   {'ok  ' if ok else 'FAIL'} theme switch flips, is remembered, beats the system; menu's Account opens the tab", flush=True)
        if not ok:
            self.problems.append(f"{browser.browser_type.name}: theme switch or account link misbehaved")
        ctx.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--quick", action="store_true", help="axe only on the 1440 laptop and the phones")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    sweep = Sweep(args.base, full_axe=not args.quick)

    with sync_playwright() as p:
        for engine in (p.chromium, p.webkit):
            browser = engine.launch()
            sweep.theme_switch(browser)
            for theme in ("light", "dark"):
                for device, width, height in DESKTOPS:
                    if engine is p.webkit and device not in ("laptop-1440", "ipad-820"):
                        continue  # desktop Safari on a MacBook, and iPad
                    sweep.run_context(browser, device, theme, {"viewport": {"width": width, "height": height}}, touch=device == "ipad-820")
                    print(f"   done {engine.name}/{device}/{theme}", flush=True)
                for device, preset in PHONES:
                    if (engine is p.webkit) != (preset == "iPhone 14"):
                        continue  # iPhone on WebKit, Android on Chromium
                    options = dict(p.devices[preset]) if preset else {
                        "viewport": {"width": 360, "height": 740}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True}
                    options.pop("default_browser_type", None)
                    sweep.run_context(browser, device, theme, options, touch=True)
                    print(f"   done {engine.name}/{device}/{theme}", flush=True)
            browser.close()

    cells = "".join(f'<figure><img src="{f}" loading="lazy"><figcaption>{label}</figcaption></figure>' for label, f in sweep.shots)
    (OUT / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Sweep</title><style>body{font:14px system-ui;margin:16px;display:grid;"
        "grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}img{width:100%;border:1px solid #ccc}"
        f"figure{{margin:0}}</style>{cells}")
    (OUT / "problems.json").write_text(json.dumps(sweep.problems, indent=2))
    print(f"\n{sweep.states} page states checked, {len(sweep.problems)} problems")
    for problem in sweep.problems:
        print(f"  - {problem}")


if __name__ == "__main__":
    main()
