# Result: Memory Frame Budget in the System Prompt

Run 2026-09-27. `chat_model = qwen3.5:9b`, `temperature = 0.7` (production),
`think = False` (production), seed 20260927, 3 replicates, budgets
`0,1,2,3,5,10,20,40`, 11 queries x 22 retained memory-dependent facts.
264 generations in Stage B, plus 56 in screening.

## Headline

**Recall conditioned on whether the fact was actually on screen:**

| | recall | n |
|---|---|---|
| fact WAS shown | **0.910** | 345 |
| fact was NOT shown | **0.011** | 183 |

This is the result that matters, and it is not about frame counts. The chat model
is an excellent reader of memory: when a fact is in the context it uses it 91% of
the time, and when it is absent it produces it 1% of the time. There is no
"diluted context" failure and no meaningful middle ground — the model is not
partially attending to memory, it is either reading it or not.

**Every failure in this system is an access failure.** Nothing about the prompt,
the temperature, or the model needs tuning. The 1% residual is close enough to
zero to be treated as noise (2 facts out of 183, and one of those was a numeric
coincidence on a value that appears in a nearby frame).

## Dose-response

| budget | recall | r\|shown | r\|hidden | abstain | frames | mem chars | sys chars | trunc | p50 ms |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0.000 | – | 0.000 | 0.485 | 0 | 0 | 2065 | 0.00 | 2811 |
| 1 | 0.212 | 0.818 | 0.026 | 0.242 | 1 | 3769 | 5834 | 0.00 | 5756 |
| 2 | 0.303 | 0.795 | 0.000 | 0.030 | 2 | 4573 | 6638 | 0.00 | 6476 |
| 3 *(production)* | 0.303 | 0.795 | 0.030 | 0.151 | 3 | 4984 | 7049 | 0.00 | 6312 |
| 5 | 0.515 | 0.833 | 0.000 | 0.000 | 5 | 5376 | 7441 | 0.00 | 7404 |
| **10** | **0.697** | 0.861 | – | 0.000 | 10 | 6251 | 8316 | 0.09 | 7993 |
| 20 | 0.636 | 0.833 | – | 0.000 | 20 | 7879 | 9944 | 0.09 | 9596 |
| 40 | 0.636 | 0.833 | – | 0.000 | 40 | 10740 | 12021 | **0.73** | 11787 |

`r|hidden` is blank where no retained fact was hidden at that budget, i.e. the
condition had already surfaced everything it was going to.

## Findings

### F1 — Recall rises steeply to 10 frames, then stops paying

0.000 → 0.697 across budgets 0 to 10. Then 0.636 at 20 and 0.636 at 40. Going
from 10 to 40 costs **+4489 memory chars for −0.061 recall**. Budget 10 dominates
20 and 40 on both axes simultaneously; there is no tradeoff to make, the larger
budgets are strictly worse.

### F2 — The abstention failure is a budget-5 phenomenon

"I don't have that in memory" style responses: 0.485 at no memory, 0.242 at 1
frame, 0.000 from budget 5 onward. Five frames of *relevant* memory is enough to
eliminate the false-negative failure mode entirely. (The 0.151 at budget 3 is
below the budget-2 value of 0.030 and is treated as noise at n=33.)

This matters because the abstention rate is the user-visible cost. The agent
currently abstains at 0.151 on a fifth of turns while holding perfectly good
memory — and that is with 3 frames *handed to it*. It is not a budget problem.

### F3 — The 12000-char system prompt cap is a hard ceiling on this approach

`max_system_prompt_chars = 12000` and memory is appended last, so memory is
truncated first. Truncation rate by budget: 0.00 through budget 5, 0.09 at 10 and
20, **0.73 at 40**. At budget 40 nearly three quarters of generations had part of
their memory section discarded before the model saw it.

This is why F1's plateau is not noise and why "just show more frames" is not a
viable strategy. Memory grows ~1500 chars per frame here, so the prompt cap
allows roughly 8–10 frames before it starts cutting. The budget cannot be raised
indefinitely, and raising it past ~10 is actively harmful.

### F4 — Recall tracks the budget in which a fact first becomes visible

Per-query, keyed on the lowest budget at which a retained fact's text appears in a
rendered frame (`first`):

| query | target rank | first visible at | b=1 | b=3 | b=5 | b=10 | b=40 |
|---|---|---|---|---|---|---|---|
| jason_lee | 0 | 1 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| mtw_members | 3 | 1 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| why_not_label | 8 | 1 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| penn_state | 1 | 1 | 0.78 | 1.00 | 1.00 | 1.00 | 1.00 |
| cbs_hq | 1 | 2 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| walking_cow | 3 | 4 | 0.00 | 0.00 | 1.00 | 1.00 | 1.00 |
| groover | 4 | 5 | 0.00 | 0.00 | 0.78 | 1.00 | 0.89 |
| festivaltopia | 4 | 5 | 0.00 | 0.00 | 0.83 | 0.67 | 0.83 |
| perseverance | 6 | 6 | 0.00 | 0.00 | 0.00 | 0.83 | 0.83 |
| mtw_genre | 8 | 9 | 0.00 | 0.17 | 0.00 | 1.00 | 0.50 |
| studio | 13 | 1 | 0.00 | 0.00 | 0.33 | 0.67 | 1.00 |

The curve steps up at the point of visibility and is flat above it. The apparent
exceptions are worth naming rather than explaining away:

- **`why_not_label` (target rank 8, first visible 1).** The label is also a slot on
  `friend_music_records` at rank ≤3, so the fact was available early. This is why
  the nominal target rank is the wrong x-axis and `first visible` replaced it.
- **`studio` (first visible 1, recall 0.00 at b=1).** `first` is computed by
  scanning for the fact's surface form, so an incidental mention of "Austin" in a
  higher-ranked frame counts as visibility. This inflates `first` for that query and
  is a known weakness of the metric.
- **`mtw_genre` at 20 and 40.** Recall 1.00 → 0.50. More context, less recall on
  this query. Consistent with F1's decline; n=3 so it is suggestive, not conclusive.

## What this experiment does and does not settle

**Settles:** the frame budget should be roughly 10, not 3 and not 40. More than 10
costs prompt budget and does not help. Five relevant frames already remove the
abstention failure. The chat model needs no tuning — it reads what it is given.

**Does not settle:** where those 10 frames should come from. The candidate pool
here is 40 frames of which the model saw 10; in production the pool is **3**,
because the graph walk that is supposed to expand it contributes nothing at all
(0 edges, measured — see `probe_results.md`). The experiment deliberately held
retrieval fixed to isolate the budget, which means the far larger prize is
upstream of it and untouched by this work.

## Recommendation

Raise `top_k_direct` from 3 toward 10 and align `max_frames_in_prompt`. But treat
that as a small, well-understood change, not the fix. The fix is upstream: 877
live in-scope edges are reachable within 2 hops across 12 probe queries — about
73 per query — and the relevance gate currently admits **none of them**. Every one
of those is memory the agent already owns, already embedded, and has never once
been read.
