#!/usr/bin/env python3
"""v3 Chunk 3 — judge eval. Does the in-fleet Gemma judge hold up against a
frontier judge at selecting/synthesizing across the three specialists' answers?

Pipeline (three phases, decoupled by on-disk JSON so the GPU box is up for the
minimum time):

  1. GENERATE  [needs the fleet up]  --generate
     Fan every query out to the specialists (candidate_cache) AND run the in-fleet
     Gemma judge over them. Both need the GPU. Cache candidates + gemma judgments.
  2. FRONTIER  [offline, API only]   --frontier
     Run the frontier judge over the SAME cached candidates. No GPU.
  3. SCORE     [offline]             --score
     Score both judges' final answers and aggregate. No GPU.

DESIGN (see docs/HANDOFF.md, Chunk 3):
  - Scorer is a pluggable protocol. Two impls ship: LocalHeuristic (deterministic,
    offline, for CI/smoke) and ReferenceGrader (an LLM grades each answer 0-5 vs
    the gold reference). Reference-anchored grading self-biases far less than open
    pairwise, so a Claude grader scoring a Claude-judged answer is acceptable here.
  - Judge + grader are provider-agnostic: an OpenAI-compatible base_url + model +
    key (ensemble.http_call, keyed via functools.partial). Points at the local
    gateway, OpenAI, or Anthropic's OpenAI-compatible endpoint unchanged.

  RIGOR UPGRADE PATH (deliberately NOT built this chunk — demoable first):
    * PairwiseScorer: grader sees BOTH final answers and picks the better. It MUST
      blind them (strip model labels) and RANDOMIZE position (A/B order) per query,
      else position bias corrupts the result. Add it as a third Scorer impl.
    * Independent grader: point the grader at a DIFFERENT vendor than the frontier
      judge (e.g. GPT grades a Claude-vs-Gemma comparison) to kill self-bias.
    * N grader samples per item + variance/significance; expand the query set.
"""
from __future__ import annotations

import functools
import hashlib
import ipaddress
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse

# Retry policy for frontier/grader calls. 429 = rate limit (Gemini free tier hands
# these out freely), 5xx = vendor hiccup. Both clear on their own; a bracket run makes
# hundreds of calls, so not retrying means a run that reliably dies partway.
_TRANSIENT = {429, 500, 502, 503, 504, 529}
_RETRIES = 8
_BACKOFF = 2.0       # seconds, doubles per attempt
_BACKOFF_CAP = 60.0  # ...but a sustained rate limit needs patience, not a stampede


class _Throttle:
    """Minimum interval between calls to one endpoint.

    Retry-with-backoff is the wrong shape for a REQUESTS-PER-MINUTE cap. Bursting into
    the limit and then backing off means most calls are spent getting refused: the
    Gemini free tier let ~6 grades through per pass before 429ing, and the run crawled.
    Pacing under the cap instead lets it run continuously — slower per call, far faster
    per run. Set GRADER_MIN_INTERVAL (seconds) to the reciprocal of your RPM."""

    def __init__(self, min_interval: float = 0.0):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        gap = self.min_interval - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ensemble import EnsembleConfig, http_call, run_judge
from eval_queryset import QUERY_SET
import candidate_cache

QUERY_BY_ID = {q["id"]: q for q in QUERY_SET}


def frontier_call(base_url, model, messages, timeout, response_format=None, api_key="none",
                  temperature=0):
    """Frontier-safe wrapper around http_call, normalizing two vendor quirks.

    1. response_format. Anthropic's OpenAI-compatible endpoint REJECTS
       response_format=json_object (it wants json_schema), so we drop it and lean on
       the prompt's 'respond ONLY with JSON' + lenient parsing — verified: sonnet and
       gemini both return clean parseable JSON this way. vLLM's local judge keeps
       json_object via the default http_call; only frontier calls route through here.

    2. temperature. Defaults to 0 (deterministic judge => reproducible scores) and is
       a passthrough, because the N-sample grader raises it deliberately to explore
       the grader's uncertainty rather than re-decode one frozen answer.
       BUT some models now refuse the parameter outright — claude-sonnet-5 returns
       400 "`temperature` is deprecated for this model". That broke the --frontier
       phase entirely (the frozen Chunk 3 run predates the deprecation). So: send it,
       and if the vendor rejects it, retry without. On such a model the N samples
       still measure the endpoint's own run-to-run nondeterminism — which IS the
       wobble the single-sample caveat was about (0.911 -> 0.889 across two runs).

    3. Transience. A bracket run is HUNDREDS of sequential calls (n queries x 2 judges
       x N samples x 2 graders), so a per-call failure rate that looks negligible is a
       near-certain run-killer: an Anthropic 503 killed a full run 4:41 in, losing all
       of it. Retry the transient codes with exponential backoff. This also absorbs
       Gemini's free-tier 429s, which are rate limits, not refusals — the API even
       tells us to come back in ~7s."""
    temp = temperature
    for attempt in range(_RETRIES):
        try:
            _throttle_for(base_url).wait()   # stay UNDER an RPM cap instead of bouncing off it
            return http_call(base_url, model, messages, timeout, response_format=None,
                             api_key=api_key, temperature=temp)
        except urllib.error.HTTPError as e:
            # A model that refuses `temperature` outright (claude-sonnet-5) — drop it
            # and retry immediately. Permanent, so it does not consume the backoff.
            if e.code == 400 and temp is not None:
                body = e.read().decode("utf-8", "replace")
                if "temperature" in body.lower():
                    # Permanent, not transient: retry immediately WITHOUT consuming an
                    # attempt. Using `continue` here would burn one, and on the final
                    # attempt would fall out of the loop into the "unreachable" raise.
                    temp = None
                    return frontier_call(base_url, model, messages, timeout,
                                         response_format, api_key, temperature=None)
                raise
            if e.code not in _TRANSIENT or attempt == _RETRIES - 1:
                raise
            # The vendor usually TELLS us how long to wait (Gemini: "retry in 6.8s").
            # Guessing a backoff when the answer is in the header is how a 429 run
            # burns its retries too fast and dies anyway.
            wait = _retry_after(e) or min(_BACKOFF * 2 ** attempt, _BACKOFF_CAP)
        except urllib.error.URLError:  # DNS/connection reset — also transient
            if attempt == _RETRIES - 1:
                raise
            wait = min(_BACKOFF * 2 ** attempt, _BACKOFF_CAP)
        time.sleep(wait)
    raise RuntimeError("unreachable")


_THROTTLES: dict[str, _Throttle] = {}


def _throttle_for(base_url: str) -> _Throttle:
    """One throttle per endpoint — a slow free-tier grader must not pace a fast judge."""
    h = _host(base_url)
    if h not in _THROTTLES:
        env = "GRADER_MIN_INTERVAL" if "googleapis" in h else "JUDGE_MIN_INTERVAL"
        _THROTTLES[h] = _Throttle(float(os.environ.get(env, "0")))
    return _THROTTLES[h]


def _retry_after(e: urllib.error.HTTPError) -> float | None:
    """Seconds the vendor asked us to wait, if it said. Header first (Anthropic/OpenAI
    send Retry-After); else Gemini's "Please retry in 6.844163053s" in the body."""
    hdr = e.headers.get("Retry-After") if e.headers else None
    if hdr:
        try:
            return min(float(hdr), _BACKOFF_CAP)
        except ValueError:
            pass
    try:
        m = re.search(r"retry in ([\d.]+)s", e.read().decode("utf-8", "replace"), re.I)
    except Exception:
        return None
    return min(float(m.group(1)) + 1.0, _BACKOFF_CAP) if m else None
_HERE = os.path.dirname(os.path.abspath(__file__))
GEMMA_JUDGMENTS = os.path.join(_HERE, "eval_judgments_gemma.json")
FRONTIER_JUDGMENTS = os.path.join(_HERE, "eval_judgments_frontier.json")

_STOP = {"the", "a", "an", "is", "are", "to", "of", "and", "or", "in", "on", "for",
         "with", "it", "its", "as", "by", "be", "not", "no", "one", "so", "if",
         "you", "your", "that", "this", "at", "from", "how", "what", "use", "using"}


# --------------------------------------------------------------------------- #
# Scorers — pluggable. Each exposes .name and .score(query, answer) -> [0,1].
# --------------------------------------------------------------------------- #
def _as_text(answer) -> str:
    """A judge answer is not always a str — a structured reply ({"Subject":..,"Body":..})
    arrives as a dict. Coerce at the boundary: otherwise --heuristic dies on .lower() and
    a grader silently receives a Python dict repr (which the published run did)."""
    if answer is None:
        return ""
    if isinstance(answer, str):
        return answer
    return json.dumps(answer, ensure_ascii=False) if isinstance(answer, (dict, list)) else str(answer)


def _terms(text: str) -> set[str]:
    toks = re.findall(r"[a-z0-9_]+", _as_text(text).lower())
    return {t for t in toks if len(t) > 2 and t not in _STOP}


def _extract_score(raw: str) -> float | None:
    """Pull a 0-5 score from a grader reply. Prefers clean JSON, but degrades to
    regex — some OpenAI-compatible endpoints (e.g. Anthropic's compat layer) don't
    honor response_format=json_object and return prose or fenced JSON. Without this
    every grade would silently collapse to 0.0."""
    try:
        v = json.loads(raw).get("score")
        if v is not None:
            return float(v)
    except (ValueError, TypeError, AttributeError):
        pass
    m = re.search(r'"?score"?\s*[:=]\s*([0-5](?:\.\d+)?)', raw, re.I)  # "score": 4
    if not m:
        m = re.search(r'\b([0-5](?:\.\d+)?)\s*/\s*5\b', raw)            # 4/5
    if not m:  # a number near a scoring word — avoids grabbing a stray digit from prose
        m = re.search(r'(?:score|rate|rating|give|grade)\D{0,15}([0-5](?:\.\d+)?)', raw, re.I)
    if not m and re.fullmatch(r'\s*[0-5](?:\.\d+)?\s*', raw):           # the whole reply is a number
        m = re.search(r'([0-5](?:\.\d+)?)', raw)
    return float(m.group(1)) if m else None


