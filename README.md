<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/stelleye-logo-dark.png">
    <img src="docs/brand/stelleye-logo-light.png" alt="Stelleye" width="420">
  </picture>
</p>

# Stelleye

**Live video help from sighted guides for blind and low-vision people, paid by the minute in USDC on Stellar.**

**Try it live (Stellar testnet):** https://samanhazemijebelli--stelleye.us-east.modal.run. Sign in, open
*Demo accounts*, and use Maria (caller) in one browser and Ana or Kofi (guide) in another.

A blind person taps one big button (or picks a guide they trust). A verified guide video calls them
on WhatsApp and describes what the camera sees: a medicine label, the right bus, a form. The
caller's budget is locked in a Soroban smart contract before the call starts; when either side hangs
up, the contract bills the exact seconds used from ledger time, refunds the rest instantly, and pays
the guide straight away.

Money moves on real rails: callers **Add money** by card or bank transfer and guides **Cash out** to a
bank through a Stellar anchor (SEP-24), in Circle's USDC. On testnet that is Stellar's test anchor and
Circle's testnet USDC; production swaps in a licensed anchor such as MoneyGram Ramps by configuration.

Built for **HackMeridian 2026** (Genesis category, Stellar Unlocked track).

## Why

- Paid visual-interpretation services exist, but they are subscription-based, region-locked, and slow to pay their agents.
- Volunteer apps are free but give no accountability, no choice of person, and no way to reward someone who helped.
- Sighted people anywhere in the world could earn from this. Cross-border micro-payouts by the minute are exactly what Stellar is good at.

## What a call looks like

