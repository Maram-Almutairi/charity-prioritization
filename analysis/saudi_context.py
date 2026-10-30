"""
Saudi context from the official 2023 household survey (real, aggregated data).

Source: GASTAT Household Income and Consumption Expenditure Survey 2023, published as open data
(public domain) on the KAPSARC data portal:
https://data.kapsarc.org/explore/dataset/household-income-and-consumption-expenditure-survey/
The file is kept in the repo at data/raw/gastat_hices_2023.csv (1,592 rows).

    python analysis/saudi_context.py   ->  docs/saudi_context.json, docs/figures/saudi_*.png

It answers: how far below the typical Saudi household are the families our charities serve
(widows, divorcees, families housed by charities, large families, poorer regions)?
"""

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model import plotting  # noqa: E402
from model.features import SAUDI_PER_CAPITA_INCOME  # noqa: E402

SRC = ROOT / "data" / "raw" / "gastat_hices_2023.csv"
OUT = ROOT / "docs" / "saudi_context.json"
FIG = ROOT / "docs" / "figures"
URL = "https://data.kapsarc.org/explore/dataset/household-income-and-consumption-expenditure-survey/"
DIMS = ["age_group", "region", "educational_level", "marital_status", "type_of_dwelling",
        "type_of_ownership", "household_size", "income_sources", "coicop_divisions"]
INC, EXP = "Average Household Monthly Disposable Income", "Average Household Monthly Consumption Expenditure"
REGION_AR = {"Riyadh": "الرياض", "Makkah": "مكة المكرمة", "Madinah": "المدينة المنورة", "Qassim": "القصيم",
             "Eastern Province": "الشرقية", "Asir": "عسير", "Tabuk": "تبوك", "Hail": "حائل",
             "Northern Border": "الحدود الشمالية", "Jazan": "جازان", "Najran": "نجران", "Al-Baha": "الباحة",
             "Al-Jouf": "الجوف"}


def load() -> pd.DataFrame:
    d = pd.read_csv(SRC)
    return d[d["nationality_of_household_head"] == "Saudi"]


def by(d: pd.DataFrame, dim: str, indicator: str) -> pd.Series:
    """Values of one indicator broken down by one dimension only (all other dimensions empty)."""
    others = [c for c in DIMS if c != dim]
    sub = d[d[dim].notna() & d[others].isna().all(axis=1) & (d["indicator"] == indicator)]
    return sub.set_index(dim)["value"]


def overall(d: pd.DataFrame, indicator: str) -> float:
    sub = d[d[DIMS].isna().all(axis=1) & (d["indicator"] == indicator)]
    return float(sub["value"].iloc[0])


def main() -> None:
    d = load()
    inc_status, inc_own = by(d, "marital_status", INC), by(d, "type_of_ownership", INC)
    inc_size, inc_region = by(d, "household_size", INC), by(d, "region", INC)
    housing_share = by(d, "coicop_divisions", "Percentage of Household Monthly Final Monetary Consumption Expenditure")
    total = overall(d, INC)
    groups = {
        "all_saudi": total,
        "widowed_head": float(inc_status["Widowed"]), "divorced_head": float(inc_status["Divorced"]),
        "housed_by_charity": float(inc_own["Provided for free by charities"]),
        "housed_by_philanthropist": float(inc_own["Provided for free by philanthropist"]),
        "rented": float(inc_own["Rented"]),
    }
    result = {
        "source": {"name": "GASTAT Household Income and Consumption Expenditure Survey 2023 (KAPSARC open data)",
                   "url": URL, "license": "Public Domain", "rows": int(len(pd.read_csv(SRC)))},
        "saudi_households": {
            "avg_disposable_income": total, "median_disposable_income": overall(d, "Median Household Monthly Disposable Income"),
            "avg_per_capita_income": overall(d, "Average Per-capita Monthly Disposable Income"),
            "median_per_capita_income": overall(d, "Median Per-capita Monthly Disposable Income"),
            "avg_consumption_expenditure": overall(d, "Average Household Monthly Consumption Expenditure"),
            "housing_utilities_share_pct": float(housing_share["Housing, water, electricity, gas and other fuels"]),
        },
        "income_by_group": groups,
        "gap_vs_all_pct": {k: round(100 * (1 - v / total), 1) for k, v in groups.items() if k != "all_saudi"},
        "income_by_household_size": {k: float(v) for k, v in inc_size.items() if k != "Total"},
        "income_by_region": {REGION_AR[k]: float(v) for k, v in inc_region.sort_values().items() if k != "Total"},
        "per_capita_reference_used_by_model": SAUDI_PER_CAPITA_INCOME,
    }
    assert result["saudi_households"]["median_per_capita_income"] == SAUDI_PER_CAPITA_INCOME
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    figures(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def figures(r: dict) -> None:
    import matplotlib.pyplot as plt
    plotting.setup()
    FIG.mkdir(parents=True, exist_ok=True)
    g = r["income_by_group"]
    items = [("كل الأسر السعودية", g["all_saudi"], plotting.INK2), ("مستأجرة", g["rented"], plotting.NEED),
             ("ربّ الأسرة مطلّق", g["divorced_head"], plotting.PREVENT), ("ربّ الأسرة أرمل", g["widowed_head"], plotting.PREVENT),
             ("مسكن من جمعية خيرية", g["housed_by_charity"], plotting.RISK),
             ("مسكن من محسن", g["housed_by_philanthropist"], plotting.RISK)]
    fig, ax = plt.subplots(figsize=(6.6, 3.4))
    ax.barh([i[0] for i in items][::-1], [i[1] for i in items][::-1], color=[i[2] for i in items][::-1])
    for y, (_, v, _) in enumerate(items[::-1]):
        ax.text(v + 200, y, f"{v:,.0f}", va="center", color=plotting.INK, fontsize=9)
    ax.set_xlim(0, g["all_saudi"] * 1.18)
    ax.set_title("بيانات سعودية رسمية 2023: متوسط الدخل الشهري المتاح للأسرة (ريال)")
    fig.savefig(FIG / "saudi_income_by_group.png"); plt.close(fig)

    reg = r["income_by_region"]
    fig, ax = plt.subplots(figsize=(6.6, 3.8))
    ax.barh(list(reg), list(reg.values()), color=plotting.NEED)
    ax.axvline(g["all_saudi"], color=plotting.INK2, ls="--", lw=1)
    ax.text(g["all_saudi"] + 200, 0, "متوسط المملكة", color=plotting.INK2, fontsize=9)
    ax.set_title("بيانات سعودية رسمية 2023: متوسط دخل الأسرة السعودية حسب المنطقة (ريال)")
    fig.savefig(FIG / "saudi_income_by_region.png"); plt.close(fig)


if __name__ == "__main__":
    main()