def _extract_winner(raw: str) -> str | None:
    """Pull "A" / "B" / "TIE" from a pairwise grader reply. Same leniency story as
    _extract_score — the compat endpoints ignore json_object and may fence or
    prose-wrap. Returns None for TIE *and* for unparseable, which score_all treats
    identically (split the point): an unreadable verdict is not evidence for either
    side, so refusing to guess is the conservative failure."""
    try:
        w = json.loads(raw).get("winner")
        if isinstance(w, str) and w.strip().upper() in ("A", "B", "TIE"):
            return {"TIE": None}.get(w.strip().upper(), w.strip().upper())
    except (ValueError, TypeError, AttributeError):
        pass
    m = re.search(r'"?winner"?\s*[:=]\s*"?\s*(A|B|TIE)\b', raw, re.I)
    if not m:  # "Answer A is better", "I choose B"
        m = re.search(r'\b(?:answer|choose|pick|prefer|winner\s+is)\W{0,10}\b(A|B)\b', raw, re.I)
    if not m:
        return None
    w = m.group(1).upper()
    return None if w == "TIE" else w


class LocalHeuristicScorer:
    """Fraction of the reference's key terms present in the answer. Deterministic,
    offline, zero deps — a CI/smoke backstop, NOT the thesis number: it is blind to
    correctness and synthesis and rewards keyword overlap. Use it to prove the
    pipeline runs; use ReferenceGrader for the real comparison."""

    name = "local_heuristic"

    def score(self, query: dict, answer: str | None) -> float:
        if not answer:
            return 0.0
        ref = _terms(query["reference"])
        if not ref:
            return 0.0
        return round(len(ref & _terms(answer)) / len(ref), 3)


