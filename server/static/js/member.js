// Member (blind or low-vision caller): choose a guide or the first available, then the call stage, then the receipt.
import { getJSON, post } from "./api.js";
import { icon, toneBadge } from "./icons.js";
import { connectLive, showElsewhere } from "./live.js";
import { announce, PATTERNS, setSpeakAloud, speakAloudEnabled, spokenDuration, tone, vibrate } from "./speak.js";
import { CallStage } from "./stage.js";
import { bindMoney } from "./money.js";
import { bindTips } from "./tips.js";
import { initTabs } from "./tabs.js";
import { bindWhatsAppForm, focusWhatsAppForm } from "./whatsapp.js";

const $ = (id) => document.getElementById(id);
const config = JSON.parse($("call-config").textContent);
const asset = config.asset;

let guides = JSON.parse($("initial-guides").textContent);
let lastCall = null; // the "ended" message of the call just finished
let pendingRequest = false;

// ---- views ---------------------------------------------------------------------

function showHome({ focus = false, message } = {}) {
  stage.close();
  $("receipt").hidden = true;
  $("home-view").hidden = false;
  setHelpBusy(false);
  if (focus) $("home-title").focus();
  if (message) announce(message);
}

function showStage() {
  $("home-view").hidden = true;
  $("receipt").hidden = true;
}

function setHelpBusy(busy) {
  pendingRequest = busy;
  const btn = $("help-btn");
  btn.disabled = busy;
  btn.setAttribute("aria-busy", String(busy));
  $("help-label").textContent = busy ? "Connecting" : "Get help now";
  document.querySelectorAll(".call-btn").forEach((b) => { b.disabled = busy || b.dataset.state !== "available"; });
}

// ---- guide directory --------------------------------------------------------------

function hue(name) {
  return [...name].reduce((sum, ch) => sum + ch.charCodeAt(0), 0) * 37 % 360;
}

function guideItem(guide) {
  const li = document.createElement("li");
  li.className = `guide guide--${guide.state}`;
  li.dataset.guideId = guide.id;

  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.style.setProperty("--hue", hue(guide.name));
  avatar.textContent = guide.initials;

  const main = document.createElement("div");
  main.className = "guide__main";
  const name = document.createElement("h3");
  name.className = "guide__name";
  name.textContent = guide.name;
  const state = { available: ["ok", "Available"], busy: ["warn", "In a call"], offline: ["muted", "Offline"] }[guide.state];
  const badge = document.createElement("span");
  badge.className = `badge badge--${state[0]}`;
  badge.textContent = state[1];
  name.append(" ", badge);
  const meta = document.createElement("p");
  meta.className = "guide__meta";
  meta.textContent = [guide.city, guide.language_names.join(", ")].filter(Boolean).join(" · ");
  const rate = document.createElement("p");
  rate.className = "guide__rate";
  rate.textContent = `${guide.rate} ${asset} per minute`;
  main.append(name, meta, rate);

  const actions = document.createElement("div");
  actions.className = "guide__actions";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "icon-btn save-toggle";
  save.dataset.guideId = guide.id;
  save.setAttribute("aria-pressed", String(guide.saved));
  save.setAttribute("aria-label", `Save ${guide.name}`);
  save.innerHTML = icon("heart", "md");
  const call = document.createElement("button");
  call.type = "button";
  call.className = "btn btn--primary btn--sm call-btn";
  call.dataset.guideId = guide.id;
  call.dataset.state = guide.state;
  call.disabled = guide.state !== "available" || pendingRequest;
  call.setAttribute("aria-label", `Call ${guide.name}`);
  call.innerHTML = `${icon("video")}<span>Call</span>`;
  actions.append(save, call);

  li.append(avatar, main, actions);
  return li;
}

function renderGuides() {
  const online = guides.filter((g) => g.state !== "offline");
  const saved = guides.filter((g) => g.saved);
  const available = guides.filter((g) => g.state === "available");
  $("guides-list").replaceChildren(...online.map(guideItem));
  $("saved-list").replaceChildren(...saved.map(guideItem));
  $("guides-empty").hidden = online.length > 0;
  $("saved-empty").hidden = saved.length > 0;
  const cheapest = available.map((g) => parseFloat(g.rate)).sort((a, b) => a - b)[0];
  $("online-summary").textContent = available.length
    ? `${available.length} guide${available.length === 1 ? "" : "s"} online${cheapest ? `, from ${cheapest.toFixed(2)} ${asset} a minute` : ""}.`
    : "No guides online right now.";
}

async function setSaved(guideId, saved) {
  const guide = guides.find((g) => g.id === guideId);
  try {
    const data = await post(`/guides/${guideId}/save/`, { saved: saved ? "1" : "0" });
    if (guide) guide.saved = data.saved;
    renderGuides();
    if (guide) announce(data.saved ? `Saved ${guide.name} to My guides.` : `Removed ${guide.name} from My guides.`);
    return data.saved;
  } catch (err) {
    announce(err.message);
    return !saved;
  }
}

