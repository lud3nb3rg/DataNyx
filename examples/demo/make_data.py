"""A seeded, messy customers-and-orders dataset, and the true answers to the demo's questions.

Two levels of mess (`mess=`), with the same records and the same true answers:

- "worst" (the default): every kind of mess a real export has and a schema can't show, stacked on the same tables:
  dates in three formats, a country spelled four ways, amounts that are sometimes text ("$1,234.50") or "N/A",
  statuses and plans in mixed case, duplicated order rows, and orders for customers that don't exist.
- "typical": the problems an ordinary export usually has. One date format, one spelling per country, one case per
  status and plan, no duplicate rows, amounts as plain numbers with some left empty, and a few orders for customers
  that don't exist.

The answers are computed from the clean records *before* the mess is applied, so they don't depend on any cleaning code.
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

COUNTRIES = {
    "US": ["US", "USA", "United States", "us"],
    "GB": ["GB", "UK", "United Kingdom", "gb"],
    "DE": ["DE", "Germany", "DEU", "de"],
    "FR": ["FR", "France", "FRA", "fr"],
    "CA": ["CA", "Canada", "CAN", "ca"],
}
# Everything a spelling above can be, mapped to the canonical code, for the grader.
CANONICAL = {spelling.lower(): code for code, spellings in COUNTRIES.items() for spelling in spellings}
PLANS = ["free", "pro", "team"]
STATUSES = ["completed", "completed", "completed", "refunded", "pending", "cancelled"]


def generate(out: Path, seed: int = 7, customers: int = 400, orders: int = 3000, mess: str = "worst") -> dict:
    """Write customers.csv and orders.csv into `out`; return the true answers."""
    if mess not in ("worst", "typical"):
        raise ValueError(f"mess must be 'worst' or 'typical', not {mess!r}")
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)

    people = []
    for cid in range(1, customers + 1):
        people.append(
            {
                "id": cid,
                "email": f"user{cid}@example.com",
                "full_name": f"Person {cid}",
                "signup": date(2021, 1, 1) + timedelta(days=rng.randrange(0, 4 * 365)),
                "country": rng.choice(list(COUNTRIES)),
                "plan": rng.choice(PLANS),
            }
        )
    by_id = {p["id"]: p for p in people}

    clean_orders = []
    for oid in range(1, orders + 1):
        orphan = rng.random() < 0.02
        clean_orders.append(
            {
                "order_id": 10_000 + oid,
                "customer_id": customers + rng.randrange(1, 50) if orphan else rng.choice(people)["id"],
                "amount": round(rng.uniform(5, 2500), 2),
                "has_amount": rng.random() > 0.08,
                "status": rng.choice(STATUSES),
                "at": datetime(2022, 1, 1) + timedelta(minutes=rng.randrange(0, 3 * 365 * 24 * 60)),
            }
        )

    # The true answers, from the clean records.
    completed = [o for o in clean_orders if o["status"] == "completed" and o["has_amount"]]
    revenue_by_country: dict[str, float] = defaultdict(float)
    amounts_by_plan: dict[str, list[float]] = defaultdict(list)
    for o in completed:
        owner = by_id.get(o["customer_id"])
        if owner:
            revenue_by_country[owner["country"]] += o["amount"]
            amounts_by_plan[owner["plan"]].append(o["amount"])
    buyers = {o["customer_id"] for o in clean_orders}
    truth = {
        "total_revenue": round(sum(o["amount"] for o in completed), 2),
        "signups_2023": sum(1 for p in people if p["signup"].year == 2023),
        "top_countries": [c for c, _ in sorted(revenue_by_country.items(), key=lambda kv: -kv[1])[:3]],
        "customers_without_orders": sum(1 for p in people if p["id"] not in buyers),
        "avg_by_plan": {plan: round(sum(v) / len(v), 2) for plan, v in sorted(amounts_by_plan.items())},
    }

    # Now the mess. (The draws below are unchanged for "worst", so its files are the same as before `mess` existed.)
    worst = mess == "worst"
    formats = ["%Y-%m-%d", "%d.%m.%Y", "%b %d, %Y"]
    with open(out / "customers.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "email", "full_name", "signup_date", "country", "plan"])
        for p in people:
            w.writerow(
                [
                    p["id"], p["email"], p["full_name"],
                    p["signup"].strftime(rng.choices(formats, weights=[6, 3, 1])[0] if worst else formats[0]),
                    rng.choice(COUNTRIES[p["country"]]) if worst else p["country"],
                    rng.choice([p["plan"], p["plan"].capitalize()]) if worst else p["plan"],
                ]
            )

    def money(o):
        if not worst:
            return f"{o['amount']:.2f}" if o["has_amount"] else ""
        if not o["has_amount"]:
            return rng.choice(["", "N/A"])
        return rng.choices([f"{o['amount']:.2f}", f"${o['amount']:,.2f}"], weights=[7, 3])[0]

    rows = []
    for o in clean_orders:
        status = o["status"]
        if worst:
            status = rng.choices([status, status.upper(), status.capitalize()], weights=[7, 1, 2])[0]
        rows.append([o["order_id"], o["customer_id"], money(o), status, o["at"].isoformat(sep=" ")])
    if worst:
        rows += [rng.choice(rows) for _ in range(int(orders * 0.03))]  # Exact duplicate rows.
    rng.shuffle(rows)
    with open(out / "orders.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "customer_id", "amount", "status", "ordered_at"])
        w.writerows(rows)
    return truth


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    print(json.dumps(generate(target, mess=sys.argv[2] if len(sys.argv) > 2 else "worst"), indent=2))
