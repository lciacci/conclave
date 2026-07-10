#!/usr/bin/env python3
"""Controlled-length contention baseline. Every call generates a FIXED token
count (ignore_eos + max_tokens), so wall-time reflects GPU contention, not R1's
variable chain-of-thought length — the confound in the first harness run.

tax = (parallel_wall - max_solo) / max_solo, per query and aggregated.
Reports completion_tokens to confirm length is actually pinned.
"""
import json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

GW = os.environ.get("CONCLAVE_GW", "").rstrip("/")
if GW and not GW.startswith("http"):
    GW = "http://" + GW
MODELS = ["coder", "reasoning", "general"]
MAX_TOK = 256

PROMPTS = [
    "Explain how a hash map works.",
    "Describe the tradeoffs between TCP and UDP.",
    "What is gradient descent and why does it work?",
    "Explain the CAP theorem.",
    "How does public-key cryptography establish a shared secret?",
    "Describe how a B-tree keeps itself balanced.",
    "Explain what a race condition is and how to prevent one.",
    "How does a garbage collector decide what to free?",
]


def call(model, prompt):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOK,
        "ignore_eos": True,      # vLLM: force exactly MAX_TOK tokens
        "temperature": 0.0,
    }).encode()
    req = urllib.request.Request(GW + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer none"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
    dt = time.monotonic() - t0
    toks = (d.get("usage") or {}).get("completion_tokens", -1)
    return dt, toks


def solo_pass(prompt):
    out = {}
    for m in MODELS:
        dt, toks = call(m, prompt)
        out[m] = (round(dt, 2), toks)
    return out


def parallel_pass(prompt):
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(MODELS)) as ex:
        list(ex.map(lambda m: call(m, prompt), MODELS))
    return round(time.monotonic() - t0, 2)


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def main():
    if not GW:
        sys.exit("set CONCLAVE_GW=100.x.y.z:4000")
    print(f"warmup... (max_tok={MAX_TOK}, ignore_eos=True)")
    call("general", "hi")
    taxes, solos, walls = [], [], []
    uncontrolled = False  # any call whose length was NOT pinned to MAX_TOK
    for i, p in enumerate(PROMPTS):
        try:
            solo = solo_pass(p)
            wall = parallel_pass(p)
        except Exception as e:  # one bad call shouldn't sink the whole run
            print(f"[{i+1}/{len(PROMPTS)}] SKIPPED — {type(e).__name__}: {e}")
            continue
        max_solo = max(v[0] for v in solo.values())
        tax = (wall - max_solo) / max_solo
        taxes.append(tax); solos.append(max_solo); walls.append(wall)
        lat = {m: solo[m][0] for m in MODELS}
        tok = {m: solo[m][1] for m in MODELS}
        # The whole method depends on ignore_eos surviving the LiteLLM gateway
        # (which runs drop_params: true — ignore_eos is a vLLM-only param). If it
        # gets dropped, calls stop at natural EOS, lengths vary, and the "control"
        # silently fails back to the noisy number this tool exists to avoid. Guard.
        if any(t != MAX_TOK for t in tok.values()):
            uncontrolled = True
        print(f"[{i+1}/{len(PROMPTS)}] solo={lat} toks={tok} "
              f"max_solo={max_solo:.2f} wall={wall:.2f} tax={tax*100:+.0f}%")
    n = len(taxes)
    if not n:
        sys.exit("no successful queries — check the gateway is reachable")
    mean = lambda xs: sum(xs) / len(xs)
    print("\n=== CONTROLLED MEAN over %d queries ===" % n)
    print(f"  max_solo   {mean(solos):.2f}s")
    print(f"  parallel   {mean(walls):.2f}s")
    print(f"  TAX        {mean(taxes)*100:+.0f}%   (median {_median(taxes)*100:+.0f}%)")
    verdict = "NOT justified" if mean(taxes) < 0.5 else "JUSTIFIED"
    print(f"  verdict: multi-GPU {verdict}")
    if uncontrolled:
        print("\n!! WARNING: some calls did not generate exactly "
              f"{MAX_TOK} tokens — ignore_eos was NOT honored (LiteLLM likely "
              "dropped it). Output length is uncontrolled, so this TAX is "
              "confounded by generation length. DISTRUST the number.")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:  # offline: verify _median, no gateway needed
        assert _median([1, 2, 3]) == 2
        assert _median([1, 2, 3, 4]) == 2.5  # even n = mean of the two middles
        print("ok — _median verified")
    else:
        main()
