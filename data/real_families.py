"""
Build the site's demo families from REAL households.

Source: Family Income and Expenditure Survey (FIES) of the Philippine Statistics Authority:
41,544 real households, published as open data (CC0 public domain) on OpenML:
https://www.openml.org/d/43443 . It is the open survey closest to our intake template: income,
every spending item, housing tenure and rent, utilities, children, electricity, water, the
household head's marital status (widows, divorced) and medical spending.

    python data/real_families.py        (downloads the file once if missing)
    -> data/raw/real_demo_families.csv   200 real families in the template's fields
    -> docs/real_demo_cleaning.json      every cleaning and mapping step, with counts

Nothing is invented per family. Each field comes from the same real household; the only
transformations are documented rules:
  - currency: amounts are scaled so the Philippine median household income equals the Saudi
    median (GASTAT HICES 2023: 13,655 SAR). Both medians are almost equal (factor ~1.0), so
    every family keeps its real position relative to a typical household in its country.
  - annual -> monthly (/ 12).
  - children aged 5-17 are split into Saudi school stages by the years each stage lasts.
  - chronic health: medical spending above 10% of the household budget (the WHO / SDG 3.8.2
    "catastrophic health spending" threshold).
  - applicants: households "at risk of poverty" (income per person below 60% of the Saudi median
    per person, the Eurostat definition), sampled at random (seed 42).
Not in the survey, so left empty (0): debts and installments, university students.
"""

import io
import json
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model.features import SAUDI_PER_CAPITA_INCOME  # noqa: E402
from model.schema import validate  # noqa: E402

SRC = ROOT / "data" / "external" / "fies_philippines" / "fies.arff"
DOWNLOAD = "https://openml.org/data/v1/download/22102268/Filipino-Family-Income-and-Expenditure.arff"
OUT = ROOT / "data" / "raw" / "real_demo_families.csv"
LOG = ROOT / "docs" / "real_demo_cleaning.json"
SAUDI_MEDIAN_HOUSEHOLD_INCOME = 13655   # SAR / month, GASTAT HICES 2023 (data/raw/gastat_hices_2023.csv)
N_DEMO, SEED = 200, 42
# "At risk of poverty": income per person below 60% of the national median (Eurostat definition).
AT_RISK_SHARE = 0.6

FOOD_PARTS = ["Bread_and_Cereals_Expenditure", "Total_Rice_Expenditure", "Meat_Expenditure",
              "Total_Fish_and__marine_products_Expenditure", "Fruit_Expenditure", "Vegetables_Expenditure"]
TENURE = {
    "Own or owner-like possession of house and lot": "owned",
    "Own house, rent-free lot with consent of owner": "owned",
    "Own house, rent lot": "owned",
    "Rent house/room including lot": "rented",
    "Rent-free house and lot with consent of owner": "shelter",      # housed for free by others
    "Rent-free house and lot without consent of owner": "shelter",   # informal
    "Own house, rent-free lot without consent of owner": "shelter",  # informal settlement
}
WATER = {
    "Own use, faucet, community water system": "yes", "Own use, tubed/piped deep well": "yes",
    "Shared, faucet, community water system": "intermittent", "Shared, tubed/piped deep well": "intermittent",
    "Tubed/piped shallow well": "intermittent", "Dug well": "intermittent", "Peddler": "intermittent",
    "Protected spring, river, stream, etc": "no", "Unprotected spring, river, stream, etc": "no",
    "Lake, river, rain and others": "no", "Others": "no",
}
# Saudi school stages by age: primary 6-11 (6 years), intermediate 12-14 (3), secondary 15-17 (3).
STAGE_YEARS = {"children_primary": 6, "children_intermediate": 3, "children_secondary": 3}
STAGE_SPAN = 13  # ages 5-17 in the survey; age 5 is kindergarten


def read_arff(path: Path) -> pd.DataFrame:
    head, data = path.read_text(encoding="utf-8").split("@DATA", 1)
    cols = [re.sub(r'^"|"$', "", line.split(" ", 1)[1].rsplit(" ", 1)[0])
            for line in head.splitlines() if line.startswith("@ATTRIBUTE")]
    return pd.read_csv(io.StringIO(data.strip()), header=None, names=cols, quotechar="'", na_values=["?"])


def split_stages(n: int) -> dict:
    """Largest-remainder split of n children aged 5-17 into school stages by stage length."""
    exact = {k: n * y / STAGE_SPAN for k, y in STAGE_YEARS.items()}
    out = {k: int(v) for k, v in exact.items()}
    left = int(round(n * sum(STAGE_YEARS.values()) / STAGE_SPAN)) - sum(out.values())
    for k in sorted(exact, key=lambda k: exact[k] - out[k], reverse=True)[:max(left, 0)]:
        out[k] += 1
    return out


