// Chat between the two people in a call, plus system notes (payment locked, call settled).
// Incoming lines are announced; every line carries a spoken "who said it" prefix.
import { icon } from "./icons.js";

const QUICK_REPLIES = {
  member: ["Thank you!", "Can you hear me?", "Please describe what you see.", "One moment, please."],
  guide: ["I can see your camera.", "Move the phone a little closer.", "Tilt the phone down slightly.", "Turn on the light, please."],
};

function timeLabel(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export class Chat {
  constructor({ root, role, send, announce }) {
    this.root = root;
    this.role = role;
    this.send = send; // (text) => void
    this.announce = announce;
    this.log = root.querySelector("[data-chat-log]");
    this.form = root.querySelector("[data-chat-form]");
    this.input = root.querySelector("[data-chat-input]");
    this.quick = root.querySelector("[data-chat-quick]");
    this.title = root.querySelector("[data-chat-title]");
    this.seen = new Set();
    this.enabled = false;

    for (const text of QUICK_REPLIES[role] || []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "quick-reply";
      button.textContent = text;
      button.addEventListener("click", () => this.submit(text));
      this.quick.append(button);
    }
    this.form.addEventListener("submit", (e) => {
      e.preventDefault();
      this.submit(this.input.value);
    });
  }

  submit(text) {
    const clean = text.trim();
    if (!clean || !this.enabled) return;
    this.send(clean);
    this.input.value = "";
  }

  reset(peerName, messages = []) {
    this.seen.clear();
    this.log.replaceChildren();
    this.title.textContent = peerName ? `Chat with ${peerName}` : "Chat";
    messages.forEach((m) => this.add(m, { quiet: true }));
  }

  setEnabled(enabled) {
    this.enabled = enabled;
    this.input.disabled = !enabled;
    this.form.querySelector("button[type=submit]").disabled = !enabled;
    this.quick.querySelectorAll("button").forEach((b) => { b.disabled = !enabled; });
  }

  add(message, { quiet = false } = {}) {
    if (this.seen.has(message.id)) return;
    this.seen.add(message.id);
    const mine = message.role === this.role;
    const kind = message.role === "system" ? "system" : mine ? "mine" : "peer";

    const li = document.createElement("li");
    li.className = `msg msg--${kind}`;
    const who = document.createElement("span");
    who.className = "visually-hidden";
    who.textContent = kind === "system" ? "Note: " : mine ? "You said: " : `${message.name} said: `;
    const body = document.createElement("span");
    body.className = "msg__text";
    body.textContent = message.text;
    li.append(who, body);
    if (message.link) {
      const a = document.createElement("a");
      a.className = "msg__link";
      a.href = message.link;
      a.target = "_blank";
      a.rel = "noopener";
      a.innerHTML = `${icon("external")}<span>View on Stellar</span>`;
      li.append(a);
    }
    const time = document.createElement("time");
    time.className = "msg__time";
    time.dateTime = message.at;
    time.textContent = timeLabel(message.at);
    li.append(time);
    this.log.append(li);
    this.log.scrollTop = this.log.scrollHeight;

    if (!quiet && kind === "peer") this.announce(`${message.name} says: ${message.text}`);
  }

  // Plain-text copy of the conversation for the receipt.
  renderTranscript(target, messages) {
    target.replaceChildren();
    for (const m of messages.filter((x) => x.role !== "system")) {
      const li = document.createElement("li");
      li.className = `msg msg--${m.role === this.role ? "mine" : "peer"}`;
      const who = document.createElement("strong");
      who.textContent = m.role === this.role ? "You: " : `${m.name}: `;
      const text = document.createElement("span");
      text.textContent = m.text;
      li.append(who, text);
      target.append(li);
    }
    if (!target.children.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "No messages in this call.";
      target.append(li);
    }
  }
}
