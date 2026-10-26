// The call stage both roles share: progress steps, meter, chat, and the video connection.
// States: calling -> matched (chat opens, payment locking) -> live -> ending.
//
// The video call itself happens in WhatsApp, the only video channel in the product: the guide taps
// "Video call on WhatsApp", the member answers. This stage wraps that hand-off: it tells the member
// when to answer, asks the guide whether the call connected, keeps the meter and chat running, and
// welcomes people back from WhatsApp. In-app video (videocall.js) is reachable only in test mode.
import { post } from "./api.js";
import { Chat } from "./chat.js";
import { icon } from "./icons.js";
import { announce, formatClock, PATTERNS, spokenDuration, tone, vibrate } from "./speak.js";
import { VideoCall } from "./videocall.js";

const $ = (id) => document.getElementById(id);
// Elements that exist only in in-app video test mode (INAPP_VIDEO_TEST_MODE=1).
const setHidden = (id, hidden) => { const el = $(id); if (el) el.hidden = hidden; };
const STEP_TEXT = { todo: "not started", doing: "in progress", done: "done" };
// iPadOS reports itself as a Mac, so touch points tell the two apart.
const IS_PHONE = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
  || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);

export class CallStage {
  constructor({ role, config, live }) {
    this.role = role;
    this.asset = config.asset;
    this.socket = live; // { send(type, data) }; not `this.live`, which would hide the live() method
    this.el = $("stage");
    this.callId = null;
    this.view = null;
    this.timer = null;
    this.unread = 0;
    this.seconds = 0;

    const main = $("video-main");
    const pip = $("video-pip");
    this.localEl = role === "member" ? main : pip;
    this.remoteEl = role === "member" ? pip : main;
    this.localEl.muted = true;
    this.remoteEl.muted = false;
    this.localEl.setAttribute("aria-label", role === "member" ? "Your camera" : "Your camera, as the caller sees it");

    this.video = new VideoCall({
      role,
      iceServers: config.ice_servers,
      send: (callId, data) => live.send("signal", { call_id: callId, data }),
    });
    this.chat = new Chat({
      root: $("chat"),
      role,
      announce,
      send: (text) => live.send("chat", { call_id: this.callId, text }),
    });
    this.bindVideo();
    this.bindControls();
    document.addEventListener("visibilitychange", () => this.welcomeBack());
  }

  get peerName() {
    return this.view?.peer?.name || (this.role === "member" ? "your guide" : "the caller");
  }

  // ---- views ---------------------------------------------------------------------

  get active() {
    return !this.el.hidden;
  }

  show(state) {
    this.el.dataset.state = state;
    if (this.el.hidden) {
      this.el.hidden = false;
      document.body.classList.add("in-call");
    }
  }

  setHeading(title, sub, { focus = false } = {}) {
    $("stage-title").textContent = title;
    $("stage-sub").textContent = sub || "";
    if (focus) $("stage-title").focus();
  }

  setBadge(text, toneName, link) {
    const badge = $("stage-badge");
    badge.className = `badge badge--${toneName}`;
    badge.replaceChildren();
    const label = document.createElement(link ? "a" : "span");
    if (link) {
      label.href = link;
      label.target = "_blank";
      label.rel = "noopener";
    }
    label.innerHTML = `${icon(link ? "lock" : "shield-check")}<span></span>`;
    label.querySelector("span").textContent = text;
    badge.append(label);
  }

  setStep(step, state) {
    const li = this.el.querySelector(`[data-step="${step}"]`);
    li.dataset.state = state;
    li.querySelector(".step__state").textContent = `, ${STEP_TEXT[state]}`;
  }

  setPlaceholder(text, { ringing = false } = {}) {
    const box = $("stage-placeholder");
    box.hidden = !text;
    box.classList.toggle("is-ringing", ringing);
    $("stage-placeholder-text").textContent = text || "";
  }

  setEndLabel(text) {
    $("ctl-end-label").textContent = text;
  }

  calling({ callId, message, guide }) {
    const first = !this.active;
    this.callId = callId ?? this.callId;
    this.show("calling");
    this.el.dataset.channel = "whatsapp";
    this.setStep("match", "doing");
    this.setStep("pay", "todo");
    this.setStep("video", "todo");
    this.setHeading(guide ? `Calling ${guide}` : "Finding a guide", message, { focus: first });
    this.setBadge("Not charged yet", "info");
    this.setPlaceholder(this.role === "member" ? "When a guide accepts, they will video call you on WhatsApp." : "", { ringing: true });
    $("wa-panel").hidden = true;
    setHidden("switcher", true);
    this.setEndLabel("Cancel");
    this.chatAvailable(false);
    $("meter").hidden = true;
  }

