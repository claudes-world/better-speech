"""Gemini Flash through the Antigravity subscription CLI, never an API key."""

from .process import prompt, run_bounded


class AgyAdapter:
    def __init__(self, profile):
        self.model = profile.agy_model

    def __call__(self, payload, *, timeout):
        return run_bounded(
            [
                "agy",
                "--print",
                "--model",
                self.model,
                "--input-format",
                "text",
                "--output-format",
                "text",
                "--mode",
                "plan",
                "--sandbox",
                "--disable-slash-commands",
            ],
            prompt(payload),
            timeout,
        )