for (const list of [$("guides-list"), $("saved-list")]) {
  list.addEventListener("click", (e) => {
    const save = e.target.closest(".save-toggle");
    if (save) {
      setSaved(Number(save.dataset.guideId), save.getAttribute("aria-pressed") !== "true");
      return;
    }
    const call = e.target.closest(".call-btn");
    if (call && !call.disabled) requestHelp(Number(call.dataset.guideId));
  });
}

// ---- asking for help ---------------------------------------------------------------

function budget() {
  return document.querySelector('input[name="budget"]:checked')?.value;
}

function requestHelp(guideId) {
  if (pendingRequest) return;
  setHelpBusy(true);
  const guide = guides.find((g) => g.id === guideId);
  announce(guide ? `Calling ${guide.name}.` : "Looking for the first available guide.");
  live.send("request_help", { language: $("language").value, budget: budget(), guide_id: guideId || undefined });
}

$("help-btn").addEventListener("click", () => requestHelp(null));

document.querySelectorAll('input[name="budget"]').forEach((input) => {
  input.addEventListener("change", () => { $("hint-budget").textContent = `${input.value} ${asset}`; });
});
$("speak-aloud").checked = speakAloudEnabled();
$("speak-aloud").addEventListener("change", (e) => setSpeakAloud(e.target.checked));

// ---- live channel ------------------------------------------------------------------

const stage = new CallStage({ role: "member", config, live: { send: (...args) => live.send(...args) } });
const tabs = initTabs(document.querySelector(".tabs"));

function showGuides() {
  tabs.show("guides");
  $("online-records").open = true;
  $("guides-title").focus();
}
$("choose-guide").addEventListener("click", showGuides);

// Call options fold out over the page; a click elsewhere or Escape folds them away again.
const callOptions = $("call-options");
document.addEventListener("click", (e) => {
  if (callOptions.open && !callOptions.contains(e.target)) callOptions.open = false;
});
callOptions.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !callOptions.open) return;
  callOptions.open = false;
  callOptions.querySelector("summary").focus();
});
window.sightlineStage = stage; // for the end-to-end tests and debugging
$("stage").addEventListener("saved", (e) => {
  const guide = guides.find((g) => g.id === e.detail.id);
  if (guide) guide.saved = e.detail.saved;
  renderGuides();
});

const live = connectLive((msg) => {
  switch (msg.type) {
    case "_open": $("connection").hidden = true; return;
    case "_closed": $("connection").hidden = false; return;
    case "_elsewhere": showElsewhere(stage); return;
    case "elsewhere": return;
    case "guides":
      guides = msg.guides;
      renderGuides();
      return;
    case "status": return onStatus(msg);
    case "matched":
      showStage();
      stage.matched(msg);
      return;
    case "connected":
      showStage();
      stage.live(msg);
      return;
    case "resume":
      showStage();
      setHelpBusy(true);
      stage.resume(msg);
      return;
    case "chat": stage.addMessage(msg); return;
    case "channel": stage.onChannel(msg); return;
    case "signal": stage.handleSignal(msg); return;
    case "ending": stage.ending(msg.message); return;
    case "ended": return onEnded(msg);
    case "error": return onError(msg);
    default:
  }
});

function onStatus(msg) {
  if (["no_guide", "idle", "declined"].includes(msg.state)) {
    showHome({ message: msg.message });
    if (msg.state === "declined") showGuides();
    $("status-text").textContent = msg.message;
    return;
  }
  if (msg.state === "securing") {
    announce(msg.message);
    return;
  }
  showStage();
  stage.calling({ callId: msg.call_id, message: msg.message, guide: msg.guide });
  if (msg.state === "ringing") announce(msg.message);
}

function onError(msg) {
  vibrate(PATTERNS.error);
  const inCall = stage.active && ["matched", "live"].includes($("stage").dataset.state);
  if (inCall && msg.code !== "payment_failed") {
    announce(msg.message);
    return;
  }
  showHome({ message: msg.message });
  $("status-text").textContent = msg.message;
  if (msg.code === "low_balance") {
    tabs.show("wallet");
    $("wallet").focus();
  }
  if (msg.code === "whatsapp_missing") {
    tabs.reveal($("whatsapp-card"));
    focusWhatsAppForm();
  }
  if (msg.code === "guide_unavailable") showGuides();
}

// ---- receipt ------------------------------------------------------------------------

