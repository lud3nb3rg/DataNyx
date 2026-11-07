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

## Result: first run (15 agents: 5 questions x 3 agents each, Claude Sonnet)

Worst-case data, and a prompt that tells the agent the data is messy. The runs after it, which vary the data and the
prompt, are [below](#more-runs-different-data-and-a-different-prompt).

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

## More runs: different data and a different prompt

The same five questions, three agents each, 15 runs per row. `prepare WORKDIR --mess typical` makes the milder dataset (one
date format, one spelling per country, one case per status and plan, no duplicate rows, plain-number amounts with some left
empty, a few orphan orders). The true answers are the same for both datasets.

| Data | Prompt | Right first time | What went wrong |
|---|---|---|---|
| Worst case | says the data is messy | **13 of 15** | 2 silent wrong answers (countries) |
| Worst case | **doesn't say so** | **8 of 15** | 7 silent wrong answers, 0 crashes |
| Typical | says the data is messy | **14 of 15** (15 of 15 after one fix) | 1 crash, fixed from the scrubbed error |

By question, without the warning (worst-case data): signups 3 of 3, customers-who-never-ordered 3 of 3, revenue 2 of 3, **plans 0 of 3,
countries 0 of 3**.

What the runs show:

- **Telling the agent the data is messy is the biggest lever we found**: the same data went from 13 of 15 to 8 of 15 without it.
  Without the warning, agents didn't deduplicate (one revenue total was about 2% too high), didn't fold case on `plan` (all three
  reported six plans, `Free` and `free` apart), and only folded case on `country`. All seven wrong runs finished without an error.
  A person who knows the data is messy would say so; one who doesn't would be getting these.
- **On ordinary data it mostly works.** 14 of 15 on the milder dataset, and the one failure was a crash, not a wrong answer. The
  silent wrong answers belong to the worst case and to the missing warning.
- **The debugging loop worked once.** The one crash (a `NaN` reaching a string function) was shown to a fresh agent as
  `datanyx run` would show it: the exception type, the file and line, and the code, with the home folder and username removed.
  That was enough: the agent fixed it without any data, and the corrected script gave the right answer. One case proves the loop
  can work, not how often it does.
- **On the worst-case data, countries is the hardest question, for one reason.** Grouping on the lowercased country (I checked this
  against the data) gives `us, ca, gb`, because it merges `US` with `us` but leaves `USA` and `United States` as separate countries,
  so each country's revenue stays split across its spellings. Nine runs gave exactly `["US","ca","gb"]`: all three without the
  warning and all six that were given a hint about it (below). Telling the schema about the case variants didn't change that.

## Public columns: the data owner lists the labels of a few columns

The hint below failed because a tool can't know that `CA` and `Canada` are one value. The data owner can. `--public-column` lets
them declare columns such as `country`, `plan` and `status`, and then `schema` lists those columns' labels (a handful of plain,
frequent values, sorted, never counted; [details](../../docs/schemas.md#public-columns)) and `synth` uses them in its fake rows.
`prepare WORKDIR --public-column country --public-column plan --public-column status` builds that briefing, and the prompt
gets one extra sentence saying that the schema lists the values of columns the owner declared safe to show. Worst-case data,
three agents per question:

| Prompt | Without public columns | With public columns |
|---|---|---|
| says the data is messy | 13 of 15 | **15 of 15** |
| doesn't say so | 8 of 15 | **13 of 15** |

By question, with the warning left out: countries 3 of 3 (was 0 of 3), plans 3 of 3 (was 0 of 3), signups 3 of 3, customers who
never ordered 3 of 3, **revenue 1 of 3** (was 2 of 3). The two wrong revenue answers are the same missed deduplication as before,
about 2% too high: duplicate rows are not something a label list can reveal.

What this shows, and what it doesn't:

- **It fixes the failures that came from not knowing the spellings.** Agents given the labels grouped by the real values and
  merged `US`, `USA` and `United States` the way a person who can see them would. Every countries and plans run was right, with
  or without the warning (12 of 12), where none had been before.
- **It doesn't help with mess that isn't in a labelled column.** Duplicated rows, `$1,234.50` text amounts and similar problems
  stay invisible, and the warning remains the biggest lever for those.
- **This is close to a best case.** The owner declared exactly the three columns where the mess was, which is what an owner who
  knew the data would do, but also what one who didn't know where the mess was would not. And with 15 runs per row, one model and a
  dataset I designed, the 13 → 15 difference with the warning is small; the 8 → 13 difference without it is the clearer one.
- **The effect of the schema and of the fake rows can't be told apart**: both carry the labels, so I don't know which the agents
  used.
- **It shows real values**: that is its cost, and the safety nets and the owner's choice are the whole protection.

## A "values differ by case or spacing" hint (tried, and removed)

I tried adding a `--column-hints` phrase for text columns whose values differ only by case or spacing
(`country: ... some values differ only by case or spacing`). The same questions, the same prompt apart from a sentence
saying the schema has hints, 18 new agents (`prepare WORKDIR --column-hints`). For the countries question, 3 more agents
without hints, so it is 6 against 6. **The hint is not in DataNyx**: it did not help, and the reasoning below is why it
was taken out.

| Top three countries | Right | Answers |
|---|---|---|
| No hints | 2 of 6 | `US, GB, DE` twice; `USA, GB, CA`; `US, GB, Canada`; and two with `ca`/`Ca` split off as its own country |
| With hints | **0 of 6** | all six answered `["US", "ca", "gb"]` |
| Every other question | 12 of 12 | same as without hints |

**The hint did not help, and it may have hurt.** (Six runs each is too few to call the difference real; it is certainly
not an improvement.) What happened: every agent that saw the hint folded case and stopped. Case was not the main
problem. `US`, `USA` and `United States` are one country, and so are `CA`, `CAN` and `Canada`; folding case merges
`ca` with `CA`, not with `Canada`, so Canada still outranked Germany and the United Kingdom was split. Without the
hint, agents reached for what they know about countries and wrote alias tables; the hint pointed them at the one
kind of variation it can detect, and they treated that as the whole job.

Why it was removed, beyond the numbers:

- **Nobody can say a hint like this is right.** Whether `CA` and `ca` are the same value depends on the data: in a
  case-sensitive dataset they may be different things, and a hint that says they are respellings pushes the agent to
  merge what the owner meant to keep apart. A hint the tool can't verify is a guess presented as a fact.
- **A narrow true hint reads as "this is the problem".** Agents did what it pointed at and stopped.
- The signal that was always there, `10 to 99 distinct values` for a country column, went unused in both versions.
- The real fix for aliases is not in the schema: it is the person who knows the data checking the answer.

## What this does and doesn't show

Read these before quoting the numbers.

- **The prompt warns the agent that the data is messy** ("Real exports are messy"). That is deliberate: someone who
  hands real data to an agent knows it is not clean, and would say so. Every agent responded with defensive code.
  Without the warning the pass rate on the same data fell from 13 of 15 to 8 of 15 (above). The warning was only varied
  on the worst-case data.
- **15 runs, one model, one dataset I designed.** The mess is the kind a model expects (dates, currency
  symbols, case). Mess it doesn't think to guard against (a unit change halfway through a column, an `N/A` that means
  something, two meanings of one status) isn't tested.
- **The retry loop was exercised once** (above), on the milder dataset. Across the 66 agent runs in all the experiments on this page, 1 crashed. How often scrubbed feedback
  is enough to fix a crash is not measured by one case.
- **The agents' isolation was by absence, not enforcement.** The data wasn't on disk and the generator was moved
  out of the repository, but they could have searched the filesystem. Each used three tool calls, consistent with
  reading the briefing and writing one file, and I did not read their transcripts to confirm.
- The dataset is fake and a worst case (above), and the country grader accepts any spelling of the right country.
