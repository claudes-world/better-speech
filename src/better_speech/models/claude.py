"""Claude subscription CLI; authentication and generation share one timeout."""

import json
import time

from ..budget import remaining
from .process import prompt, run_bounded


class ClaudeAdapter:
    def __init__(self, profile):
        self.model = profile.claude_model

    def __call__(self, payload, *, timeout):
        deadline = time.monotonic() + timeout
        status = json.loads(
            run_bounded(
                ["claude", "--setting-sources", "", "auth", "status", "--json"], "", remaining(deadline)
            )
        )
        if (
            status.get("loggedIn") is not True
            or status.get("authMethod") != "claude.ai"
            or status.get("apiProvider") != "firstParty"
            or status.get("apiKeySource")
        ):
            raise RuntimeError("subscription authentication required")
        command = [
            "claude",
            "-p",
            "--model",
            self.model,
            "--output-format",
            "text",
            "--tools",
            "",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--settings",
            '{"forceLoginMethod":"claudeai","disableAllHooks":true}',
        ]
        return run_bounded(command, prompt(payload), remaining(deadline))