class GradeCache:
    """Disk-backed memo of grader verdicts. Same contract as candidate_cache: persist
    incrementally, skip what's already there, resume cheaply after a crash.

    It earns its keep twice:
      - A bracket run is hundreds of calls against rate-limited keys and takes tens of
        minutes. Runs died at 4:41 and 5:26 and threw away everything they had done.
        With this, a re-run resumes instead of restarting.
      - Re-scoring — a new report format, an added judge, a tweaked aggregate — costs
        ZERO API calls, which is the same "iterate with the expensive thing off"
        principle candidate_cache applies to the GPU.

    The key includes the answer TEXT, not just the query id: if a judgment changes, its
    stale grade must not be silently reused."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(_HERE, "eval_grade_cache.json")
        self.hits = self.misses = 0
        src = self.path
        if not os.path.exists(src):  # fresh clone: seed from the committed fixture
            fx = os.path.join(_HERE, "eval_fixtures", os.path.basename(self.path))
            if os.path.exists(fx):
                src = fx
        try:
            with open(src) as f:
                self._d = json.load(f)
        except (FileNotFoundError, ValueError):
            self._d = {}

    @staticmethod
    def key(*parts) -> str:
        return hashlib.sha256("\x00".join(str(p) for p in parts).encode()).hexdigest()[:32]

    def get(self, k: str):
        v = self._d.get(k)
        if v is None:
            self.misses += 1
        else:
            self.hits += 1
        return v

    def put(self, k: str, value) -> None:
        self._d[k] = value
        with open(self.path, "w") as f:  # incremental: a crash keeps everything prior
            json.dump(self._d, f)

    def call_cached(self, k: str, fn):
        """A None from `fn` means "no verdict" — NOT a verdict of zero. It is never
        cached, so a retry can get a real answer instead of a frozen failure. (A cached
        0.0 is a legitimate hit and is returned as one: `is None`, never truthiness.)"""
        v = self.get(k)
        if v is not None:
            return v
        v = fn()
        if v is not None:
            self.put(k, v)
        return v


class ReferenceGrader:
    """An LLM grades each answer 0-5 against the gold reference, normalized to
    [0,1]. Anchored to a fixed reference (not open A/B preference), so grader
    self-bias is limited. Provider-agnostic via a keyed OpenAI-compatible call.

    RIGOR PASS — N samples + variance. The frozen Chunk 3 run took ONE grader sample
    per answer, and a re-run moved Gemma 0.911 -> 0.889: a ~0.02 wobble with no error
    bar on it, so we could not say whether a gap that size was real. `samples=N` now
    grades each answer N times and reports mean + stdev, and evaluate() propagates the
    stdev into the report so every aggregate carries an uncertainty.

    temperature matters here. At temperature=0 repeated samples mostly collapse to the
    same string, and the resulting stdev of ~0 UNDERSTATES the real grader uncertainty
    (the 0.911->0.889 wobble happened across runs anyway). So when samples > 1 we grade
    at a small non-zero temperature by default, which probes the grader's actual
    sensitivity rather than re-measuring one frozen decode. Mean-of-N is also a
    lower-variance estimate than the single sample the frozen run used."""

    name = "reference_grader"

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 60.0,
                 call=http_call, samples: int = 1, temperature: float | None = None,
                 cache: "GradeCache | None" = None):
        self.base_url, self.model, self.timeout = base_url, model, timeout
        self.samples = max(1, samples)
        # 1 sample => temperature 0, reproducible. N samples => probe the spread.
        temp = temperature if temperature is not None else (0 if self.samples == 1 else 0.3)
        self.temperature = temp
        self.cache = cache
        self._call = functools.partial(call, api_key=api_key, temperature=temp)

    def _grade_once(self, query: dict, answer: str, i: int = 0) -> float | None:
        """None = the grader gave NO USABLE VERDICT (empty reply, refusal, max-tokens
        cutoff, content filter). That is NOT a score of 0.0. Returning 0.0 here made an
        unreadable reply indistinguishable from "the answer was wrong", froze it in the
        cache so a re-run never retried it, and moved an aggregate by ~1/36 = 0.028 —
        roughly 3x the published +/-0.010 error bar. The judge path already refuses to
        cache a failure (judge_over_cache); the grader path must not manufacture one."""
        def go() -> float | None:
            msg = [{"role": "user", "content": (
                "You are grading one answer to a question against a reference answer. "
                "Score 0-5 how correct and complete it is (0=wrong/empty, 5=fully "
                "correct and complete). Judge substance, not style.\n\n"
                f"Question:\n{query['prompt']}\n\nReference answer:\n{query['reference']}"
                f"\n\nAnswer to grade:\n{_as_text(answer)}\n\n"
                'Respond ONLY with JSON: {"score": <0-5>, "reason": "<one sentence>"}')}]
            raw = self._call(self.base_url, self.model, msg, self.timeout,
                             response_format={"type": "json_object"})
            s = _extract_score(raw)
            return None if s is None else max(0.0, min(5.0, s)) / 5.0

        if self.cache is None:
            return go()
        # Everything that can change the grade is in the key:
        #  - base_url: --bracket shares ONE cache across two graders. Two vendors can
        #    expose the same model string, and without the URL their grades collide.
        #  - prompt + reference: edit a query or a gold answer in eval_queryset.py while
        #    keeping its id and every stale grade would be silently reused. The id is not
        #    the query — and BOTH the prompt and the reference go into the grading message.
        #  - sample index i: N samples of one answer must be N DISTINCT entries, or the
        #    variance we went to the trouble of measuring memoizes away to a fake 0.0.
        k = GradeCache.key("ref", self.base_url, self.model, self.temperature,
                           query["id"], query["prompt"], query["reference"], answer, i)
        return self.cache.call_cached(k, go)

    def score_detail(self, query: dict, answer: str | None) -> dict:
        """mean + stdev + samples, plus `ungraded` — samples the grader gave no usable
        verdict on. Those are DROPPED, never counted as 0.0. If every sample is ungraded
        the item has no score at all (mean=None) and evaluate() skips it rather than
        inventing one."""
        if not answer:
            return {"mean": 0.0, "stdev": 0.0, "samples": [], "ungraded": 0}
        raw = [self._grade_once(query, answer, i) for i in range(self.samples)]
        xs = [x for x in raw if x is not None]
        ungraded = len(raw) - len(xs)
        if not xs:
            return {"mean": None, "stdev": 0.0, "samples": [], "ungraded": ungraded}
        return {"mean": round(statistics.fmean(xs), 3),
                "stdev": round(statistics.stdev(xs), 3) if len(xs) > 1 else 0.0,
                "samples": [round(x, 3) for x in xs], "ungraded": ungraded}

    def score(self, query: dict, answer: str | None) -> float:
        return self.score_detail(query, answer)["mean"]


class PairwiseScorer:
    """RIGOR PASS — blinded, position-debiased head-to-head.

    Reference grading asks "how good is this answer, alone?". Pairwise asks the
    question the thesis actually cares about: "shown both, which is better?" It is
    the more sensitive instrument — but it is also the format most vulnerable to two
    biases, and both must be controlled or the number is worthless:

      1. SELF-BIAS. A grader shown its OWN vendor's output prefers it. This scorer is
         therefore only meaningful with a grader from a THIRD vendor, independent of
         both judges being compared. `--score --pairwise` refuses to run against a
         grader that shares a host with the frontier judge (see _assert_independent).
      2. POSITION BIAS. LLM graders systematically favor whichever answer is shown
         first. Randomizing the order per query only cancels this IN EXPECTATION and
         leaves it in the variance. We do the stronger thing: grade BOTH orders (A/B
         and B/A) and average. A judge that wins under both orders really won. If the
         grader flips with the order, that query scores 0.5/0.5 and is counted in
         `diagnostics["position_flips"]` — which turns position bias from an unmeasured
         confound into a reported number.

    Answers are BLINDED: the grader never sees the judge names ("gemma"/"frontier"),
    only "Answer A"/"Answer B"."""

    name = "pairwise"

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 60.0,
                 call=http_call, temperature: float = 0, cache: "GradeCache | None" = None):
        self.base_url, self.model, self.timeout = base_url, model, timeout
        self.cache = cache
        self._call = functools.partial(call, api_key=api_key, temperature=temperature)
        self.diagnostics: dict = {"position_flips": [], "n_compared": 0}

    def _ask(self, query: dict, first: str, second: str) -> str | None:
        def go() -> str:
            msg = [{"role": "user", "content": (
                "Two assistants answered the same question. Decide which answer is better "
                "on correctness and completeness — judge substance, not style or length. "
                "A reference answer is given to anchor correctness.\n\n"
                f"Question:\n{query['prompt']}\n\nReference answer:\n{query['reference']}\n\n"
                f"[Answer A]\n{_as_text(first)}\n\n[Answer B]\n{_as_text(second)}\n\n"
                'Respond ONLY with JSON: {"winner": "A" | "B" | "TIE", '
                '"reason": "<one sentence>"}')}]
            return self._call(self.base_url, self.model, msg, self.timeout,
                              response_format={"type": "json_object"})

        # Cache the RAW reply, not the parsed winner: None is a legitimate verdict
        # (tie/unparseable) and would be indistinguishable from a cache miss.
        if self.cache is None:
            return _extract_winner(go())
        # base_url + reference in the key, same reasoning as ReferenceGrader: one cache
        # is shared across graders, and the reference is part of the question asked.
        k = GradeCache.key("pair", self.base_url, self.model, query["id"],
                           query["reference"], first, second)
        return _extract_winner(self.cache.call_cached(k, go))

    def score_all(self, query: dict, answers: dict[str, str | None]) -> dict[str, float]:
        """answers = {judge_name: final_answer}. Exactly 2 judges."""
        names = list(answers)
        if len(names) != 2:
            raise ValueError(f"pairwise needs exactly 2 judges, got {names}")
        a, b = names
        ta, tb = answers[a], answers[b]
        if not ta and not tb:
            return {a: 0.0, b: 0.0}
        if not ta:  # one side failed to answer — the other wins by default
            return {a: 0.0, b: 1.0}
        if not tb:
            return {a: 1.0, b: 0.0}

        # Both orders. w1 is the winner with `a` shown first; w2 with `b` shown first.
        w1 = self._ask(query, ta, tb)          # A=a, B=b
        w2 = self._ask(query, tb, ta)          # A=b, B=a  (positions swapped)
        # Map each verdict from A/B-position back to a judge name.
        v1 = {"A": a, "B": b}.get(w1)          # None => TIE or unparseable
        v2 = {"A": b, "B": a}.get(w2)

        self.diagnostics["n_compared"] += 1
        if v1 and v2 and v1 != v2:             # order flipped the verdict => position bias
            self.diagnostics["position_flips"].append(query["id"])

        pts = {a: 0.0, b: 0.0}
        for v in (v1, v2):
            if v is None:                      # tie / unparsed: split the point
                pts[a] += 0.5
                pts[b] += 0.5
            else:
                pts[v] += 1.0
        return {a: round(pts[a] / 2, 3), b: round(pts[b] / 2, 3)}

    def score(self, query: dict, answer: str | None) -> float:
        raise NotImplementedError("pairwise scores a pair — evaluate() uses score_all")


# --------------------------------------------------------------------------- #
# Judging over cached candidates (any judge config)
# --------------------------------------------------------------------------- #
def _candidates_sha(cands: list[dict]) -> str:
    """Fingerprint of the answers a judgment was made over. A judgment is only valid for
    the candidate set it saw; this is what lets carry-over detect that the answers moved
    underneath it."""
    body = "\x00".join(f"{c.get('model')}\x01{c.get('content')}" for c in cands)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


# Model-name -> vendor, for artifacts written before judge_url was stamped into rows.
# Coarse on purpose: it only has to be right about WHICH HOUSE, not which model.
_MODEL_VENDOR_HINTS = (("claude", "anthropic"), ("gpt", "openai"), ("o1", "openai"),
                       ("gemini", "google"), ("gemma", "google"))


def _judge_identity(judgments: dict[str, dict], fallback_url: str) -> tuple[str, str]:
    """(url, vendor) of the judge that ACTUALLY produced these judgments.

    The bias guard used to ask the ENVIRONMENT who the frontier judge was — JUDGE_URL,
    defaulting to OpenAI — while the frozen judgments were made by claude-sonnet-5, a
    fact recorded in the file and read by nobody. So simply leaving JUDGE_URL unset made
    a Sonnet grader grading Sonnet's own judge get stamped [INDEPENDENT], in the printed
    report AND in the saved JSON. The one guard the published number rests on was keyed
    off a variable with no causal link to the artifact. Identity now comes from the
    artifact: the stamped judge_url, else inferred from the recorded model name."""
    urls = {j["judge_url"] for j in judgments.values() if j.get("judge_url")}
    if len(urls) == 1:
        u = urls.pop()
        return u, _vendor(u)
    models = {j.get("model", "").lower() for j in judgments.values()}
    for m in models:
        for hint, vendor in _MODEL_VENDOR_HINTS:
            if hint in m:
                return fallback_url, vendor
    return fallback_url, _vendor(fallback_url)


def judge_over_cache(cache: dict[str, list[dict]], judge_cfg: EnsembleConfig,
                     call=http_call, prior: dict[str, dict] | None = None,
                     refresh: bool = False) -> dict[str, dict]:
    """Run one judge over every cached query's candidates. Returns
    {query_id: {answer, chosen, rationale, model}}. Skips ids not in QUERY_BY_ID.

    INCREMENTAL by default: a query already judged in `prior` is carried over untouched.
    This is not just a cost saving. Judges are nondeterministic, so re-judging the
    original 18 when the query set grew to 36 would silently REWRITE the frozen result
    we are trying to extend — the n=36 numbers would no longer be a superset of the
    published n=18 ones, and every cached grade for them would be invalidated. Growing
    the query set must ADD rows, not disturb existing ones. Pass refresh=True to
    deliberately re-judge everything.

    Two ways a carried-over row would POISON the result, both guarded here:
      - A row judged by a DIFFERENT model. Swap JUDGE_MODEL and the prior file still
        holds the old model's rows; they would be carried over silently and the report
        labelled with the new model only — a file of mixed judges reported as one.
        Prior rows whose `model` disagrees are dropped and re-judged.
      - A row where the judge actually FAILED. run_judge degrades to a raw specialist
        answer on any exception (sane for serving — never sink the ensemble — but a
        catastrophe for an eval: a stale key 401s, every "judge" row becomes the coder's
        raw answer, and we would grade and PUBLISH that as the judge's output). Failed
        rows are NOT cached, so they are retried instead of frozen in."""
    mismatched = [qid for qid, j in (prior or {}).items()
                  if j.get("model") != judge_cfg.judge_model]
    if mismatched and not refresh:
        # REFUSE, do not "announce and proceed". Silently re-judging these would REWRITE
        # a published artifact and spend real money — and the way you get here is by
        # simply forgetting to set JUDGE_MODEL, since _frontier_from_env() defaults to
        # gpt-5.2 while the frozen run is claude-sonnet-5. A destructive default must be
        # a refusal, not a warning (this repo's gate convention).
        was = sorted({(prior or {})[q].get("model") for q in mismatched})
        sys.exit(
            f"REFUSING to re-judge: {len(mismatched)} existing judgment(s) were made by "
            f"{was}, but the configured judge is '{judge_cfg.judge_model}'.\n"
            f"Carrying them over would mix two judges in one file; re-judging them would "
            f"overwrite a frozen result and cost real API calls.\n"
            f"  - Meant to keep the existing run? Set JUDGE_MODEL={was[0]!r}.\n"
            f"  - Meant to re-judge everything with '{judge_cfg.judge_model}'? "
            f"Re-run with --refresh.")
    # A judgment's real input is the CANDIDATES, not the query id. Carry-over keyed on id
    # alone lets a judgment survive a complete replacement of the answers it judged —
    # regenerate eval_candidates.json (fleet change, deleted cache) and every existing
    # judgment is stale, silently reported as if fresh. GradeCache already keys on the
    # answer TEXT for exactly this reason; the judgment memo never got the same treatment.
    # Refuse rather than silently re-judge: a changed fleet is something you must know.
    stale = [qid for qid, j in (prior or {}).items()
             if qid in cache and j.get("candidates_sha")
             and j["candidates_sha"] != _candidates_sha(cache[qid])]
    if stale and not refresh:
        sys.exit(
            f"REFUSING to score against stale judgments: the candidates for {len(stale)} "
            f"query(s) have CHANGED since they were judged (e.g. {stale[:3]}).\n"
            f"Those judgments were made over different answers and no longer describe "
            f"this candidate set.\n"
            f"  - Re-judge them against the new candidates: re-run with --refresh.\n"
            f"  - Or restore the candidate set they were judged over.")

    keep = {} if refresh else dict(prior or {})
    out: dict[str, dict] = dict(keep)
    failed = 0
    for qid, cands in cache.items():
        if qid in out:
            continue
        q = QUERY_BY_ID.get(qid)
        if not q:
            continue
        j = run_judge(q["prompt"], cands, judge_cfg, call)
        if j.get("error"):
            # Do NOT cache. run_judge's fallback answer is a raw candidate, not a
            # judgment — caching it would publish a specialist's answer as the judge's,
            # and the incremental carry-over would never retry it.
            # Branch on the STRUCTURED `error` field, never on the rationale wording:
            # a string sentinel would break silently the moment ensemble.py rewords its
            # message, quietly restoring exactly this bug.
            print(f"  JUDGE FAILED on {qid}: {j['error']} — not cached", file=sys.stderr)
            failed += 1
            continue
        out[qid] = {k: j[k] for k in ("answer", "chosen", "rationale", "model")}
        # Stamp WHO judged and WHAT they judged, into the artifact itself. The scoring
        # path must be able to answer "which vendor produced these judgments?" from the
        # file, never from an env var that has no causal link to it (see _judge_identity).
        out[qid]["judge_url"] = judge_cfg.resolved_judge_url()
        out[qid]["candidates_sha"] = _candidates_sha(cands)
        print(f"  judged {qid} ({judge_cfg.judge_model})")
    if failed:
        print(f"  WARNING: {failed} judge call(s) failed and were NOT cached — re-run to "
              f"retry them. The eval will refuse to score an incomplete judgment set.",
              file=sys.stderr)
    return out


# --------------------------------------------------------------------------- #
# Scoring + report
# --------------------------------------------------------------------------- #
def evaluate(cache: dict, judgments: dict[str, dict[str, dict]], scorer,
             eps: float = 1e-6) -> dict:
    """judgments = {judge_name: {qid: judgment}}. Scores every judge's final answer
    per query, aggregates mean overall + per-category, and (for exactly 2 judges)
    a head-to-head win/tie count. Returns a JSON-able report.

    Scorers plug in at three levels of richness, and we take the richest offered:
      .score_all(query, {judge: answer}) -> {judge: float}   — pairwise, needs both
      .score_detail(query, answer) -> {mean, stdev, samples} — N-sample, has error bars
      .score(query, answer) -> float                         — the plain case
    Aggregate stdev is the mean of the per-query stdevs: "how much does the grader
    wobble on a typical item", which is the quantity the single-sample caveat was
    about. It is NOT the stdev of the aggregate mean (that would be smaller by
    sqrt(n)) — the honest, more conservative reading of the two."""
    judges = list(judgments)
    per_query, by_cat = [], {}
    stdevs = {jn: [] for jn in judges}

    # A query MISSING from a judge's file is not a wrong answer — it is no answer, and
    # scoring it 0.0 (or forfeiting the pairwise point) would fabricate a result. This
    # is not hypothetical: it is exactly what happens on the documented growth path if
    # you expand QUERY_SET, run --generate (candidates + gemma), and score WITHOUT
    # re-running --frontier. Frontier would take a 0.0 on every new query and Gemma
    # would "win" a comparison it never actually had. Score only queries every judge
    # answered, and say loudly what was dropped.
    scorable = [qid for qid in cache
                if QUERY_BY_ID.get(qid) and all(judgments[jn].get(qid, {}).get("answer")
                                                for jn in judges)]
    skipped = [qid for qid in cache if QUERY_BY_ID.get(qid) and qid not in scorable]
    if skipped:
        missing = {jn: [q for q in skipped if not judgments[jn].get(q, {}).get("answer")]
                   for jn in judges}
        print(f"WARNING — {len(skipped)} query(s) NOT scored: absent from a judge's "
              f"judgments, so no comparison exists. Missing per judge: "
              f"{ {jn: len(v) for jn, v in missing.items()} }. "
              f"Re-run the missing judge phase (--generate / --frontier) for a full n.",
              file=sys.stderr)

    ungraded: list[str] = []
    for qid in scorable:
        q = QUERY_BY_ID[qid]
        answers = {jn: judgments[jn].get(qid, {}).get("answer") for jn in judges}

        if hasattr(scorer, "score_all"):          # pairwise: needs both answers at once
            scores = scorer.score_all(q, answers)
            detail = None
        elif hasattr(scorer, "score_detail"):     # N-sample: carries stdev
            det = {jn: scorer.score_detail(q, answers[jn]) for jn in judges}
            # mean is None when the grader gave NO usable verdict on any sample. That is
            # not a score of zero — the item is unscorable and must leave the sample, or
            # a content-filtered reply becomes a 0.0 that drags the aggregate.
            if any(det[jn]["mean"] is None for jn in judges):
                ungraded.append(qid)
                continue
            scores = {jn: det[jn]["mean"] for jn in judges}
            detail = {jn: {"stdev": det[jn]["stdev"], "samples": det[jn]["samples"],
                           "ungraded_samples": det[jn].get("ungraded", 0)}
                      for jn in judges}
            for jn in judges:
                stdevs[jn].append(det[jn]["stdev"])
        else:
            scores = {jn: scorer.score(q, answers[jn]) for jn in judges}
            detail = None

        row = {"id": qid, "category": q["category"], "scores": scores}
        if detail:
            row["detail"] = detail
        per_query.append(row)
        cat = by_cat.setdefault(q["category"], {jn: [] for jn in judges})
        for jn in judges:
            cat[jn].append(scores[jn])

    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else 0.0
    aggregate = {jn: mean([pq["scores"][jn] for pq in per_query]) for jn in judges}
    by_category = {c: {jn: mean(v[jn]) for jn in judges} for c, v in by_cat.items()}

    report = {"scorer": scorer.name, "judges": judges, "n": len(per_query),
              "aggregate": aggregate, "by_category": by_category,
              "per_query": per_query}
    if skipped:  # n shrank — that must be visible in the artifact, not just on stderr
        report["skipped_unjudged"] = sorted(skipped)
    if ungraded:
        report["skipped_ungraded"] = sorted(ungraded)
        print(f"WARNING — {len(ungraded)} query(s) NOT scored: the grader returned no usable "
              f"verdict on every sample (refusal / content filter / cutoff). n is reduced "
              f"accordingly. ids: {sorted(ungraded)}", file=sys.stderr)
    if any(stdevs[jn] for jn in judges):
        report["mean_grader_stdev"] = {jn: mean(stdevs[jn]) for jn in judges}
        report["grader_samples"] = getattr(scorer, "samples", 1)

    # THE ERROR BAR THAT ACTUALLY BOUNDS THE CLAIM.
    # mean_grader_stdev is grader REPLICATION noise: "if I ask the same grader again, how
    # much does it wobble on one item". It says nothing about sampling error across
    # QUERIES — which is what a claim like "judge A beats judge B" needs, because the 36
    # queries are a sample from a population of possible queries. Quoting the replication
    # noise as the resolution floor overstates precision by ~4x. The paired SEM over items
    # is the honest one, and it is what a reader should compare the gap against.
    if len(judges) == 2 and len(per_query) > 1:
        a, b = judges
        diffs = [pq["scores"][b] - pq["scores"][a] for pq in per_query]
        sem = statistics.stdev(diffs) / math.sqrt(len(diffs))
        gap = statistics.fmean(diffs)
        report["paired_gap"] = {
            "judges": f"{b} - {a}", "gap": round(gap, 4),
            "sd_of_diff": round(statistics.stdev(diffs), 4),
            "sem": round(sem, 4), "n": len(diffs),
            "ci95": [round(gap - 1.96 * sem, 4), round(gap + 1.96 * sem, 4)],
            "note": "SEM over queries — the error bar for the GAP. Compare the gap to "
                    "this, not to mean_grader_stdev (which is grader replication noise "
                    "and is several times smaller).",
        }
    if getattr(scorer, "diagnostics", None):
        report["diagnostics"] = scorer.diagnostics
    if len(judges) == 2:
        a, b = judges
        wins = {a: 0, b: 0, "tie": 0}
        for pq in per_query:
            da, db = pq["scores"][a], pq["scores"][b]
            wins[a if da - db > eps else b if db - da > eps else "tie"] += 1
        report["head_to_head"] = wins
    return report


def print_report(r: dict) -> None:
    print(f"\n=== JUDGE EVAL — scorer={r['scorer']}, n={r['n']} ===")
    if "grader" in r:
        g = r["grader"]
        tag = "INDEPENDENT" if g.get("independent") else "SELF-BIASED (same vendor as judge)"
        print(f"grader: {g['model']} @ {g['host']}  [{tag}]")
    sd = r.get("mean_grader_stdev")
    print("aggregate mean score:")
    for jn, s in r["aggregate"].items():
        bar = f"  +/- {sd[jn]:.3f}" if sd else ""
        print(f"  {jn:20s} {s:.3f}{bar}")
    if sd:
        print(f"  (+/- = mean per-item grader stdev over {r.get('grader_samples', 1)} samples;"
              f" a gap smaller than this is NOT resolved by this eval)")
    print("by category:")
    for c, d in r["by_category"].items():
        print(f"  {c:10s} " + "  ".join(f"{jn}={s:.3f}" for jn, s in d.items()))
    pg = r.get("paired_gap")
    if pg:
        lo, hi = pg["ci95"]
        print(f"paired gap ({pg['judges']}): {pg['gap']:.3f}  SEM {pg['sem']:.4f}  "
              f"95% CI [{lo:.3f}, {hi:.3f}]  n={pg['n']}")
        print("  ^ THIS is the error bar for the gap. mean_grader_stdev above is grader")
        print("    replication noise and is several times smaller — do not quote it as the floor.")
    if "head_to_head" in r:
        print("head-to-head (per-query wins):", r["head_to_head"])
    diag = r.get("diagnostics")
    if diag and "position_flips" in diag:
        flips, n = len(diag["position_flips"]), diag.get("n_compared", 0)
        print(f"position bias: grader flipped its verdict when the order was swapped on "
              f"{flips}/{n} queries" + (f" ({', '.join(diag['position_flips'])})" if flips else ""))


# --------------------------------------------------------------------------- #
# Offline self-check
# --------------------------------------------------------------------------- #
def demo() -> None:
    """No network: mock cache + two mock judges + both scorers, verify the
    pipeline shape, scoring bounds, and head-to-head tally."""
    qs = QUERY_SET[:4]
    cache = {q["id"]: [{"model": m, "content": f"cand {m}", "latency_s": 0.1,
                        "error": None} for m in ("coder", "reasoning", "general")]
             for q in qs}

    # Two judges: "strong" echoes the reference (high heuristic score); "weak"
    # returns a fixed off-topic string (low score). Proves the scorer discriminates.
    strong = {q["id"]: {"answer": q["reference"], "chosen": -1, "rationale": "",
                        "model": "gemma"} for q in qs}
    weak = {q["id"]: {"answer": "purple monkey dishwasher", "chosen": 0,
                      "rationale": "", "model": "frontier"} for q in qs}

    sc = LocalHeuristicScorer()
    for q in qs:
        assert sc.score(q, q["reference"]) > 0.5, "reference should self-score high"
        assert sc.score(q, "purple monkey dishwasher") < 0.2, "off-topic scores low"
        assert sc.score(q, None) == 0.0, "missing answer scores 0"

    rep = evaluate(cache, {"gemma": strong, "frontier": weak}, sc)
    assert rep["n"] == 4 and rep["judges"] == ["gemma", "frontier"]
    assert rep["aggregate"]["gemma"] > rep["aggregate"]["frontier"], "strong judge wins"
    assert rep["head_to_head"]["gemma"] == 4, "strong wins every query"
    assert set(rep["by_category"]) <= {"coder", "reasoning", "general"}
    assert all(0.0 <= s <= 1.0 for pq in rep["per_query"] for s in pq["scores"].values())

    # ReferenceGrader with a canned grader call (no network): returns a fixed JSON.
    def fake_grader(base_url, model, messages, timeout, response_format=None, api_key="none",
                    temperature=None):
        assert api_key == "testkey", "grader key threaded through"
        assert response_format == {"type": "json_object"}
        return json.dumps({"score": 4, "reason": "good"})
    rg = ReferenceGrader("http://frontier", "grader-x", "testkey", call=fake_grader)
    assert rg.score(qs[0], "anything") == round(4 / 5, 3), "grade normalized to [0,1]"
    assert rg.score(qs[0], None) == 0.0
    assert rg.temperature == 0, "single sample grades deterministically"

    # lenient score extraction — non-JSON grader replies must not collapse to 0
    assert _extract_score('{"score": 3, "reason": "ok"}') == 3.0
    assert _extract_score('The score is 4/5 because...') == 4.0
    assert _extract_score('Score: 5 — fully correct') == 5.0
    assert _extract_score('I would rate this a 2.') == 2.0
    assert _extract_score('4') == 4.0                          # whole reply is a number
    assert _extract_score('Summarized in 3 crisp points.') is None  # stray digit, no score word
    assert _extract_score('no number here') is None

    # ---------------- RIGOR PASS ----------------
    # N samples + variance. A wobbling grader must produce a non-zero stdev and a
    # mean of the samples — that is the error bar the frozen single-sample run lacked.
    wobble = iter([5, 3, 4, 4, 4] * 20)
    def noisy_grader(base_url, model, messages, timeout, response_format=None, api_key="none",
                     temperature=None):
        assert temperature and temperature > 0, "N-sample grading must not be temp 0"
        return json.dumps({"score": next(wobble), "reason": "varies"})
    ng = ReferenceGrader("http://g", "grader-x", "k", call=noisy_grader, samples=5)
    d = ng.score_detail(qs[0], "an answer")
    assert len(d["samples"]) == 5, "N samples taken"
    assert d["mean"] == round(statistics.fmean([1.0, 0.6, 0.8, 0.8, 0.8]), 3), "mean of samples"
    assert d["stdev"] > 0, "a wobbling grader reports non-zero spread"
    # and evaluate() must surface it as an error bar on the aggregate
    rep_v = evaluate(cache, {"gemma": strong, "frontier": weak}, ng)
    assert rep_v["mean_grader_stdev"]["gemma"] > 0 and rep_v["grader_samples"] == 5
    assert "detail" in rep_v["per_query"][0], "per-query samples retained"

    # frontier_call must survive a vendor that REFUSES temperature. claude-sonnet-5
    # 400s with "`temperature` is deprecated for this model" — which broke --frontier
    # outright. Send it, and on that specific rejection retry without it.
    import io
    tries = []
    def temp_hostile(base_url, model, messages, timeout, response_format=None, api_key="none",
                     temperature=None):
        tries.append(temperature)
        if temperature is not None:
            raise urllib.error.HTTPError(
                base_url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":{"message":"`temperature` is deprecated for this model."}}'))
        return "score: 5"
    # frontier_call closes over judge_eval's own `http_call` binding (imported by
    # name), so that is the global to swap — patching ensemble.http_call would do
    # nothing here.
    g = globals()
    real_http = g["http_call"]
    g["http_call"] = temp_hostile
    try:
        got = frontier_call("https://api.anthropic.com", "claude-sonnet-5",
                            [{"role": "user", "content": "x"}], 10, temperature=0.3)
        assert got == "score: 5", "retried without temperature and got the answer"
        assert tries == [0.3, None], f"sent temp, then dropped it on rejection: {tries}"

        # A 400 that is NOT about temperature must still surface, not be swallowed.
        def other_400(base_url, model, messages, timeout, response_format=None, api_key="none",
                      temperature=None):
            raise urllib.error.HTTPError(base_url, 400, "Bad Request", {},
                                         io.BytesIO(b'{"error":{"message":"bad model"}}'))
        g["http_call"] = other_400
        try:
            frontier_call("https://x", "m", [{"role": "user", "content": "x"}], 10, temperature=0)
            raise AssertionError("a non-temperature 400 must propagate")
        except urllib.error.HTTPError:
            pass

        # Transient failures must be RETRIED, not fatal — a 503 four minutes into a
        # 216-call bracket run destroyed a full run before this existed.
        calls = []
        def flaky(base_url, model, messages, timeout, response_format=None, api_key="none",
                  temperature=None):
            calls.append(1)
            if len(calls) < 3:   # 503, 429, then succeed
                code = 503 if len(calls) == 1 else 429
                raise urllib.error.HTTPError(base_url, code, "transient", {}, io.BytesIO(b"{}"))
            return "score: 4"
        g["http_call"] = flaky
        globals()["_BACKOFF"] = 0.001  # don't actually sleep a minute in a self-check
        assert frontier_call("https://x", "m", [{"role": "user", "content": "x"}], 10) == "score: 4"
        assert len(calls) == 3, f"retried through 503 then 429, got {len(calls)} calls"

        # ...but a permanent error must NOT be retried into the ground.
        def dead(base_url, model, messages, timeout, response_format=None, api_key="none",
                 temperature=None):
            raise urllib.error.HTTPError(base_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        g["http_call"] = dead
        try:
            frontier_call("https://x", "m", [{"role": "user", "content": "x"}], 10)
            raise AssertionError("401 must not be retried")
        except urllib.error.HTTPError as e:
            assert e.code == 401
    finally:
        g["http_call"] = real_http
        globals()["_BACKOFF"] = 2.0

    # judge_over_cache is INCREMENTAL: growing the query set must ADD rows, never
    # rewrite existing ones — else n=36 stops being a superset of the published n=18
    # and every cached grade for those 18 is silently invalidated.
    judged = []
    def counting_judge(base_url, model, messages, timeout, response_format=None):
        judged.append(1)
        return json.dumps({"chosen": 0, "rationale": "r", "answer": "fresh answer"})
    # model must match judge_cfg.judge_model, else the new same-model guard drops it —
    # the real gemma judgments carry model="general" (the gateway's name for Gemma).
    frozen = {qs[0]["id"]: {"answer": "ORIGINAL", "chosen": 1, "rationale": "old",
                            "model": "general"}}
    grown = judge_over_cache(cache, EnsembleConfig(judge_model="general"),
                             counting_judge, prior=frozen)
    assert grown[qs[0]["id"]]["answer"] == "ORIGINAL", "existing judgment NOT rewritten"
    assert len(judged) == len(cache) - 1, "only the un-judged queries cost a call"
    assert len(grown) == len(cache), "new queries added"
    refreshed = judge_over_cache(cache, EnsembleConfig(judge_model="general"),
                                 counting_judge, prior=frozen, refresh=True)
    assert refreshed[qs[0]["id"]]["answer"] == "fresh answer", "refresh=True re-judges"

    # ---- review round 3: four MORE bugs, three of them introduced BY the round-2 fixes ----

    # (A) _load must fall back to the fixture and return {} when neither exists — callers
    # must NOT gate on os.path.exists(live), which short-circuits the fallback and leaves
    # prior={} on a fresh clone => a full-price re-judge with every guard silently disarmed.
    assert _load(os.path.join(_HERE, "definitely-not-a-file.json")) == {}, \
        "_load returns {} when neither live nor fixture exists"

    # (B) the bias guard must take the VENDOR established from the ARTIFACT, not re-derive it
    # from a URL that may have come from the environment. Passing an env URL is exactly how a
    # Sonnet grader grading a Sonnet judge got certified INDEPENDENT.
    assert _grader_bias("anthropic", "https://api.anthropic.com") == "frontier", \
        "vendor-in, vendor-compared: a Sonnet grader is NOT independent of a Sonnet judge"
    assert _grader_bias("anthropic", "https://api.openai.com") is None
    assert _grader_bias("anthropic", "https://generativelanguage.googleapis.com/v1beta/openai") == "gemma"
    # ...and a URL still works, for convenience
    assert _grader_bias("https://api.anthropic.com", "https://api.anthropic.com") == "frontier"

    # ---- code-review regressions (PR #9). All six are "silent wrong number" bugs. ----

    # (1) A FAILED judge must not be cached. run_judge degrades to a raw specialist
    # answer on any exception — fine for serving, catastrophic for an eval: a stale key
    # 401s and every "judge" row becomes the coder's raw answer, which we would then
    # grade and publish as the judge's output. And the carry-over would never retry it.
    def dead_judge(base_url, model, messages, timeout, response_format=None):
        raise ConnectionError("401 unauthorized")
    out_failed = judge_over_cache(cache, EnsembleConfig(judge_model="general"), dead_judge)
    assert out_failed == {}, f"a failed judge must NOT be cached, got {out_failed}"

    # The guard branches on the STRUCTURED `error` field, not on rationale wording — a
    # string sentinel would break silently the moment ensemble.py rewords its message.
    # Pin the contract in both directions.
    from ensemble import run_judge as _rj
    _cands = [{"model": "coder", "content": "RAW CANDIDATE", "latency_s": 0.1, "error": None}]
    _failed = _rj("q", _cands, EnsembleConfig(), dead_judge)
    assert _failed["error"], "run_judge must flag a failed judge in `error`"
    assert _failed["answer"] == "RAW CANDIDATE", \
        "the fallback answer IS a raw candidate — which is exactly why it must not be cached"

    # ...but an UNPARSED judge reply is NOT an error: the judge did answer, just not as
    # JSON. That row is a real (degraded) judgment and MUST still be cached, or a
    # non-guided backend would score as if the judge never replied.
    def prose_judge(base_url, model, messages, timeout, response_format=None):
        return "I prefer answer 1, it is more precise."
    _prose = _rj("q", _cands, EnsembleConfig(), prose_judge)
    assert _prose["error"] is None, "an unparsed-but-real reply is not an error"
    kept = judge_over_cache(cache, EnsembleConfig(judge_model="general"), prose_judge)
    assert len(kept) == len(cache), "unparsed-but-real judgments are still cached"

    # (3) A prior row judged by a DIFFERENT model must not be silently carried over — and
    # must not be silently RE-judged either. Re-judging would overwrite a frozen result
    # and spend real money, and you reach it just by forgetting JUDGE_MODEL (whose default
    # is gpt-5.2 while the frozen run is claude-sonnet-5). It must REFUSE.
    stale = {qs[0]["id"]: {"answer": "old", "chosen": 0, "rationale": "r", "model": "gpt-5.2"}}
    try:
        judge_over_cache(cache, EnsembleConfig(judge_model="general"), counting_judge,
                         prior=stale)
        raise AssertionError("a judge-model mismatch must REFUSE, not silently re-judge")
    except SystemExit:
        pass
    # ...and --refresh is the explicit opt-in that re-judges everything.
    forced = judge_over_cache(cache, EnsembleConfig(judge_model="general"), counting_judge,
                              prior=stale, refresh=True)
    assert {j["model"] for j in forced.values()} == {"general"}, "--refresh re-judges with the new model"
    assert len(forced) == len(cache)

    # (2) A query MISSING from one judge's file must be SKIPPED, not scored 0.0. This is
    # the documented growth path: expand QUERY_SET, --generate, then score without
    # re-running --frontier. Scoring the absent judge 0.0 fabricates a Gemma "win".
    partial = {k: v for k, v in list(strong.items())[:2]}          # gemma missing 2 of 4
    rep_gap = evaluate(cache, {"gemma": partial, "frontier": weak}, sc)
    assert rep_gap["n"] == 2, f"unjudged queries must not be scored, got n={rep_gap['n']}"
    assert len(rep_gap["skipped_unjudged"]) == 2, "the skipped ids are recorded in the report"
    assert all(pq["scores"]["gemma"] > 0 for pq in rep_gap["per_query"]), \
        "a missing judgment must never appear as a 0.0 score"

    # (4) Vendor matching is SUFFIX-based, and an unknown host is UNVERIFIED, not neutral.
    assert _vendor("https://us-central1-aiplatform.googleapis.com/v1beta1/openai") == "google", \
        "Gemini via Vertex is still Google — exact-host matching missed this"
    assert _vendor("https://openrouter.ai/api/v1") == UNVERIFIED, \
        "a reseller's host cannot establish the vendor — must not read as neutral"
    _ANTH = "https://api.anthropic.com"
    assert _grader_bias(_ANTH, "https://us-central1-aiplatform.googleapis.com") == "gemma"
    assert _grader_bias(_ANTH, "https://openrouter.ai/api/v1") == UNVERIFIED
    try:
        _assert_independent(_ANTH, "https://openrouter.ai/api/v1", strict=True)
        raise AssertionError("pairwise must refuse a grader whose house is unverifiable")
    except SystemExit:
        pass

    # THE LOCAL GATEWAY IS GEMMA'S HOUSE. An earlier fix classified it as a neutral
    # "local" vendor, which made the guard certify Gemma-grading-its-own-judgments — the
    # most self-biased config that exists, and a tempting one because it is free — as
    # INDEPENDENT. Regression-guard it hard.
    for gw in ("http://100.87.121.89:4000", "http://localhost:4000", "http://127.0.0.1:4000"):
        assert _vendor(gw) == GEMMA_VENDOR, f"the local fleet serves Gemma: {gw}"
        assert _grader_bias(_ANTH, gw) == "gemma", f"an in-fleet grader is NOT neutral: {gw}"
        try:
            _assert_independent(_ANTH, gw, strict=True)
            raise AssertionError(f"pairwise must refuse an in-fleet grader: {gw}")
        except SystemExit:
            pass
    # ...but "100." is a RANGE (Tailscale CGNAT 100.64/10), not a string prefix: a real
    # public host must not be mistaken for our fleet.
    assert _vendor("https://100.datacenter-isp.net") == UNVERIFIED, \
        "a public host starting '100.' is not our Tailscale fleet"
    assert not _is_local_fleet("100.200.1.1"), "100.200.x is outside CGNAT 100.64/10"
    assert _is_local_fleet("100.87.121.89"), "100.87.x IS inside CGNAT 100.64/10"

    # (5) The cache key covers base_url and the REFERENCE, not just the query id. Edit a
    # gold answer while keeping its id and the stale grade must not be reused.
    kb = GradeCache.key("ref", "http://a", "m", 0, "qid", "REF-1", "ans", 0)
    assert kb != GradeCache.key("ref", "http://b", "m", 0, "qid", "REF-1", "ans", 0), "base_url in key"
    assert kb != GradeCache.key("ref", "http://a", "m", 0, "qid", "REF-2", "ans", 0), "reference in key"

    # (6) A temperature rejection on the FINAL attempt must not fall through to the
    # "unreachable" raise — it is permanent, so it retries without consuming an attempt.
    hostile_calls = []
    def always_temp_hostile(base_url, model, messages, timeout, response_format=None,
                            api_key="none", temperature=None):
        hostile_calls.append(temperature)
        if temperature is not None:
            raise urllib.error.HTTPError(
                base_url, 400, "Bad Request", {},
                io.BytesIO(b'{"error":{"message":"`temperature` is deprecated"}}'))
        return "score: 3"
    g["http_call"] = always_temp_hostile
    try:
        assert frontier_call("https://x", "m", [{"role": "user", "content": "x"}], 10,
                             temperature=0.3) == "score: 3"
        assert hostile_calls == [0.3, None], f"retried without temp, no attempt burned: {hostile_calls}"
    finally:
        g["http_call"] = real_http

    # GradeCache: resumability. The subtle trap is that N samples of the SAME answer
    # must stay N distinct entries — memoize on (answer) alone and the variance we
    # went to the trouble of measuring collapses to a fake 0.0.
    import tempfile as _tf
    cpath = os.path.join(_tf.mkdtemp(), "grades.json")
    live = []
    spread = iter([5, 3, 4] * 40)
    def counted(base_url, model, messages, timeout, response_format=None, api_key="none",
                temperature=None):
        live.append(1)
        return json.dumps({"score": next(spread), "reason": "x"})

    gc1 = GradeCache(cpath)
    g1 = ReferenceGrader("http://g", "grader-x", "k", call=counted, samples=3, cache=gc1)
    d1 = g1.score_detail(qs[0], "an answer")
    assert len(live) == 3, "3 samples => 3 live calls on a cold cache"
    assert d1["stdev"] > 0, "distinct samples cached separately — variance survives"

    gc2 = GradeCache(cpath)                      # reopen from disk: a resumed run
    g2 = ReferenceGrader("http://g", "grader-x", "k", call=counted, samples=3, cache=gc2)
    d2 = g2.score_detail(qs[0], "an answer")
    assert len(live) == 3, "warm cache makes ZERO new calls"
    assert d2 == d1, "resumed run reproduces the scores exactly"
    assert gc2.hits == 3 and gc2.misses == 0

    # A CHANGED answer must not reuse the old grade.
    g2.score_detail(qs[0], "a different answer")
    assert len(live) == 6, "new answer text => cache miss, regraded"

    # Pairwise winner extraction, incl. the TIE/unparseable -> None contract.
    assert _extract_winner('{"winner": "A", "reason": "clearer"}') == "A"
    assert _extract_winner('{"winner": "B"}') == "B"
    assert _extract_winner('{"winner": "TIE"}') is None, "tie is not a win for either"
    assert _extract_winner('Answer B is better because...') == "B"
    assert _extract_winner('I choose A.') == "A"
    assert _extract_winner('both are equally good') is None, "unparseable -> no evidence"

    # Pairwise BLINDS the judge names and grades BOTH orders. A grader that always
    # says "A" is pure position bias: it must NOT hand a win to either judge, and the
    # flip must be REPORTED, not silently averaged away.
    seen_prompts = []
    def position_biased(base_url, model, messages, timeout, response_format=None, api_key="none",
                        temperature=None):
        seen_prompts.append(messages[-1]["content"])
        return json.dumps({"winner": "A", "reason": "first one looks nice"})
    pw = PairwiseScorer("http://g", "grader-x", "k", call=position_biased)
    out = pw.score_all(qs[0], {"gemma": "answer one", "frontier": "answer two"})
    assert out == {"gemma": 0.5, "frontier": 0.5}, f"position bias must cancel, got {out}"
    assert pw.diagnostics["position_flips"] == [qs[0]["id"]], "the flip is reported, not hidden"
    assert len(seen_prompts) == 2, "both orders graded"
    assert all("gemma" not in p and "frontier" not in p for p in seen_prompts), \
        "judge identities are BLINDED from the grader"

    # A genuinely better answer wins under BOTH orders -> a clean 1.0 / 0.0, no flip.
    def real_preference(base_url, model, messages, timeout, response_format=None, api_key="none",
                        temperature=None):
        body = messages[-1]["content"]
        a_block = body.split("[Answer A]")[1].split("[Answer B]")[0]  # whatever sits in slot A
        return json.dumps({"winner": "A" if "GOOD" in a_block else "B", "reason": "substance"})
    pw2 = PairwiseScorer("http://g", "grader-x", "k", call=real_preference)
    out2 = pw2.score_all(qs[1], {"gemma": "GOOD answer", "frontier": "bad answer"})
    assert out2 == {"gemma": 1.0, "frontier": 0.0}, f"consistent winner sweeps, got {out2}"
    assert pw2.diagnostics["position_flips"] == [], "no flip when the preference is real"

    # A judge that produced no answer forfeits — it cannot win by the grader's silence.
    assert pw2.score_all(qs[2], {"gemma": None, "frontier": "an answer"}) == \
        {"gemma": 0.0, "frontier": 1.0}

    # Grader bias is a VENDOR property, not a hostname one. Three cases matter:
    ANTHROPIC = "https://api.anthropic.com"
    GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai"
    OPENAI = "https://api.openai.com"
    # 1. grader shares the frontier judge's house -> inflates FRONTIER (the Chunk 3 caveat)
    assert _grader_bias(ANTHROPIC, ANTHROPIC + "/v1") == "frontier"
    # 2. grader shares GEMMA's house. Gemma is a Google model, so a Gemini grader is
    #    NOT neutral — the hostname differs from Anthropic's, which is exactly why a
    #    host-equality check waves this through. This assert is the regression guard.
    assert _grader_bias(ANTHROPIC, GEMINI) == "gemma", \
        "a Google grader is NOT independent of a Google (Gemma) judge"
    # 3. a third house is neutral to both
    assert _grader_bias(ANTHROPIC, OPENAI) is None, "openai is neutral to anthropic+google"
    assert _assert_independent(ANTHROPIC, OPENAI, strict=True) is None

    for colliding in (ANTHROPIC, GEMINI):  # both collisions are fatal to bare pairwise
        try:
            _assert_independent(ANTHROPIC, colliding, strict=True)
            raise AssertionError(f"pairwise must REFUSE a grader biased toward {colliding}")
        except SystemExit:
            pass
    assert _assert_independent(ANTHROPIC, GEMINI, strict=False) == "gemma", "warns, does not die"

    print("ok — judge_eval: scorers, N-sample variance, blinded/position-debiased "
          "pairwise, grader-independence guard verified offline")


# THE DEFAULTS ARE THE FROZEN RUN'S CONFIG, deliberately.
#
# They used to default to OpenAI/gpt-5.2 while the published run was Anthropic/
# claude-sonnet-5. That made the *safe* path (replay the frozen result for $0) require
# secret knowledge of three env vars, and the *default* path silently destructive:
#   - bare `--score` with any OPENAI_API_KEY in the shell missed every cache key and made
#     ~72 live PAID calls, then printed a plausible-but-different number;
#   - bare `--frontier` would drop all 36 frozen claude-sonnet-5 judgments and re-judge
#     with gpt-5.2, overwriting a published artifact.
# A default must be the safe, reproducible thing. Override explicitly to spend.
FROZEN_JUDGE_URL = "https://api.anthropic.com"
FROZEN_JUDGE_MODEL = "claude-sonnet-5"
FROZEN_GRADER_SAMPLES = 3


def _frontier_from_env() -> tuple[str, str, str]:
    """The frontier JUDGE — the thing being compared against Gemma."""
    base = os.environ.get("JUDGE_URL", FROZEN_JUDGE_URL)
    model = os.environ.get("JUDGE_MODEL", FROZEN_JUDGE_MODEL)
    key = os.environ.get("JUDGE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") \
        or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        # Scoring a fully-cached run needs no key at all — do not demand one.
        return base.rstrip("/"), model, "none"
    return base.rstrip("/"), model, key


def _grader_from_env() -> tuple[str, str, str]:
    """The GRADER — the thing that scores both judges' answers.

    RIGOR PASS: this used to be the same endpoint as the frontier judge, which is
    the self-bias the Chunk 3 writeup flagged: Sonnet grading Sonnet's own judge
    produced a non-discriminating 1.000. Splitting GRADER_* from JUDGE_* is the fix
    — point the grader at a THIRD vendor with no stake in either judge.

    Falls back to JUDGE_* when unset, purely so the frozen n=18 run stays
    reproducible; _assert_independent() is what stops that fallback from silently
    reintroducing the bias in a rigor run."""
    base = os.environ.get("GRADER_URL") or os.environ.get("JUDGE_URL", FROZEN_JUDGE_URL)
    model = os.environ.get("GRADER_MODEL") or os.environ.get("JUDGE_MODEL", FROZEN_JUDGE_MODEL)
    key = os.environ.get("GRADER_API_KEY") or os.environ.get("JUDGE_API_KEY") \
        or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        # A fully-cached replay makes zero calls, so a missing key is not an error here.
        # If a call IS attempted it will fail loudly, which is the correct outcome.
        return base.rstrip("/"), model, "none"
    return base.rstrip("/"), model, key


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


# Which house does an endpoint belong to? Grader bias is a VENDOR/LINEAGE property,
# not a hostname one — comparing hosts alone is the trap this table exists to close.
# SUFFIX-matched, not exact: Gemini is also served from *.googleapis.com (Vertex, e.g.
# us-central1-aiplatform.googleapis.com), and an exact-match table would call that
# "independent" of Gemma and wave it straight through --pairwise. Same class of hole
# the table was written to close, one layer down.
_VENDOR_SUFFIXES = (
    (".anthropic.com", "anthropic"),
    (".openai.com", "openai"),
    (".googleapis.com", "google"),
    (".google.com", "google"),
)

# Hosts that RESELL other vendors' models (openrouter, together, ...). The vendor is a
# property of the MODEL served, not the host, so the host cannot answer the question —
# refuse to guess rather than silently return "neutral".
_RESELLERS = ("openrouter.ai", "together.xyz", "together.ai", "fireworks.ai",
              "groq.com", "replicate.com", "huggingface.co")

UNVERIFIED = "unverified"


def _vendor(url: str) -> str:
    """The house behind an endpoint, or UNVERIFIED when the host cannot settle it.

    An unknown host is NOT evidence of neutrality — a reseller can serve a Gemini model
    (Gemma's house) from a domain that looks like nobody's. Returning the raw host there
    would make the bias guard silently pass. UNVERIFIED makes it speak up instead."""
    h = _host(url)
    if any(h == r or h.endswith("." + r) for r in _RESELLERS):
        return UNVERIFIED
    for suffix, vendor in _VENDOR_SUFFIXES:
        if h.endswith(suffix) or h == suffix.lstrip("."):
            return vendor
    if _is_local_fleet(h):
        # THE LOCAL GATEWAY IS WHAT SERVES GEMMA. An earlier version of this returned a
        # neutral "local" here, which made _grader_bias certify the single most
        # self-biased config that exists — GRADER_URL=<gateway> GRADER_MODEL=general is
        # Gemma grading its own judgments — as independent. A free in-fleet grader is a
        # tempting cost saving, which is exactly why the guard must refuse it. The local
        # fleet IS Gemma's house.
        return GEMMA_VENDOR
    return UNVERIFIED


def _is_local_fleet(host: str) -> bool:
    """Our own gateway: localhost, loopback, or the Tailscale CGNAT range 100.64.0.0/10.

    Range-checked, not prefix-matched: `host.startswith("100.")` is a string test that
    also swallows a real public host like 100.datacenter-isp.net, silently classifying
    a stranger's endpoint as our fleet."""
    h = host.split(":")[0]  # strip :4000
    if h == "localhost" or h.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")


# The in-fleet judge is Gemma — a GOOGLE model. That is not visible from any URL (it
# is served off our own Tailscale gateway), so it has to be stated. Without this, a
# Gemini grader looks "independent" of Anthropic and sails through, while quietly
# sharing a house with the very judge it is grading.
GEMMA_VENDOR = "google"


def _grader_bias(judge: str, grader_url: str) -> str | None:
    """`judge` is the frontier judge's VENDOR (preferred — established from the judgments
    artifact by _judge_identity) or, for convenience, its URL. Passing a URL here is what
    reintroduced the bug this guard exists for: in _judge_identity's fallback branch the
    URL is the ENV's, not the artifact's, so re-deriving the vendor from it certified a
    Sonnet grader as independent of a Sonnet judge. Take the vendor, don't recompute it."""
    """Which side, if any, does this grader share a house with?

    Two distinct collisions, and BOTH corrupt the result — in opposite directions:
      - grader == frontier judge's vendor  -> inflates FRONTIER (this was the Chunk 3
        caveat: Sonnet grading Sonnet's own judge scored a non-discriminating 1.000).
      - grader == Gemma's vendor (google)  -> inflates GEMMA, which is the direction
        that flatters our own thesis and is therefore the one a skeptic attacks first.

    A grader whose house cannot be established (a reseller, an unrecognized host) is
    reported as "unverified" — NOT as neutral. A reseller can serve a Gemini model from
    a host that belongs to nobody, and treating "I don't know" as "no bias" is how the
    guard would be silently defeated. Callers must decide; --pairwise refuses it.

    Returns "frontier", "gemma", "unverified", or None if provably neutral to both."""
    gv = _vendor(grader_url)
    jv = judge if judge in ("anthropic", "openai", "google", "local", UNVERIFIED) else _vendor(judge)
    if gv == UNVERIFIED:
        return UNVERIFIED
    if gv == jv:
        return "frontier"
    if gv == GEMMA_VENDOR:
        return "gemma"
    return None


def _assert_independent(judge: str, grader_url: str, strict: bool) -> str | None:
    """Warn (or, for pairwise, refuse) when the grader shares a house with either
    judge. Reference-anchored grading survives a collision — barely; it is why Chunk 3
    was publishable at all — because the reference anchors correctness. Open pairwise
    has no anchor and the bias goes straight into the verdict, so --pairwise treats a
    collision as fatal UNLESS the run is deliberately bracketing (see --bracket).

    Returns the favored side ("frontier" | "gemma"), or None when neutral."""
    biased = _grader_bias(judge, grader_url)
    if biased is None:
        return None
    if biased == UNVERIFIED:
        msg = (f"GRADER HOUSE UNVERIFIABLE: cannot establish which vendor is behind "
               f"{_host(grader_url)} (a reseller can serve a Gemini model — Gemma's "
               f"house — from a neutral-looking domain). 'Unknown' is not 'unbiased'.")
        hint = ("Point GRADER_* at a first-party endpoint whose vendor is known "
                "(api.openai.com is neutral to both Anthropic and Google).")
    else:
        msg = (f"GRADER BIAS: grader ({_vendor(grader_url)}) shares a house with the "
               f"{biased.upper()} judge — its scores for that side are inflated.")
        hint = ("Either point GRADER_* at a vendor that is neither the frontier judge's "
                "nor google (Gemma's house), or run --bracket to report BOTH biased "
                "graders as bounds.")
    if strict:
        sys.exit(f"{msg}\nPairwise has no reference to anchor against, so this lands "
                 f"directly in the verdict. {hint}")
    print(f"WARNING — {msg}\n  {hint}", file=sys.stderr)
    return biased


FIXTURES = os.path.join(_HERE, "eval_fixtures")


def _load(path: str) -> dict:
    """Live artifact if present, else the committed fixture of the same name.

    The live eval_*.json are GITIGNORED; only eval_fixtures/ is committed. Without this
    fallback nothing in the code ever read eval_fixtures/, so `--score` on a fresh clone
    died with FileNotFoundError — and the documented "$0 replay of the published result"
    was only ever true on the machine that generated it. Reproducibility that works
    exclusively on the author's laptop is not reproducibility."""
    if not os.path.exists(path):
        fixture = os.path.join(FIXTURES, os.path.basename(path))
        if os.path.exists(fixture):
            print(f"  (using committed fixture {os.path.basename(fixture)})", file=sys.stderr)
            path = fixture
        else:
            return {}   # neither live nor fixture — callers must not have to pre-check
    with open(path) as f:
        return json.load(f)


def _save(obj: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()

    elif "--generate" in sys.argv:  # BOOT: candidates + in-fleet Gemma judge
        gw = os.environ.get("CONCLAVE_GW")
        if not gw:
            sys.exit("set CONCLAVE_GW=<ts-ip>:4000")
        gw = gw if gw.startswith("http") else f"http://{gw}"
        cfg = EnsembleConfig(gateway_url=gw, judge_model="general")  # Gemma
        cache = candidate_cache.populate(cfg)          # skips already-cached queries
        # NOT gated on os.path.exists(live): that would short-circuit _load's fixture
        # fallback, leaving prior={} on a fresh clone -> a full-price re-judge of all 36,
        # with the model-mismatch and stale-candidates guards silently never firing.
        prior = _load(GEMMA_JUDGMENTS)
        gemma = judge_over_cache(cache, cfg, prior=prior,  # judges only the NEW ones
                                 refresh="--refresh" in sys.argv)
        _save(gemma, GEMMA_JUDGMENTS)
        print(f"cached {len(cache)} candidate sets + {len(gemma)} gemma judgments "
              f"({len(gemma) - len(prior)} new, {len(prior)} carried over)")

    elif "--frontier" in sys.argv:  # OFFLINE: frontier judge over cached candidates
        base, model, key = _frontier_from_env()
        cache = candidate_cache.load()
        if not cache:
            sys.exit("no candidate cache — run --generate on a boot first")
        cfg = EnsembleConfig(judge_url=base, judge_model=model)
        call = functools.partial(frontier_call, api_key=key)
        prior = _load(FRONTIER_JUDGMENTS)   # see the note in --generate: never gate on the live path
        frontier = judge_over_cache(cache, cfg, call, prior=prior,  # only the NEW ones
                                    refresh="--refresh" in sys.argv)
        _save(frontier, FRONTIER_JUDGMENTS)
        print(f"cached {len(frontier)} frontier judgments ({model}) "
              f"({len(frontier) - len(prior)} new, {len(prior)} carried over)")

    elif "--score" in sys.argv:  # OFFLINE: score + compare
        cache = candidate_cache.load()
        judgments = {"gemma": _load(GEMMA_JUDGMENTS), "frontier": _load(FRONTIER_JUDGMENTS)}
        pairwise = "--pairwise" in sys.argv
        # Default = the frozen run's sample count, so a bare --score REPLAYS (temperature
        # is derived from `samples` and is part of the cache key: samples=1 => temp 0 =>
        # a 100% cache miss => a silently paid re-grade).
        n = int(os.environ.get("GRADER_SAMPLES", str(FROZEN_GRADER_SAMPLES)))
        gc = GradeCache()  # resume a killed run; re-score for free

        def build(g_base, g_model, g_key, favors):
            s = (PairwiseScorer(g_base, g_model, g_key, call=frontier_call, cache=gc) if pairwise
                 else ReferenceGrader(g_base, g_model, g_key, call=frontier_call, samples=n,
                                      cache=gc))
            return s, {"model": g_model, "host": _host(g_base), "vendor": _vendor(g_base),
                       "favors": favors, "independent": favors is None}

        if "--heuristic" in sys.argv:  # deterministic, offline, no grader => no bias guard
            reports = {"local_heuristic": evaluate(cache, judgments, LocalHeuristicScorer())}

        elif "--bracket" in sys.argv:
            # BOTH biased graders, reported as BOUNDS. Neither is neutral: the frontier
            # judge's own vendor grades frontier generously, and Gemma's vendor (google)
            # grades Gemma generously. Run both and the truth is bracketed between them.
            # A conclusion that survives BOTH is robust to grader bias in either
            # direction — a stronger claim than any single grader can support.
            env_j_base, env_j_model, env_j_key = _frontier_from_env()
            j_base, j_vendor = _judge_identity(judgments["frontier"], env_j_base)
            g_base, g_model, g_key = _grader_from_env()
            gbias = _grader_bias(j_vendor, g_base)
            # The bracket is only a BRACKET if the two graders lean OPPOSITE ways. It used
            # to accept anything non-neutral and then hard-label it "gemma_vendor" — so an
            # Anthropic or an unverifiable grader got folded in as the upper bound and the
            # report still claimed "a conclusion that holds at BOTH ends is bias-robust",
            # which is false when both ends lean the same way.
            if gbias != "gemma":
                sys.exit(
                    f"--bracket needs two OPPOSED graders: one from the frontier judge's "
                    f"house (a LOWER bound on Gemma) and one from Gemma's house, google "
                    f"(an UPPER bound).\nGRADER_* resolves to bias={gbias!r}, which is not "
                    f"google's.\n  - Neutral grader? Just run --score, no bracket needed.\n"
                    f"  - Want the bracket? Point GRADER_* at a Google endpoint.")
            reports = {}
            for tag, (b, m, k) in {
                # grades frontier's own output -> Gemma's score here is a LOWER bound
                "grader=frontier_vendor": (j_base, env_j_model, env_j_key),
                # shares Gemma's house       -> Gemma's score here is an UPPER bound
                "grader=gemma_vendor": (g_base, g_model, g_key),
            }.items():
                scorer, meta = build(b, m, k, _grader_bias(j_vendor, b))
                r = evaluate(cache, judgments, scorer)
                r["grader"] = meta
                r["frontier_judge"] = {"url": j_base, "vendor": j_vendor}
                reports[tag] = r

        else:
            env_j_base, _, _ = _frontier_from_env()
            j_base, j_vendor = _judge_identity(judgments["frontier"], env_j_base)
            g_base, g_model, g_key = _grader_from_env()  # who is grading
            # Pairwise is fatal on a colliding grader; reference grading only warns.
            favors = _assert_independent(j_vendor, g_base, strict=pairwise)
            scorer, meta = build(g_base, g_model, g_key, favors)
            r = evaluate(cache, judgments, scorer)
            r["grader"] = meta
            r["frontier_judge"] = {"url": j_base, "vendor": j_vendor}
            reports = {scorer.name: r}

        for tag, r in reports.items():
            print(f"\n----- {tag} -----" if len(reports) > 1 else "", end="")
            print_report(r)
        if gc.misses and "--heuristic" not in sys.argv:
            print(f"\nNOTE: {gc.misses} grade(s) were computed LIVE (paid). The frozen run "
                  f"replays for $0 only under the exact config that produced it — see "
                  f"--replay.", file=sys.stderr)
        if len(reports) > 1:
            print("\n=== BRACKET ===")
            for tag, r in reports.items():
                fav = r["grader"]["favors"]
                print(f"  {tag:24s} gemma={r['aggregate']['gemma']:.3f} "
                      f"frontier={r['aggregate']['frontier']:.3f}   (inflates {fav})")
            los = min(r["aggregate"]["gemma"] for r in reports.values())
            his = max(r["aggregate"]["gemma"] for r in reports.values())
            print(f"  => Gemma's true score is bracketed in [{los:.3f}, {his:.3f}]. "
                  f"A conclusion that holds at BOTH ends is bias-robust.")
        if "--heuristic" not in sys.argv:
            print(f"\ngrader calls: {gc.misses} live, {gc.hits} from cache ({gc.path})")
        if "--save" in sys.argv:
            out = os.path.join(_HERE, "eval_report_rigor.json")
            _save(reports, out)
            print(f"\nsaved -> {out}")

    else:
        sys.exit("usage: judge_eval.py [--demo | --generate [--refresh] | "
                 "--frontier [--refresh] | --score [--heuristic] [--pairwise] "
                 "[--bracket] [--save]]\n"
                 "  --refresh: re-judge EVERY query (overwrites frozen judgments; costs "
                 "real calls). Required to change JUDGE_MODEL on an existing run.\n"
                 "  env: JUDGE_*   = the frontier judge under comparison (anthropic)\n"
                 "       GRADER_*  = the grader. A vendor that is NEITHER the frontier\n"
                 "                   judge's NOR google (Gemma's house) is neutral; anything\n"
                 "                   else is a bound, so pair it with --bracket.\n"
                 "       GRADER_SAMPLES = N grader samples per answer (default 1; >1 adds error bars)")
