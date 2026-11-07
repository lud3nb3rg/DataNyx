# End-to-end demo: can an agent do real analysis from a schema alone?

An agent gets the [schema](https://github.com/lud3nb3rg/DataNyx/blob/main/docs/schemas.md) of two CSV files and
[fake rows](https://github.com/lud3nb3rg/DataNyx/blob/main/docs/synthetic-data.md) made from it, and nothing else. It writes a script. The script is
then run on the real data, and its answer is checked against the true one.

```sh
uv run python examples/demo/run_demo.py prepare WORKDIR   # schema + fake rows into WORKDIR/briefing
# ... an agent writes WORKDIR/trials/<task>_<n>/solution.py from the briefing alone (see agent_prompt.md) ...
uv run python examples/demo/run_demo.py grade WORKDIR     # run each solution on the real data, check the answers
uv run python examples/demo/run_demo.py report WORKDIR
```

The real data doesn't exist while the agent works: it is generated from a seed (`make_data.py`) only when `grade`
runs, and deleted afterwards. Solutions run through `datanyx run`, so the feedback saved for each one
(`feedback.txt`) is what the agent would be shown: scrubbed stderr and the size of stdout.

## The data

400 customers and about 3,000 orders, with the kind of mess real exports have and a schema can't show: dates in
three formats, a country written four ways (`US`, `USA`, `United States`, `us`), amounts that are sometimes text
(`$1,234.50`) or `N/A`, statuses in mixed case, exact duplicate order rows, and orders for customers that don't exist.
Five questions, each with a true answer computed from the clean records before the mess was added.

**This is a worst case on purpose.** Every column that can be messy is, several kinds of mess are stacked on the
same tables, and the questions are chosen to trip over them. Most real data is cleaner than this, so treat the
failure rate below as an upper bound for ordinary exports, not a typical figure.

## Result (one run, 15 agents: 5 questions x 3 agents each, Claude Sonnet)

| Question | Agent 1 | Agent 2 | Agent 3 |
|---|---|---|---|
| Total revenue from completed orders | pass | pass | pass |
| Customers who signed up in 2023 | pass | pass | pass |
| Customers who never ordered | pass | pass | pass |
| Average completed order per plan | pass | pass | pass |
| Top three countries by revenue | **wrong** | **wrong** | pass |

**13 of 15 were right the first time. None crashed. Both failures ran cleanly and printed a plausible, wrong answer.**

What that says:

- **A schema plus fake rows is enough to get most of the way.** Every agent wrote code that handled the dates,
  the `$` amounts, the casing and the duplicates without having seen one real value.
- **The failures were silent.** The two wrong agents knew `US` and `UK` were aliases, but not that `CA`, `DE` and `FR` are
  codes for countries that also appear spelled out, so Canada was split into `ca` and `Ca`, and Germany lost its rank. Nothing crashed, so
  `datanyx run`'s scrubbed error output had nothing to report. **The scrubbed-error loop catches crashes, not wrong
  answers.** A person who knows the data has to sanity-check the answer, which is the step the
  [container setup](../../docs/agents.md#hard-isolation-with-containers) leaves to you on purpose.

## What this does and doesn't show

Read these before quoting the numbers.

- **The prompt warns the agent that the data is messy** ("Real exports are messy"). That is deliberate: someone who
  hands real data to an agent knows it is not clean, and would say so. Every agent responded with defensive code.
  The pass rate without the warning wasn't measured.
- **15 runs, one model, one dataset I designed.** The mess is the kind a model expects (dates, currency
  symbols, case). Mess it doesn't think to guard against (a unit change halfway through a column, an `N/A` that means
  something, two meanings of one status) isn't tested.
- **No agent crashed, so the retry loop wasn't exercised.** That the scrubbed feedback is enough to fix a crash is
  tested elsewhere (`docs/debugging.md`), not here.
- **The agents' isolation was by absence, not enforcement.** The data wasn't on disk and the generator was moved
  out of the repository, but they could have searched the filesystem. Each used three tool calls, consistent with
  reading the briefing and writing one file, and I did not read their transcripts to confirm.
- The dataset is fake and a worst case (above), and the country grader accepts any spelling of the right country.
