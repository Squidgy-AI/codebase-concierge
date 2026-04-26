"""
Capability gap insights — read-only report over the Q&A cache.

Heuristic: a sales-mode answer that contains negative-capability markers
("not yet", "doesn't support", "limited", "partial", "currently does not")
is a candidate gap. Group by question signature, rank by repeat-asks +
hit-count, surface the top N as a product-opportunity list.

No new Nia or Claude calls — this is pure post-hoc analysis of cache rows.
"""
import re
from collections import defaultdict

import cache


_NEG_MARKERS = re.compile(
    r"\b(no support|don't support|doesn't support|do not support|does not support|"
    r"not supported|not yet|partial|limited|currently does not|currently doesn't|"
    r"missing|lacks?|cannot|can't|unsupported|coming soon|on (the )?roadmap|"
    r"no, |no\.|not currently|out of scope|not (?:provided|available))\b",
    re.IGNORECASE,
)


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "").strip()


def _signature(question: str) -> str:
    # Mirror cache._signature so similar questions cluster.
    tokens = re.findall(r"[a-z0-9]+", question.lower())
    stop = {
        "a", "an", "the", "is", "are", "was", "were", "be", "of", "to", "in",
        "on", "at", "by", "for", "with", "from", "as", "and", "or", "but",
        "not", "this", "that", "do", "does", "did", "how", "what", "why",
        "when", "where", "i", "you", "we", "they", "it", "its",
    }
    return " ".join(sorted(t for t in tokens if t not in stop and len(t) > 1))


def find_capability_gaps(limit: int = 50, top: int = 10) -> list[dict]:
    """Pull recent sales-mode rows; return clusters of likely gaps."""
    rows = cache.recent(limit=limit)
    clusters: dict[str, dict] = defaultdict(
        lambda: {"signature": "", "questions": [], "askers": set(), "asks": 0, "snippet": ""}
    )

    for r in rows:
        q = r["question"] or ""
        if not q.startswith("[sales]"):
            continue
        plain = _strip_html(r["answer_html"] or "")
        if not _NEG_MARKERS.search(plain):
            continue
        clean_q = q[len("[sales]"):].strip()
        sig = _signature(clean_q) or clean_q[:60].lower()
        c = clusters[sig]
        c["signature"] = sig
        if clean_q not in c["questions"]:
            c["questions"].append(clean_q)
        if r.get("original_sender"):
            c["askers"].add(r["original_sender"])
        c["asks"] += 1 + (r.get("hit_count") or 0)
        if not c["snippet"]:
            # First 240 chars of the prose answer as evidence.
            c["snippet"] = plain[:240].strip()

    out = []
    for c in clusters.values():
        out.append({
            "signature": c["signature"],
            "exemplar_question": c["questions"][0],
            "all_questions": c["questions"],
            "asker_count": len(c["askers"]),
            "askers": sorted(c["askers"]),
            "ask_count": c["asks"],
            "snippet": c["snippet"],
        })
    out.sort(key=lambda c: (c["ask_count"], c["asker_count"]), reverse=True)
    return out[:top]
