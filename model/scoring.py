"""
Transparent Need and Risk scores (0-100) with fixed, documented weights.

These are the "expert rules" of the system. They are used to:
1. label the synthetic training data (data/generate_synthetic.py),
2. show a human-readable breakdown for every household in the UI.

Weights are justified in docs/methodology.md and should be reviewed with the charity's
social workers. Every function works on a DataFrame of raw intake fields.
"""

import pandas as pd

from model.features import SAMA_DEBT_BURDEN_CAP, SAUDI_PER_CAPITA_INCOME, add_engineered

SERVICE_PTS = {"yes": 0.0, "intermittent": 0.5, "no": 1.0}
EDUCATION_LOAD_CAP = 6.0  # e.g. 2 primary + 1 secondary + 1 university + ... saturates here

# Each component is scaled to 0..1, so a weight equals its maximum points.
NEED_WEIGHTS = {
    "deficit": 0.35,          # total expenses above income
    "low_income": 0.25,       # per-capita income vs Saudi average
    "housing": 0.15,          # owned < rented < shelter
    "education": 0.15,        # students weighted by school stage
    "services": 0.10,         # electricity / water / internet deprivation
}
RISK_WEIGHTS = {
    "income_instability": 0.20,  # stable < irregular < none
    "debt_service": 0.20,        # installment / income, max at SAMA's 33.33% cap
    "debt_size": 0.10,           # total debt vs one year of income
    "thin_margin": 0.10,         # surplus below 20% of expenses
    "housing_burden": 0.15,      # (rent + bills) / income, max at 50%
    "health": 0.15,              # chronic illness or disability
    "cutoff_warning": 0.10,      # intermittent electricity/water (unpaid bills)
}

COMPONENT_LABELS_AR = {
    "deficit": "المصروفات أعلى من الدخل",
    "low_income": "دخل الفرد منخفض مقارنة بالوسيط السعودي",
    "housing": "وضع السكن",
    "education": "أعباء الدراسة حسب المرحلة",
    "services": "ضعف الوصول للخدمات الأساسية",
    "income_instability": "عدم ثبات مصدر الدخل",
    "debt_service": "القسط الشهري مرتفع مقارنة بالدخل",
    "debt_size": "حجم الدين كبير مقارنة بالدخل",
    "thin_margin": "هامش أمان مالي ضيق",
    "housing_burden": "الإيجار والفواتير مرتفعة مقارنة بالدخل",
    "health": "حالة صحية أو إعاقة",
    "cutoff_warning": "انقطاعات في الكهرباء أو الماء",
}


def need_components(df: pd.DataFrame) -> pd.DataFrame:
    e = add_engineered(df)
    return pd.DataFrame({
        "deficit": (-e.surplus_ratio).clip(0, 1),
        "low_income": 1 - (e.per_capita_income / SAUDI_PER_CAPITA_INCOME).clip(0, 1),
        "housing": e.housing_status.map({"owned": 0, "rented": 0.5, "shelter": 1}),
        "education": (e.education_load / EDUCATION_LOAD_CAP).clip(0, 1),
        "services": (e.electricity_access.map(SERVICE_PTS) * 0.4
                     + e.water_access.map(SERVICE_PTS) * 0.4
                     + e.internet_access.map(SERVICE_PTS) * 0.2),
    }, index=df.index)


def risk_components(df: pd.DataFrame) -> pd.DataFrame:
    e = add_engineered(df)
    return pd.DataFrame({
        "income_instability": e.income_source.map({"stable": 0, "irregular": 0.7, "none": 1}),
        "debt_service": (e.debt_service_ratio / SAMA_DEBT_BURDEN_CAP).clip(0, 1),
        "debt_size": e.debt_to_annual_income.clip(0, 1),
        "thin_margin": 1 - (e.surplus_ratio / 0.2).clip(0, 1),
        "housing_burden": (e.housing_cost_burden / 0.5).clip(0, 1),
        "health": (e.has_health_or_disability == "yes").astype(float),
        "cutoff_warning": e.cutoff_warning.astype(float),
    }, index=df.index)


def _weighted(components: pd.DataFrame, weights: dict) -> pd.Series:
    return sum(components[k] * w for k, w in weights.items()) * 100


def need_score(df: pd.DataFrame) -> pd.Series:
    return _weighted(need_components(df), NEED_WEIGHTS)


def risk_score(df: pd.DataFrame) -> pd.Series:
    return _weighted(risk_components(df), RISK_WEIGHTS)


def combined_score(df: pd.DataFrame) -> pd.Series:
    return 0.5 * need_score(df) + 0.5 * risk_score(df)


def breakdown_many(df: pd.DataFrame) -> list[list[dict]]:
    """Points each component adds to its score, per household (sorted, non-zero)."""
    parts = (("need", need_components(df), NEED_WEIGHTS), ("risk", risk_components(df), RISK_WEIGHTS))
    out = []
    for i in range(len(df)):
        items = []
        for kind, comps, weights in parts:
            for key, w in weights.items():
                pts = float(comps[key].iat[i] * w * 100)
                if pts > 0.05:
                    items.append({"factor": key, "label": COMPONENT_LABELS_AR[key],
                                  "score": kind, "points": round(pts, 1), "max_points": w * 100})
        out.append(sorted(items, key=lambda x: -x["points"]))
    return out


def breakdown(row: pd.DataFrame) -> list[dict]:
    """Breakdown for a single household."""
    return breakdown_many(row)[0]
