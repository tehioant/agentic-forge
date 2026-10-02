"""Command-line entry point for Agentic Forge."""
from __future__ import annotations

import argparse
import json
import shlex
import sys

from .core import Forge, ForgeError


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="forge", description="Approval-gated local agent factory")
    p.add_argument("--home", help="isolated factory HERMES_HOME (defaults to ~/.local/share/agentic-forge/hermes)")
    p.add_argument("--state", help="factory state directory (defaults to ~/.local/share/agentic-forge/state)")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("register")
    r.add_argument("slug")
    r.add_argument("repo")
    r.add_argument("--verify", required=True, help="verification argv, parsed with shlex.split (never a shell)")
    r.add_argument("--remote")
    t = sub.add_parser("ticket")
    t.add_argument("project")
    t.add_argument("title")
    t.add_argument("--spec-file", required=True)
    t.add_argument("--allow-path", action="append", required=True)
    a = sub.add_parser("approve"); a.add_argument("project"); a.add_argument("id")
    run = sub.add_parser("run"); run.add_argument("project")
    run.add_argument("--campaign", default="default")
    run.add_argument("--max-tickets", type=int, default=5)
    run.add_argument("--budget", type=int, default=3600)
    run.add_argument("--ticket-id", action="append", dest="ticket_ids")
    for name in ("status", "pause", "resume"):
        q = sub.add_parser(name); q.add_argument("project")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    forge = Forge(home=args.home, state_root=args.state)
    try:
        if args.command == "register":
            out = forge.register(args.slug, args.repo, verify=shlex.split(args.verify), remote=args.remote)
        elif args.command == "ticket":
            out = forge.ticket(args.project, args.title, spec_file=args.spec_file, allow_path=args.allow_path)
        elif args.command == "approve":
            out = forge.approve(args.project, args.id)
        elif args.command == "run":
            out = forge.run(args.project, campaign=args.campaign, max_tickets=args.max_tickets,
                            budget=args.budget, ticket_ids=args.ticket_ids)
        elif args.command == "status":
            out = forge.status(args.project)
        elif args.command == "pause":
            out = forge.pause(args.project)
        else:
            out = forge.resume(args.project)
        print(json.dumps(out, sort_keys=True))
        return 0
    except (ForgeError, OSError, ValueError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
