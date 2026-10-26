// Icons point at the SVG sprite that base.html renders from ui/icons.py, so there is one icon set.
const SIZES = { sm: 16, md: 20, lg: 28, xl: 44 };

// Call status -> [badge tone, icon]; mirrors STATUS_BADGES in ui/icons.py.
const BADGES = {
  ringing: ["info", "phone"],
  connecting: ["info", "shield-check"],
  active: ["live", "video"],
  ending: ["info", "clock"],
  held: ["warn", "clock"],
  disputed: ["danger", "alert"],
  released: ["ok", "check"],
  resolved: ["info", "scale"],
  missed: ["muted", "phone-off"],
  failed: ["danger", "alert"],
};

export function icon(name, size = "sm") {
  const px = SIZES[size] || SIZES.sm;
  return `<svg class="icon icon--${size}" width="${px}" height="${px}" fill="none" stroke="currentColor" stroke-width="2" `
    + `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><use href="#i-${name}"></use></svg>`;
}

export function statusBadge(status, label) {
  const [tone, name] = BADGES[status] || ["muted", "clock"];
  const span = document.createElement("span");
  span.className = `badge badge--${tone}`;
  span.innerHTML = icon(name);
  const text = document.createElement("span");
  text.textContent = label;
  span.append(text);
  return span;
}

// A badge with an explicit tone and icon (for things that are not calls, e.g. money transfers).
export function toneBadge(tone, name, label) {
  const span = document.createElement("span");
  span.className = `badge badge--${tone}`;
  span.innerHTML = icon(name);
  const text = document.createElement("span");
  text.textContent = label;
  span.append(text);
  return span;
}
