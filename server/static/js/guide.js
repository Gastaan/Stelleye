// Guide screen: availability, incoming calls, the shared call stage, earnings.
import { getJSON, post } from "./api.js";
import { icon, statusBadge } from "./icons.js";
import { connectLive, showElsewhere } from "./live.js";
import { announce, PATTERNS, spokenDuration, tone, vibrate } from "./speak.js";
import { CallStage } from "./stage.js";
import { bindMoney } from "./money.js";
import { initTabs } from "./tabs.js";
import { bindTips } from "./tips.js";
import { bindWhatsAppForm, focusWhatsAppForm } from "./whatsapp.js";

const $ = (id) => document.getElementById(id);
const config = JSON.parse($("call-config").textContent);
const asset = config.asset;
const LANGUAGE_NAMES = { en: "English", es: "Spanish", fr: "French", de: "German", pt: "Portuguese", ar: "Arabic", fa: "Persian" };
const ONLINE_KEY = "sightline.guideOnline";

let wantOnline = false;
try { wantOnline = sessionStorage.getItem(ONLINE_KEY) === "1"; } catch { /* private mode */ }
let incoming = null; // { callId }
let ringTimer = null;
let endedCallId = null; // the call whose summary is showing

function remember(online) {
  try { sessionStorage.setItem(ONLINE_KEY, online ? "1" : "0"); } catch { /* private mode */ }
}

const stage = new CallStage({ role: "guide", config, live: { send: (...args) => live.send(...args) } });
window.sightlineStage = stage; // for the end-to-end tests and debugging
const tabs = initTabs(document.querySelector(".tabs"));

function showStage() {
  $("home-view").hidden = true;
}

function showHome({ focus = false } = {}) {
  stage.close();
  $("home-view").hidden = false;
  if (focus) $("home-title").focus();
}

// ---- live channel ----------------------------------------------------------------

const live = connectLive((msg) => {
  switch (msg.type) {
    case "_open":
      $("connection").hidden = true;
      if (wantOnline) live.send("go_online");
      return;
    case "_closed": $("connection").hidden = false; return;
    case "_elsewhere": showElsewhere(stage); return;
    case "elsewhere": return;
    case "presence": return setOnline(msg.online);
    case "incoming": return onIncoming(msg);
    case "cancelled":
      stopRinging();
      $("incoming-view").hidden = true;
      incoming = null;
      announce(msg.message);
      return;
    case "matched":
      stopRinging();
      $("incoming-view").hidden = true;
      showStage();
      stage.matched(msg);
      return;
    case "connected":
      showStage();
      stage.live(msg);
      return;
    case "resume":
      showStage();
      stage.resume(msg);
      return;
    case "chat": stage.addMessage(msg); return;
    case "channel": stage.onChannel(msg); return;
    case "signal": stage.handleSignal(msg); return;
    case "status": announce(msg.message); return;
    case "ending": return onEnding(msg);
    case "ended": return onEnded(msg);
    case "paid_out": return onPaidOut(msg);
    case "verified": return onVerified(msg);
    case "error":
      vibrate(PATTERNS.error);
      if (msg.code === "settle_failed" && endedCallId === msg.call_id) {
        $("ended-text").textContent = "Stellar is slow right now. Trying again…";
        setTimeout(() => live.send("hangup", { call_id: msg.call_id }), 3000);
        return;
      }
      $("online-btn").removeAttribute("aria-busy");
      $("accept-btn").removeAttribute("aria-busy");
      if (msg.code === "whatsapp_missing") {
        tabs.reveal($("whatsapp-card"));
        focusWhatsAppForm();
      }
      if (msg.code === "payment_failed") {
        tabs.show("calls");
        showHome({ focus: true });
        $("status-text").textContent = msg.message;
      }
      announce(msg.message);
      return;
    default:
  }
});

function setOnline(online) {
  wantOnline = online;
  remember(online);
  const btn = $("online-btn");
  btn.setAttribute("aria-pressed", String(online));
  btn.removeAttribute("aria-busy");
  $("online-label").textContent = online ? "You're online" : "Go online";
  $("online-sub").textContent = online ? "Tap to go offline" : "Tap to take calls";
  const text = online ? "You are online. Calls will ring here." : "You are offline.";
  $("status-text").textContent = text;
  announce(text);
}

function onVerified(msg) {
  $("verify-card").hidden = true;
  $("online-btn").disabled = false;
  $("online-sub").textContent = "Tap to take calls";
  $("verified-line").hidden = false;
  $("profile-status").textContent = "Verified on Stellar";
  $("status-text").textContent = msg.message;
  announce(msg.message);
  tone("connected");
}

function onIncoming(msg) {
  incoming = { callId: msg.call_id };
  const language = LANGUAGE_NAMES[msg.language] || msg.language;
  $("incoming-avatar").textContent = msg.initials || "?";
  $("incoming-text").textContent = `${msg.member} needs help in ${language}.`;
  $("incoming-meta").textContent = `You earn ${msg.rate} ${asset} a minute. Up to ${msg.budget} ${asset} will be locked on Stellar.`
    + (msg.direct ? ` ${msg.member} chose you.` : "");
  $("ended-view").hidden = true;
  tabs.show("calls");
  $("incoming-view").hidden = false;
  $("incoming-heading").focus();
  announce(`Incoming call from ${msg.member}${msg.direct ? ", who chose you" : ""}. Accept or decline.`);
  tone("ring");
  vibrate(PATTERNS.ring);
  clearInterval(ringTimer);
  ringTimer = setInterval(() => { tone("ring"); vibrate(PATTERNS.ring); }, 2500);
}

