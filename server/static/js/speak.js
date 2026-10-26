// Accessible feedback: screen-reader announcements, optional speech, vibration.
const SPEAK_KEY = "sightline.speakAloud";

export function speakAloudEnabled() {
  try {
    return localStorage.getItem(SPEAK_KEY) === "1";
  } catch {
    return false;
  }
}

export function setSpeakAloud(on) {
  try {
    localStorage.setItem(SPEAK_KEY, on ? "1" : "0");
  } catch {
    /* private mode: keep the default */
  }
}

// Writes to the visible status line and the assertive live region.
// Re-setting the same text still announces, because we clear it first.
export function announce(
  message,
  { statusEl = document.getElementById("status-text") || document.getElementById("status") } = {},
) {
  if (statusEl) statusEl.textContent = message;
  const region = document.getElementById("announcer");
  if (region) {
    region.textContent = "";
    setTimeout(() => { region.textContent = message; }, 60);
  }
  if (speakAloudEnabled() && "speechSynthesis" in window) {
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(message));
  }
}

export const PATTERNS = { connected: [80, 60, 80], ended: [300], ring: [400, 200, 400], error: [100, 50, 100, 50, 100] };

export function vibrate(pattern) {
  // Browsers ignore (and warn about) vibration before the person has touched the page.
  if (navigator.vibrate && navigator.userActivation?.hasBeenActive !== false) navigator.vibrate(pattern);
}

// Short tones so status is audible even without speech.
export function tone(kind) {
  const freqs = { connected: [660, 880], ended: [520, 390], ring: [740, 740] }[kind];
  if (!freqs || !window.AudioContext) return;
  const ctx = new AudioContext();
  freqs.forEach((f, i) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = f;
    gain.gain.value = 0.08;
    osc.connect(gain).connect(ctx.destination);
    osc.start(ctx.currentTime + i * 0.18);
    osc.stop(ctx.currentTime + i * 0.18 + 0.15);
  });
  setTimeout(() => ctx.close(), 800);
}

export function formatClock(totalSeconds) {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function spokenDuration(totalSeconds) {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  const parts = [];
  if (m) parts.push(`${m} minute${m === 1 ? "" : "s"}`);
  if (s || !m) parts.push(`${s} second${s === 1 ? "" : "s"}`);
  return parts.join(" ");
}
