// "?" tips: a small icon button that folds a short explanation open. One tip at a time;
// a click elsewhere or Escape closes it and returns focus to its button.
export function bindTips(root = document) {
  const buttons = [...root.querySelectorAll(".tip__btn")];
  const panelOf = (btn) => document.getElementById(btn.getAttribute("aria-controls"));
  const close = (btn) => {
    btn.setAttribute("aria-expanded", "false");
    panelOf(btn).hidden = true;
  };
  for (const btn of buttons) {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = btn.getAttribute("aria-expanded") !== "true";
      buttons.forEach(close);
      btn.setAttribute("aria-expanded", String(open));
      panelOf(btn).hidden = !open;
    });
    btn.closest(".tip").addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || btn.getAttribute("aria-expanded") !== "true") return;
      close(btn);
      btn.focus();
    });
  }
  document.addEventListener("click", (e) => {
    for (const btn of buttons) if (!btn.closest(".tip").contains(e.target)) close(btn);
  });
}
