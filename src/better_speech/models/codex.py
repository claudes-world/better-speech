"""Codex subscription CLI, without a fallback route."""

from .process import prompt, run_bounded


class CodexAdapter:
    def __init__(self, profile):
        self.model = profile.codex_model

    def __call__(self, payload, *, timeout):
        return run_bounded(
            [
                "codex",
                "exec",
                "--ignore-user-config",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--disable",
                "shell_tool",
                "--disable",
                "unified_exec",
                "-c",
                'forced_login_method="chatgpt"',
                "-c",
                'model_provider="openai"',
                "-m",
                self.model,
                "-",
            ],
            prompt(payload),
            timeout,
        )
