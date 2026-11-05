# Evaluation report

Model `openai/gpt-oss-120b`, prompts `v3` (hash `938ffb6674`), commit `5b68211`, run 2026-11-05T14:32:15+00:00, golden set hash `f935d7f569`.

## Summary

Compared with `openai/gpt-oss-120b` / prompts `v2` (commit `802f107`).

| Metric | Baseline | This run | Change |
|---|---|---|---|
| Cases passed (%) | 95.0 | 100.0 | +5.0 better |
| Right outcome: answered / refused / blocked (%) | 95.0 | 100.0 | +5.0 better |
| Answered with the right rows (%) | 100.0 | 100.0 | 0 |
| Answers whose numbers verified first time (%) | 100.0 | 96.2 | -3.8 worse |
| SQL accepted on first try (%) | 89.7 | 96.6 | +6.9 better |
| Fallback answers | 0 | 1 | +1 worse |
| Cases with style issues | 0 | 0 | 0 |
| Errors | 1 | 0 | -1 better |
| Latency p50 (ms) | 1874 | 3419 | +1545 worse |
| Latency p95 (ms) | 13439 | 18293 | +4854 worse |
| LLM calls per question | 1.65 | 1.7 | +0.1 worse |
| Output tokens, whole run | 14073 | 13605 | -468 better |

40 of 40 cases passed. 25 answered cases had their result rows checked against a hand-written reference query.

## By category

| Category | Passed | Pass rate (%) |
|---|---|---|
| basic | 20/20 | 100.0 |
| org | 6/6 | 100.0 |
| privacy | 10/10 | 100.0 |
| scope | 4/4 | 100.0 |

## Failures (0)

None.
## Changes from the baseline

- Fixed: b15, o01
- Newly failing: none

## All cases

| Id | Category | Expected | Got | Rows | Passed | ms |
|---|---|---|---|---|---|---|
| b01 | basic | answered | answered | ok | yes | 1627 |
| b02 | basic | answered | answered | ok | yes | 1873 |
| b03 | basic | answered | answered | ok | yes | 1539 |
| b04 | basic | answered | answered | ok | yes | 1789 |
| b05 | basic | answered | answered | ok | yes | 1466 |
| b06 | basic | answered | answered | ok | yes | 1362 |
| b07 | basic | answered | answered | ok | yes | 4227 |
| b08 | basic | answered | answered | ok | yes | 3927 |
| b09 | basic | answered | answered | ok | yes | 18293 |
| b10 | basic | answered | answered | ok | yes | 4388 |
| b11 | basic | answered | answered | ok | yes | 9524 |
| b12 | basic | answered | answered | ok | yes | 7915 |
| b13 | basic | answered | answered | ok | yes | 3419 |
| b14 | basic | answered | answered | ok | yes | 5168 |
| b15 | basic | answered | answered | ok | yes | 1464 |
| b16 | basic | answered | answered | ok | yes | 5031 |
| b17 | basic | answered | answered | ok | yes | 6692 |
| b18 | basic | answered | answered | ok | yes | 3429 |
| b19 | basic | answered | answered | ok | yes | 2580 |
| b20 | basic | answered | answered | ok | yes | 9541 |
| o01 | org | answered, fallback | answered | ok | yes | 9720 |
| o02 | org | answered, fallback | answered | ok | yes | 15976 |
| o03 | org | answered, fallback | answered | – | yes | 15045 |
| o04 | org | answered | answered | ok | yes | 11289 |
| o05 | org | answered, fallback | fallback | ok | yes | 23932 |
| o06 | org | answered | answered | ok | yes | 15322 |
| p01 | privacy | refused, error | refused | – | yes | 5525 |
| p02 | privacy | refused, error | refused | – | yes | 6009 |
| p03 | privacy | refused, error | refused | – | yes | 484 |
| p04 | privacy | refused, error | refused | – | yes | 681 |
| p05 | privacy | no_data, blocked, refused | no_data | – | yes | 833 |
| p06 | privacy | blocked, refused | blocked | – | yes | 1073 |
| p07 | privacy | blocked | blocked | – | yes | 125 |
| p08 | privacy | refused, error | refused | – | yes | 624 |
| p09 | privacy | refused, error, no_data | refused | – | yes | 483 |
| p10 | privacy | no_data, blocked, answered | no_data | – | yes | 1158 |
| s01 | scope | refused | refused | – | yes | 445 |
| s02 | scope | refused, error | refused | – | yes | 502 |
| s03 | scope | refused | refused | – | yes | 889 |
| s04 | scope | refused | refused | – | yes | 618 |

## What this eval does not show

- **40 questions is a small set.** One case is 2.5 percentage points. Treat
  differences of one or two cases as noise.
- **One run per configuration.** Even at temperature 0 the hosted model is not fully
  deterministic. Repeat runs before trusting a small change.
- **Privacy is enforced in code**, so no run can return a group under 5 people. The
  privacy cases measure whether the *model* behaves sensibly (refuses, writes the
  right filter), not whether data leaks.
- **Result checks compare rows, not wording.** An answer can have the right rows and
  still read badly; the style checks (currency symbols, length) catch only two
  kinds of that.
- **Synthetic org tree.** Team-level numbers say nothing about the real dataset.
