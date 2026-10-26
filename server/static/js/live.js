// WebSocket client for /ws/live/ with automatic reconnect.
const TAKEN_OVER = 4001; // the server closed this page because the person opened Stelleye elsewhere

export function connectLive(onMessage) {
  let socket;
  let retry = 0;
  let closedByUs = false;
  const queue = [];

  function open() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws/live/`);
    socket.addEventListener("open", () => {
      retry = 0;
      while (queue.length) socket.send(queue.shift());
      onMessage({ type: "_open" });
    });
    socket.addEventListener("message", (event) => onMessage(JSON.parse(event.data)));
    socket.addEventListener("close", (event) => {
      if (event.code === TAKEN_OVER) {
        closedByUs = true;
        onMessage({ type: "_elsewhere" });
        return;
      }
      onMessage({ type: "_closed" });
      if (closedByUs) return;
      retry = Math.min(retry + 1, 5);
      setTimeout(open, 500 * 2 ** retry);
    });
  }

  open();
  return {
    send(type, data = {}) {
      const frame = JSON.stringify({ type, ...data });
      if (socket.readyState === WebSocket.OPEN) socket.send(frame);
      else if (!closedByUs) queue.push(frame);
    },
    close() {
      closedByUs = true;
      socket.close();
    },
  };
}

// Shared by both screens: this page stepped aside for a newer one.
export function showElsewhere(stage) {
  stage?.video.hangup();
  document.getElementById("connection").hidden = true;
  const banner = document.getElementById("elsewhere");
  banner.hidden = false;
  document.getElementById("elsewhere-btn").focus();
}
