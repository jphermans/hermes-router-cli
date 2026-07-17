"""hermes-router — cheap-or-capable LLM routing from one CLI.

Release history:
  2.1.0  Added hr_route_default tool: PREFERRED DEFAULT that picks the
           cheapest free-tier model. The plugin now exposes a directive
           telling Hermes to use this for cost reduction on everyday
           prompts (greetings, summaries, translations, factual Q&A,
           short code) while keeping the standard model for explicit
           complex tasks. No more manual "route this through the free
           pool" — the agent does it automatically.
  2.0.0  Major release. Added: --auto-fallback, per-provider fallback
           chains, model blacklist/prefer, --verbose doctor, parallel
           fallback, --max-cost, --show-cost in chat, hr init wizard,
           rich --help with colors and icons.
  1.0.0  Initial release. 7 sub-commands, 11 providers, ~37 models,
           basic routing with curated fallback chain.
"""

__version__ = "2.2.0"
