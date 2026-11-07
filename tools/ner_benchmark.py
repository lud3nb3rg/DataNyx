"""How well the default name detector hides names: the numbers in docs/anonymization.md.

    uv run python tools/ner_benchmark.py            # print the table
    uv run python tools/ner_benchmark.py --write    # put it in docs/anonymization.md

72 invented names, six from each of 12 regions, each placed in each of 6 sentences, written six ways. A name counts as
hidden when none of its distinctive words survives in the output, as in the tests (`tests/text_corpus.py`).
It needs `en_core_web_lg` (`uv sync --group models`). CI runs it on every push and fails if a number drops below
`FLOORS`, so a model or detector change that makes names leak shows up before release.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
PAGE = ROOT / "docs" / "anonymization.md"
START, END = "<!-- ner-benchmark:start -->", "<!-- ner-benchmark:end -->"

NAMES = {
    "English": ["Oliver Thornton", "Charlotte Hargreaves", "Harry Pemberton", "Eleanor Whitcombe", "Jack Fairweather", "Imogen Ashworth"],
    "German": ["Lukas Hartmann", "Annelie Brunner", "Matthias Kellner", "Sabine Vogelsang", "Florian Reichardt", "Greta Lindqvist"],
    "French": ["Mathieu Delaunay", "Camille Beauregard", "Étienne Marchetti", "Solène Duvernay", "Baptiste Lemaire", "Océane Rochefort"],
    "Spanish": ["Alejandro Quintero", "Valentina Barrientos", "Rodrigo Maldonado", "Inés Echeverría", "Joaquín Salcedo", "Marisol Ibarra"],
    "Polish": ["Tomasz Kowalczyk", "Agnieszka Wiśniewska", "Krzysztof Dąbrowski", "Magdalena Zielińska", "Błażej Wojciechowski", "Zofia Mazurek"],
    "Nigerian": ["Chinedu Okafor", "Amara Okonkwo", "Oluwaseun Adeyemi", "Ngozi Eze", "Babatunde Ogunleye", "Folasade Balogun"],
    "Arabic": ["Omar Haddad", "Layla Al-Mansouri", "Yusuf Khalil", "Samira Nasser", "Tariq Benali", "Fatima Zahra El-Idrissi"],
    "Indian": ["Arjun Venkataraman", "Priya Chakraborty", "Rohan Deshpande", "Ananya Subramanian", "Vikram Raghunathan", "Meera Kulkarni"],
    "Chinese": ["Wei Zhang", "Xiaoling Chen", "Jianhua Liu", "Mei Huang", "Zhihao Wu", "Yuqing Zhao"],
    "Japanese": ["Haruto Nakamura", "Yui Kobayashi", "Kenji Takahashi", "Sakura Matsumoto", "Daiki Yamaguchi", "Aoi Fujimoto"],
    "Korean": ["Minjun Park", "Seoyeon Kim", "Jihoon Choi", "Haeun Jeong", "Donghyun Kang", "Yerin Shin"],
    "Pacific": ["Tane Rangihuna", "Moana Tuilagi", "Sione Fakatava", "Aroha Kereama", "Manu Leiataua", "Losa Taufa"],
}
TEMPLATES = [
    "Please forward the invoice to {name} before Friday.",
    "{name} joined the support team in March and handles escalations.",
    "The contract was signed by {name} on behalf of the client.",
    "According to the report, {name} reviewed the findings last week.",
    "We spoke with {name} about the delayed shipment.",
    "Thanks for your help, {name}, the issue is resolved now.",
]
# (label in the table, how the name is written, whether the whole line is changed too)
CONDITIONS = [
    ("First name only", lambda n: n.split()[0], str),
    ("Full name in ALL CAPS", lambda n: n.upper(), str),
    ("Full name, capitalized normally", lambda n: n, str),
    ("Whole line in lowercase", lambda n: n, str.lower),
    ("Whole line in ALL CAPS", lambda n: n, str.upper),
    ("Full name in lowercase, in a normally capitalized sentence", lambda n: n.lower(), str),
]
# Each number may fall this far (in points) below what was measured before CI fails; the model is pinned, so
# this is room for platform noise, not for regressions.
TOLERANCE = 3.0


def _survives(word: str, output: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(word.lower())}(?!\w)", output.lower()) is not None


def measure() -> tuple[list[tuple[str, float, int]], list[tuple[str, float]]]:
    """Percent hidden for each way of writing the name, and for each region across all of them."""
    from datanyx.text import PatternDetector, SpacyDetector, anonymize

    detectors = [PatternDetector(), SpacyDetector("en_core_web_lg")]
    by_region = {region: [0, 0] for region in NAMES}  # [hidden, trials]
    rows = []
    for label, write_name, write_line in CONDITIONS:
        hidden = trials = 0
        for template in TEMPLATES:
            for region, names in NAMES.items():
                for name in names:
                    shown = write_name(name)
                    output = anonymize(write_line(template.format(name=shown)), detectors)
                    ok = not any(_survives(word, output) for word in shown.split())
                    trials += 1
                    hidden += ok
                    by_region[region][0] += ok
                    by_region[region][1] += 1
        rows.append((label, 100.0 * hidden / trials, trials))
    regions = [(region, 100.0 * h / t) for region, (h, t) in by_region.items()]
    return rows, regions


def render(rows: list[tuple[str, float, int]], regions: list[tuple[str, float]]) -> str:
    lines = ["| Name appears as | Hidden |", "|---|---|"]
    lines += [f"| {label} | {percent:.1f}% |" for label, percent, _ in sorted(rows, key=lambda r: -r[1])]
    lines += ["", "| Names from | Hidden, over all six ways of writing them |", "|---|---|"]
    lines += [f"| {region} | {percent:.1f}% |" for region, percent in sorted(regions, key=lambda r: r[1])]
    return "\n".join(lines)


def documented() -> dict[str, float]:
    """The numbers now in the docs page."""
    block = PAGE.read_text(encoding="utf-8").split(START)[1].split(END)[0]
    return {m.group(1): float(m.group(2)) for m in re.finditer(r"^\| (.+?) \| ([\d.]+)% \|$", block, re.M)}


def main() -> int:
    table = render(*measure())
    if "--write" in sys.argv:
        text = PAGE.read_text(encoding="utf-8")
        newline = "\r\n" if "\r\n" in text else "\n"
        head, rest = text.split(START)
        tail = rest.split(END)[1]
        PAGE.write_text(head + START + newline + table.replace("\n", newline) + newline + END + tail, encoding="utf-8", newline="")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
