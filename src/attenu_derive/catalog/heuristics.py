"""
Heuristic tier of the tool→scope catalog (v1): when a tool name is neither an exact entry nor a
glob pattern, classify it into a vocabulary FAMILY from its name (verb/noun) and description.
Every heuristic result is flagged `heuristic: True` (lower confidence; reported separately in
coverage) — it is a lead for catalog curation, not a curated fact. Unknown -> None (fail closed).
"""
from __future__ import annotations

import re

RULES: list[tuple[str, str, int]] = [    # (regex on "name description", scope, tier)
    # ---- pure computation: no resource authority needed (still a scope so it is explicit) ----
    (r"\b(calc|calculate|compute|convert|conversion|math|factorial|hypot|mean|median|std|variance|logarithm|sqrt|round|percent|probability|prime|gcd|lcm|area|volume|distance|estimate|derivative|integral|sum|add|subtract|multiply|divide|absolute|min_value|max_value|sort_?value|permutation|combination|binomial|matrix)\w*", "compute.pure", 0),
    # ---- filesystem verbs (BFCL GorillaFileSystem-style) ----
    (r"^(cat|cd|ls|pwd|find|grep|wc|tail|head|diff|du|echo|sort|tree)\b", "fs.read", 0),
    (r"^(mkdir|touch|mv|cp|rm|rmdir|write|edit|append)\b|\b(create_?file|write_?file|save_?file)\b", "fs.write", 1),
    (r"^(rm|rmdir|delete_?file|remove_?file)\b", "fs.delete", 2),
    # ---- web / network ----
    (r"\b(fetch_?url|http_?get|download|crawl|scrape|open_?url|fetch_?content)\w*", "web.fetch", 1),
    (r"\b(search_?engine|web_?search|search_?web|google|bing|query_?search)\w*", "web.search", 1),
    # ---- messaging / social ----
    (r"\b(send_?(message|mail|email|sms|dm)|post_?tweet|tweet|comment|reply|notify|message_?send|email)\w*", "mail.send", 2),
    # ---- payments / finance / booking (money moves) ----
    (r"\b(pay|payment|transfer|purchase|buy|sell|order|place_?order|book_?(flight|hotel|car|ticket)|fund_?account|withdraw|deposit|trade|checkout|refund)\w*", "payments.transfer", 2),
    # ---- generic data verbs (APIs, memory, tickets, watchlists) ----
    (r"\b(delete|remove|clear|cancel|close|revoke|drop|purge)\w*", "data.delete", 2),
    (r"\b(create|add|update|edit|set|put|post|insert|register|resolve|archive|append|replace|follow|unfollow|activate|deactivate|adjust|start|stop|toggle|enable|disable|lock|unlock|login|logout|authenticate)\w*", "data.write", 1),
    (r"\b(get|list|read|retrieve|fetch|show|display|view|check|status|info|history|search|find|query|filter|lookup|describe|stats|estimate|is_|has_|verify)\w*", "data.read", 0),
    # ---- code execution ----
    (r"\b(exec|execute|run_?(code|shell|command|script)|shell|bash|python_?exec|eval)\w*", "code.exec", 2),
]
_COMPILED = [(re.compile(p, re.I), sc, t) for p, sc, t in RULES]


def heuristic_resolve(name: str, description: str = "") -> dict | None:
    """Return {"scope", "tier", "heuristic": True, "rule": <index>} or None."""
    text = f"{name} {description or ''}"
    base = name.split(".")[-1]                       # `math.factorial` -> factorial ; `weather.get` -> get
    for i, (rx, scope, tier) in enumerate(_COMPILED):
        if rx.search(base) or rx.search(name) or (i not in (1, 2, 3) and rx.search(text)):
            return {"scope": scope, "tier": tier, "heuristic": True, "rule": i}
    return None
