// Accessible tabs (WAI-ARIA tabs pattern): arrow keys, Home and End move between tabs and select them;
// the URL hash names the open tab, so links like /help/#wallet open the Wallet tab.
export function initTabs(root, { onChange } = {}) {
  const tabs = [...root.querySelectorAll('[role="tab"]')];
  const panelOf = (tab) => document.getElementById(tab.getAttribute("aria-controls"));

  function select(tab, { focus = false, updateHash = true } = {}) {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      panelOf(t).hidden = !on;
    }
    if (focus) tab.focus();
    if (updateHash) history.replaceState(null, "", `#${tab.dataset.tab}`);
    onChange?.(tab.dataset.tab);
  }

  for (const tab of tabs) {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (e) => {
      const i = tabs.indexOf(tab);
      const next = { ArrowRight: tabs[(i + 1) % tabs.length], ArrowLeft: tabs[(i - 1 + tabs.length) % tabs.length],
        Home: tabs[0], End: tabs[tabs.length - 1] }[e.key];
      if (!next) return;
      e.preventDefault();
      select(next, { focus: true });
    });
  }

  const fromHash = () => {
    const tab = tabs.find((t) => t.dataset.tab === location.hash.slice(1));
    if (tab) select(tab, { updateHash: false });
  };
  window.addEventListener("hashchange", fromHash);
  fromHash();

  return {
    show(name, options) {
      const tab = tabs.find((t) => t.dataset.tab === name);
      if (tab) select(tab, options);
    },
    // Open whichever tab holds this element (e.g. before moving focus into it).
    reveal(element) {
      const panel = element?.closest('[role="tabpanel"]');
      const tab = panel && tabs.find((t) => t.getAttribute("aria-controls") === panel.id);
      if (tab) select(tab);
    },
  };
}
