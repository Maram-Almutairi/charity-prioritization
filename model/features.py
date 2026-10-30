"""
Shared feature engineering for EDA, training and the API.

Keeping one implementation guarantees the model sees identical features at
training time and at prediction time (no train/serve skew).
"""

import numpy as np
import pandas as pd

from model.schema import EDUCATION_WEIGHTS

# Median per-capita monthly disposable income of Saudi households, SAR (GASTAT HICES 2023,
# data/raw/gastat_hices_2023.csv). The median is used so a few very rich households do not move it.
SAUDI_PER_CAPITA_INCOME = 2896
SAMA_DEBT_BURDEN_CAP = 1 / 3     # SAMA responsible lending: installments <= 33.33% of salary

ORDINAL_MAPS = {
    "housing_status": {"owned": 0, "rented": 1, "shelter": 2},
    "income_source": {"stable": 0, "irregular": 1, "none": 2},
    "has_health_or_disability": {"no": 0, "yes": 1},
    "electricity_access": {"yes": 0, "intermittent": 1, "no": 2},
    "water_access": {"yes": 0, "intermittent": 1, "no": 2},
    "internet_access": {"yes": 0, "intermittent": 1, "no": 2},
}


def _safe_ratio(num: pd.Series, den: pd.Series, when_zero: float) -> pd.Series:
    """num / den, with a fixed value where den is 0 and num > 0 (e.g. debt with no income)."""
    out = num / den.where(den > 0)
    return out.fillna(pd.Series(np.where(num > 0, when_zero, 0.0), index=num.index))


def add_engineered(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with derived ratios that are easier to interpret than raw amounts."""
    out = df.copy()
    out["total_expenses"] = (out.basic_expenses + out.monthly_rent + out.utility_bills
                             + out.monthly_debt_payment)
    out["per_capita_income"] = out.monthly_income / out.household_size
    out["income_to_saudi_avg"] = out.per_capita_income / SAUDI_PER_CAPITA_INCOME
    out["surplus_ratio"] = (out.monthly_income - out.total_expenses) / out.total_expenses
    out["housing_cost_burden"] = _safe_ratio(out.monthly_rent + out.utility_bills,
                                             out.monthly_income, when_zero=2.0).clip(upper=2.0)
    out["debt_service_ratio"] = _safe_ratio(out.monthly_debt_payment, out.monthly_income,
                                            when_zero=1.0).clip(upper=1.5)
    out["debt_to_annual_income"] = _safe_ratio(out.total_debt, out.monthly_income * 12,
                                               when_zero=3.0).clip(upper=3.0)
    out["education_load"] = sum(out[k] * w for k, w in EDUCATION_WEIGHTS.items())
    out["students"] = sum(out[k] for k in EDUCATION_WEIGHTS)
    out["has_debt"] = (out.total_debt > 0).astype(int)
    out["cutoff_warning"] = ((out.electricity_access == "intermittent")
                             | (out.water_access == "intermittent")).astype(int)
    return out


def encode(df: pd.DataFrame) -> pd.DataFrame:
    """Ordinal-encode categoricals (order is meaningful: higher = worse)."""
    out = df.copy()
    for col, mapping in ORDINAL_MAPS.items():
        out[col] = out[col].map(mapping)
    return out


# Chosen in notebooks/eda.ipynb (feature selection section).
SELECTED = ["income_to_saudi_avg", "surplus_ratio", "housing_status", "housing_cost_burden",
            "education_load", "household_size", "income_source", "debt_service_ratio",
            "debt_to_annual_income", "has_health_or_disability", "cutoff_warning",
            "internet_access"]

FEATURE_LABELS_AR = {
    "income_to_saudi_avg": "دخل الفرد مقارنة بالوسيط السعودي",
    "surplus_ratio": "الفائض أو العجز الشهري",
    "housing_status": "الحالة السكنية",
    "housing_cost_burden": "عبء الإيجار والفواتير",
    "education_load": "أعباء الدراسة حسب المرحلة",
    "household_size": "عدد أفراد الأسرة",
    "income_source": "مصدر الدخل",
    "debt_service_ratio": "نسبة القسط من الدخل",
    "debt_to_annual_income": "حجم الدين مقارنة بالدخل السنوي",
    "has_health_or_disability": "حالة صحية أو إعاقة",
    "cutoff_warning": "انقطاعات الكهرباء أو الماء",
    "internet_access": "الوصول للإنترنت",
}


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Raw intake rows -> model matrix with the selected features, in a fixed order."""
    return encode(add_engineered(df))[SELECTED]
