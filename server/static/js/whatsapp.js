// Saving the WhatsApp number (the video call channel), shared by both screens.
import { post } from "./api.js";
import { announce } from "./speak.js";

export function focusWhatsAppForm() {
  const card = document.getElementById("whatsapp-card");
  if (!card) return;
  card.open = true;
  card.scrollIntoView({ block: "center" });
  document.getElementById("whatsapp-input")?.focus();
}

export function bindWhatsAppForm({ onSaved } = {}) {
  const form = document.getElementById("whatsapp-form");
  if (!form) return;
  const input = document.getElementById("whatsapp-input");
  const error = document.getElementById("wa-error");

  input.addEventListener("input", () => { error.hidden = true; });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
      const data = await post("/me/whatsapp/", { whatsapp: input.value });
      input.value = data.whatsapp;
      document.getElementById("wa-heading").textContent = `Your WhatsApp: ${data.whatsapp}`;
      document.getElementById("whatsapp-card").classList.remove("card--notice");
      announce(`Saved your WhatsApp number, ${data.whatsapp}.`);
      onSaved?.(data.whatsapp);
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
      input.focus();
      announce(err.message);
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  });
}
