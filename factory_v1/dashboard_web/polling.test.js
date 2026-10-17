"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createPoller, mergeSources } = require("./polling.js");

function fakeClock() {
  let now = 0, next = 0;
  const timers = new Map();
  return {
    now: () => now,
    setTimeout(fn, delay) { const id = ++next; timers.set(id, { at: now + delay, fn }); return id; },
    clearTimeout(id) { timers.delete(id); },
    tick(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        now = due[1].at; timers.delete(due[0]); due[1].fn();
      }
      now = end;
    },
    count: () => timers.size,
  };
}
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

test("read-only polling serializes slow requests, times out, recovers and stops without late updates", async () => {
  const clock = fakeClock(), requests = [], updates = [], errors = [];
  const poller = createPoller({
    url: "/snapshot", baseURL: "http://localhost/", clock, intervalMs: 100, timeoutMs: 250,
    fetch(url, options) {
      let resolve, reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      requests.push({ url, options, resolve, reject });
      return promise;
    },
    read: response => response.text(), apply: value => updates.push(value),
    onError: error => errors.push(error.message),
  });
  poller.start(); poller.start();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "http://localhost/snapshot");
  assert.equal(requests[0].options.method, "GET");
  assert.equal(requests[0].options.mode, "same-origin");
  assert.equal(requests[0].options.redirect, "error");
  assert.equal(requests[0].options.cache, "no-store");
  assert.equal(requests[0].options.body, undefined);
  clock.tick(200); await settle();
  assert.equal(requests.length, 1);
  requests[0].resolve({ ok: true, text: async () => "first" }); await settle();
  assert.deepEqual(updates, ["first"]);
  clock.tick(100); await settle();
  assert.equal(requests.length, 2);
  clock.tick(250); await settle();
  assert.equal(requests[1].options.signal.aborted, true);
  assert.deepEqual(errors, ["Dashboard request timed out"]);
  // An abort-ignoring boundary must not cause accumulated overlapping requests.
  clock.tick(1000); await settle(); assert.equal(requests.length, 2);
  requests[1].resolve({ ok: true, text: async () => "late" }); await settle();
  assert.deepEqual(updates, ["first"]);
  clock.tick(100); await settle();
  requests[2].resolve({ ok: false }); await settle();
  assert.equal(errors.length, 2);
  clock.tick(100); await settle();
  requests[3].resolve({ ok: true, text: async () => "recovered" }); await settle();
  assert.deepEqual(updates, ["first", "recovered"]);
  clock.tick(100); await settle();
  poller.stop();
  assert.equal(requests[4].options.signal.aborted, true);
  assert.equal(clock.count(), 0);
  requests[4].resolve({ ok: true, text: async () => "after stop" }); await settle();
  clock.tick(1000); await settle();
  assert.equal(requests.length, 5);
  assert.deepEqual(updates, ["first", "recovered"]);
  // Restart is allowed, but stop/start cannot admit the abandoned request's result.
  poller.start();
  assert.equal(requests.length, 6);
  poller.stop(); poller.start();
  assert.equal(requests.length, 6);
  requests[5].resolve({ ok: true, text: async () => "abandoned" }); await settle();
  assert.deepEqual(updates, ["first", "recovered"]);
  clock.tick(100); await settle();
  requests[6].reject(new Error("offline")); await settle();
  assert.equal(errors.at(-1), "offline");
  clock.tick(100); await settle();
  // The timeout also bounds response-body reading, not only response headers.
  let finishBody;
  requests[7].resolve({ ok: true, text: () => new Promise(resolve => { finishBody = resolve; }) }); await settle();
  clock.tick(250); await settle();
  assert.equal(requests[7].options.signal.aborted, true);
  finishBody("late body"); await settle();
  assert.deepEqual(updates, ["first", "recovered"]);
  poller.stop();
  assert.equal(clock.count(), 0);
  assert.throws(() => createPoller({ url: "https://other.test/", baseURL: "http://localhost/" }), /same-origin/);
  assert.throws(() => createPoller({ url: "/", baseURL: "http://localhost/", intervalMs: 0 }), /positive/);
});

test("source freshness retains independent last-good observations without inventing activity times", () => {
  const options = { now: 10000, staleAfterMs: 1000 };
  let sources = mergeSources({}, {
    workers: { status: "ok", data: [{ activityAt: null }], observedAt: new Date(9500).toISOString() },
    github: { status: "unavailable" },
  }, options);
  assert.equal(sources.workers.stale, false);
  assert.equal(sources.github.hasData, false);
  assert.equal(sources.github.status, "unavailable");
  const lastGood = sources.workers;
  sources = mergeSources(sources, {
    workers: { status: "unavailable", data: [] },
    github: { status: "ok", data: [], observedAt: new Date(100).toISOString() },
  }, { ...options, now: 11000 });
  assert.deepEqual(sources.workers.data, lastGood.data);
  assert.equal(sources.workers.observedAt, lastGood.observedAt);
  assert.equal(sources.workers.lastSuccessAt, 10000);
  assert.equal(sources.workers.stale, true);
  assert.equal(sources.github.stale, true); // HTTP success cannot refresh an old observation.
  assert.equal(sources.workers.data[0].activityAt, null);
  sources = mergeSources(sources, {
    workers: { status: "ok", data: [], observedAt: new Date(11000).toISOString() },
    github: { status: "ok", data: ["cache"], observedAt: null },
  }, { ...options, now: 11000 });
  assert.equal(sources.workers.stale, false);
  assert.equal(sources.workers.error, null);
  assert.deepEqual(sources.workers.data, []); // Explicit healthy empty is valid.
  assert.equal(sources.github.observedAt, null);
  assert.equal(sources.github.stale, true);
  sources = mergeSources(sources, {}, { ...options, now: 12000 });
  assert.equal(sources.workers.status, "unavailable");
  assert.deepEqual(sources.github.data, ["cache"]);
  sources = mergeSources(sources, { github: { status: "ok", observedAt: new Date(12000).toISOString() } }, { ...options, now: 12000 });
  assert.equal(sources.github.status, "unavailable"); // Missing data is not healthy empty.
  assert.deepEqual(sources.github.data, ["cache"]);
});
