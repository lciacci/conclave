#!/usr/bin/env python3
"""v3 live harness — smoke + contention baseline against a running fleet.

Gateway comes from argv[1] or $CONCLAVE_GW (the Tailscale IP changes every boot):
    CONCLAVE_GW=100.x.y.z:4000 python3 orchestrator/harness.py
    python3 orchestrator/harness.py 100.x.y.z:4000 --baseline

Modes:
    (default)    one ensemble query end-to-end — the Chunk 1 smoke check
    --baseline   Chunk 2 contention baseline: seq fan-out wall vs slowest model
                 (= ideal-parallel lower bound); the gap is the co-residency tax.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ensemble import EnsembleConfig, ensemble, fan_out, fan_out_parallel


def _gateway() -> str:
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            return a if a.startswith("http") else f"http://{a}"
    gw = os.environ.get("CONCLAVE_GW")
    if not gw:
        sys.exit("gateway required: argv[1] or $CONCLAVE_GW (e.g. 100.x.y.z:4000)")
    return gw if gw.startswith("http") else f"http://{gw}"


def smoke(cfg: EnsembleConfig) -> None:
    q = ("Write a Python one-liner to return the second-largest unique value in "
         "a list. Explain in one sentence.")
    res = ensemble(q, cfg)
    j = res["metadata"]["judge"]
    print("=== JUDGED ANSWER (judge=%s) ===" % j["model"])
    print(res["choices"][0]["message"]["content"][:600])
    print("\nchosen:", j["chosen"], "| rationale:", j["rationale"],
          "| judge_latency_s:", j["latency_s"])
    print("\n=== CANDIDATES ===")
    for c in res["metadata"]["candidates"]:
        tag = "ERR: " + str(c["error"]) if c["error"] else c["content"][:70].replace("\n", " ")
        print("%-9s %6.2fs  %s" % (c["model"], c["latency_s"], tag))
    print("\nwall_s:", res["metadata"]["wall_s"])


def baseline(cfg: EnsembleConfig) -> None:
    """Co-residency contention baseline.

    For each query: a SOLO pass (one model at a time -> max_solo = the
    ideal-parallel lower bound) then a PARALLEL pass (all models at once ->
    real wall). contention_tax = (parallel_wall - max_solo) / max_solo.

    ~0%   -> the L40S timeshares fine; Chunk 4 multi-GPU is NOT justified.
    ~+100% -> co-residents effectively serialize; multi-GPU IS justified.
    """
    queries = [
        "Explain what a race condition is in two sentences.",
        "Write a Python function to check if a string is a palindrome.",
        "What is the time complexity of binary search and why?",
        "Summarize the CAP theorem in three bullet points.",
    ]
    print("warmup...")
    fan_out("Say OK.", cfg)  # first call pays cold caches; keep it out of the numbers

    maxes, walls, seqs = [], [], []
    for q in queries:
        solo_res = fan_out(q, cfg)
        par_res, wall = fan_out_parallel(q, cfg)
        # An errored call returns fast, which would understate the tax — name it.
        errs = [c["model"] for c in solo_res + par_res if c["error"]]
        solo = {c["model"]: c["latency_s"] for c in solo_res}
        seq, max_solo = sum(solo.values()), max(solo.values())
        seqs.append(seq)
        maxes.append(max_solo)
        walls.append(wall)
        print("solo=%s  max_solo=%5.2fs  parallel_wall=%5.2fs  tax=%+d%%%s"
              % ({k: round(v, 1) for k, v in solo.items()}, max_solo, wall,
                 round((wall - max_solo) / max_solo * 100),
                 "  ERRORS: " + ",".join(errs) if errs else ""))

    n = len(queries)
    ms, mm, mw = sum(seqs) / n, sum(maxes) / n, sum(walls) / n
    tax = round((mw - mm) / mm * 100)
    print("\nMEAN over %d queries:" % n)
    print("  sequential wall   %.2fs   (fan-out one at a time)" % ms)
    print("  max solo latency  %.2fs   (ideal-parallel lower bound)" % mm)
    print("  parallel wall     %.2fs   (all %d concurrent)" % (mw, len(cfg.candidates)))
    print("  CONTENTION TAX   %+d%%   over ideal parallel" % tax)
    print("  speedup vs seq    %.2fx" % (ms / mw))
    print("\nverdict: multi-GPU %s justified by this run"
          % ("IS" if tax > 50 else "is NOT"))
    print("NB: a valid run needs a healthy box — boot with dev_mode=true or "
          "idle-stop may reap mid-run (uniform ~75s latencies = dying box).")


if __name__ == "__main__":
    cfg = EnsembleConfig(gateway_url=_gateway())
    (baseline if "--baseline" in sys.argv else smoke)(cfg)
