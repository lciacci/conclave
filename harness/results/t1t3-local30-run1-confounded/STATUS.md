# CONFOUNDED — do not grade this run

Produced by the **pre-fix** harness on 2026-07-28. Two defects make its rows unreadable as model
evidence:

- **No `grade.py` pre-add.** Aider silently discarded any reply whose SEARCH block quoted the
  target's docstring (which names `bench_local30_grade.py`). `T2.diff` is 0 lines because the edit
  was **dropped**, not because the model failed — the correct edit is visible in `T2.log`.
- **No `applied=` / `llm_turns=` columns**, so the drop is invisible in `summary.txt`.

`T3.diff` here IS gradeable and IS wrong — it breaks the file's own `--demo` self-check
(`AssertionError: dry-run generates nothing`). That is a real result, kept for the record.

Superseded by `../t1t3-local30-run2/` (harness fixed) and `../t1t3-local30-k3/` (3 reps,
byte-identical). Both of those predate the pinned-`BASE` and portable-timeout fixes, so they are
**not comparable to a hosted leg** either — see `../../EXPERIMENT.md` § Grading rubric.