1. **Join and add money.** Choose *I need visual help* or *I want to be a guide*. Stelleye creates a Stellar wallet for you (no seed phrase to manage). Callers press *Add money*: the anchor's secure page opens in a new tab for the card or bank step, and Stelleye announces "Added 9.00 USDC" when the money arrives.
2. **Guides are verified.** A reviewer approves each guide, and the approval is written to the guide registry inside the `CallEscrow` contract. The guide's page unlocks by itself.
3. **Choose.** Callers see guides online right now (saved guides first, then cheapest, with languages and rates), or press *Get help now* for the first available.
4. **Match → chat → payment lock.** When the guide accepts, a chat opens for both. The caller's budget is locked on-chain (`open`), and a progress row shows *Guide found → Payment locked*.
5. **Video on WhatsApp.** The guide taps *Video call on WhatsApp* (on a Mac this opens the WhatsApp app, since WhatsApp Web cannot call). The caller hears *"Ana is calling you on WhatsApp now, answer the call"* and only has to answer. Stelleye keeps the meter and chat running, welcomes the caller back, and asks the guide to confirm the call connected.
6. **WhatsApp only.** WhatsApp is the one video channel. If the call does not connect, the guide taps *Could not connect* and tries again; the caller can message the guide from the call screen. (An in-app WebRTC video mode exists for testing only, switched on with `INAPP_VIDEO_TEST_MODE=1`.)
7. **Settle and pay.** Either side ends the call and the receipt opens at once; Stellar fills in the amounts seconds later (`close`: exact seconds billed, the rest refunded). v1 assumes the call helped, so Stelleye then releases the guide's earnings straight away (`confirm`, signed with the caller's custodial key) and both sides see the payment land.
8. **Receipt.** The caller sees what they paid and got back, every transaction, the chat, and can *Save* the guide to call them directly next time. The contract also supports a hold with *Report a problem* (`report`, then a reviewer's `resolve`) and late withdrawal (`claim`); v1 does not use them.
9. **Cash out.** Guides enter bank details on the anchor's page, then confirm in Stelleye ("send 3.00 USDC, you receive 2.70 USD"). Only then does Stelleye pay the anchor, once, and track it to "Sent to your bank".

## Stellar integration

| Piece | Where |
|---|---|
| `CallEscrow` Soroban contract (Rust, soroban-sdk 29) | [`contracts/call_escrow/src/lib.rs`](contracts/call_escrow/src/lib.rs) |
| Deployed on testnet | [`CAYXBP4D…UPTP`](https://stellar.expert/explorer/testnet/contract/CAYXBP4DF3SG2J3224M6HA7X3OXVUCL2XLP3EWPPJ6LTX6UPFMBVUPTP) |
| Circle's testnet USDC (Stellar Asset Contract) | [`CBIELTK6…DAMA`](https://stellar.expert/explorer/testnet/contract/CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA) |
| Anchor client: SEP-1 discovery, SEP-10 sign-in, SEP-24 hosted deposit and withdrawal | [`server/stellar_bridge/anchor.py`](server/stellar_bridge/anchor.py), [`server/wallets/services.py`](server/wallets/services.py) |
| Python contract client (simulate → sign → submit → decode, typed contract errors) | [`server/stellar_bridge/`](server/stellar_bridge/) |
| Wallets (friendbot on testnet, USDC trustline, balances, payments with memos) | [`server/stellar_bridge/accounts.py`](server/stellar_bridge/accounts.py) |

Contract lifecycle: `verify_guide` / `revoke_guide` (on-chain registry) → `open` (lock the budget) →
`close` (per-second billing from ledger time, rounded up, capped at the budget; instant refund;
earnings held) → `confirm` (v1 calls it right after `close`, paying the guide at once) | `report` → `resolve`
(reviewer split) | `claim` (after the hold); the last two are kept for a later version with disputes.
A 5% platform fee is taken only on what the guide receives. Every transaction is linked from the UI to
stellar.expert.

Each call remembers the contract it was opened on, so redeploys never strand a call. Calls made before
the switch to Circle's USDC ran on an earlier contract with a self-issued demo token
(`CCBNJTJT…HQI4C`) and still settle there. Only test money is used: the anchor is
`testanchor.stellar.org`, and its hosted pages accept made-up details.

## Brand

The mark is an eye whose iris is a small planet with a gold orbit, a nod to Stellar's star-and-orbit
feel (an original mark, not Stellar's logo), with a star as the catchlight. Files are in
[`docs/brand/`](docs/brand/) (SVG mark, app icon, PNG lockups) and `server/static/img/`.

## Accessibility

- Screen-reader first: one assertive live region announces every state change once ("Ana says: …", "Payment secured", "About one minute of your budget is left"). Focus moves only when a new view appears.
- Huge targets (the main button is 290 px), visible focus, 4.5:1 contrast, reduced motion, light and dark themes, Atkinson Hyperlegible type.
- Optional speech without a screen reader, plus vibration and tone cues.
- Sign-up works with password managers, has an error summary that takes focus, and errors linked to their fields.
- **axe-core (WCAG 2.2 AA): 0 violations** on every page in the end-to-end run, including the live call screens in both themes.

## Architecture

```
Browser (Django templates + vanilla JS modules)
  member.js / guide.js ── stage.js (call stage: WhatsApp hand-off, meter, chat) · chat.js · money.js
                           └─ videocall.js (WebRTC, simple-peer; test mode only)
        │  WebSocket /ws/live/  (presence, matching, chat, WhatsApp hand-off, resume)
Django 5.2 + Channels 4 (Daphne)
  calls/      consumer, matching, call services, chat, WhatsApp hand-off
  people/     sign-up, profiles, saved guides, verification
  disputes/   reports and reviewer decisions
  wallets/    app-managed Stellar keys (v1), Add money / Cash out
  stellar_bridge/  Soroban RPC, Horizon and SEP-10/24 anchor client
        │                                   │
Stellar testnet: CallEscrow + Circle USDC   Anchor (testanchor.stellar.org → MoneyGram in production)
```

One live page per person: opening Stelleye in a second tab or device takes over, and the older page
offers *Use Stelleye here*, so a call can never run twice. Reloading during a paid call restores the
call screen, chat, meter and WhatsApp hand-off state.

## Run it

Requirements: Python 3.11, [uv](https://docs.astral.sh/uv/), and for redeploying the contract the Stellar CLI and a Rust toolchain.

```bash
cd server
uv venv && uv pip install -r pyproject.toml
.venv/bin/python manage.py migrate
.venv/bin/python manage.py setup_testnet --asset-issuer GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5
                                           # deploys CallEscrow for Circle testnet USDC, writes server/.env
.venv/bin/python manage.py seed_demo       # Maria (caller), Ana and Kofi (guides), reviewer; password "sightline"
.venv/bin/python manage.py runserver 8000 --nostatic   # --nostatic: scripts are never served stale
.venv/bin/python e2e/fund_demo.py --times 2  # tops Maria up with 18 USDC through the test anchor
```

Open http://localhost:8000. With `DJANGO_DEBUG=1` the sign-in page lists one-tap demo accounts.

**Video on a Mac.** The guide's button opens the WhatsApp app with `whatsapp://send?phone=…`, because
calls work only in WhatsApp for Mac, not WhatsApp Web. Phones get the universal `https://wa.me/…` link.
In-app WebRTC video is off in the product (WhatsApp only). For testing it, start the server with
`INAPP_VIDEO_TEST_MODE=1`; on restrictive networks add a TURN relay, e.g. Metered's Open Relay (free monthly
allowance; create an account to get credentials):

```bash
WEBRTC_ICE_SERVERS='[{"urls":"stun:stun.l.google.com:19302"},{"urls":["turn:global.relay.metered.ca:80","turns:global.relay.metered.ca:443?transport=tcp"],"username":"<user>","credential":"<pass>"}]'
```
For phones, serve over HTTPS (for example with a tunnel).

## Deploy on Modal

`modal_app.py` serves the Django ASGI app (pages, API and the live WebSocket) from one always-warm
container: presence and the call channel live in that process's memory. WebSockets stay open for up to
24 hours, and the SQLite database runs on the container's disk with a consistent copy saved to a Modal
Volume every few seconds and on shutdown, then restored on start.

```bash
modal volume create stelleye-data
modal volume put stelleye-data server/db.sqlite3 /db.sqlite3     # optional: start from an existing database
modal secret create stelleye-env --from-dotenv server/.env DJANGO_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
modal deploy modal_app.py
server/.venv/bin/python server/e2e/live_check.py --base https://<workspace>--stelleye.us-east.modal.run   # a real call
```

## Tests

```bash
cd server
uv pip install --group dev -r pyproject.toml && .venv/bin/python -m playwright install chromium webkit
.venv/bin/python manage.py test                 # 51 unit tests, no network (fake escrow and fake anchor)
.venv/bin/python e2e/run_e2e.py                 # full journey on testnet: 3 browsers, fake camera, axe scans
.venv/bin/python e2e/run_money.py               # real-money rails: Add money and Cash out through the test anchor
.venv/bin/python e2e/responsive_sweep.py        # every page on laptops, iPad and phones, light and dark, axe
INAPP_VIDEO_TEST_MODE=1 .venv/bin/python manage.py runserver 8000 --nostatic   # then, for the test-only in-app video:
.venv/bin/python e2e/video_matrix.py            # Mac matrix: Safari engine (WebKit) and Chrome, every pairing
cd ../contracts && cargo test                   # 11 contract tests
```

The end-to-end run signs up a new caller and a new guide (real testnet wallets) and covers:
- the reviewer verifying the guide on-chain;
- the caller seeing the guide appear live, calling them, and the budget locking on-chain;
- the WhatsApp hand-off (Mac app link for the guide, `wa.me` for the phone);
- two-way chat and saving the guide;
- that no in-app video option is offered (WhatsApp only), or, when the server runs in test mode, the WebRTC video with fake cameras;
- reloading mid-call, then settling, the receipt, paying the guide, and adding money through the anchor.

The money run adds 10 USD for a new caller (9.00 USDC arrives), makes a paid call, pays the guide, and has
the guide cash out 1.00 USDC to a bank account after confirming the amounts.

## Limitations and next steps

- **Smart accounts.** Wallets are app-managed keys so blind users never handle seed phrases. Next: passkey smart accounts (Face ID / Touch ID) with fee sponsorship, so users own their funds and never need XLM.
- **Going live with money.** The rails are production-shaped (Circle USDC, SEP-10/24). Mainnet needs a licensed anchor partnership (MoneyGram Ramps, Bitso, Fonbnk), its KYC, and Stelleye's compliance work. Anchor pages are hosted by the anchor, so their screen-reader accessibility must be part of choosing one.
- **Zero XLM for users.** Accounts still get testnet XLM from friendbot. Next: sponsored reserves and fee bumps (or the OpenZeppelin Relayer), so users only ever hold USDC.
- **Scale.** Presence and the channel layer are in-memory (one process). Next: Redis.
- **Dispute evidence.** The contract has a `recording_hash` field that is still unused. Next: store a hash of the chat transcript (and, with consent, a recording) there at the end of each call.
- **Guide verification** is a manual reviewer step today. Next: ID checks before the on-chain approval.
