/* Classic-script global ForgePolling; CommonJS exports for focused Node tests. */
(function (root) {
  "use strict";

  /**
   * Read-only fixed-delay polling. apply(value) and onError(error) are synchronous
   * UI callbacks; read(response) may be async (default: JSON). No DOM is owned.
   * Call stop() on pagehide. Fetch/read must honor the supplied abort signal for
   * retry after timeout; an abort-ignoring operation is quarantined until settled
   * rather than allowing overlapping work. Late results are always discarded.
   */
  function createPoller(options) {
    const base = options.baseURL || (root.location && root.location.href);
    const url = new URL(options.url, base);
    const origin = new URL(base);
    if (!/^https?:$/.test(url.protocol) || url.origin !== origin.origin || url.username || url.password) {
      throw new Error("Dashboard polling requires a same-origin HTTP URL");
    }
    const intervalMs = options.intervalMs === undefined ? 5000 : options.intervalMs;
    const timeoutMs = options.timeoutMs === undefined ? 10000 : options.timeoutMs;
    for (const value of [intervalMs, timeoutMs]) {
      if (!Number.isFinite(value) || value <= 0 || value > 2147483647) {
        throw new Error("Polling delays must be positive finite timer durations");
      }
    }
    const clock = options.clock || root;
    const fetch = options.fetch || root.fetch.bind(root);
    const read = options.read || (response => response.json());
    const apply = options.apply || (() => {});
    const onError = options.onError || (() => {});
    let running = false, generation = 0, timer = null, active = null;

    function report(error) {
      // A broken renderer must not turn a background read into an unhandled rejection.
      try { onError(error); } catch (_) { /* The host owns UI error handling. */ }
    }

    async function poll() {
      if (!running || active) return;
      const request = { controller: new AbortController(), generation, expired: false, timeout: null };
      active = request;
      const current = () => running && generation === request.generation && !request.expired;
      request.timeout = clock.setTimeout(() => {
        request.expired = true;
        request.controller.abort();
        if (running && generation === request.generation) report(new Error("Dashboard request timed out"));
      }, timeoutMs);
      try {
        const response = await fetch(url.href, {
          method: "GET", mode: "same-origin", credentials: "same-origin",
          cache: "no-store", redirect: "error", signal: request.controller.signal,
        });
        if (!current()) return;
        if (!response.ok) throw new Error("Dashboard request failed");
        const value = await read(response, { signal: request.controller.signal });
        if (current()) apply(value);
      } catch (error) {
        if (current()) report(error);
      } finally {
        clock.clearTimeout(request.timeout);
        active = null;
        if (running) timer = clock.setTimeout(poll, intervalMs);
      }
    }

    return {
      start() {
        if (running) return;
        running = true;
        generation++;
        void poll();
      },
      stop() {
        running = false;
        generation++;
        clock.clearTimeout(timer);
        timer = null;
        if (active) {
          clock.clearTimeout(active.timeout);
          active.controller.abort();
        }
      },
    };
  }

  /**
   * Pure per-source retention. incoming[name] is {status:'ok', data, observedAt,
   * stale?} or {status:'unavailable'}. Missing/invalid sources fail independently.
   * observedAt is an authoritative ISO timestamp, never dashboard fetch time.
   * Null/invalid/future/aged observations stay stale. lastSuccessAt and checkedAt
   * are dashboard receipt times (epoch ms), NOT worker activity/observation times.
   * An explicit ok + empty data is valid; a missing data field is not.
   */
  function mergeSources(previous, incoming, options) {
    const now = options.now;
    const staleAfterMs = options.staleAfterMs === undefined ? 15000 : options.staleAfterMs;
    if (!Number.isFinite(now) || !Number.isFinite(staleAfterMs) || staleAfterMs < 0) {
      throw new Error("Source freshness requires finite clock and age values");
    }
    const result = Object.create(null);
    for (const name of new Set([...Object.keys(previous), ...Object.keys(incoming)])) {
      const old = previous[name];
      const source = incoming[name];
      const ok = source && source.status === "ok" && Object.prototype.hasOwnProperty.call(source, "data");
      if (!ok) {
        result[name] = {
          hasData: Boolean(old && old.hasData), data: old && old.hasData ? old.data : null,
          observedAt: old ? old.observedAt : null,
          lastSuccessAt: old ? old.lastSuccessAt : null,
          checkedAt: now, status: "unavailable", stale: true, error: "Source unavailable",
        };
        continue;
      }
      const observedAt = typeof source.observedAt === "string" ? source.observedAt : null;
      const observedTime = observedAt === null ? NaN : Date.parse(observedAt);
      const stale = source.stale === true || !Number.isFinite(observedTime) || observedTime > now || now - observedTime > staleAfterMs;
      result[name] = {
        hasData: true, data: source.data, observedAt, lastSuccessAt: now,
        checkedAt: now, status: stale ? "stale" : "ok", stale, error: null,
      };
    }
    return result;
  }

  const api = { createPoller, mergeSources };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ForgePolling = api;
})(typeof globalThis === "object" ? globalThis : this);
