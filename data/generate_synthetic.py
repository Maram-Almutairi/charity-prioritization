"""
Generate a documented synthetic dataset of Saudi households for the charity prioritization prototype.

Purpose: help Saudi charities spot families in need *and* families at risk of a crisis
(service cut-off, eviction, debt default) early, so support arrives before things break.

Why synthetic: no open household-level dataset covers the intake fields for Saudi families
(GASTAT microdata is confidential; see data/README.md). Distributions are *calibrated* to
published Saudi aggregates, never copied from real records.

Target population: families that approach a charity (applicants), NOT all Saudi households,
so incomes are deliberately lower than the national average.

Calibration anchors (all amounts in SAR / month):
- Saudi household size ~4.8 (GASTAT Census 2022).
- Saudi household income avg 14,823; per-capita 2,741 (GASTAT HIES 2018).
- Saudi home ownership 65.4% nationally (Housing Program 2024); lower for applicants.
- Social security reference: ~1,320 head + ~660 per dependent (unofficial sources, verify).
- Debt installments capped at 33.33% of salary for employees (SAMA responsible lending).

Labels
------
Need and Risk scores come from model/scoring.py (fixed, documented weights).
priority = 0.5 * need + 0.5 * risk + noise, cut into high / medium / low (25/40/35%).

Usage:
    python data/generate_synthetic.py --n 3000 --seed 42
Output:
    data/raw/synthetic_households.csv
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model.scoring import combined_score  # noqa: E402  (single source of truth for weights)

OUT_PATH = Path(__file__).parent / "raw" / "synthetic_households.csv"

INCOME_BY_SOURCE = {               # (mean, std) SAR / month for charity applicants
    "stable": (7500, 2500),        # government/private salary or retirement pension
    "irregular": (3500, 1500),     # daily work, freelance, small trade, delivery apps
}
SOCIAL_SECURITY_HEAD = 1320        # "none": the family relies on social security only
SOCIAL_SECURITY_DEPENDENT = 660

# Stage of each child (ages: <6, 6-11, 12-14, 15-17, 18-22). Shares follow the length of
# each stage with a lower university enrolment rate.
CHILD_STAGES = ["under_school", "children_primary", "children_intermediate",
                "children_secondary", "children_university"]
CHILD_STAGE_P = [0.22, 0.33, 0.17, 0.15, 0.13]

SERVICE_LEVELS = ["yes", "intermittent", "no"]
LABEL_CUTS = {"high": 0.25, "medium": 0.40}  # remaining 0.35 -> low


def sample_households(n: int, rng: np.random.Generator) -> pd.DataFrame:
    household_size = np.clip(rng.poisson(5.2, n), 1, 15)

    # Children: at least one adult; each child is assigned a stage.
    n_children = np.minimum(rng.binomial(household_size, 0.45), household_size - 1)
    stages = np.zeros((n, len(CHILD_STAGES)), dtype=int)
    for i, k in enumerate(n_children):
        if k:
            stages[i] = rng.multinomial(k, CHILD_STAGE_P)

    income_source = rng.choice(["stable", "irregular", "none"], n, p=[0.45, 0.35, 0.20])
    income = np.empty(n)
    for src, (mu, sd) in INCOME_BY_SOURCE.items():
        mask = income_source == src
        income[mask] = rng.normal(mu, sd, mask.sum())
    none = income_source == "none"
    income[none] = rng.uniform(0.4, 1.0, none.sum()) * (
        SOCIAL_SECURITY_HEAD + SOCIAL_SECURITY_DEPENDENT * (household_size[none] - 1))
    income = np.clip(income, 0, None).round(-1)

    housing_status = rng.choice(["owned", "rented", "shelter"], n, p=[0.40, 0.50, 0.10])
    rent = np.where(housing_status == "rented", np.clip(rng.normal(1800, 500, n), 800, None), 0)

    # Living costs (food, transport, school supplies) scale with size; bills are separate.
    basic_expenses = np.clip(1100 + household_size * rng.normal(650, 120, n), 800, None).round(-1)
    internet_access_draw = rng.random(n)
    utility_bills = np.clip(250 + 45 * household_size + rng.normal(0, 90, n), 120, None)
    utility_bills = (utility_bills + np.where(internet_access_draw < 0.85, 230, 0)).round(-1)

    # Debt: more likely when living costs already exceed income. Salaried families carry
    # bank loans near the SAMA cap; others carry smaller informal debts and arrears.
    pre_debt_gap = basic_expenses + rent + utility_bills > income
    has_debt = rng.random(n) < np.where(pre_debt_gap, 0.65, 0.35)
    share = np.where(income_source == "stable", rng.uniform(0.12, 0.33, n), rng.uniform(0.05, 0.25, n))
    installment = np.where(has_debt, np.maximum(income * share, rng.uniform(150, 600, n)), 0)
    months_left = rng.integers(10, 60, n)
    total_debt = np.where(has_debt, installment * months_left, 0)
    installment, total_debt = installment.round(-1), total_debt.round(-2)

    has_health = rng.random(n) < 0.20
    gap = basic_expenses + rent + utility_bills + installment > income

    # Grid electricity/water are near-universal in Saudi Arabia; "intermittent" mostly means
    # disconnection for unpaid bills, so it correlates with the financial gap.
    def services(p_ok_base, p_mid_base, gap_shift):
        p_ok = np.where(gap, p_ok_base - gap_shift, p_ok_base)
        p_no = np.full(n, 1 - p_ok_base - p_mid_base)
        probs = np.column_stack([p_ok, 1 - p_ok - p_no, p_no])
        return np.array([rng.choice(SERVICE_LEVELS, p=row) for row in probs])

    internet = np.where(internet_access_draw < 0.85, "yes",
                        np.where(internet_access_draw < 0.95, "intermittent", "no"))

    return pd.DataFrame({
        "household_id": [f"SYN-{i:05d}" for i in range(n)],
        "household_size": household_size,
        "children_primary": stages[:, 1],
        "children_intermediate": stages[:, 2],
        "children_secondary": stages[:, 3],
        "children_university": stages[:, 4],
        "monthly_income": income,
        "income_source": income_source,
        "basic_expenses": basic_expenses,
        "housing_status": housing_status,
        "monthly_rent": rent.round(-1),
        "utility_bills": utility_bills,
        "total_debt": total_debt,
        "monthly_debt_payment": installment,
        "has_health_or_disability": np.where(has_health, "yes", "no"),
        "electricity_access": services(0.95, 0.04, 0.15),
        "water_access": services(0.93, 0.05, 0.12),
        "internet_access": internet,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic Saudi household data.")
    parser.add_argument("--n", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--noise", type=float, default=0.04, help="label noise std")
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    df = sample_households(args.n, rng)

    combined = combined_score(df) / 100 + rng.normal(0, args.noise, len(df))
    hi_cut = combined.quantile(1 - LABEL_CUTS["high"])
    lo_cut = combined.quantile(1 - LABEL_CUTS["high"] - LABEL_CUTS["medium"])
    df["priority_label"] = np.select([combined >= hi_cut, combined >= lo_cut],
                                     ["high", "medium"], "low")
    df["data_source"] = "synthetic"

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(df)} rows -> {OUT_PATH}")
    print(df.priority_label.value_counts(normalize=True).round(3).to_string())


if __name__ == "__main__":
    main()
