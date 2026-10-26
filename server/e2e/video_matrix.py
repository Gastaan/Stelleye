"""Mac browser matrix for the call: Safari's engine (WebKit) and Google Chrome, in every pairing.

For each (member browser, guide browser): sign in as the seeded demo pair (maria / ana), place a
real paid call on testnet, check the WhatsApp button opens the WhatsApp app (whatsapp://) on a Mac,
switch to the in-app backup video with fake cameras, check both people receive video and sound,
then end the call and see the receipt.

    server/.venv/bin/python server/e2e/video_matrix.py [--base http://localhost:8000] [--pairs webkit:chrome,...]
"""
import argparse
import sys
import time

from playwright.sync_api import Page, expect, sync_playwright

CHAIN = 90_000
FAKE_MEDIA = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"]
DEFAULT_PAIRS = "webkit:chrome,chrome:webkit,webkit:webkit,chrome:chrome"

STREAMS = """(selector) => {
  const v = document.querySelector(selector);
  const s = v && v.srcObject;
  return { width: v ? v.videoWidth : 0, playing: !!v && !v.paused,
           video: s ? s.getVideoTracks().filter(t => t.readyState === 'live').length : 0,
           audio: s ? s.getAudioTracks().filter(t => t.readyState === 'live').length : 0 };
}"""


def launch(pw, kind: str):
    if kind == "webkit":
        return pw.webkit.launch()
    if kind == "chrome":
        return pw.chromium.launch(channel="chrome", args=FAKE_MEDIA)
    return pw.chromium.launch(args=FAKE_MEDIA)


def sign_in(page: Page, base: str, username: str) -> None:
    page.goto(f"{base}/login/")
    page.fill("#id_username", username)
    page.fill("#id_password", "sightline")
    page.click("#signin button[type=submit]")
    page.wait_for_url(f"{base}/{'guide' if username == 'ana' else 'help'}/")


def run_pair(pw, base: str, member_kind: str, guide_kind: str) -> list[str]:
    problems: list[str] = []
    browsers = [launch(pw, member_kind), launch(pw, guide_kind)]
    perms = {"permissions": ["camera", "microphone"]}
    member_ctx = browsers[0].new_context(**({} if member_kind == "webkit" else perms))
    guide_ctx = browsers[1].new_context(**({} if guide_kind == "webkit" else perms))
    member, guide = member_ctx.new_page(), guide_ctx.new_page()
    for label, page in (("member", member), ("guide", guide)):
        page.on("pageerror", lambda exc, label=label: problems.append(f"{label} page error: {exc}"))
    try:
        sign_in(guide, base, "ana")
        sign_in(member, base, "maria")
        member.wait_for_timeout(1500)
        if member.locator("#stage").is_visible():  # a call left running by an earlier run comes back; settle it
            print("   settling a call left over from an earlier run", flush=True)
            member.click("#ctl-end")
            expect(member.locator("#receipt")).to_be_visible(timeout=CHAIN)
            member.click("#again-btn")
            guide.reload()
        if guide.locator("#online-btn").get_attribute("aria-pressed") != "true":
            guide.click("#online-btn")
        expect(guide.locator("#online-btn")).to_have_attribute("aria-pressed", "true")
        member.click("#tab-guides")
        expect(member.locator("#guides-list .guide", has_text="Ana")).to_be_visible(timeout=10_000)
        member.click("#tab-help")
        member.click("#help-btn")
        expect(guide.locator("#incoming-view")).to_be_visible(timeout=15_000)
        guide.click("#accept-btn")
        expect(member.locator("#stage")).to_have_attribute("data-state", "live", timeout=CHAIN)
        expect(guide.locator("#stage")).to_have_attribute("data-state", "live", timeout=CHAIN)

        href = guide.locator("#wa-open").get_attribute("href") or ""
        if not href.startswith("whatsapp://send?phone="):
            problems.append(f"guide WhatsApp link on a Mac should open the app, got {href[:30]}")

        member.click("#use-inapp")
        expect(guide.locator("#stage")).to_have_attribute("data-channel", "inapp", timeout=10_000)
        started = time.monotonic()
        # Headless WebKit's simulated camera can take several seconds to start; real Safari takes about one.
        video_wait = 60_000 if "webkit" in (member_kind, guide_kind) else 30_000
        guide.wait_for_function("() => document.querySelector('#video-main').videoWidth > 0", timeout=video_wait)
        member.wait_for_function("() => document.querySelector('#video-pip').srcObject && document.querySelector('#video-pip').srcObject.getTracks().length > 0", timeout=video_wait)
        elapsed = time.monotonic() - started
        guide_sees = guide.evaluate(STREAMS, "#video-main")
        member_hears = member.evaluate(STREAMS, "#video-pip")
        print(f"   connected in {elapsed:.1f}s; guide sees member {guide_sees}; member gets guide {member_hears}", flush=True)
        if not (guide_sees["width"] > 0 and guide_sees["video"] and guide_sees["audio"]):
            problems.append(f"guide does not receive the member's camera and sound: {guide_sees}")
        if not member_hears["audio"]:
            problems.append(f"member does not receive the guide's sound: {member_hears}")
        flip_hidden = member.locator("#ctl-flip").is_hidden()
        print(f"   member flip button hidden (one camera): {flip_hidden}", flush=True)
        member.screenshot(path=f"{__import__('pathlib').Path(__file__).parent}/artifacts/matrix-{member_kind}-{guide_kind}-member.png")
        guide.screenshot(path=f"{__import__('pathlib').Path(__file__).parent}/artifacts/matrix-{member_kind}-{guide_kind}-guide.png")

        member.click("#ctl-end")
        expect(member.locator("#receipt")).to_be_visible(timeout=CHAIN)
        expect(guide.locator("#ended-view")).to_be_visible(timeout=15_000)
        guide.click("#ended-ok")  # the summary sits in the online button's place until dismissed
        guide.click("#online-btn")
    except Exception as exc:  # noqa: BLE001 - report and move on to the next pair
        problems.append(f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
        for page, label in ((member, "member"), (guide, "guide")):
            try:
                if page.locator("#stage").is_visible() and page.locator("#stage").get_attribute("data-state") == "live":
                    page.click("#ctl-end")
                    page.wait_for_timeout(15_000)
                    break
            except Exception:  # noqa: BLE001
                pass
    finally:
        for b in browsers:
            b.close()
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--pairs", default=DEFAULT_PAIRS, help="member:guide browser pairs (webkit, chrome, chromium)")
    args = parser.parse_args()
    results = {}
    with sync_playwright() as pw:
        for pair in args.pairs.split(","):
            member_kind, guide_kind = pair.split(":")
            print(f"\n== member on {member_kind}, guide on {guide_kind}", flush=True)
            problems = run_pair(pw, args.base.rstrip("/"), member_kind, guide_kind)
            results[pair] = problems
            print("   ok" if not problems else "\n".join(f"   FAIL {p}" for p in problems), flush=True)
    print("\nSummary:")
    for pair, problems in results.items():
        print(f"  {pair:18} {'ok' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if any(results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
