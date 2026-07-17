"""
Prompt complexity classifier — heuristic, zero extra LLM cost.

Maps a prompt to a capability tier: cheap | standard | pro
Used by the router to filter the candidate model pool before price-ranking.

Also detects whether the prompt requires vision (image understanding).
"""
from __future__ import annotations

import re

# Signals that a task is "hard" and needs a pro-tier model.
PRO_SIGNALS = [
    r"\breason\b", r"\breasoning\b", r"\banalyz", r"\bdebug\b", r"\btrace\b",
    r"\bmulti-step\b", r"\bagent\b", r"\bcomplex\b", r"\bthink step by step\b",
    r"\bprove\b", r"\barchitect", r"\boptimiz", r"\brefactor\b", r"\binvestigat",
    r"\bcompare and contrast\b", r"\bwhy\b.*\bbecause\b",
]

# Signals that a task is "easy" and can use a cheap-tier model.
CHEAP_SIGNALS = [
    r"\btranslate\b", r"\bsummar", r"\brephras", r"\bextract\b", r"\bclassif",
    r"\blist\b", r"\btitle\b", r"\bcaption\b", r"\bshort\b", r"\bone word\b",
    r"\byes or no\b", r"\bconvert\b", r"\bformat\b", r"\bclean up\b",
]

# Signals that a task needs vision (image understanding).
VISION_SIGNALS = [
    r"\bimage\b", r"\bscreenshot\b", r"\bphoto\b", r"\bpicture\b",
    r"\bdiagram\b", r"\bchart\b", r"\bfigure\b", r"\bocr\b",
    r"\bread (the )?(image|screenshot|photo|picture|chart|diagram)\b",
    r"\bdescrib.*(image|screenshot|photo|picture|chart|diagram)\b",
    r"\bwhat.*(in|on|shown in).*(image|screenshot|photo|picture|chart|diagram)\b",
    r"\bidentif.*(image|screenshot|photo|picture)\b",
    r"\bvision\b", r"\bsee\b.*\b(image|this|that|screenshot)\b",
    r"\bbase64\b", r"\bdata:image",
    r"\bdetect\b.*\b(in|on)\b.*\b(image|photo|picture|screen)\b",
]

# Above this token estimate, even simple tasks benefit from a larger context ->
# we bump cheap -> standard. Rough char-count heuristic (~4 chars/token).
LONG_PROMPT_CHARS = 4000


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def classify(prompt: str) -> str:
    """Return one of: 'cheap', 'standard', 'pro'."""
    p = prompt.lower()
    pro_hits = sum(1 for rx in PRO_SIGNALS if re.search(rx, p))
    cheap_hits = sum(1 for rx in CHEAP_SIGNALS if re.search(rx, p))
    est_tokens = estimate_tokens(prompt)

    # Strong hard signals win outright.
    if pro_hits >= 2 or any(re.search(rx, p) for rx in PRO_SIGNALS[:6]):
        return "pro"

    # Clear easy signals and short prompt -> cheap.
    if cheap_hits >= 1 and est_tokens < LONG_PROMPT_CHARS:
        return "cheap"

    # Long prompts (need context) go to standard minimum.
    if est_tokens >= LONG_PROMPT_CHARS:
        return "standard"

    # Default middle ground.
    return "standard"


def needs_vision(prompt: str) -> bool:
    """Return True if the prompt likely requires a vision-capable model."""
    p = prompt.lower()
    return any(re.search(rx, p) for rx in VISION_SIGNALS)


def explain(prompt: str) -> dict:
    """Return classification + the reasons (for logging / dry-run output)."""
    p = prompt.lower()
    pro_hits = [rx for rx in PRO_SIGNALS if re.search(rx, p)]
    cheap_hits = [rx for rx in CHEAP_SIGNALS if re.search(rx, p)]
    vision_hits = [rx for rx in VISION_SIGNALS if re.search(rx, p)]
    est = estimate_tokens(prompt)
    tier = classify(prompt)
    return {
        "tier": tier,
        "estimated_tokens": est,
        "pro_signals": pro_hits,
        "cheap_signals": cheap_hits,
        "needs_vision": bool(vision_hits),
        "vision_signals": vision_hits,
    }


if __name__ == "__main__":
    import sys
    sample = sys.argv[1] if len(sys.argv) > 1 else "Summarize this article in one paragraph."
    print(explain(sample))
