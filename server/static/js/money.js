// Real money in and out: the anchor's hosted page opens in a new tab for the card, bank or identity
// steps, and this page follows the transfer by itself, announcing each change. A cash-out sends USDC
// only after the person confirms the amounts.
import { post, getJSON } from "./api.js";
import { announce } from "./speak.js";

const $ = (id) => document.getElementById(id);
const POLL_MS = 3000;

export function bindMoney({ asset, onBalance }) {
  const panel = $("transfer-panel");
  if (!panel) return;
  let current = null;
  let timer = null;
  let lastLabel = "";

  function setBusy(button, busy) {
    if (!button) return;
    button.disabled = busy;
    button.setAttribute("aria-busy", String(busy));
  }

  function render(data) {
    current = data;
    panel.hidden = false;
    const deposit = data.kind === "deposit";
    $("transfer-title").textContent = deposit ? "Adding money" : "Cashing out";
    $("transfer-status").textContent = data.label;
    panel.dataset.state = data.succeeded ? "ok" : data.done ? "failed" : data.needs_confirmation ? "confirm" : "working";
    $("transfer-confirm").hidden = !data.needs_confirmation;
    $("transfer-open").hidden = data.done || data.status !== "incomplete" || !data.url;
    if (data.url) $("transfer-open").href = data.url;
    $("transfer-tx").hidden = !data.tx_url;
    $("transfer-tx").href = data.tx_url || "#";
    if (data.label !== lastLabel) {
      lastLabel = data.label;
      announce(data.label);
    }
    if (data.needs_confirmation) $("transfer-confirm").focus();
    if (data.done) {
      stop();
      if (data.balance) onBalance?.(data.balance);
    }
  }

  async function refresh() {
    if (!current || current.done) return;
    try {
      const data = await getJSON(`/wallet/transfers/${current.id}/`);
      render({ ...data, url: current.url });
    } catch {
      /* the next poll retries */
    }
  }

  function poll() {
    stop();
    timer = setInterval(refresh, POLL_MS);
  }

  function stop() {
    clearInterval(timer);
    timer = null;
  }

  async function start(kind, amount, button) {
    // Open the tab inside the click, before any network wait, so popup blockers allow it.
    const tab = window.open("", "_blank");
    if (tab) {
      tab.opener = null;
      tab.document.write('<p style="font:20px system-ui;padding:24px">Opening the secure payment page…</p>');
    }
    setBusy(button, true);
    try {
      const data = await post(kind === "deposit" ? "/wallet/add-money/" : "/wallet/cash-out/", { amount });
      if (tab && !tab.closed) tab.location.href = data.url;
      lastLabel = "";
      render(data);
      announce(tab
        ? "The payment partner's page opened in a new tab. Finish there, then come back. This page will tell you when it is done."
        : "Press Open the payment page to continue.");
      poll();
    } catch (err) {
      tab?.close();
      announce(err.message);
      render({ kind, label: err.message, done: true, succeeded: false, status: "error" });
    } finally {
      setBusy(button, false);
    }
  }

  document.querySelectorAll('.money-btn[data-kind="deposit"]').forEach((button) => {
    button.addEventListener("click", () => start("deposit", button.dataset.amount, button));
  });
  $("cashout-form")?.addEventListener("submit", (e) => {
    e.preventDefault();
    start("withdraw", $("cashout-amount").value, e.submitter || $("cashout-form").querySelector("button"));
  });
  $("transfer-confirm").addEventListener("click", async () => {
    const button = $("transfer-confirm");
    setBusy(button, true);
    announce(`Sending ${current.amount_in} ${asset} to the payment partner on Stellar.`);
    try {
      render({ ...(await post(`/wallet/transfers/${current.id}/confirm/`)), url: current.url });
      poll();
    } catch (err) {
      announce(err.message);
    } finally {
      setBusy(button, false);
    }
  });
  // Coming back from the payment tab: check straight away instead of waiting for the next poll.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refresh();
  });
}
