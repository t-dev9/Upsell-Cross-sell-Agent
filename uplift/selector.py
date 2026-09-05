"""[3] SELECT - SEQUENCE - PITCH. The only stage that touches a model.

The model proposes from the stage-[2] eligible set as strict JSON, with exactly ONE
repair attempt on invalid output. If the call fails, times out, or is still unparseable
after that repair, the deterministic fallback selects from the SAME eligible set — never
an ungated candidate — and returns an ordinary Proposal that every later stage treats
identically to a model pick.

JSON validity determines only whether a proposal is parseable. It is NOT what makes an
action safe. Money Guard is the only security boundary, and it re-derives every claim
made here regardless of which path produced it.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

import httpx

from .config import Config
from .eligibility import EligibleSet
from .models import Candidate, Proposal

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = """You select ONE offer for a customer who has just paid.

You must choose from the numbered candidate list you are given. You may not invent an
offer, change a price, or propose anything not on the list. Reply with JSON only:

{"choice": <number>, "pitch": "<one sentence to the customer>"}

Choose the offer with the best expected value to the merchant that a customer would
plausibly accept."""


class LLMProvider(Protocol):
    """Anything that can turn a prompt into text. Swappable without touching the pipeline."""

    name: str

    def complete(self, system: str, user: str) -> str: ...


class GroqProvider:
    """Free-tier open-weights model over HTTP.

    That a 70B open model sits here rather than a frontier model is the point being
    demonstrated: the deterministic layer does the constraining, not the model.
    """

    def __init__(self, config: Config) -> None:
        self.name = f"groq/{config.llm_model}"
        self._model = config.llm_model
        self._timeout = config.llm_timeout_s
        self._key = os.environ.get("GROQ_API_KEY", "")

    def complete(self, system: str, user: str) -> str:
        resp = httpx.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {self._key}"},
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


class FixtureProvider:
    """Recorded response. The no-key default, so `uplift demo` runs for a fresh clone.

    Also how the red-team injects a jailbroken model's output without needing a model
    that will actually misbehave on demand.
    """

    def __init__(self, response: str | None = None) -> None:
        self.name = "fixture"
        self._response = response or '{"choice": 1, "pitch": "Recorded fixture selection."}'

    def complete(self, system: str, user: str) -> str:
        return self._response


class AnthropicProvider:
    """Stub. One-line switch if an API key ever appears; nothing depends on it today."""

    def __init__(self, config: Config) -> None:
        self.name = "anthropic"

    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("AnthropicProvider not wired — set llm_provider to groq or fixture")


def build_provider(config: Config) -> LLMProvider:
    if config.llm_provider == "groq" and os.environ.get("GROQ_API_KEY"):
        return GroqProvider(config)
    if config.llm_provider == "anthropic":
        return AnthropicProvider(config)
    return FixtureProvider()


def _render_candidates(eligible: EligibleSet) -> str:
    lines = []
    for i, c in enumerate(eligible.candidates, start=1):
        lines.append(
            f"{i}. lever={c.lever.value} sku={c.sku_code} qty={c.quantity} "
            f"price=INR {c.offer_price} — {c.rationale}"
        )
    return "\n".join(lines)


def _parse(raw: str, eligible: EligibleSet) -> tuple[Candidate, str]:
    """Parse a model reply into a candidate. Raises ValueError on anything unusable.

    An out-of-range choice is a parse failure, not a silent clamp — clamping would let a
    manipulated model steer selection by emitting garbage indices.
    """
    data = json.loads(raw)
    choice = int(data["choice"])
    if not 1 <= choice <= len(eligible.candidates):
        raise ValueError(f"choice {choice} outside 1..{len(eligible.candidates)}")
    pitch = str(data.get("pitch", ""))[:300]
    return eligible.candidates[choice - 1], pitch


def fallback_select(eligible: EligibleSet) -> Proposal:
    """Deterministic selection from the SAME eligible set the model was shown.

    Highest offer price wins — a stand-in for expected value that is reproducible and
    needs no model. Never an ungated candidate: the pool is stage [2]'s output.
    """
    best = max(eligible.candidates, key=lambda c: c.offer_price)
    return Proposal(
        candidate=best,
        pitch=best.rationale,
        source="fallback",
        repaired=False,
    )


def select(
    eligible: EligibleSet,
    config: Config,
    *,
    provider: LLMProvider | None = None,
) -> tuple[Proposal, list[str]]:
    """Return (proposal, notes). Notes record what happened, for the failure log.

    Never raises: any provider failure becomes a fallback proposal. The caller always
    gets something for Money Guard to evaluate.
    """
    notes: list[str] = []
    if not eligible.candidates:
        raise ValueError("empty eligible set — stage [2] produced no candidates")

    llm = provider or build_provider(config)
    notes.append(f"provider={llm.name}")
    user = f"Candidates:\n{_render_candidates(eligible)}\n\nReply with JSON only."

    raw: str | None = None
    try:
        raw = llm.complete(SYSTEM_PROMPT, user)
    except Exception as exc:  # noqa: BLE001 — any provider failure falls back
        notes.append(f"provider error: {type(exc).__name__} — falling back")
        return fallback_select(eligible), notes

    try:
        candidate, pitch = _parse(raw, eligible)
        return Proposal(candidate=candidate, pitch=pitch, source="ai"), notes
    except Exception as exc:  # noqa: BLE001
        notes.append(f"invalid JSON ({type(exc).__name__}) — one repair attempt")

    # The single repair attempt.
    try:
        repair_user = (
            f"{user}\n\nYour previous reply was not valid JSON matching "
            '{"choice": <int>, "pitch": "<str>"}. Reply again, JSON only.'
        )
        raw = llm.complete(SYSTEM_PROMPT, repair_user)
        candidate, pitch = _parse(raw, eligible)
        notes.append("repair succeeded")
        return Proposal(candidate=candidate, pitch=pitch, source="ai", repaired=True), notes
    except Exception as exc:  # noqa: BLE001
        notes.append(f"repair failed ({type(exc).__name__}) — deterministic fallback")
        return fallback_select(eligible), notes
