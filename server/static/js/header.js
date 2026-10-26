// Header controls on every page: the light/dark switch and the signed-in account menu.
// The head script in base.html already applied the theme before first paint; this keeps it in sync.
const THEME_KEY = "stelleye.theme";
const root = document.documentElement;
const systemDark = matchMedia("(prefers-color-scheme: dark)");

function savedTheme() {
  try { return localStorage.getItem(THEME_KEY); } catch { return null; }
}

function applyTheme(theme) {
  root.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#1e1810" : "#f0953c");
  for (const button of document.querySelectorAll("[data-theme-toggle]")) {
    button.setAttribute("aria-pressed", String(theme === "dark"));
  }
}

applyTheme(savedTheme() || (systemDark.matches ? "dark" : "light"));
// Until someone picks a theme, follow the system as it changes (e.g. at sunset).
systemDark.addEventListener("change", (e) => { if (!savedTheme()) applyTheme(e.matches ? "dark" : "light"); });

document.addEventListener("click", (e) => {
  if (!e.target.closest("[data-theme-toggle]")) return;
  const next = root.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem(THEME_KEY, next); } catch { /* private mode */ }
  applyTheme(next);
});

// Account menu: a disclosure under the name chip; a click elsewhere or Escape closes it.
const accountBtn = document.getElementById("account-btn");
const accountPanel = document.getElementById("account-panel");

function setMenu(open) {
  accountBtn.setAttribute("aria-expanded", String(open));
  accountPanel.hidden = !open;
}

if (accountBtn && accountPanel) {
  accountBtn.addEventListener("click", () => setMenu(accountPanel.hidden));
  document.addEventListener("click", (e) => {
    if (!accountPanel.hidden && !accountBtn.parentElement.contains(e.target)) setMenu(false);
  });
  // Following a link inside the menu (Account opens a tab on this page) closes it.
  accountPanel.addEventListener("click", (e) => { if (e.target.closest("a")) setMenu(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !accountPanel.hidden) {
      setMenu(false);
      accountBtn.focus();
    }
  });
}
