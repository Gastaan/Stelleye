// POST helpers for Django views (CSRF-aware).
function csrfToken() {
  const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export async function post(url, fields = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "X-CSRFToken": csrfToken() },
    body: new URLSearchParams(fields),
    credentials: "same-origin",
  });
  const data = await response.json().catch(() => ({ ok: false, message: "Unexpected server response." }));
  if (!response.ok || data.ok === false) throw new Error(data.message || "Request failed.");
  return data;
}

export async function getJSON(url) {
  const response = await fetch(url, { credentials: "same-origin" });
  if (!response.ok) throw new Error("Request failed.");
  return response.json();
}
