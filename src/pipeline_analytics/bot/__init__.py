"""Feasibility Specialist Bot: a Claude agent that runs viability studies through the engine.

Needs the ``bot`` extra (``anthropic``) and Claude API credentials
(``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile).

>>> from pipeline_analytics.bot import TOOLS
>>> len(TOOLS) >= 9
True
"""

from pipeline_analytics.bot.agent import (
    MODEL,
    SYSTEM_PROMPT,
    FeasibilityBot,
    Reply,
    unsupported_numbers,
)
from pipeline_analytics.bot.study import DesignBasis, Study
from pipeline_analytics.bot.tools import TOOL_BY_NAME, TOOLS, run_tool

__all__ = [
    "MODEL",
    "SYSTEM_PROMPT",
    "TOOLS",
    "TOOL_BY_NAME",
    "DesignBasis",
    "FeasibilityBot",
    "Reply",
    "Study",
    "run_tool",
    "unsupported_numbers",
]
