#!/usr/bin/env python3
"""Labeled query set for the v3 judge eval (Chunk 3).

The experiment: does the in-fleet Gemma judge hold up against a frontier judge at
selecting/synthesizing across the three specialists' answers? To measure that we
need queries where the specialists genuinely DIVERGE — one clearly stronger, the
others plausible-but-worse — so the judge's choice actually matters. A query all
three answer identically well tells the judge apart from nothing.

Each item:
  id        — stable slug (also the candidate-cache key)
  category  — the specialist expected to answer best: coder | reasoning | general
              (this is the *hypothesis*, not ground truth — part of what the eval
              checks is whether the judge routes toward the right specialist)
  prompt    — the user turn sent to every specialist
  reference — concise gold answer / the key points a correct answer must contain.
              Used by a reference-based scorer; harmless to a pairwise scorer.

~6 per category. Kept deliberately short so a boot generates all candidates fast
and a human can eyeball the judge's picks.
"""
from __future__ import annotations

QUERY_SET: list[dict] = [
    # ---------- coder: code-writing / debugging (Qwen-Coder expected strongest) ----------
    {"id": "coder-dedupe-order", "category": "coder",
     "prompt": "Write a Python function that removes duplicates from a list while preserving order. One idiomatic line if possible.",
     "reference": "list(dict.fromkeys(xs)) — dict preserves insertion order (3.7+) and dedupes by key."},
    {"id": "coder-bug-mutable-default", "category": "coder",
     "prompt": "This Python function misbehaves across calls: `def add(x, acc=[]): acc.append(x); return acc`. What's the bug and the fix?",
     "reference": "Mutable default argument is shared across calls; use acc=None then acc = [] if acc is None inside."},
    {"id": "coder-sql-param", "category": "coder",
     "prompt": "Rewrite this to be safe: `cur.execute(\"SELECT * FROM users WHERE name = '\" + name + \"'\")`.",
     "reference": "Parameterized query: cur.execute('SELECT * FROM users WHERE name = %s', (name,)) — prevents SQL injection."},
    {"id": "coder-async-gather", "category": "coder",
     "prompt": "How do I run three async coroutines concurrently in Python and collect all their results?",
     "reference": "await asyncio.gather(c1(), c2(), c3()) returns results in order; runs them concurrently on one loop."},
    {"id": "coder-git-undo-commit", "category": "coder",
     "prompt": "I committed to the wrong branch but haven't pushed. How do I move the last commit to a new branch and remove it from the current one?",
     "reference": "git branch newbranch; git reset --hard HEAD~1 (on current); commit now lives on newbranch. Or cherry-pick then reset."},
    {"id": "coder-bigo-nested", "category": "coder",
     "prompt": "What is the time complexity of checking, for each element of a list of n items, whether it appears in a second list of n items using `in`? How would you make it faster?",
     "reference": "O(n^2) — `in` on a list is O(n). Convert the second list to a set for O(1) membership -> O(n) total."},

    # ---------- reasoning: math / logic / multi-step (R1-distill expected strongest) ----------
    {"id": "reason-prime-337", "category": "reasoning",
     "prompt": "Is 337 a prime number? Show your reasoning.",
     "reference": "Yes. No prime <= floor(sqrt(337))=18 (2,3,5,7,11,13,17) divides 337, so it is prime."},
    {"id": "reason-trains", "category": "reasoning",
     "prompt": "Two trains 300 km apart approach each other at 60 and 90 km/h. How long until they meet?",
     "reference": "Closing speed 150 km/h; time = 300/150 = 2 hours."},
    {"id": "reason-bat-ball", "category": "reasoning",
     "prompt": "A bat and a ball cost $1.10 together. The bat costs $1.00 more than the ball. How much is the ball?",
     "reference": "$0.05. (ball x, bat x+1, 2x+1=1.10 -> x=0.05.) Not $0.10 — the classic trap."},
    {"id": "reason-work-rate", "category": "reasoning",
     "prompt": "If 3 machines make 3 widgets in 3 minutes, how long do 100 machines take to make 100 widgets?",
     "reference": "3 minutes. Each machine makes 1 widget in 3 minutes; rate scales with machine count."},
    {"id": "reason-logic-cards", "category": "reasoning",
     "prompt": "Every card has a letter on one side and a number on the other. To test 'if a card shows a vowel, its other side is even', which of A, K, 4, 7 must you flip?",
     "reference": "A and 7. Flip the vowel (A) and the odd number (7, to check it isn't a vowel). 4 and K are irrelevant."},
    {"id": "reason-compound", "category": "reasoning",
     "prompt": "You invest $1000 at 10% annual interest compounded yearly. What is it worth after 3 years? Give the exact figure.",
     "reference": "1000 * 1.1^3 = $1331."},

    # ---------- general: explanation / knowledge / writing (Gemma expected strongest) ----------
    {"id": "gen-mutex-1sentence", "category": "general",
     "prompt": "Explain what a mutex is in one clear sentence.",
     "reference": "A mutex is a lock that lets only one thread access a shared resource at a time, preventing race conditions."},
    {"id": "gen-tcp-udp", "category": "general",
     "prompt": "In two or three sentences, explain the practical difference between TCP and UDP and when you'd pick each.",
     "reference": "TCP is reliable, ordered, connection-based (web, files); UDP is fast, connectionless, lossy (video, games, DNS). Pick TCP for correctness, UDP for latency."},
    {"id": "gen-photosynthesis", "category": "general",
     "prompt": "Explain photosynthesis to a curious 10-year-old in a few sentences.",
     "reference": "Plants use sunlight, water, and carbon dioxide from the air to make their own sugar (food) and release oxygen; the green part (chlorophyll) captures the light."},
    {"id": "gen-email-decline", "category": "general",
     "prompt": "Write a short, polite email declining a meeting invitation because of a scheduling conflict, and proposing to follow up async.",
     "reference": "Polite, concise: thanks for the invite, notes the conflict, declines, offers an async follow-up / alternative. Warm but professional tone."},
    {"id": "gen-analogy-cache", "category": "general",
     "prompt": "Give a simple everyday analogy that explains what a cache is.",
     "reference": "A cache is like keeping frequently used items on your desk instead of walking to the storage room each time — fast access to recently/often used things."},
    {"id": "gen-summarize-pros-cons", "category": "general",
     "prompt": "Give two pros and two cons of remote work, in a compact bulleted list.",
     "reference": "Pros: flexibility/no commute, wider talent/focus time. Cons: isolation/collaboration friction, blurred work-life boundaries. Balanced, concise."},
]


def by_category() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for q in QUERY_SET:
        out.setdefault(q["category"], []).append(q)
    return out


def demo() -> None:
    """Offline self-check — the query set is well-formed."""
    ids = [q["id"] for q in QUERY_SET]
    assert len(ids) == len(set(ids)), "duplicate query ids"
    cats = {q["category"] for q in QUERY_SET}
    assert cats == {"coder", "reasoning", "general"}, f"unexpected categories: {cats}"
    for q in QUERY_SET:
        assert q["prompt"].strip() and q["reference"].strip(), f"empty field in {q['id']}"
    counts = {c: len(v) for c, v in by_category().items()}
    print(f"ok — {len(QUERY_SET)} queries, balanced: {counts}")


if __name__ == "__main__":
    demo()
