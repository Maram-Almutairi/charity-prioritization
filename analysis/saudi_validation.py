"""
Validate the method against official SAUDI data only.

There is no open Saudi household-level dataset with a known priority (GASTAT microdata is
confidential), so accuracy on real Saudi families can only be measured in a pilot with a charity.
What we can check with official Saudi data:

  1. Context: incomes of the households our charities serve (widowed and divorced heads,
     households housed by charities or philanthropists, poorer regions).
     GASTAT Household Income and Consumption Expenditure Survey 2023 (open data, KAPSARC):
     https://data.kapsarc.org/explore/dataset/household-income-and-consumption-expenditure-survey/
  2. Consistency test: build one "average household" for every official group (marital status of
     the head, type of housing, region) from the survey's own averages, score it with the tool's
     Need score, and check that the groups the survey shows as poorest get the highest need.
  3. Calibration checks of the synthetic training households against Saudi references:
     - King Khalid Foundation sufficiency line, 8,926 SAR / month for a Saudi family (2014):
       https://www.aleqt.com/2014/04/12/article_840564.html
     - GASTAT share of household income spent on electricity and fuel, by range (2019, KAPSARC):
       https://data.kapsarc.org/explore/dataset/percentage-of-annual-costs-of-electricity-and-other-fuel-to-total-annual-househo/

    python analysis/saudi_validation.py  ->  docs/saudi_validation.json, docs/figures/saudi_*.png
"""

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model import plotting  # noqa: E402
from model.features import SAUDI_PER_CAPITA_INCOME  # noqa: E402
from model.scoring import need_score  # noqa: E402

SRC = ROOT / "data" / "raw" / "gastat_hices_2023.csv"
ELEC = ROOT / "data" / "raw" / "gastat_electricity_cost_share.csv"
SYN = ROOT / "data" / "raw" / "synthetic_households.csv"
OUT = ROOT / "docs" / "saudi_validation.json"
FIG = ROOT / "docs" / "figures"
URL = "https://data.kapsarc.org/explore/dataset/household-income-and-consumption-expenditure-survey/"
SUFFICIENCY_LINE = 8926   # SAR / month, King Khalid Foundation (2014)
DIMS = ["age_group", "region", "educational_level", "marital_status", "type_of_dwelling",
        "type_of_ownership", "household_size", "income_sources", "coicop_divisions"]
INC = "Average Household Monthly Disposable Income"
EXP = "Average Household Monthly Final Monetary Consumption Expenditure"  # cash spending, incl. rent paid
PC = "Average Per-capita Monthly Disposable Income"
REGION_AR = {"Riyadh": "الرياض", "Makkah": "مكة المكرمة", "Madinah": "المدينة المنورة", "Qassim": "القصيم",
             "Eastern Province": "الشرقية", "Asir": "عسير", "Tabuk": "تبوك", "Hail": "حائل",
             "Northern Border": "الحدود الشمالية", "Jazan": "جازان", "Najran": "نجران", "Al-Baha": "الباحة",
             "Al-Jouf": "الجوف"}
STATUS_AR = {"Widowed": "ربّ الأسرة أرمل", "Divorced": "ربّ الأسرة مطلّق", "Married": "ربّ الأسرة متزوج",
             "Never Married": "ربّ الأسرة لم يتزوج"}
OWNERSHIP = {  # official tenure -> (Arabic label, the tool's housing status)
    "Owned without mortgage or loan": ("مسكن ملك بلا قرض", "owned"),
    "Owned with mortgage or loan": ("مسكن ملك بقرض", "owned"),
    "Rented": ("مسكن مستأجر", "rented"),
    "Provided for free by charities": ("مسكن من جمعية خيرية", "shelter"),
    "Provided for free by philanthropist": ("مسكن من محسن", "shelter"),
    "Provided for free by a household  member": ("مسكن من أحد الأقارب", "shelter"),
    "Provided for free by government employer": ("سكن من جهة العمل الحكومية", "owned"),
    "Provided for free by private employer": ("سكن من جهة العمل الخاصة", "owned"),
}


def load() -> pd.DataFrame:
    d = pd.read_csv(SRC)
    return d[d["nationality_of_household_head"] == "Saudi"]


