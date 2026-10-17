/* Only same-origin server-sanitized source fragments enter innerHTML. */
(function (root) {
  "use strict";
  const names = ["forge", "github"];
  const panels = Object.fromEntries(names.map(name => [name, root.document.getElementById(name + "-source")]));
  const notices = Object.fromEntries(names.map(name => [name, root.document.getElementById(name + "-notice")]));
  let sources = {};

  function render() {
    for (const name of names) {
      const source = sources[name];
      if (panels[name].dataset.sourceStatus === "unconfigured") {
        notices[name].textContent = "Board unconfigured.";
        continue;
      }
      if (source.hasData) panels[name].innerHTML = source.data;
      if (source.error) {
        notices[name].textContent = source.hasData
          ? "Source unavailable; showing last successful view (stale)."
          : "Source unavailable; no successful observation available.";
      } else if (source.stale) {
        notices[name].textContent = source.observedAt === null
          ? "Source observation time unknown; freshness unverified (stale)."
          : "Source observation stale; dashboard fetch does not refresh source time.";
      } else {
        notices[name].textContent = "Source observation current.";
      }
    }
  }

  const initial = {};
  for (const name of names) {
    const panel = panels[name];
    initial[name] = { status: panel.dataset.sourceStatus, data: panel.innerHTML,
                      observedAt: panel.dataset.observedAt || null };
  }
  sources = root.ForgePolling.mergeSources({}, initial, { now: Date.now() });
  render();
  const poller = root.ForgePolling.createPoller({
    url: "/api/snapshot", timeoutMs: 75000,
    apply(snapshot) {
      const incoming = {};
      for (const name of names) {
        const metadata = snapshot.sources && snapshot.sources[name];
        const fragment = snapshot.fragments && snapshot.fragments[name];
        incoming[name] = metadata && metadata.status === "ok" && typeof fragment === "string"
          ? { status: "ok", data: fragment, observedAt: metadata.observed_at }
          : { status: "unavailable" };
      }
      sources = root.ForgePolling.mergeSources(sources, incoming, { now: Date.now() });
      render();
    },
    onError() {
      sources = root.ForgePolling.mergeSources(sources, {}, { now: Date.now() });
      render();
    },
  });
  root.addEventListener("pagehide", () => poller.stop(), { once: true });
  poller.start();
})(globalThis);
