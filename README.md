# agentic-forge
Agentic Forge — a personal workshop for agents, tools, and workflows.

## Development setup

Install [mise](https://mise.jdx.dev/getting-started.html), then run from the
repository root:

```bash
mise trust
mise install
mise run setup
mise run test
```

`.mise.toml` pins the latest stable Python, GitHub CLI, Lefthook, ShellCheck and
Betterleaks releases selected for this setup. Use `mise exec -- <command>` if
mise is not activated in your shell; for example, `mise exec -- git commit`.
Lefthook's postinstall installs Git hooks on tool installation; `mise run setup`
also installs them when the tools were already cached or in a fresh clone.

`lefthook.yml` is adapted from
[`tehioant/hermes-elastic-metal`](https://github.com/tehioant/hermes-elastic-metal/blob/04f48da59f0a910fe8c8f34befbb01b1495af304/lefthook.yml).
Only the unsupported command `name` labels were removed for Lefthook 2.1.16,
and the Conventional Commit regex was minimally fixed to require a type (while
keeping the optional breaking-change marker); all other hook commands and
policies are unchanged. It checks staged secrets with
Betterleaks, staged `.sh`/`.bash` contents with
ShellCheck, rejects conflicted merge commits, and validates Conventional Commit
messages. `.betterleaks.toml` extends the scanner's built-in rules without
allowlisting paths. Never commit credentials; use environment variables or a
secret manager. The supporting ShellCheck script reads the Git index, not
unstaged working-tree changes.

To run the staged checks manually:

```bash
mise exec -- lefthook run pre-commit
```

See [`factory_v1/README.md`](factory_v1/README.md) for the controller CLI and its
trust boundaries.
