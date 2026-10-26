// VideoCall: the in-app video between member and guide, built on simple-peer
// (static/vendor/simplepeer-9.11.1.min.js, MIT, github.com/feross/simple-peer).
// Signalling rides the live WebSocket; the server relays it only between the two people in a paid call.
//
// The guide initiates and alone decides when to (re)start. Every attempt has a generation
// number, so a reloaded page or a failed network path starts a clean peer instead of mixing
// old and new offers:
//   member: media ready -> "ready" {instance, gen}     guide: media ready -> "hello"
//   guide:  on "ready" -> new peer (gen + 1) -> offer and candidates
//   member: on an offer with a newer gen -> new peer -> answer
//   lost:   guide restarts every few seconds; the member asks with "ready" {restart: true}

const LOST_GRACE_MS = 6000; // "disconnected" often heals by itself (Wi-Fi blips, network handover)
const TROUBLE_AFTER_MS = 12000; // then the page offers the WhatsApp fallback
const RETRY_MS = 5000;

const IS_MAC = /Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints <= 1;
const IS_SAFARI = /^((?!chrome|android|crios|fxios).)*safari/i.test(navigator.userAgent);

const MEDIA_ERRORS = {
  NotAllowedError: "Camera and microphone are blocked. Allow them in your browser settings so you can see and hear each other.",
  NotFoundError: "No camera or microphone was found on this device.",
  NotReadableError: "Another app is using the camera, for example WhatsApp or FaceTime. Close it and try again.",
  OverconstrainedError: "This camera does not support the requested mode.",
};

// Say exactly where to fix a blocked camera; on a Mac the block is often macOS itself, not the site.
function mediaErrorMessage(err) {
  if (err?.name === "NotAllowedError") {
    if (IS_MAC && /system/i.test(err.message || "")) {
      return "macOS is blocking the camera for this browser. Open System Settings, then Privacy & Security, then Camera and Microphone, turn your browser on, and reload Stelleye.";
    }
    if (IS_SAFARI) {
      return "Safari is blocking the camera for Stelleye. Choose Safari, Settings, Websites, then Camera and Microphone, set Stelleye to Allow, and reload.";
    }
    return "The camera is blocked for Stelleye. Use the camera icon in the address bar to allow the camera and microphone, then reload.";
  }
  return MEDIA_ERRORS[err?.name] || "The camera and microphone could not start.";
}

const randomId = () => Math.random().toString(36).slice(2, 10);

export class VideoCall extends EventTarget {
  constructor({ role, iceServers, send }) {
    super();
    this.role = role;
    this.initiator = role === "guide";
    this.iceServers = iceServers || [];
    this.send = send; // (callId, data) => void
    this.instance = randomId();
    this.state = "idle";
    this.clear();
  }

  clear() {
    this.session = {}; // replaced on hang-up so late camera grants are dropped
    this.callId = null;
    this.gen = 0;
    this.peer = null;
    this.peerInstance = null;
    this.peerStartedAt = 0;
    this.local = null;
    this.remote = null;
    this.mediaSettled = false;
    this.mediaPromise = null;
    this.announced = false;
    this.facing = this.role === "member" ? "environment" : "user";
    this.cameras = 0;
    this.muted = false;
    this.cameraOff = false;
    this.torchOn = false;
    this.queue = [];
    clearTimeout(this.lostTimer);
    clearTimeout(this.troubleTimer);
    clearInterval(this.retryTimer);
    this.retryTimer = null;
  }

