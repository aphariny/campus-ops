"""Explainable triage: DETERMINISTIC KEYWORD RULES (not trained machine learning).
A human (facility manager) confirms or overrides the suggestion at assignment time."""
import re

CATEGORY_RULES = {
    "safety":          ["fire", "smoke", "gas", "alarm", "hazard", "sparking"],
    "electrical":      ["power", "electric", "socket", "outlet", "breaker", "voltage", "light", "flicker", "overload"],
    "hvac":            ["temperature", "hvac", "cooling", "air conditioning", "ventilation", "overheating", r"\bac\b"],
    "plumbing":        ["leak", "water", "pipe", "tap", "drain", "flood", "toilet"],
    "network_it":      ["wifi", "network", "projector", "internet", "server", "lan"],
    "furniture_civil": ["door", "window", "chair", "desk", "ceiling", "lock", "broken"],
}
SEVERITY_RULES = [
    ("critical", ["fire", "smoke", "flood", "sparking", "gas", "water leak"]),
    ("high",     ["not working", "failed", "outage", "no power", "overheating", "leak", "overload", "high temperature"]),
    ("medium",   ["slow", "flicker", "noisy", "intermittent"]),
]

def _hit(kw, text):
    pat = kw if kw.startswith("\\b") else r"\b" + re.escape(kw)
    return re.search(pat, text) is not None

def suggest(title, description=""):
    text = f"{title} {description}".lower()
    matched, scores = {}, {}
    for cat, kws in CATEGORY_RULES.items():
        hits = [k.replace("\\b", "") for k in kws if _hit(k, text)]
        if hits:
            matched[cat], scores[cat] = hits, len(hits)
    category = max(scores, key=scores.get) if scores else "general"   # first max in dict order breaks ties
    priority, sev_hits = "low", []
    for level, kws in SEVERITY_RULES:
        h = [k for k in kws if _hit(k, text)]
        if h:
            priority, sev_hits = level, h
            break
    if category == "safety" and priority in ("low", "medium"):
        priority, sev_hits = "high", sev_hits + ["rule:safety-floor"]
    explanation = (f"Category '{category}' from keywords {matched.get(category, [])}; "
                   f"priority '{priority}' from keywords {sev_hits or ['(none: default low)']}.")
    return {"category": category, "priority": priority, "matched": matched, "severity_matches": sev_hits,
            "explanation": explanation, "method": "deterministic_keyword_rules",
            "note": "Suggestion only; not machine learning. A facility manager must confirm priority."}
