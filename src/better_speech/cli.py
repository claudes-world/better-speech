"""speak: local preparation and learner queue commands. Never sends or counts delivery."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from .compiler import compile
from .config import load_profile
from .learning import due, learn, learned
from .ledger import Ledger
from .rendering import render


def parser():
    root = argparse.ArgumentParser(prog="speak", description="Vietnamese lessons inside English voice notes")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("plan", "render", "learn", "learned", "due"):
        command = commands.add_parser(name)
        command.add_argument("--profile", type=Path)
        command.add_argument("--learner")
        if name in ("plan", "render"):
            command.add_argument("input", type=Path)
        if name == "render":
            command.add_argument("--out", required=True, type=Path)
            command.add_argument("--cache", type=Path)
        if name == "learn":
            command.add_argument("phrase")
            command.add_argument("--gloss", required=True)
            command.add_argument("--hint")
        if name == "learned":
            command.add_argument("key")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        profile = load_profile(args.profile)
        if args.learner:
            profile = replace(profile, learner_id=args.learner)
        ledger = Ledger.for_profile(profile)
        if args.command == "learn":
            print(json.dumps(learn(args.phrase, args.gloss, profile, ledger=ledger, hint=args.hint), ensure_ascii=False, indent=2))
        elif args.command == "learned":
            print(json.dumps(learned(args.key, profile, ledger=ledger), ensure_ascii=False, indent=2))
        elif args.command == "due":
            print(json.dumps(due(profile, ledger=ledger), ensure_ascii=False, indent=2))
        else:
            plan = compile(args.input.read_text(encoding="utf-8"), profile, ledger=ledger)
            if args.command == "plan":
                print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
            else:
                from .providers.elevenlabs import ElevenLabsProvider
                from .providers.google import GoogleProvider

                result = render(
                    plan,
                    {
                        "google": GoogleProvider(),
                        "elevenlabs": ElevenLabsProvider(os.environ.get("ELEVENLABS_API_KEY")),
                    },
                    args.cache,
                    render_timeout_s=profile.render_timeout_s,
                    provider_timeout_s=profile.provider_timeout_s,
                    fallback_reserve_s=profile.fallback_reserve_s,
                )
                if result.audio_path:
                    temporary = None
                    try:
                        args.out.parent.mkdir(parents=True, exist_ok=True)
                        with tempfile.NamedTemporaryFile(
                            dir=args.out.parent, prefix=".speak-", delete=False
                        ) as stream:
                            temporary = stream.name
                            with result.audio_path.open("rb") as audio:
                                shutil.copyfileobj(audio, stream)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, args.out)
                    finally:
                        if temporary:
                            Path(temporary).unlink(missing_ok=True)
                        result.audio_path.unlink(missing_ok=True)
                        result.audio_path.parent.rmdir()
                else:
                    args.out.unlink(missing_ok=True)
                print(result.markdown, end="")
                for diagnostic in result.diagnostics:
                    print(f"speak: {diagnostic.stage}: {diagnostic.reason}", file=sys.stderr)
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(f"speak: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
