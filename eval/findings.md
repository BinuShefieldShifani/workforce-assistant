# Findings from the unseen eval (prompt v3, 2026-11-05)

Result: 58/60 on questions written after v3 was frozen. 6/6 paraphrase groups
(including Swedish) and 20/20 public prompt-injection texts handled correctly.
These numbers are from before any of the changes below.

## k05: pivoted query sidesteps small-group suppression
The model answered "men and women at each job level" with one row per level and
SUM(CASE ...) columns per gender. The numbers were correct. But suppression works
per row: in long format a cell of 3 people is its own row and gets hidden; in the
pivot it sits inside a row of 69 and is shown. Nothing leaked in this run because
every cell had 5+ people, but a smaller leader would see counts of 1-4.
Decision: fix in code by masking count cells between 1 and 4 regardless of query
shape. Not fixed in the prompt, so the protection does not depend on how the model
writes SQL. (The eval also marked this answer wrong only because of the wide vs
long shape; that is a known limit of the result comparator.)

## k08: span of control not expressed within the SQL rules
COUNT(manager_id) / COUNT(DISTINCT manager_id) is allowed, but the model wrapped
both in CASE/FILTER, which the guard rejects. The retry hint suggested GROUP BY,
which does not help with a ratio. Future work: a hint that COUNT(column) already
skips NULLs. Not changed now, to keep this set unseen.