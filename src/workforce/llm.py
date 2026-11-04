"""Thin LLM clients. The agent only needs: give a system + user prompt, get text back.

Pick the provider with environment variables (see .env.example):
    LLM_PROVIDER=openai      any OpenAI-compatible endpoint. Default setup: Groq's
                             free tier serving open-weight models (Llama 3.3 70B,
                             GPT-OSS 120B, Qwen3 32B), via OPENAI_BASE_URL.
    LLM_PROVIDER=anthropic   Claude, if you have a key.
Tests use FakeLLM and never call a real API.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.calls += 1
        self.input_tokens += input_tokens or 0
        self.output_tokens += output_tokens or 0


class LLM:
    model: str = "unknown"

    def __init__(self) -> None:
        self.usage = Usage()

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError


class AnthropicLLM(LLM):
    def __init__(self, model: str | None = None, max_tokens: int = 1024):
        super().__init__()
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model or os.getenv("LLM_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        self.usage.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


THINK_BLOCK = re.compile(r"<think>.*?</think>", flags=re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Reasoning models (Qwen3, DeepSeek R1) may put their thinking in <think> tags."""
    return THINK_BLOCK.sub("", text).strip()


class OpenAILLM(LLM):
    def __init__(self, model: str | None = None):
        super().__init__()
        from openai import OpenAI

        self.client = OpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)
        self.model = model or os.environ["LLM_MODEL"]

    def complete(self, system: str, user: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        if resp.usage:
            self.usage.add(resp.usage.prompt_tokens, resp.usage.completion_tokens)
        return strip_reasoning(resp.choices[0].message.content or "")


@dataclass
class FakeLLM(LLM):
    """Replays scripted replies. Each item is a string or a function of (system, user)."""

    replies: list[str | Callable[[str, str], str]] = field(default_factory=list)
    prompts: list[tuple[str, str]] = field(default_factory=list)
    model: str = "fake"

    def __post_init__(self) -> None:
        super().__init__()

    def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        if not self.replies:
            raise RuntimeError("FakeLLM ran out of scripted replies")
        reply = self.replies.pop(0)
        out = reply(system, user) if callable(reply) else reply
        self.usage.add(len(system + user) // 4, len(out) // 4)
        return out


def get_llm() -> LLM:
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    if provider == "anthropic":
        return AnthropicLLM()
    if provider == "openai":
        return OpenAILLM()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