  matched(view) {
    const first = this.view?.call_id !== view.call_id || this.el.dataset.state === "calling";
    this.view = view;
    this.callId = view.call_id;
    this.show("matched");
    this.setStep("match", "done");
    this.setStep("pay", "doing");
    this.setStep("video", "todo");
    $("stage-avatar").textContent = view.peer.initials;
    const sub = `Locking up to ${view.budget} ${this.asset} on Stellar. ${view.rate} ${this.asset} per minute, unused money comes back.`;
    this.setHeading(view.peer.name, sub, { focus: first });
    this.setBadge("Securing payment on Stellar", "info");
    this.setPlaceholder(this.role === "member"
      ? `${view.peer.name} will video call you on WhatsApp as soon as the payment is locked.`
      : `Locking ${view.peer.name}'s payment. Then you video call them on WhatsApp.`);
    $("wa-panel").hidden = true;
    setHidden("switcher", true);
    this.setEndLabel("End call");
    this.chat.reset(view.peer.name, view.messages);
    this.chatAvailable(true);
    this.chat.setEnabled(true);
    this.updateSave(view.peer);
    if (first) {
      tone("connected");
      vibrate(PATTERNS.connected);
      announce(this.role === "member"
        ? `${view.peer.name} accepted. Securing payment on Stellar. A chat with ${view.peer.name} is open.`
        : `Accepted. Securing ${view.peer.name}'s payment on Stellar.`);
    }
  }

  live(view) {
    const wasLive = this.el.dataset.state === "live" && this.callId === view.call_id;
    if (this.el.dataset.state !== "matched" || this.callId !== view.call_id) this.matched(view);
    this.view = view;
    this.show("live");
    this.setStep("pay", "done");
    view.messages.forEach((m) => this.chat.add(m, { quiet: true }));
    this.setBadge("Payment secured on Stellar", "live", view.open_tx_url);
    this.setEndLabel(this.role === "member" ? "End call and pay" : "End call");
    this.startMeter(view);
    this.applyVideoState(view.video_state || "", { quiet: true });
    if (!wasLive) {
      announce(this.role === "member"
        ? `Payment secured. ${view.peer.name} will video call you on WhatsApp now. Answer the call. ${view.rate} ${this.asset} per minute. Come back here and press End call and pay when you are done.`
        : `Payment secured. The meter is running. Press Video call on WhatsApp to call ${view.peer.name}.`);
    }
  }

  resume(view) {
    if (view.state === "connecting") this.matched(view);
    else if (view.state === "active") this.live(view);
    else if (view.state === "ending") this.ending("Settling payment on Stellar.");
    announce(`Back in your call with ${view.peer.name}.`);
  }

  ending(message) {
    this.show("ending");
    this.stopMeter();
    this.video.hangup();
    this.chat.setEnabled(false);
    this.setHeading("Settling payment", message);
    this.setBadge("Settling on Stellar", "info");
    $("wa-panel").hidden = true;
    setHidden("switcher", true);
    this.setPlaceholder("Settling payment on Stellar.");
    announce(message);
  }

  close() {
    this.stopMeter();
    this.video.hangup();
    this.localEl.srcObject = null;
    this.remoteEl.srcObject = null;
    this.el.hidden = true;
    this.el.dataset.channel = "whatsapp";
    document.body.classList.remove("in-call");
    this.chatOpen(false);
    $("video-note").hidden = true;
    $("wa-panel").hidden = true;
    this.callId = null;
    this.view = null;
  }

  welcomeBack() {
    if (document.visibilityState !== "visible" || this.el.dataset.state !== "live") return;
    const time = spokenDuration(this.seconds);
    announce(this.role === "member"
      ? `Welcome back. Your call with ${this.peerName} is still running, ${time} so far. Press End call and pay when you are done.`
      : `Welcome back. The call with ${this.peerName} is still running, ${time} so far.`
        + (this.view?.video_state === "opened" ? " Did the WhatsApp video call connect?" : ""));
  }

  // ---- the video connection: WhatsApp hand-off or in-app backup ---------------------------

  applyVideoState(state, { quiet = false } = {}) {
    if (!this.view) return;
    this.view.video_state = state;
    if (state === "inapp") {
      this.useInApp(quiet);
      return;
    }
    if (this.el.dataset.channel === "inapp") this.useWhatsApp();
    this.renderWhatsApp(state, quiet);
  }

