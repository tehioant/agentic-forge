# Pinned native API contracts

These small, exact Hermes source slices are external-boundary fixtures, not an
installed Hermes runtime, implementation substitute, or evidence of live work.
`metadata.json` records original source path/qualified name/line range plus exact
slice and source-file SHA-256. The tests execute only these slices in explicit
mock namespaces. Canonical CI requires no Hermes installation and never skips
these assertions. Existing feature assertions remain in `test_planning_wakeup`.

For the independent local check (not canonical CI), run:

```sh
PYTHONDONTWRITEBYTECODE=1 mise exec -- python evidence/issue25/native-portability/verify_installed.py /path/to/hermes-agent
```

It independently extracts qualified bodies from the supplied installed source,
fails on missing source or any whole-file/slice drift, byte-compares snapshots,
and reruns all three API probes plus the strict-hook input-shape probe using the
**installed bodies**, not the fixture loader. No native modules, credentials,
providers, adapters or services are imported or launched.

The input-shape probe is a bounded composition of pure dispatch, sender-prefix,
timestamp-preparation, original-message-selection and hook-collection slices.
Timestamp primitives/config and delivery are explicit mocks; it is not the full
native gateway/agent pipeline. Static installed caller inspection confirms
`_run_conversation_with_approval` forwards `persist_user_message_override`, and
`build_turn_context` selects that override as `original_user_message`. Timestamps
therefore do not require weakening the strict hook. The injected event retains
its original `SessionSource.user_id`; the hook uses `agent._user_id`.

Shared multi-user sender attribution is an explicit unsupported case: its prefix
remains in persisted/original input and the real strict hook refuses it. The hook
API does not expose the event's trusted plugin-injection metadata, so arbitrary
prefix stripping would be unsafe. Channel-context/recovery/other envelopes are
not qualified here. Unchanged raw input, retained original history and the bound
operator reach the strict hook successfully; wrong sender and attributed input
remain refused without changing original observations or planning sufficiency.
