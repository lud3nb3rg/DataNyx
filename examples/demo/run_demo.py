"""Run the demo: brief an agent with a schema and fake rows only, then run what it wrote on the real data.

    uv run python examples/demo/run_demo.py prepare WORKDIR    # schema + fake rows into WORKDIR/briefing
                                                               # (add --column-hints to put the hints in the schema,
                                                               #  --mess typical for the milder dataset,
                                                               #  --public-column NAME to list that column's labels)
    ... an agent writes WORKDIR/trials/<task>_<n>/solution.py from the briefing alone ...
    uv run python examples/demo/run_demo.py grade WORKDIR      # run every solution on the real data, check the answers
    uv run python examples/demo/run_demo.py report WORKDIR     # the table

The real data exists only while `grade` runs. It is regenerated from a seed and deleted again, so during the
agent's work there is nothing to read. Solutions run through `datanyx run`, so what is saved as feedback for the
agent is what `datanyx run` would show it: scrubbed stderr, and the size of stdout.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from make_data import CANONICAL, generate  # noqa: E402

DATANYX = [sys.executable, "-m", "datanyx"]


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", **kwargs)


def prepare(work: Path, hints: bool = False, mess: str = "worst", public: tuple[str, ...] = ()) -> None:
    briefing = work / "briefing"
    shutil.rmtree(briefing, ignore_errors=True)
    briefing.mkdir(parents=True)
    (work / "trials").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        generate(data, mess=mess)
        flags = [word for name in public for word in ("--public-column", name)]
        schema = _run([*DATANYX, "schema", str(data), "--format", "text", *(["--column-hints"] if hints else []), *flags])
        (briefing / "schema.txt").write_text(schema.stdout, encoding="utf-8")
        _run([*DATANYX, "synth", str(data), "--rows", "15", "--seed", "1", "--format", "csv", "-o", str(briefing / "sample"), *flags])
    shutil.copy(HERE / "tasks.json", briefing / "tasks.json")
    (work / "config.json").write_text(json.dumps({"mess": mess}), encoding="utf-8")
    print(f"briefing written to {briefing}; the real data is gone again")


def _canon_country(value: str) -> str | None:
    return CANONICAL.get(str(value).strip().lower())


def _close(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) <= 0.01
    except (TypeError, ValueError):
        return False


def check(task: str, answer: dict, truth: dict) -> bool:
    if task == "revenue":
        return _close(answer["total_revenue"], truth["total_revenue"])
    if task == "signups":
        return int(answer["signups_2023"]) == truth["signups_2023"]
    if task == "inactive":
        return int(answer["customers_without_orders"]) == truth["customers_without_orders"]
    if task == "countries":
        return [_canon_country(c) for c in answer["top_countries"]] == truth["top_countries"]
    if task == "plans":
        got = {str(k).strip().lower(): v for k, v in answer["avg_by_plan"].items()}
        return set(got) == set(truth["avg_by_plan"]) and all(_close(got[k], v) for k, v in truth["avg_by_plan"].items())
    raise KeyError(task)


def grade(work: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        config = work / "config.json"
        truth = generate(data, mess=json.loads(config.read_text(encoding="utf-8"))["mess"] if config.exists() else "worst")
        for trial in sorted(p for p in (work / "trials").iterdir() if (p / "solution.py").exists()):
            task = trial.name.split("_")[0]
            saved = trial / "stdout.real.txt"
            saved.unlink(missing_ok=True)
            try:
                shown = _run(
                    [*DATANYX, "run", "--no-ner", "--names-from", str(data), "--timeout", "120", "--save-stdout", str(saved),
                     "--", sys.executable, str(trial / "solution.py"), str(data)],
                    cwd=trial,
                )
                code = shown.returncode
                feedback = shown.stdout + shown.stderr
            except Exception as error:  # A harness problem is not an agent failure.
                code, feedback = -1, f"harness error: {type(error).__name__}"
            (trial / "feedback.txt").write_text(feedback, encoding="utf-8")
            if code != 0:
                status = "crash"
            else:
                try:
                    answer = json.loads(saved.read_text(encoding="utf-8").strip().splitlines()[-1])
                    status = "pass" if check(task, answer, truth) else "wrong"
                except Exception:
                    status = "bad-output"
            (trial / "result.json").write_text(json.dumps({"status": status, "exit": code}), encoding="utf-8")
            print(f"{trial.name:20} {status}")


def report(work: Path) -> None:
    rows: dict[str, dict[str, str]] = {}
    for result in sorted((work / "trials").glob("*/result.json")):
        name = result.parent.name
        base, _, round_two = name.partition("_r2")
        rows.setdefault(base, {})["r2" if round_two == "" and name.endswith("_r2") else "r1"] = json.loads(
            result.read_text(encoding="utf-8")
        )["status"]
    tasks = sorted({k.split("_")[0] for k in rows})
    retried = any("r2" in r for r in rows.values())
    head = f"{'task':10} {'attempt 1':28}" + (" after one fix from scrubbed feedback" if retried else "")
    print(head)
    totals = {"pass1": 0, "pass2": 0, "wrong": 0, "crash": 0, "n": 0}
    for task in tasks:
        trials = [rows[k] for k in sorted(rows) if k.split("_")[0] == task]
        first = [t["r1"] for t in trials]
        final = [t.get("r2", t["r1"]) for t in trials]
        print(f"{task:10} {' '.join(first):28}" + (f" {' '.join(final)}" if retried else ""))
        totals["n"] += len(trials)
        totals["pass1"] += first.count("pass")
        totals["pass2"] += final.count("pass")
        totals["wrong"] += first.count("wrong")
        totals["crash"] += first.count("crash")
    n = totals["n"]
    line = f"\n{n} runs: {totals['pass1']} right first time"
    if retried:
        line += f", {totals['pass2']} right after one fix"
    print(line + f"; on attempt 1, {totals['wrong']} ran fine but gave a wrong answer, {totals['crash']} crashed")


if __name__ == "__main__":
    commands = {"prepare": prepare, "grade": grade, "report": report}
    argv = sys.argv[1:]
    public: list[str] = []
    while "--public-column" in argv:
        at = argv.index("--public-column")
        if at + 1 >= len(argv):
            sys.exit(__doc__)
        public.append(argv[at + 1])
        del argv[at : at + 2]
    mess = "worst"
    if "--mess" in argv:
        at = argv.index("--mess")
        mess = argv[at + 1] if at + 1 < len(argv) else ""
        del argv[at : at + 2]
    args = [a for a in argv if a != "--column-hints"]
    if len(args) != 2 or args[0] not in commands or mess not in ("worst", "typical"):
        sys.exit(__doc__)
    if args[0] == "prepare":
        prepare(Path(args[1]), hints="--column-hints" in sys.argv, mess=mess, public=tuple(public))
    else:
        commands[args[0]](Path(args[1]))