  renderWhatsApp(state, quiet) {
    const wa = this.view.whatsapp;
    const peer = this.peerName;
    const member = this.role === "member";
    const copy = {
      "": member
        ? [`${peer} will video call you on WhatsApp`, "Answer the WhatsApp call when it rings. This page keeps the meter and chat running."]
        : [`Video call ${peer} on WhatsApp`, `Tap the green button, then tap the video camera icon in WhatsApp. ${peer} only has to answer.`],
      opened: member
        ? [`${peer} is calling you on WhatsApp now`, "Answer the WhatsApp video call. Come back here to end the call and pay."]
        : [`Calling ${peer} on WhatsApp`, "Come back here when the video call is connected, or if it did not work."],
      connected: member
        ? [`On WhatsApp video with ${peer}`, "When you are done, come back here and press End call and pay."]
        : [`On WhatsApp video with ${peer}`, "Describe what you see. Come back here to end the call."],
      failed: member
        ? ["The WhatsApp call did not connect", `${peer} can try again, or you can both use video in Stelleye.`]
        : ["WhatsApp did not connect", `Try again, or switch to video in Stelleye. ${peer} is billed only for the seconds used.`],
    };
    const [title, text] = copy[state === "whatsapp" ? "" : state] || copy[""];
    this.el.dataset.channel = "whatsapp";
    this.setPlaceholder("");
    $("wa-panel").hidden = false;
    $("wa-title").textContent = title;
    $("wa-text").textContent = text;
    $("wa-open").hidden = !wa;
    $("wa-hint").hidden = !wa || IS_PHONE;
    if (wa) {
      $("wa-open").href = IS_PHONE ? wa.link : wa.app_link;
      $("wa-open-label").textContent = member
        ? `Message ${peer} on WhatsApp`
        : state === "opened" ? "Open WhatsApp again" : state === "failed" ? "Try WhatsApp again" : `Video call ${peer} on WhatsApp`;
      $("wa-number").textContent = `${peer}'s WhatsApp: ${wa.peer_number}`;
    } else {
      $("wa-number").textContent = "WhatsApp is not available for this call. Use video in Stelleye.";
    }
    $("wa-open").classList.toggle("btn--secondary", member);
    $("wa-open").classList.toggle("btn--whatsapp", !member);
    $("wa-confirm").hidden = member || state !== "opened";
    setHidden("switcher", false);
    setHidden("use-inapp", false);
    setHidden("use-whatsapp", true);
    this.setStep("video", state === "connected" ? "done" : "doing");
    this.setHeading(peer, member ? "Paid call on WhatsApp video" : "Paid call · the meter is running");
    if (!quiet && member && state === "opened") {
      tone("ring");
      vibrate(PATTERNS.ring);
    }
  }

  useInApp(quiet) {
    this.el.dataset.channel = "inapp";
    $("wa-panel").hidden = true;
    setHidden("switcher", false);
    setHidden("use-inapp", true);
    setHidden("use-whatsapp", !this.view?.whatsapp);
    this.setStep("video", this.video.state === "connected" ? "done" : "doing");
    this.setPlaceholder(this.video.state === "connected" ? "" : "Connecting video in Stelleye. Allow the camera and microphone if asked.");
    this.setHeading(this.peerName, this.role === "member"
      ? `${this.peerName} sees through your camera. Hold the phone steady.`
      : `You see ${this.peerName}'s camera. Describe what you see.`);
    this.video.attach(this.callId);
    if (!quiet) announce("Switching to video in Stelleye. Allow the camera and microphone if asked.");
  }

  useWhatsApp() {
    this.video.hangup();
    this.localEl.srcObject = null;
    this.remoteEl.srcObject = null;
    $("video-note").hidden = true;
    this.el.dataset.channel = "whatsapp";
  }

  sendVideoState(state) {
    if (this.callId) this.socket.send("channel", { call_id: this.callId, state });
  }

  onChannel(msg) {
    if (msg.call_id !== this.callId || !this.view) return;
    if (msg.note) this.chat.add(msg.note, { quiet: true });
    const mine = msg.by === this.role;
    this.applyVideoState(msg.state, { quiet: mine });
    const peer = this.peerName;
    const spoken = {
      opened: mine ? `Opening WhatsApp. Video call ${peer}, then come back here.` : `${peer} is video calling you on WhatsApp now. Answer the call.`,
      connected: "Video call connected. The meter is running.",
      failed: mine ? "Noted. Try WhatsApp again, or switch to video in Stelleye." : `${msg.who} could not connect on WhatsApp. You can try again, or use video in Stelleye.`,
      inapp: mine ? null : `${msg.who} switched to video in Stelleye. Allow the camera and microphone if asked.`,
      whatsapp: mine ? "Back to WhatsApp." : `${msg.who} switched back to WhatsApp.`,
    }[msg.state];
    if (spoken) announce(spoken);
  }