def build(raw: pd.DataFrame):
    log = {"source_rows": len(raw)}
    d = raw.copy()
    d["source_row"] = d.index + 1
    n = len(d)
    d = d[d["Tenure_Status"].isin(TENURE)]
    log["tenure_not_applicable_dropped"] = n - len(d)
    n = len(d)
    d = d[d["Main_Source_of_Water_Supply"].isin(WATER)]
    log["water_unknown_dropped"] = n - len(d)

    php_median_month = raw["Total_Household_Income"].median() / 12
    factor = SAUDI_MEDIAN_HOUSEHOLD_INCOME / php_median_month
    log["philippine_median_household_income_month_php"] = round(php_median_month)
    log["saudi_median_household_income_month_sar"] = SAUDI_MEDIAN_HOUSEHOLD_INCOME
    log["currency_factor"] = round(factor, 4)
    money = lambda s: (s / 12 * factor).round()  # annual PHP -> monthly SAR-equivalent

    spend_cols = [c for c in raw.columns if c.endswith("Expenditure") and c not in FOOD_PARTS]
    housing = d["Housing_and_water_Expenditure"]
    owners_utilities = (housing - d["Imputed_House_Rental_Value"]).clip(lower=0)
    renter = d["Tenure_Status"].map(TENURE) == "rented"
    # Renters' housing spending = rent + utilities; their utilities are not split out, so we use
    # the median utilities of non-renting households of the same size.
    size_util = owners_utilities[~renter].groupby(d.loc[~renter, "Total_Number_of_Family_members"].clip(upper=10)).median()
    renter_util = d.loc[renter, "Total_Number_of_Family_members"].clip(upper=10).map(size_util)
    utilities = owners_utilities.where(~renter, renter_util.reindex(d.index))
    rent = (housing - utilities).where(renter, 0).clip(lower=0)
    basic = d[spend_cols].sum(axis=1) - housing
    log["renters_utilities_estimated_from_same_size_households"] = int(renter.sum())

    employed = d["Total_number_of_family_members_employed"]
    source = d["Main_Source_of_Income"].map({"Wage/Salaries": "stable", "Enterpreneurial Activities": "irregular"})
    source = source.fillna(pd.Series(np.where(employed > 0, "irregular", "none"), index=d.index))

    medical_share = d["Medical_Care_Expenditure"] / d[spend_cols].sum(axis=1)
    kids = d["Members_with_age_5_-_17_years_old"].apply(split_stages).apply(pd.Series)
    out = pd.DataFrame({
        "household_id": "PHL-" + d["source_row"].astype(str),
        "household_size": d["Total_Number_of_Family_members"],
        **{k: kids[k] for k in STAGE_YEARS},
        "children_university": 0,
        "monthly_income": money(d["Total_Household_Income"]),
        "income_source": source,
        "basic_expenses": money(basic),
        "housing_status": d["Tenure_Status"].map(TENURE),
        "monthly_rent": money(rent),
        "utility_bills": money(utilities),
        "total_debt": 0, "monthly_debt_payment": 0,
        "has_health_or_disability": np.where(medical_share > 0.10, "yes", "no"),
        "electricity_access": np.where(d["Electricity"] == 1, "yes", "no"),
        "water_access": d["Main_Source_of_Water_Supply"].map(WATER),
        "internet_access": np.where(d["Number_of_Personal_Computer"] > 0, "yes",
                                    np.where(d["Number_of_Cellular_phone"] > 0, "intermittent", "no")),
        "head_marital_status": d["Household_Head_Marital_Status"],
        "source_row": d["source_row"],
    }, index=d.index)

    # Families who might apply to a charity: at risk of poverty, i.e. income per person below 60%
    # of the Saudi median per person (2,896 SAR, GASTAT HICES 2023).
    per_capita = out["monthly_income"] / out["household_size"]
    pool = out[per_capita < AT_RISK_SHARE * SAUDI_PER_CAPITA_INCOME]
    log["households_mapped"] = len(out)
    log["at_risk_of_poverty_line_sar_per_person"] = round(AT_RISK_SHARE * SAUDI_PER_CAPITA_INCOME)
    log["applicant_pool_at_risk_of_poverty"] = len(pool)

    rows, rejected = [], 0
    for rec in pool.sample(frac=1, random_state=SEED).to_dict("records"):
        try:
            validate({k: v for k, v in rec.items() if k not in ("household_id", "head_marital_status", "source_row")})
        except Exception:
            rejected += 1
            continue
        rows.append(rec)
        if len(rows) == N_DEMO:
            break
    log["failed_template_validation_skipped"] = rejected
    demo = pd.DataFrame(rows)
    log["demo_families"] = len(demo)
    log["demo_profile"] = {
        "widowed_or_divorced_head": int(demo["head_marital_status"].isin(["Widowed", "Divorced/Separated"]).sum()),
        "rented": int((demo["housing_status"] == "rented").sum()),
        "shelter": int((demo["housing_status"] == "shelter").sum()),
        "no_electricity": int((demo["electricity_access"] == "no").sum()),
        "chronic_health_spending": int((demo["has_health_or_disability"] == "yes").sum()),
        "median_income_sar": float(demo["monthly_income"].median()),
        "median_household_size": float(demo["household_size"].median()),
    }
    log["not_in_survey_left_empty"] = ["total_debt", "monthly_debt_payment", "children_university"]
    return demo, log


def main() -> None:
    if not SRC.exists():
        SRC.parent.mkdir(parents=True, exist_ok=True)
        print("downloading", DOWNLOAD)
        urllib.request.urlretrieve(DOWNLOAD, SRC)
    demo, log = build(read_arff(SRC))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    demo.to_csv(OUT, index=False)
    LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
