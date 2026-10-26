// Sign-up: show the rate field only for guides, reveal the password on request,
// and keep people informed while the Stellar wallet is created.
import { announce } from "./speak.js";
import { bindTips } from "./tips.js";

const $ = (id) => document.getElementById(id);
const form = $("signup-form");

function syncRole() {
  const role = form.querySelector('input[name="role"]:checked')?.value;
  $("rate-field").hidden = role !== "guide";
  $("signup-label").textContent = role === "guide" ? "Create guide account and wallet" : "Create account and wallet";
}
form.querySelectorAll('input[name="role"]').forEach((input) => input.addEventListener("change", syncRole));
syncRole();

$("password-toggle").addEventListener("click", () => {
  const input = $("id_password");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  $("password-toggle").setAttribute("aria-pressed", String(show));
  $("password-toggle").querySelector("span").textContent = show ? "Hide" : "Show";
  $("password-toggle").querySelector("use").setAttribute("href", show ? "#i-eye-off" : "#i-eye");
});

form.addEventListener("submit", () => {
  const button = $("signup-submit");
  // Defer disabling so the browser still submits the form.
  setTimeout(() => {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  }, 0);
  $("signup-progress").hidden = false;
  announce("Creating your account and your Stellar wallet. This takes about 15 seconds.");
  let seconds = 0;
  setInterval(() => {
    seconds += 6;
    if (seconds <= 30) announce(seconds < 12 ? "Funding your wallet on the Stellar test network." : "Almost done.");
  }, 6000);
});

bindTips();

const summary = $("error-summary");
if (summary) summary.focus();
