"""
Impact simulation on the synthetic population (3,000 families): what changes when a charity
ranks by Need + Risk instead of by need alone, and how far a monthly budget goes.

    python docs/impact_simulation.py      -> docs/impact.json (numbers quoted in the report)

Synthetic data: these are properties of the method, not observed outcomes for real families.
"""

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from model.features import add_engineered  # noqa: E402
from model.schema import INPUT_FIELDS  # noqa: E402
from services import Assessor, allocate  # noqa: E402


def main() -> None:
    df = pd.read_csv(ROOT / "data" / "raw" / "synthetic_households.csv")
    families = [{k: r[k] for k in INPUT_FIELDS if k in r} | {"ref_code": r["household_id"]}
                for r in df.to_dict("records")]
    scored = pd.DataFrame(Assessor().assess_many(families))
    e = add_engineered(df)
    n, k = len(df), len(df) // 4                      # capacity: support a quarter of applicants

    groups = {
        "prevent_quadrant": (scored.need_score < 50) & (scored.risk_score >= 50),
        "service_cutoff_warning": e.cutoff_warning == 1,
        "installment_above_sama_cap": e.debt_service_ratio > 1 / 3,
    }
    need_only = scored.sort_values("need_score", ascending=False).head(k).index
    model_high = scored.index[scored.priority == "high"]
    reach = {name: {"total": int(m.sum()),
                    "need_only_top_quarter": int(m[need_only].sum()),
                    "model_high_priority": int(m[model_high].sum())} for name, m in groups.items()}

    rows = [{**families[i], **scored.iloc[i].to_dict(), "id": i} for i in range(n)]
    budgets = {}
    for budget in (500_000, 1_000_000, 3_000_000):
        a = allocate(rows, budget)
        budgets[str(budget)] = {k2: a[k2] for k2 in ("allocated_total", "total_target", "coverage", "uncovered")} | {
            "by_tier": a["by_tier"],
            "families_fully_covered": sum(r["allocated"] >= r["target"] > 0 for r in a["rows"])}

    out = {
        "families": n, "capacity_top_quarter": k,
        "quadrants": scored.quadrant.value_counts().to_dict(),
        "priority_counts": scored.priority.value_counts().to_dict(),
        "needs_review": int(scored.needs_review.sum()),
        "total_monthly_deficit": round(float(scored.monthly_deficit.sum())),
        "reach": reach,
        "budgets": budgets,
    }
    path = ROOT / "docs" / "impact.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
