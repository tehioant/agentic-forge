# Reviewing structured-text admission and mutation

Use when a diff accepts, scopes or rewrites human-authored Markdown contracts. This is a bounded review recipe, not a requirement to implement a general Markdown renderer.

## 1. Establish one semantic grammar

1. Read the documented accepted formats and the real public admission/mutation callers.
2. Check that admission, dependency readback, scheduling and source-span replacement use the same section-name normalization. Normalize supported heading whitespace and optional whitespace-separated closing ATX hashes before duplicate/control checks; otherwise a visually identical Status heading can escape a hold.
3. For alternative lean/canonical formats, enumerate mechanical scope fields. Reject conflicting or mixed declarations by presence, including empty declarations; silently choosing one parent leaves contradictory scope admitted.
4. Keep repository, parent, requirement document and story identities bound to registered inputs. Do not infer all stories, progress or authorization from a permissive heading or missing status.

## 2. Preserve raw bytes while interpreting semantics

1. Separate normalized section names/content from raw source spans; use the original body for replacement.
2. Define the supported footer shape explicitly. For terminal single-line HTML-comment tails, include indentation, no delimiter-inner spaces, empty comments, multiple comments and trailing whitespace in the preservation boundary. Protecting only a canonical identity-marker spelling leaves neighboring comments inside the writable span.
3. Compare the whole resulting body with an expected body that changes only the intended blocker content. Require identity markers, unrelated comments, prefix, LF/CRLF and EOF whitespace to remain byte-exact; marker presence alone does not prove unrelated-byte preservation.
4. Verify the desired textual edge before native dependency mutation, then verify exact native/textual agreement after readback. A successful edge write must not qualify a partially rewritten body.

## 3. Build the tight regression loop

1. Run the original defect-confirmation probe against the pinned old candidate and preserve its output. An exit-zero probe that asserts broken behavior is red evidence, not acceptance.
2. Build a cheap direct semantic/span matrix across supported heading, indentation, delimiter-space, comment-sequence and newline/EOF variants. Assert exact span boundaries, preserved tails and the resulting contract, not just parser survival.
3. Add negative boundaries for nonterminal, inline, unclosed or out-of-grammar content so a wider preservation rule cannot silently change admission semantics.
4. Run the same final regression bytes against the archived old parser or an isolated baseline worktree. Require the targeted guard to fail the cases; do not mutate the reviewed candidate or disable unrelated guards that could mask the defect.
5. Retain a small public CLI/loopback replay journey: complete the intended mutation, drop its response, reconcile through the public command, read durable state, and replay again. Require full expected-body equality, exact dependencies, unchanged unrelated items and no duplicate writes. Keep large formatting matrices at the local seam rather than multiplying controller or worker launches.

## 4. Qualify the correction review

- Bind the fresh verdict to the corrected HEAD/tree, original requirements and exact correction diff.
- Preserve earlier approvals only after byte/mode and relevant assertion comparisons establish unchanged assumptions; a tiny regex diff does not establish correctness.
- Verify retained test commands, exits, unique IDs and before/after fingerprints before reuse. Count local matrix cases separately from public test methods, and label loopback evidence as simulated external state rather than live integration.