def by(d: pd.DataFrame, dim: str, indicator: str) -> pd.Series:
    """One indicator broken down by one dimension only (all other dimensions empty)."""
    others = [c for c in DIMS if c != dim]
    sub = d[d[dim].notna() & d[others].isna().all(axis=1) & (d["indicator"] == indicator)]
    return sub.set_index(dim)["value"]


def overall(d: pd.DataFrame, indicator: str) -> float:
    sub = d[d[DIMS].isna().all(axis=1) & (d["indicator"] == indicator)]
    return float(sub["value"].iloc[0])


def group_households(d: pd.DataFrame) -> pd.DataFrame:
    """One average household per official group, in the tool's template fields."""
    avg_size = overall(d, INC) / overall(d, PC)
    rows = []
    for dim, labels in [("marital_status", STATUS_AR), ("type_of_ownership", OWNERSHIP), ("region", REGION_AR)]:
        inc, exp = by(d, dim, INC), by(d, dim, EXP)
        pc = by(d, dim, "Average Per-Capita Monthly Disposable Income") if dim == "region" else None
        for key, label in labels.items():
            name, housing = label if isinstance(label, tuple) else (label, "owned")
            size = inc[key] / pc[key] if pc is not None else avg_size
            rows.append({"dimension": dim, "group": name, "official_income": float(inc[key]),
                         "household_size": size, "monthly_income": float(inc[key]),
                         "basic_expenses": float(exp[key]), "housing_status": housing})
    g = pd.DataFrame(rows)
    zeros = ["children_primary", "children_intermediate", "children_secondary", "children_university",
             "monthly_rent", "utility_bills", "total_debt", "monthly_debt_payment"]
    tool = g.assign(**{c: 0 for c in zeros}, income_source="stable", has_health_or_disability="no",
                    electricity_access="yes", water_access="yes", internet_access="yes")
    g["need_score"] = need_score(tool).round(1)
    g["avg_size"] = round(avg_size, 2)
    return g


def main() -> None:
    d = load()
    total = overall(d, INC)
    inc_status, inc_own = by(d, "marital_status", INC), by(d, "type_of_ownership", INC)
    groups = {"all_saudi": total, "widowed_head": float(inc_status["Widowed"]), "divorced_head": float(inc_status["Divorced"]),
              "housed_by_charity": float(inc_own["Provided for free by charities"]),
              "housed_by_philanthropist": float(inc_own["Provided for free by philanthropist"]),
              "rented": float(inc_own["Rented"])}

    g = group_households(d)
    rho = float(g["need_score"].corr(g["official_income"], method="spearman"))
    top = g.nlargest(5, "need_score")[["group", "official_income", "need_score"]]

    syn = pd.read_csv(SYN)
    elec = pd.read_csv(ELEC)
    k = elec[(elec["administrative_region"] == "Total of the Kingdom") & (elec["time_period"] == elec["time_period"].max())]
    saudi_over_15 = float(k[k["percentage_range"].isin(["From 16 -20%", "More than 20%"])]["percentage_of_households"].sum())
    bills_share = syn["utility_bills"] / syn["monthly_income"].where(syn["monthly_income"] > 0)

    result = {
        "source": {"hices_2023": URL, "license": "Public Domain", "rows": int(len(pd.read_csv(SRC)))},
        "saudi_households": {
            "avg_disposable_income": total, "median_disposable_income": overall(d, "Median Household Monthly Disposable Income"),
            "avg_per_capita_income": overall(d, "Average Per-capita Monthly Disposable Income"),
            "median_per_capita_income": overall(d, "Median Per-capita Monthly Disposable Income"),
            "avg_consumption_expenditure": overall(d, "Average Household Monthly Consumption Expenditure"),
        },
        "income_by_group": groups,
        "gap_vs_all_pct": {k_: round(100 * (1 - v / total), 1) for k_, v in groups.items() if k_ != "all_saudi"},
        "income_by_region": {REGION_AR[k_]: float(v) for k_, v in by(d, "region", INC).sort_values().items() if k_ != "Total"},
        "consistency_test": {
            "groups": len(g), "spearman_need_vs_income": round(rho, 3),
            "top5_need": top.to_dict("records"),
            "table": g[["dimension", "group", "official_income", "need_score"]].to_dict("records"),
        },
        "calibration": {
            "sufficiency_line_sar": SUFFICIENCY_LINE,
            "synthetic_share_below_sufficiency_line": round(float((syn["monthly_income"] < SUFFICIENCY_LINE).mean()), 3),
            "saudi_households_avg_income_vs_line": round(total / SUFFICIENCY_LINE, 2),
            "electricity_over_15pct_of_income_saudi_2019": round(saudi_over_15, 1),
            "bills_over_15pct_of_income_synthetic": round(float((bills_share > 0.15).mean() * 100), 1),
        },
        "per_capita_reference_used_by_model": SAUDI_PER_CAPITA_INCOME,
    }
    assert result["saudi_households"]["median_per_capita_income"] == SAUDI_PER_CAPITA_INCOME
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    figures(result, g)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=float))


