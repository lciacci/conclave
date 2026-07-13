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

12 per category (n=36, expanded from 18 in the rigor pass). Kept deliberately short
so a boot generates all candidates fast and a human can eyeball the judge's picks.
The second block of 12 leans on TRAP questions — ones with a fluent wrong answer a
weak judge will happily pick — because n=18 discriminated less than we wanted.
"""
from __future__ import annotations

import os

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

    # ======================= RIGOR PASS: n 18 -> 36 =======================
    # Second block, added for the rigor pass. Same 6-per-category balance. These
    # lean harder on TRAPS — questions with a plausible wrong answer a weak model
    # reaches for (bare `except`, 45 km/h, 1/2 on Monty Hall). A judge that can
    # only pattern-match fluency picks the trap; a judge that reasons rejects it.
    # That is the discrimination the first 18 were light on.

    # ---------- coder ----------
    {"id": "coder-float-equality", "category": "coder",
     "prompt": "Why does `0.1 + 0.2 == 0.3` return False in Python, and how should I compare floats instead?",
     "reference": "Binary floating point cannot represent 0.1/0.2/0.3 exactly, so the sum is 0.30000000000000004. Compare with math.isclose(a, b) (or an explicit tolerance), never ==."},
    {"id": "coder-mutate-while-iterating", "category": "coder",
     "prompt": "This skips elements: `for x in xs: if x < 0: xs.remove(x)`. Why, and what's the correct way?",
     "reference": "Removing during iteration shifts subsequent elements left while the internal index advances, so items are skipped. Build a new list instead: xs = [x for x in xs if x >= 0] (or iterate over a copy, xs[:])."},
    {"id": "coder-shallow-copy", "category": "coder",
     "prompt": "I did `b = a.copy()` on a list of lists, then changed `b[0][0]` and `a` changed too. Why, and how do I avoid it?",
     "reference": "list.copy() is shallow — the outer list is new but the inner lists are the same objects. Use copy.deepcopy(a) for an independent nested copy."},
    {"id": "coder-threads-vs-processes", "category": "coder",
     "prompt": "For a CPU-bound Python workload, should I use threads or processes? Why?",
     "reference": "Processes (multiprocessing / ProcessPoolExecutor). The GIL lets only one thread execute Python bytecode at a time, so threads give no CPU parallelism; they only help I/O-bound work, where the GIL is released while waiting."},
    {"id": "coder-bare-except", "category": "coder",
     "prompt": "What's wrong with writing `try: ... except: pass` in Python?",
     "reference": "A bare except catches BaseException — including KeyboardInterrupt and SystemExit — so it swallows Ctrl-C and shutdown, and `pass` hides real bugs. Catch the specific exception, or `except Exception` at minimum, and log it."},
    {"id": "coder-string-concat-loop", "category": "coder",
     "prompt": "Building a big string by doing `s += part` inside a loop over 100k parts is slow. Why, and what's the fix?",
     "reference": "Strings are immutable, so each += allocates a new string and copies everything so far — O(n^2) total. Collect the parts in a list and use ''.join(parts), which is O(n)."},

    # ---------- reasoning ----------
    {"id": "reason-monty-hall", "category": "reasoning",
     "prompt": "On a game show there are 3 doors, a car behind one and goats behind the other two. You pick door 1. The host, who knows what's behind each door, opens door 3 to reveal a goat and offers you the switch to door 2. Should you switch?",
     "reference": "Yes, switch. Your first pick wins 1/3 of the time; switching wins 2/3. The host's knowing choice moves that 2/3 onto the one remaining door. It is NOT 50/50."},
    {"id": "reason-avg-speed", "category": "reasoning",
     "prompt": "You drive up a hill at 60 km/h and back down the same road at 30 km/h. What is your average speed for the round trip?",
     "reference": "40 km/h — the harmonic mean, 2*60*30/(60+30). Not 45: you spend twice as long on the slow leg, so the two speeds are not weighted equally."},
    {"id": "reason-lily-pad", "category": "reasoning",
     "prompt": "A patch of lily pads doubles in size every day and covers the whole lake on day 48. On what day did it cover half the lake?",
     "reference": "Day 47. It doubles daily, so the day before it is full it is exactly half. Not day 24."},
    {"id": "reason-handshakes", "category": "reasoning",
     "prompt": "Ten people are at a party and each person shakes hands exactly once with every other person. How many handshakes happen in total?",
     "reference": "45. C(10,2) = 10*9/2 = 45 — divide by 2 because each handshake involves two people and would otherwise be counted twice."},
    {"id": "reason-clock-angle", "category": "reasoning",
     "prompt": "What is the angle between the hour and minute hands of a clock at exactly 3:15?",
     "reference": "7.5 degrees. The minute hand is at 90 degrees; the hour hand has moved past 3 by 15 minutes' worth: 3*30 + 15*0.5 = 97.5. Difference = 7.5. Not 0 — the hour hand does not sit on the 3."},
    {"id": "reason-socks-pigeonhole", "category": "reasoning",
     "prompt": "A drawer holds an unknown number of black socks and white socks, mixed at random in the dark. How many socks must you take out to be certain you have a matching pair?",
     "reference": "3. With only 2 colors, any 3 socks must contain two of the same color (pigeonhole principle). Two socks is not enough — they could be one of each."},

    # ---------- general ----------
    {"id": "gen-https-explain", "category": "general",
     "prompt": "In a few sentences, explain what HTTPS actually protects and what it does not.",
     "reference": "HTTPS encrypts the traffic between browser and server and authenticates the server's identity, so an eavesdropper cannot read or tamper with the contents. It does NOT make the site itself trustworthy or safe — a phishing site can have a valid certificate; it also does not hide which site you visited."},
    {"id": "gen-api-vs-library", "category": "general",
     "prompt": "What's the difference between an API and a library? Explain simply.",
     "reference": "A library is code you pull into your own program and call directly. An API is an interface/contract for talking to something else — often over a network, on someone else's machine. A library ships to you; an API is something you call out to. (A library also has an API — its public surface.)"},
    {"id": "gen-recursion-analogy", "category": "general",
     "prompt": "Give a simple everyday analogy that explains recursion, including why it needs a base case.",
     "reference": "Like standing in a line and asking the person ahead 'how many people are in front of you?', each passing the question forward until someone at the front says 'zero' and the answers come back +1 each. The base case is the front of the line — without it the question passes forever (infinite recursion / stack overflow)."},
    {"id": "gen-inflation-explain", "category": "general",
     "prompt": "Explain inflation, and why a little is considered healthy, to someone with no economics background.",
     "reference": "Inflation is prices rising over time, so each unit of money buys less. A low, steady rate (~2%) is considered healthy: it encourages spending/investing rather than hoarding cash, and gives central banks room to cut rates in a downturn. Deflation (falling prices) is worse — people delay purchases and the economy stalls."},
    {"id": "gen-apology-email", "category": "general",
     "prompt": "Write a short, professional email apologizing to a client for missing an agreed deadline, giving a new date and not making excuses.",
     "reference": "Short, takes clear ownership without blaming or over-explaining, states the concrete new delivery date, says what is being done to hold it, offers to talk. Professional and direct, not grovelling."},
    {"id": "gen-ev-vs-gas", "category": "general",
     "prompt": "Give a balanced, compact comparison of electric vs petrol cars — two points in favor of each.",
     "reference": "EV: much lower running/fuel and maintenance cost; no tailpipe emissions (cleaner in use, cleaner still on a low-carbon grid). Petrol: fast refuelling and dense refuelling network; lower purchase price and no range/charging anxiety on long trips. Balanced, no advocacy."},
]


def active_set_name() -> str:
    """base (default) | hard | all — selected by $CONCLAVE_QUERYSET.

    Defaults to `base` so every existing command keeps replaying the frozen, published
    run for $0. You must opt IN to the hard set."""
    name = os.environ.get("CONCLAVE_QUERYSET", "base").strip().lower()
    if name not in ("base", "hard", "all"):
        raise SystemExit(f"CONCLAVE_QUERYSET must be base|hard|all, got {name!r}")
    return name


def active_query_set(name: str | None = None) -> list[dict]:
    """The query set the eval tools should run against.

    `hard` is the UNSATURATED set (eval_queryset_hard.py): the base 36 pinned 31 of their
    best candidates at the grader's ceiling, where headroom is 0 by construction, so the
    fleet could not be diagnosed with them."""
    name = name or active_set_name()
    if name == "base":
        return QUERY_SET
    from eval_queryset_hard import HARD_QUERY_SET
    return HARD_QUERY_SET if name == "hard" else QUERY_SET + HARD_QUERY_SET


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
    assert len(set(counts.values())) == 1, f"categories not balanced: {counts}"
    print(f"ok — {len(QUERY_SET)} queries, balanced: {counts}")


if __name__ == "__main__":
    demo()