function timeOf(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function setLink(id, url) {
  $(id).hidden = !url;
  $(id).href = url || "#";
}

function updateReceiptSave(peer) {
  $("r-save").hidden = !peer?.id;
  if (!peer?.id) return;
  $("r-avatar").textContent = peer.initials;
  $("r-avatar").style.setProperty("--hue", hue(peer.name));
  $("r-save-text").textContent = peer.saved
    ? `${peer.name} is in My guides. Call them directly next time.`
    : `Did ${peer.name} help? Save them to call them again.`;
  $("r-save-btn").setAttribute("aria-pressed", String(peer.saved));
  $("r-save-label").textContent = peer.saved ? "Saved" : `Save ${peer.name}`;
}

function onEnded(msg) {
  stage.close();
  lastCall = msg;
  tone("ended");
  vibrate(PATTERNS.ended);
  setHelpBusy(false);
  $("home-view").hidden = true;

  const free = parseFloat(msg.earned) === 0;
  $("receipt-heading").textContent = msg.peer?.name ? `Call with ${msg.peer.name} ended` : "Call ended";
  $("r-duration").textContent = spokenDuration(msg.seconds);
  $("r-paid").textContent = `${msg.earned} ${asset}`;
  $("r-refund").textContent = `${msg.refunded} ${asset}`;
  $("r-hold-row").hidden = !msg.hold_until;
  if (msg.hold_until) $("r-hold").textContent = timeOf(msg.hold_until);
  $("receipt-sub").textContent = free ? "The call ended before it started. You were not charged." : "Here is what happened with your money.";
  setLink("r-open-tx", msg.open_tx_url);
  setLink("r-close-tx", msg.close_tx_url);
  setLink("r-payout-tx", msg.payout_tx_url);
  $("confirm-btn").hidden = !msg.can_confirm;
  $("report-open-btn").hidden = !msg.can_report;
  $("report-form").hidden = true;
  updateReceiptSave(msg.peer);
  stage.chat.renderTranscript($("r-transcript"), msg.messages || []);
  $("receipt").hidden = false;
  $("receipt-heading").focus();

  announce(free
    ? "The call ended before it started. You were not charged."
    : `The call lasted ${spokenDuration(msg.seconds)}. You paid ${msg.earned} ${asset}, and ${msg.refunded} ${asset} came back to your wallet. `
      + `The guide's payment is held until ${timeOf(msg.hold_until)} in case you need to report a problem.`);
  refresh();
}

function setBusy(button, busy) {
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
}

$("r-save-btn").addEventListener("click", async () => {
  const peer = lastCall?.peer;
  if (!peer) return;
  peer.saved = await setSaved(peer.id, !peer.saved);
  updateReceiptSave(peer);
});

$("confirm-btn").addEventListener("click", async () => {
  const btn = $("confirm-btn");
  setBusy(btn, true);
  announce("Paying the guide on Stellar.");
  try {
    const data = await post(`/calls/${lastCall.call_id}/confirm/`);
    btn.hidden = true;
    $("report-open-btn").hidden = true;
    $("r-hold-row").hidden = true;
    setLink("r-payout-tx", data.payout_tx_url);
    announce("Thank you. The guide has been paid.");
    refresh();
  } catch (err) {
    announce(err.message);
  } finally {
    setBusy(btn, false);
  }
});

$("report-open-btn").addEventListener("click", () => {
  $("report-form").hidden = false;
  $("report-reason").focus();
});

$("report-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const submit = e.submitter || $("report-form").querySelector("button");
  setBusy(submit, true);
  try {
    await post(`/review/calls/${lastCall.call_id}/report/`, { reason: $("report-reason").value });
    $("report-form").hidden = true;
    $("confirm-btn").hidden = true;
    $("report-open-btn").hidden = true;
    announce("Report sent. The guide's payment is frozen and a person will review the call.");
    refresh();
  } catch (err) {
    announce(err.message);
  } finally {
    setBusy(submit, false);
  }
});

$("again-btn").addEventListener("click", () => {
  showHome({ focus: true, message: "Ready when you are." });
  $("status-text").textContent = "Ready when you are.";
});

// ---- wallet --------------------------------------------------------------------------

function updateBalance(balance) {
  $("balance").textContent = balance;
  $("header-balance").textContent = balance;
  $("header-balance").closest("a").setAttribute("aria-label", `Wallet, ${balance} ${asset}`);
}

function transactionRow(t) {
  const li = document.createElement("li");
  li.className = "list-item tx";
  const main = document.createElement("div");
  main.className = "list-item__main";
  const title = document.createElement("span");
  title.className = "list-item__title";
  title.textContent = t.title;
  const meta = document.createElement("span");
  meta.className = "list-item__meta";
  const when = new Date(t.date).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  meta.textContent = `${when} · ${t.detail}`;
  if (t.tx_url) {
    const link = document.createElement("a");
    link.href = t.tx_url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "Stellar";
    meta.append(" · ", link);
  }
  main.append(title, meta);
  const side = document.createElement("div");
  side.className = "tx__side";
  const amount = document.createElement("span");
  amount.className = `tx__amount${t.incoming ? " tx__amount--in" : ""}`;
  amount.textContent = `${t.amount} `;
  const unit = document.createElement("small");
  unit.textContent = asset;
  amount.append(unit);
  side.append(amount, toneBadge(t.tone, t.icon, t.status_label));
  li.append(main, side);
  return li;
}

async function refresh() {
  try {
    const data = await getJSON("/api/me/");
    updateBalance(data.balance);
    if (data.transactions?.length) $("history").replaceChildren(...data.transactions.map(transactionRow));
  } catch {
    /* keep the last known values */
  }
}

bindMoney({ asset, onBalance: (balance) => { updateBalance(balance); refresh(); } });
bindTips();
bindWhatsAppForm({ onSaved: () => { $("status-text").textContent = "Ready when you are."; } });
renderGuides();