def figures(r: dict, g: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt
    plotting.setup()
    FIG.mkdir(parents=True, exist_ok=True)
    gr = r["income_by_group"]
    items = [("كل الأسر السعودية", gr["all_saudi"], plotting.INK2), ("مستأجرة", gr["rented"], plotting.NEED),
             ("ربّ الأسرة مطلّق", gr["divorced_head"], plotting.PREVENT), ("ربّ الأسرة أرمل", gr["widowed_head"], plotting.PREVENT),
             ("مسكن من جمعية خيرية", gr["housed_by_charity"], plotting.RISK),
             ("مسكن من محسن", gr["housed_by_philanthropist"], plotting.RISK)]
    fig, ax = plt.subplots(figsize=(6.6, 3.4))
    ax.barh([i[0] for i in items][::-1], [i[1] for i in items][::-1], color=[i[2] for i in items][::-1])
    for y, (_, v, _) in enumerate(items[::-1]):
        ax.text(v + 200, y, f"{v:,.0f}", va="center", color=plotting.INK, fontsize=9)
    ax.set_xlim(0, gr["all_saudi"] * 1.18)
    ax.set_title("بيانات سعودية رسمية 2023: متوسط الدخل الشهري المتاح للأسرة (ريال)")
    fig.savefig(FIG / "saudi_income_by_group.png"); plt.close(fig)

    reg = r["income_by_region"]
    fig, ax = plt.subplots(figsize=(6.6, 3.8))
    ax.barh(list(reg), list(reg.values()), color=plotting.NEED)
    ax.axvline(gr["all_saudi"], color=plotting.INK2, ls="--", lw=1)
    ax.text(gr["all_saudi"] + 200, 0, "متوسط المملكة", color=plotting.INK2, fontsize=9)
    ax.set_title("بيانات سعودية رسمية 2023: متوسط دخل الأسرة السعودية حسب المنطقة (ريال)")
    fig.savefig(FIG / "saudi_income_by_region.png"); plt.close(fig)

    colors = {"marital_status": plotting.PREVENT, "type_of_ownership": plotting.RISK, "region": plotting.NEED}
    names = {"marital_status": "الحالة الاجتماعية لربّ الأسرة", "type_of_ownership": "نوع السكن", "region": "المنطقة"}
    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    for dim, sub in g.groupby("dimension"):
        ax.scatter(sub["official_income"], sub["need_score"], s=36, color=colors[dim], label=names[dim], alpha=.9)
    offsets = [(8, 2), (8, -12), (8, 2), (8, 2)]
    for off, (_, row) in zip(offsets, g.nlargest(4, "need_score").sort_values("official_income").iterrows()):
        ax.annotate(row["group"], (row["official_income"], row["need_score"]), textcoords="offset points",
                    xytext=off, fontsize=8, color=plotting.INK)
    ax.set_xlabel("متوسط دخل الأسرة في الفئة حسب المسح الرسمي (ريال)")
    ax.set_ylabel("درجة الحاجة في أداتنا")
    ax.set_title("بيانات سعودية رسمية: الفئات الأفقر في المسح تأخذ أعلى درجة حاجة")
    ax.legend(fontsize=8, loc="upper right")
    fig.savefig(FIG / "saudi_group_need.png"); plt.close(fig)


if __name__ == "__main__":
    main()
