"""End-to-end run of the whole Stelleye journey against a live server and the Stellar testnet.

Three browsers: a brand-new member (phone size), a brand-new guide (laptop size) and the reviewer.
  join (wallet created on testnet) -> reviewer verifies the guide on-chain -> guide goes online ->
  member sees the guide live and calls them -> budget locked on-chain -> WhatsApp hand-off ->
  chat -> save the guide -> backup in-app video (fake camera) -> member reloads mid-call ->
  end and settle on-chain -> receipt -> pay the guide now -> add funds.
Every page is scanned with axe-core (WCAG 2.2 A/AA); screenshots go to e2e/artifacts/.

    server/.venv/bin/python server/e2e/run_e2e.py [--base http://localhost:8000] [--headed]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

from anchor_helpers import add_money

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE / "artifacts"
AXE = (HERE / "vendor" / "axe-4.10.3.min.js").read_text()
CHAIN = 90_000  # ms to wait for a testnet round-trip
PHONE = {"width": 390, "height": 844}
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
LAPTOP = {"width": 1280, "height": 860}

findings: list[dict] = []
failures: list[str] = []


def step(name: str) -> None:
    print(f"\n== {name}", flush=True)


def shot(page: Page, name: str, full: bool = False) -> None:
    page.screenshot(path=str(ARTIFACTS / f"{name}.png"), full_page=full)


def axe(page: Page, name: str) -> None:
    """Run axe-core and record serious or critical WCAG violations."""
    page.add_script_tag(content=AXE)
    result = page.evaluate("""async () => {
        const r = await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] } });
        return r.violations.map(v => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.map(n => n.target.join(' ')).slice(0, 4) }));
    }""")
    serious = [v for v in result if v["impact"] in ("serious", "critical")]
    print(f"   axe {name}: {len(result)} violations, {len(serious)} serious/critical", flush=True)
    for v in result:
        print(f"     - [{v['impact']}] {v['id']}: {v['help']} -> {v['nodes']}", flush=True)
        findings.append({"page": name, **v})


def check(condition: bool, message: str) -> None:
    print(f"   {'ok ' if condition else 'FAIL'} {message}", flush=True)
    if not condition:
        failures.append(message)


def announcer(page: Page) -> str:
    return page.locator("#announcer").inner_text()


def signup(page: Page, base: str, role: str, name: str, username: str, whatsapp: str, rate: str | None = None) -> None:
    page.goto(f"{base}/join/?as={role}")
    axe(page, f"signup-{role}")
    page.fill("#id_display_name", name)
    page.fill("#id_username", username)
    page.fill("#id_password", "sightline-e2e")
    page.fill("#id_whatsapp", whatsapp)
    page.fill("#id_city", "Lisbon")
    if rate:
        page.fill("#id_rate", rate)
    shot(page, f"01-signup-{role}", full=True)
    started = time.monotonic()
    page.click("#signup-submit")
    page.wait_for_url(re.compile(r"/(help|guide)/$"), timeout=CHAIN)
    print(f"   {role} account and testnet wallet created in {time.monotonic() - started:.1f}s", flush=True)


def video_playing(page: Page, selector: str) -> bool:
    return page.evaluate(f"() => {{ const v = document.querySelector('{selector}'); return !!v && v.videoWidth > 0 && !v.paused; }}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--debug-ws", action="store_true", help="print every live message the browsers receive")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    ARTIFACTS.mkdir(exist_ok=True)
    suffix = time.strftime("%H%M%S")
    member_name, guide_name = "Lucia", "Tomas"
    member_user, guide_user = f"lucia{suffix}", f"tomas{suffix}"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, args=[
            "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required",
        ])
        member_ctx = browser.new_context(viewport=PHONE, has_touch=True, user_agent=IPHONE_UA, permissions=["camera", "microphone"])
        guide_ctx = browser.new_context(viewport=LAPTOP, permissions=["camera", "microphone"])
        reviewer_ctx = browser.new_context(viewport=LAPTOP)
        for ctx in (member_ctx, guide_ctx, reviewer_ctx):
            # Never leave the test for the real WhatsApp; record that the hand-off link was opened.
            ctx.route("https://wa.me/**", lambda route: route.fulfill(status=200, content_type="text/html", body="<title>WhatsApp</title>WhatsApp stub"))
        member, guide, reviewer = member_ctx.new_page(), guide_ctx.new_page(), reviewer_ctx.new_page()
        for label, page in (("member", member), ("guide", guide), ("reviewer", reviewer)):
            page.on("pageerror", lambda exc, label=label: (failures.append(f"{label} page error: {exc}"), print(f"   [{label} PAGE ERROR] {exc}", flush=True)))
            page.on("console", lambda msg, label=label: msg.type == "error" and print(f"   [{label} console] {msg.text}", flush=True))
            if args.debug_ws:
                def log_frames(ws, label=label):
                    ws.on("framereceived", lambda frame: print(f"   [{label} <-] {str(frame)[:160]}", flush=True))
                    ws.on("framesent", lambda frame: print(f"   [{label} ->] {str(frame)[:160]}", flush=True))
                page.on("websocket", log_frames)

        step("Landing page: introduction only, actions in the header")
        member.goto(f"{base}/")
        check(member.locator("form input[name=password]").count() == 0, "no sign-in form on the landing page")
        check(member.get_by_role("navigation", name="Main").count() == 1, "visitors get the main navigation")
        axe(member, "landing")
        shot(member, "00-landing", full=True)
        member.goto(f"{base}/login/")
        axe(member, "sign-in")

        step("New member joins (wallet created and funded on testnet)")
        signup(member, base, "member", member_name, member_user, "+351 912 000 111")
        member.click("#tab-wallet")
        expect(member.locator("#balance")).to_have_text("0.00", timeout=20_000)
        add_money(member, "10")  # real-money rails: the SEP-24 test anchor pays Circle testnet USDC
        expect(member.locator("#balance")).to_have_text("9.00", timeout=20_000)
        check(True, "member added 10 USD through the anchor and holds 9.00 USDC")
        axe(member, "member-home")
        shot(member, "02-member-home", full=True)

        step("New guide joins and waits for verification")
        signup(guide, base, "guide", guide_name, guide_user, "+351 913 000 222", rate="0.30")
        expect(guide.locator("#verify-card")).to_be_visible()
        check(guide.locator("#online-btn").is_disabled(), "unverified guide cannot go online")
        axe(guide, "guide-home-unverified")
        shot(guide, "03-guide-unverified", full=True)

        step("Reviewer verifies the guide on-chain; the guide's page updates by itself")
        reviewer.goto(f"{base}/login/")
        reviewer.locator(".auth-demo summary").click()  # demo accounts sit in a collapsed section
        reviewer.locator("form[action$='/demo-login/reviewer/'] button").click()
        reviewer.wait_for_url(f"{base}/review/")
        axe(reviewer, "review")
        reviewer.get_by_role("button", name=f"Verify {guide_name} on Stellar").click()
        expect(reviewer.locator(".message.success")).to_contain_text("verified in the on-chain guide registry", timeout=CHAIN)
        shot(reviewer, "04-review-verified", full=True)
        expect(guide.locator("#online-btn")).to_be_enabled(timeout=10_000)
        expect(guide.locator("#verify-card")).to_be_hidden()
        check(True, "guide page enabled the online toggle after the on-chain verification")

        step("Guide goes online; the member sees them appear live")
        guide.click("#online-btn")
        expect(guide.locator("#online-btn")).to_have_attribute("aria-pressed", "true")
        member.click("#tab-guides")
        card = member.locator("#guides-list .guide", has_text=guide_name)
        expect(card).to_be_visible(timeout=10_000)
        expect(card.locator(".badge")).to_have_text("Available")
        check(True, "guide listed as Available on the member's screen without a reload")
        shot(member, "05-member-sees-guide", full=True)

        step("Member calls this guide; the guide accepts")
        card.get_by_role("button", name=f"Call {guide_name}").click()
        expect(member.locator("#stage")).to_be_visible()
        expect(guide.locator("#incoming-view")).to_be_visible(timeout=10_000)
        check(f"{member_name} chose you" in guide.locator("#incoming-meta").inner_text(), "guide sees this is a direct call")
        shot(member, "06-member-calling")
        shot(guide, "06-guide-incoming")
        axe(guide, "guide-incoming")
        guide.click("#accept-btn")

        step("Match: chat opens while the budget is locked on Stellar")
        expect(member.locator("#stage")).to_have_attribute("data-state", "matched", timeout=10_000)
        expect(member.locator("#save-peer")).to_be_visible()
        shot(member, "07-member-matched")
        expect(member.locator("#stage")).to_have_attribute("data-state", "live", timeout=CHAIN)
        expect(guide.locator("#stage")).to_have_attribute("data-state", "live", timeout=CHAIN)
        check(member.locator("[data-step='pay']").get_attribute("data-state") == "done", "payment step shows done")
        lock_link = member.locator("#stage-badge a").get_attribute("href") or ""
        check("/tx/" in lock_link, f"lock transaction linked: {lock_link[-24:]}")
        expect(member.locator("#wa-panel")).to_be_visible()
        expect(member.locator("#meter")).to_be_visible()
        shot(member, "08-member-live-whatsapp")
        shot(guide, "08-guide-live-whatsapp")
        axe(member, "member-stage-live")
        axe(guide, "guide-stage-live")

        step("WhatsApp hand-off")
        # On a Mac the guide's button must open the WhatsApp app (whatsapp://), not WhatsApp Web, which cannot call.
        guide.evaluate("""() => document.addEventListener('click', (e) => {
            const a = e.target.closest('a[href^="whatsapp:"]');
            if (a) { e.preventDefault(); window.__waOpened = a.href; } }, true)""")
        guide_href = guide.locator("#wa-open").get_attribute("href") or ""
        check(guide_href.startswith("whatsapp://send?phone=351912000111"), f"Mac guide gets the WhatsApp app link: {guide_href[:44]}")
        check(guide.locator("#wa-hint").is_visible(), "Mac guide sees the 'opens the WhatsApp app' hint")
        member_href = member.locator("#wa-open").get_attribute("href") or ""
        check(member_href.startswith("https://wa.me/351913000222"), f"phone member gets the universal wa.me link: {member_href[:34]}")
        guide.click("#wa-open")
        check(guide.evaluate("() => window.__waOpened") == guide_href, "clicking the button opened the WhatsApp app link")
        expect(member.locator("#wa-title")).to_contain_text(f"{guide_name} is calling you on WhatsApp now", timeout=10_000)
        shot(member, "09-member-wa-calling")
        expect(guide.locator("#wa-confirm")).to_be_visible()
        guide.click("#wa-connected")
        expect(member.locator("[data-step='video']")).to_have_attribute("data-state", "done", timeout=10_000)
        expect(member.locator("#wa-title")).to_contain_text(f"On WhatsApp video with {guide_name}")
        check(True, "member sees the WhatsApp video call as connected")

        step("Chat both ways")
        member.click("#ctl-chat")
        member.fill("#chat-input", "Which bus number is this?")
        member.press("#chat-input", "Enter")
        expect(guide.locator(".msg--peer", has_text="Which bus number is this?")).to_be_visible(timeout=10_000)
        guide.locator(".quick-reply", has_text="I can see your camera.").click()
        expect(member.locator(".msg--peer", has_text="I can see your camera.")).to_be_visible(timeout=10_000)
        check(True, "chat delivered both ways")
        shot(member, "10-member-chat")
        shot(guide, "10-guide-chat")

        step("Member saves the guide")
        member.click("#save-peer")
        expect(member.locator("#save-peer")).to_have_attribute("aria-pressed", "true", timeout=10_000)
        check(True, "guide saved to My guides from the call")

        inapp = member.locator("#use-inapp").count() > 0  # only when the server runs INAPP_VIDEO_TEST_MODE=1
        if not inapp:
            step("WhatsApp only: no in-app video anywhere in the product")
            check(member.locator("#use-inapp, #ctl-mute, #ctl-flip").count() == 0, "caller sees no in-app video option or controls")
            check(guide.locator("#use-inapp, #ctl-mute, #ctl-camera").count() == 0, "guide sees no in-app video option or controls")
            check(member.locator("script[src*='simplepeer']").count() == 0, "the WebRTC library is not even loaded")

            step("Member's page reloads mid-call: the paid call and the WhatsApp state come back")
            member.reload()
            expect(member.locator("#stage")).to_have_attribute("data-state", "live", timeout=15_000)
            expect(member.locator("#wa-title")).to_contain_text(f"On WhatsApp video with {guide_name}")
            check(member.locator(".msg", has_text="Which bus number is this?").count() > 0, "chat history restored after reload")
            time.sleep(8)  # let a few billable seconds pass
        else:
            step("Backup: switch to video in Stelleye (fake camera)")
            member.click("#use-inapp")
            expect(guide.locator("#stage")).to_have_attribute("data-channel", "inapp", timeout=10_000)
            expect(member.locator("#stage")).to_have_attribute("data-channel", "inapp")
            if args.debug_ws:
                time.sleep(4)
                for label, page in (("member", member), ("guide", guide)):
                    print(f"   [{label} video]", page.evaluate("""() => { const v = window.sightlineStage.video;
                        return { state: v.state, callId: v.callId, settled: v.mediaSettled, announced: v.announced, gen: v.gen,
                                 hasPeer: !!v.peer, local: !!v.local, queue: v.queue.length, media: !!navigator.mediaDevices }; }"""), flush=True)
            guide.wait_for_function("() => { const v = document.querySelector('#video-main'); return v && v.videoWidth > 0; }", timeout=30_000)
            member.wait_for_function("() => { const v = document.querySelector('#video-pip'); return v && v.videoWidth > 0; }", timeout=30_000)
            check(video_playing(guide, "#video-main"), "guide sees the member's camera over WebRTC")
            check(video_playing(member, "#video-main"), "member sees their own camera preview")
            expect(member.locator("[data-step='video']")).to_have_attribute("data-state", "done", timeout=15_000)
            shot(member, "11-member-inapp-video")
            shot(guide, "11-guide-inapp-video")

            step("Member's page reloads mid-call: the paid call and the video come back")
            member.reload()
            expect(member.locator("#stage")).to_have_attribute("data-state", "live", timeout=15_000)
            expect(member.locator("#stage")).to_have_attribute("data-channel", "inapp")
            guide.wait_for_function("() => { const v = document.querySelector('#video-main'); return v && v.videoWidth > 0; }", timeout=30_000)
            member.wait_for_function("() => { const v = document.querySelector('#video-pip'); return v && v.videoWidth > 0; }", timeout=30_000)
            check(member.locator(".msg", has_text="Which bus number is this?").count() > 0, "chat history restored after reload")
            check(True, "video reconnected after the member's reload")
            member.click("#use-whatsapp")
            expect(guide.locator("#stage")).to_have_attribute("data-channel", "whatsapp", timeout=10_000)
            time.sleep(8)  # let a few billable seconds pass


        step("Member ends the call: settled on Stellar, receipt")
        member.click("#ctl-end")
        expect(member.locator("#receipt")).to_be_visible(timeout=CHAIN)
        paid = member.locator("#r-paid").inner_text()
        refund = member.locator("#r-refund").inner_text()
        check(paid != "0.00 USDC", f"member paid {paid}, got back {refund}")
        check(member.locator("#r-save-btn").get_attribute("aria-pressed") == "true", "receipt shows the guide as saved")
        member.locator(".transcript summary").click()
        check(member.locator("#r-transcript", has_text="Which bus number is this?").count() == 1, "transcript on the receipt")
        axe(member, "member-receipt")
        shot(member, "12-member-receipt", full=True)
        expect(guide.locator("#ended-view")).to_be_visible(timeout=15_000)
        shot(guide, "12-guide-ended", full=True)

        step("Member pays the guide now (releases the hold on-chain)")
        member.click("#confirm-btn")
        expect(member.locator("#r-payout-tx")).to_be_visible(timeout=CHAIN)
        check("/tx/" in (member.locator("#r-payout-tx").get_attribute("href") or ""), "payout transaction linked")
        member.click("#again-btn")
        member.click("#tab-guides")
        saved = member.locator("#saved-list .guide", has_text=guide_name)
        expect(saved).to_be_visible()
        check(True, "guide appears under My guides")

        step("Member adds more money through the anchor")
        before = float(member.locator("#balance").inner_text())
        add_money(member, "5")
        expect(member.locator("#balance")).not_to_have_text(f"{before:.2f}", timeout=CHAIN)
        after = float(member.locator("#balance").inner_text())
        check(abs(after - before - 4.5) < 0.01, f"balance {before:.2f} -> {after:.2f} (5 USD in, anchor fee 0.50)")
        shot(member, "13-member-home-after", full=True)

        step("Guide earnings show the payout")
        guide.reload()
        axe(guide, "guide-home")
        guide.click("#tab-earnings")
        guide.locator("#earnings-records").evaluate("d => { d.open = true; }")  # the call history may be folded
        expect(guide.locator("#earnings-list .list-item").first).to_contain_text(member_name)
        expect(guide.locator("#earnings-list .list-item").first).to_contain_text("Paid out")
        axe(guide, "guide-earnings")
        shot(guide, "14-guide-earnings", full=True)
        guide.click("#tab-account")
        check(guide.locator("#profile-status").inner_text() == "Verified on Stellar", "guide's Account tab shows the verification")
        axe(guide, "guide-account")

        step("Dark and light themes")
        for scheme in ("light", "dark"):
            member.emulate_media(color_scheme=scheme)
            shot(member, f"15-member-home-{scheme}", full=True)
            axe(member, f"member-home-{scheme}")

        browser.close()

    serious = [f for f in findings if f["impact"] in ("serious", "critical")]
    (ARTIFACTS / "axe.json").write_text(json.dumps(findings, indent=2))
    print(f"\n{len(failures)} failed checks, {len(serious)} serious/critical axe findings ({len(findings)} total)")
    for failure in failures:
        print(f"  FAIL {failure}")
    return 1 if failures or serious else 0


if __name__ == "__main__":
    sys.exit(main())