  emit(type, detail = {}) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }

  setState(state) {
    if (this.state === state) return;
    this.state = state;
    this.emit("state", { state });
  }

  // ---- media ------------------------------------------------------------------

  prepareMedia() {
    this.mediaPromise ||= this.openMedia(this.session);
    return this.mediaPromise;
  }

  async openMedia(session) {
    const video = { facingMode: { ideal: this.facing }, width: { ideal: 1280 }, height: { ideal: 720 } };
    const audio = { echoCancellation: true, noiseSuppression: true };
    let stream = null;
    if (!navigator.mediaDevices?.getUserMedia) {
      this.emit("media-error", { message: "This browser cannot share a camera. Open Stelleye over https or on localhost." });
    } else {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video, audio });
      } catch (err) {
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio });
          this.emit("media-error", { message: `${mediaErrorMessage(err)} Continuing with sound only.` });
        } catch (err2) {
          this.emit("media-error", { message: mediaErrorMessage(err2) });
        }
      }
    }
    if (session !== this.session) {
      stream?.getTracks().forEach((t) => t.stop());
      return null;
    }
    this.local = stream;
    this.mediaSettled = true;
    await this.countCameras();
    if (stream) this.emit("local", { stream });
    this.emit("capabilities", this.capabilities());
    this.flush();
    return stream;
  }

  // Macs and most laptops have one camera; phones have two. Labels are readable once permission is granted.
  async countCameras() {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      this.cameras = devices.filter((d) => d.kind === "videoinput").length;
    } catch {
      this.cameras = 1;
    }
  }

  videoTrack() {
    return this.local?.getVideoTracks()[0] || null;
  }

  audioTrack() {
    return this.local?.getAudioTracks()[0] || null;
  }

  capabilities() {
    const track = this.videoTrack();
    const caps = track?.getCapabilities ? track.getCapabilities() : {};
    // A laptop webcam reports no facing mode (Safari and Chrome on macOS); treat it as a selfie camera.
    const facing = track?.getSettings?.().facingMode || (this.cameras > 1 ? this.facing : "user");
    return {
      video: Boolean(track), audio: Boolean(this.audioTrack()), torch: Boolean(caps.torch),
      flip: Boolean(track) && this.cameras > 1, mirrored: facing === "user",
    };
  }

  setMuted(muted) {
    this.muted = muted;
    const track = this.audioTrack();
    if (track) track.enabled = !muted;
    return this.muted;
  }

  setCameraOff(off) {
    this.cameraOff = off;
    const track = this.videoTrack();
    if (track) track.enabled = !off;
    return this.cameraOff;
  }

  async setTorch(on) {
    const track = this.videoTrack();
    if (!track) return false;
    try {
      await track.applyConstraints({ advanced: [{ torch: on }] });
      this.torchOn = on;
    } catch {
      this.torchOn = false;
    }
    return this.torchOn;
  }

  async flipCamera() {
    const old = this.videoTrack();
    if (!old) return this.facing;
    const facing = this.facing === "environment" ? "user" : "environment";
    const fresh = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: facing } } });
    const track = fresh.getVideoTracks()[0];
    track.enabled = !this.cameraOff;
    if (this.peer && !this.peer.destroyed) {
      try {
        this.peer.replaceTrack(old, track, this.local);
      } catch {
        /* the next reconnect sends the new track */
      }
    }
    this.local.removeTrack(old);
    this.local.addTrack(track);
    old.stop();
    this.facing = facing;
    this.torchOn = false;
    this.emit("local", { stream: this.local });
    this.emit("capabilities", this.capabilities());
    return facing;
  }

  // ---- call lifecycle -------------------------------------------------------------

  // Start signalling for a paid call. Safe to call again after a reload (resume).
  attach(callId) {
    if (this.callId === callId && this.announced) return;
    this.callId = callId;
    this.announced = false;
    this.setState("connecting");
    this.watchForTrouble();
    if (this.mediaSettled) this.flush();
    else this.prepareMedia();
  }

  flush() {
    if (!this.mediaSettled || !this.callId) return;
    if (!this.announced) {
      this.announced = true;
      this.announceSelf(false);
    }
    this.queue.splice(0).forEach((data) => this.handleSignal(data));
  }

  announceSelf(restart) {
    if (this.initiator) this.signal({ kind: "hello" });
    else this.signal({ kind: "ready", instance: this.instance, gen: this.gen, restart });
  }

  signal(data) {
    if (this.callId) this.send(this.callId, data);
  }

  handleSignal(data) {
    if (!data || !this.callId) return;
    if (!this.mediaSettled) {
      this.queue.push(data);
      return;
    }
    if (data.kind === "hello" && !this.initiator) {
      this.announceSelf(false);
    } else if (data.kind === "ready" && this.initiator) {
      const live = this.peer && !this.peer.destroyed;
      if (live && data.instance === this.peerInstance && !data.restart) return; // duplicate of the current attempt
      if (live && data.restart && Date.now() - this.peerStartedAt < RETRY_MS - 500) return; // already retrying
      this.peerInstance = data.instance;
      this.startPeer(Math.max(this.gen, data.gen || 0) + 1);
    } else if (data.kind === "sdp") {
      if (data.gen < this.gen) return;
      if (!this.initiator && (data.gen > this.gen || !this.peer || this.peer.destroyed)) this.startPeer(data.gen);
      if (data.gen === this.gen && this.peer && !this.peer.destroyed) {
        // A second answer to a settled offer (e.g. a stale tab) would tear a working call down.
        if (data.payload?.type === "answer" && this.peer._pc?.signalingState === "stable") return;
        try {
          this.peer.signal(data.payload);
        } catch {
          /* a candidate for an attempt that was just replaced */
        }
      }
    }
  }

  startPeer(gen) {
    this.destroyPeer();
    this.gen = gen;
    this.peerStartedAt = Date.now();
    const peer = new window.SimplePeer({
      initiator: this.initiator,
      stream: this.local || undefined,
      trickle: true,
      config: { iceServers: this.iceServers },
      offerOptions: { offerToReceiveAudio: true, offerToReceiveVideo: true },
    });
    this.peer = peer;
    const current = () => peer === this.peer;
    peer.on("signal", (payload) => { if (current()) this.signal({ kind: "sdp", gen, payload }); });
    peer.on("stream", (stream) => {
      if (!current()) return;
      this.remote = stream;
      this.emit("remote", { stream });
    });
    peer.on("connect", () => { if (current()) this.connected(); });
    peer.on("iceStateChange", (ice) => {
      if (!current()) return;
      if (ice === "connected" || ice === "completed") this.connected();
      else if (ice === "disconnected") this.lostSoon();
      else if (ice === "failed") this.lost();
    });
    peer.on("close", () => { if (current()) this.lost(); });
    peer.on("error", () => { if (current()) this.lost(); });
  }

  connected() {
    clearTimeout(this.lostTimer);
    clearTimeout(this.troubleTimer);
    clearInterval(this.retryTimer);
    this.retryTimer = null;
    this.setState("connected");
  }

  lostSoon() {
    clearTimeout(this.lostTimer);
    this.lostTimer = setTimeout(() => this.lost(), LOST_GRACE_MS);
  }

  lost() {
    if (!this.callId || this.retryTimer) return;
    clearTimeout(this.lostTimer);
    this.setState("reconnecting");
    this.watchForTrouble();
    const retry = () => {
      if (!this.callId || this.state === "connected") return;
      if (this.initiator) this.startPeer(this.gen + 1);
      else this.announceSelf(true);
    };
    retry();
    this.retryTimer = setInterval(retry, RETRY_MS);
  }

  watchForTrouble() {
    clearTimeout(this.troubleTimer);
    this.troubleTimer = setTimeout(() => {
      if (this.callId && this.state !== "connected") this.emit("trouble");
    }, TROUBLE_AFTER_MS);
  }

  destroyPeer() {
    const peer = this.peer;
    this.peer = null;
    if (peer && !peer.destroyed) peer.destroy();
  }

  // Ends the video side of the call and releases the camera.
  hangup() {
    this.destroyPeer();
    this.local?.getTracks().forEach((t) => t.stop());
    this.clear();
    this.setState("idle");
  }
}
