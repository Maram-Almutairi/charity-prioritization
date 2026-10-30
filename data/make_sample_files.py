"""
Build sample Excel files from REAL households (data/raw/real_demo_families.csv, built by
data/real_families.py from the open FIES survey), in the same layout as the site's template.

- data/samples/sample_families.xlsx : 40 clean families, ready to drag into the site.
- data/samples/sample_messy.xlsx    : the same kind of sheet with the mistakes staff really
  make (Arabic-Indic digits, "ريال", spelling variants, blank rows, a duplicate code, an
  impossible row). It demonstrates and tests the cleaning pipeline (model/cleaning.py).

Usage:
    python data/make_sample_files.py
"""

import io
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from excel_io import DATA_SHEET, build_template  # noqa: E402
from model.schema import CHOICES, FIELDS  # noqa: E402

OUT = ROOT / "data" / "samples"
ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def as_sheet_rows(df: pd.DataFrame) -> list[list]:
    rows = []
    for i, rec in enumerate(df.to_dict("records")):
        row = []
        for f in FIELDS:
            if f.name == "ref_code":
                row.append(rec["household_id"])
            elif f.kind == "choice":
                row.append(CHOICES[f.name][rec[f.name]])
            else:
                value = rec[f.name]
                row.append("" if f.name == "monthly_rent" and value == 0 else int(value))
        rows.append(row)
    return rows


def write(rows: list[list], path: Path) -> None:
    wb = load_workbook(io.BytesIO(build_template()))
    ws = wb[DATA_SHEET]
    for r, row in enumerate(rows, start=2):
        for c, value in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=value)
    wb.save(path)


def make_messy(rows: list[list]) -> list[list]:
    """Inject realistic data-entry problems; each one is documented in the EDA notebook."""
    col = {f.name: i for i, f in enumerate(FIELDS)}
    m = [list(r) for r in rows[:30]]
    m[0][col["monthly_income"]] = str(m[0][col["monthly_income"]]).translate(ARABIC_DIGITS) + " ريال"
    m[1][col["basic_expenses"]] = f"{m[1][col['basic_expenses']]:,}"            # "4,300"
    m[2][col["housing_status"]] = "ايجار" if m[2][col["housing_status"]] == "إيجار" else m[2][col["housing_status"]]
    m[3][col["has_health_or_disability"]] = " نعم ✓"
    m[4][col["income_source"]] = "راتب"                                         # synonym of ثابت
    m[5][col["utility_bills"]] = ""                                             # blank -> 0
    m[6][col["electricity_access"]] = "مفصول احيانا"                            # -> متقطع
    m[7][col["ref_code"]] = m[6][col["ref_code"]]                               # duplicate code
    m[8][col["household_size"]] = 2                                             # students > size
    m[8][col["children_primary"]] = 3
    m[9][col["monthly_income"]] = "غير معروف"                                    # not a number
    m[10][col["housing_status"]] = "خيمة"                                        # unknown value
    m[11][col["monthly_income"]] = 95000                                         # outlier -> review
    m[12][col["ref_code"]] = ""                                                  # missing code
    m[13] = [""] * len(FIELDS)                                                   # blank row
    m[14][col["total_debt"]] = "١٢٬٠٠٠"
    return m


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(ROOT / "data" / "raw" / "real_demo_families.csv").sample(40, random_state=11)
    rows = as_sheet_rows(df)
    write(rows, OUT / "sample_families.xlsx")
    write(make_messy(rows), OUT / "sample_messy.xlsx")
    print("wrote", OUT / "sample_families.xlsx", "and", OUT / "sample_messy.xlsx")


if __name__ == "__main__":
    main()