  // ---- meter ---------------------------------------------------------------------

  startMeter(view) {
    this.stopMeter();
    if (!view.started_at) return;
    const offset = view.server_now - Date.now() / 1000;
    const rate = parseFloat(view.rate);
    const budget = parseFloat(view.budget);
    let announcedMinute = -1;
    let warned = false;
    $("meter").hidden = false;
    const tick = () => {
      const seconds = Math.max(0, Math.floor(Date.now() / 1000 + offset - view.started_at));
      this.seconds = seconds;
      const cost = Math.min((rate * seconds) / 60, budget);
      $("meter-time").textContent = formatClock(seconds);
      $("meter-cost").textContent = this.role === "member"
        ? `${cost.toFixed(2)} of ${view.budget} ${this.asset}`
        : `earning ${cost.toFixed(2)} ${this.asset}`;
      const minute = Math.floor(seconds / 60);
      if (this.role === "member" && minute > 0 && minute !== announcedMinute && seconds % 60 < 2 && document.visibilityState === "visible") {
        announcedMinute = minute;
        announce(`${spokenDuration(minute * 60)}, about ${cost.toFixed(2)} ${this.asset} so far.`);
      }
      const left = view.max_seconds - seconds;
      if (this.role === "member" && left <= 60 && left > 0 && !warned) {
        warned = true;
        announce("About one minute of your budget is left.");
        vibrate(PATTERNS.error);
      }
      if (this.role === "member" && left <= 0) {
        this.stopMeter();
        announce("Your budget for this call is used up. Ending the call.");
        this.socket.send("hangup", { call_id: view.call_id });
      }
    };
    tick();
    this.timer = setInterval(tick, 1000);
  }

  stopMeter() {
    clearInterval(this.timer);
    this.timer = null;
  }

  // ---- in-app backup video -----------------------------------------------------------

  bindVideo() {
    const v = this.video;
    v.addEventListener("local", (e) => {
      this.localEl.srcObject = e.detail.stream;
      this.localEl.hidden = false;
    });
    v.addEventListener("remote", (e) => {
      const stream = e.detail.stream;
      this.remoteEl.srcObject = stream;
      this.remoteEl.hidden = this.role === "member" && !stream.getVideoTracks().length;
      this.playRemote();
    });
    v.addEventListener("capabilities", (e) => {
      const caps = e.detail;
      this.localEl.classList.toggle("is-mirrored", caps.mirrored);
      if ($("ctl-mute")) $("ctl-mute").disabled = !caps.audio;
      if ($("ctl-flip")) {
        $("ctl-flip").hidden = !caps.flip;
        $("ctl-flip").disabled = !caps.flip;
      }
      if ($("ctl-camera")) $("ctl-camera").disabled = !caps.video;
      if ($("ctl-torch")) $("ctl-torch").hidden = !caps.torch;
    });
    v.addEventListener("state", (e) => {
      if (this.el.dataset.channel !== "inapp" || this.el.dataset.state !== "live") return;
      if (e.detail.state === "connected") {
        this.setStep("video", "done");
        this.setPlaceholder("");
        $("video-note").hidden = true;
        announce(this.role === "member"
          ? `Video connected. ${this.peerName} can see your camera.`
          : `Video connected. You can see ${this.peerName}'s camera.`);
      } else if (e.detail.state === "reconnecting") {
        this.setStep("video", "doing");
        this.note("Video interrupted. Reconnecting. The call and payment continue.");
      }
    });
    v.addEventListener("trouble", () => {
      if (this.el.dataset.channel !== "inapp" || this.el.dataset.state !== "live") return;
      this.note(this.view?.whatsapp ? "The video is having trouble connecting. You can go back to WhatsApp." : "The video is having trouble connecting.");
    });
    v.addEventListener("media-error", (e) => this.note(e.detail.message));
  }

  // Safari may refuse to start sound without a tap; offer one big button instead of silence.
  playRemote() {
    const button = $("play-remote");
    const attempt = this.remoteEl.play?.();
    if (!attempt) return;
    attempt.then(() => { button.hidden = true; }).catch(() => {
      button.hidden = false;
      announce(`Press Turn on sound to hear ${this.peerName}.`);
    });
  }