function stopRinging() {
  clearInterval(ringTimer);
  ringTimer = null;
}

// The summary appears in the online button's place the moment the call ends; Stellar settles it
// (onEnded) and pays the guide (onPaidOut) a few seconds later.
function onEnding(msg) {
  const name = stage.view?.peer?.name || "the caller";
  const seconds = stage.seconds;
  stage.close();
  endedCallId = msg.call_id;
  $("home-view").hidden = false;
  tabs.show("calls");
  tone("ended");
  $("ended-text").textContent = `The call with ${name} lasted ${spokenDuration(seconds)}. Settling your earnings on Stellar…`;
  $("ended-tx").hidden = true;
  $("incoming-view").hidden = true;
  $("ended-view").hidden = false;
  $("ended-heading").focus();
  announce(`Call ended. Settling your earnings on Stellar.`);
}

function onEnded(msg) {
  if (endedCallId !== msg.call_id) onEnding(msg);
  const text = parseFloat(msg.earned) === 0
    ? `The call with ${msg.peer?.name || "the caller"} ended before it started.`
    : `The call with ${msg.peer?.name || "the caller"} lasted ${spokenDuration(msg.seconds)}. You earned ${msg.guide_share} ${asset}. Paying it into your wallet…`;
  $("ended-text").textContent = text;
  setEndedLink(msg.close_tx_url, "See the settlement on Stellar");
  announce(text);
  refresh();
}

function onPaidOut(msg) {
  if (endedCallId !== msg.call_id) return;
  const text = `${msg.guide_share} ${asset} is in your wallet.`;
  $("ended-text").textContent = $("ended-text").textContent.replace(/Paying it into your wallet…$/, text);
  setEndedLink(msg.payout_tx_url, "See the payment on Stellar");
  announce(`Paid. ${text}`);
  refresh();
}

function setEndedLink(url, label) {
  $("ended-tx").hidden = !url;
  $("ended-tx").href = url || "#";
  $("ended-tx-label").textContent = label;
}

// ---- buttons ------------------------------------------------------------------

$("online-btn").addEventListener("click", () => {
  $("online-btn").setAttribute("aria-busy", "true");
  live.send(wantOnline ? "go_offline" : "go_online");
});

$("accept-btn").addEventListener("click", () => {
  if (!incoming) return;
  stopRinging();
  $("accept-btn").setAttribute("aria-busy", "true");
  live.send("accept", { call_id: incoming.callId });
  announce("Accepted. Securing payment on Stellar.");
});

$("decline-btn").addEventListener("click", () => {
  if (!incoming) return;
  stopRinging();
  live.send("decline", { call_id: incoming.callId });
  $("incoming-view").hidden = true;
  incoming = null;
  announce("Declined.");
});

$("ended-ok").addEventListener("click", () => {
  $("ended-view").hidden = true;
  endedCallId = null;
  $("home-title").focus();
});

$("earnings-list").addEventListener("click", async (e) => {
  const button = e.target.closest(".claim-btn");
  if (!button) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  announce("Withdrawing on Stellar.");
  try {
    const data = await post(`/calls/${button.dataset.callId}/claim/`);
    announce(`Withdrew ${data.guide_share} ${asset} to your wallet.`);
    refresh();
  } catch (err) {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    announce(err.message);
  }
});

// ---- earnings -------------------------------------------------------------------

function row(call) {
  const li = document.createElement("li");
  li.className = "list-item";
  const main = document.createElement("div");
  main.className = "list-item__main";
  const title = document.createElement("span");
  title.className = "list-item__title";
  title.textContent = `${call.with} · ${call.guide_share} ${asset}`;
  const meta = document.createElement("span");
  meta.className = "list-item__meta";
  meta.textContent = `${new Date(call.date).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })} · ${call.seconds} s`;
  main.append(title, meta);

  const side = document.createElement("div");
  side.className = "list-item__side";
  if (call.can_claim) {
    const btn = document.createElement("button");
    btn.className = "btn btn--success btn--sm claim-btn";
    btn.dataset.callId = call.call_id;
    btn.innerHTML = `${icon("download")}<span>Withdraw</span>`;
    side.append(btn);
  } else {
    side.append(statusBadge(call.status, call.status_label));
  }
  li.append(main, side);
  return li;
}

async function refresh() {
  try {
    const data = await getJSON("/api/me/");
    $("balance").textContent = data.balance;
    for (const [id, value] of [["stat-ready", data.totals.ready], ["stat-held", data.totals.held]]) {
      $(id).textContent = value;
      $(id).closest(".stat").hidden = value === "0.00"; // only older calls still wait for a hold
    }
    if (data.calls.length) $("earnings-list").replaceChildren(...data.calls.map(row));
  } catch {
    /* keep the last known values */
  }
}

bindMoney({ asset, onBalance: (balance) => { $("balance").textContent = balance; refresh(); } });
bindTips();
bindWhatsAppForm();
// Re-check every 15 s so Withdraw appears when the hold ends.
setInterval(() => { if (!stage.active) refresh(); }, 15000);
