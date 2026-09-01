"""Very simple keyword-based negative-news detection.

This is a heuristic, not real sentiment analysis or NLP -- it flags a headline
as "bad news" if it contains any of a curated list of negative-indicator
words or phrases. It will miss subtler bad news (no keyword match) and can
occasionally misfire on a headline that merely mentions one of these words
in an unrelated context. It only controls what shows up in the dashboard's
urgent alert banner -- it never influences any trading decision.
"""

_NEGATIVE_KEYWORDS = [
    "lawsuit", "sues", "sued", "suing",
    "investigation", "investigated", "inquiry", "probe", "subpoena",
    "recall", "recalls", "recalled",
    "downgrade", "downgraded", "downgrades",
    "misses estimates", "miss estimates", "missed estimates",
    "cuts guidance", "cut guidance", "guidance cut", "lowers guidance", "lowered guidance",
    "layoffs", "layoff", "job cuts",
    "bankruptcy", "bankrupt", "insolvent", "insolvency",
    "fraud", "scandal",
    "resigns", "resignation", "steps down", "ousted", "fired",
    "plunge", "plunges", "plunged", "tumbles", "tumbled", "sinks", "sinking",
    "crash", "crashes", "crashed", "slumps", "slumped",
    "warns", "warning", "profit warning",
    "delisted", "delisting",
    "sec charges", "charges filed", "indicted", "indictment", "class action",
    "data breach", "breach", "hacked", "cyberattack", "ransomware",
    "outage", "halted", "trading halt",
    "short seller", "short-seller", "shortseller",
    "accounting irregularities", "restate", "restatement", "restated earnings",
    "default", "defaults", "debt crisis",
    "strike", "walkout", "boycott",
]


def is_bad_news(headline: str) -> bool:
    lower = (headline or "").lower()
    return any(keyword in lower for keyword in _NEGATIVE_KEYWORDS)