  note(text) {
    const el = $("video-note");
    el.textContent = text;
    el.hidden = false;
    announce(text);
  }

  handleSignal(msg) {
    if (msg.call_id === this.callId && this.el.dataset.channel === "inapp") this.video.handleSignal(msg.data);
  }

  // ---- chat --------------------------------------------------------------------------

  chatAvailable(on) {
    this.el.dataset.chat = on ? "available" : "none";
    $("ctl-chat").disabled = !on;
    if (!on) this.chatOpen(false);
  }

  chatOpen(open) {
    this.el.dataset.chatOpen = String(open);
    $("ctl-chat").setAttribute("aria-pressed", String(open));
    if (open) {
      this.unread = 0;
      $("chat-unread").hidden = true;
    }
  }

  addMessage(message) {
    if (message.call_id !== this.callId) return;
    this.chat.add(message);
    const isPeer = message.role !== this.role && message.role !== "system";
    if (isPeer && this.el.dataset.chatOpen !== "true" && !window.matchMedia("(min-width: 900px)").matches) {
      this.unread += 1;
      $("chat-unread").textContent = String(this.unread);
      $("chat-unread").hidden = false;
    }
  }

  // ---- controls ----------------------------------------------------------------------

  bindControls() {
    $("ctl-end").addEventListener("click", () => {
      if (this.callId) this.socket.send("hangup", { call_id: this.callId });
      else this.close();
    });
    $("ctl-chat").addEventListener("click", () => {
      const open = this.el.dataset.chatOpen !== "true";
      this.chatOpen(open);
      if (open) $("chat-input").focus();
    });
    $("wa-open").addEventListener("click", () => {
      if (this.role === "guide") this.sendVideoState("opened");
    });
    $("wa-connected").addEventListener("click", () => this.sendVideoState("connected"));
    $("wa-failed").addEventListener("click", () => this.sendVideoState("failed"));
    $("use-inapp")?.addEventListener("click", () => this.sendVideoState("inapp"));
    $("use-whatsapp")?.addEventListener("click", () => this.sendVideoState("whatsapp"));
    $("ctl-mute")?.addEventListener("click", () => {
      const muted = this.video.setMuted(!this.video.muted);
      this.toggleButton("ctl-mute", muted, muted ? "mic-off" : "mic");
      announce(muted ? "Microphone muted." : "Microphone on.");
    });
    $("ctl-flip")?.addEventListener("click", async () => {
      try {
        const facing = await this.video.flipCamera();
        announce(facing === "environment" ? "Using the back camera." : "Using the front camera.");
      } catch {
        announce("Could not switch the camera.");
      }
    });
    $("ctl-torch")?.addEventListener("click", async () => {
      const on = await this.video.setTorch(!this.video.torchOn);
      this.toggleButton("ctl-torch", on, "flashlight");
      announce(on ? "Light on." : "Light off.");
    });
    $("ctl-camera")?.addEventListener("click", () => {
      const off = this.video.setCameraOff(!this.video.cameraOff);
      this.toggleButton("ctl-camera", off, off ? "video-off" : "video");
      announce(off ? "Your camera is off. You still see the caller." : "Your camera is on.");
    });
    $("save-peer")?.addEventListener("click", () => this.toggleSave());
    $("play-remote").addEventListener("click", () => this.playRemote());
  }

  toggleButton(id, pressed, iconName) {
    const btn = $(id);
    btn.setAttribute("aria-pressed", String(pressed));
    btn.querySelector("use")?.setAttribute("href", `#i-${iconName}`);
  }

  updateSave(peer) {
    const btn = $("save-peer");
    if (!btn || !peer?.id) return;
    btn.hidden = false;
    btn.setAttribute("aria-pressed", String(peer.saved));
    $("save-peer-label").textContent = peer.saved ? `${peer.name} is saved` : `Save ${peer.name}`;
  }

  async toggleSave() {
    const peer = this.view?.peer;
    if (!peer) return;
    const next = !peer.saved;
    try {
      const data = await post(`/guides/${peer.id}/save/`, { saved: next ? "1" : "0" });
      peer.saved = data.saved;
      this.updateSave(peer);
      announce(peer.saved ? `Saved ${peer.name} to My guides. You can call them directly next time.` : `Removed ${peer.name} from My guides.`);
      this.el.dispatchEvent(new CustomEvent("saved", { detail: { ...peer } }));
    } catch (err) {
      announce(err.message);
    }
  }
}
