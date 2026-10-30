"""
Validate the RISK side on real people: does a transparent risk index (installment burden,
late payments, income per person, credit use) spot financial crises before they happen?

Data: "Give Me Some Credit" (Credit Fusion & Kaggle, 2011): 150,000 real borrowers, with whether
each one fell into serious financial distress (90+ days late) within the next two years.
Mirror without login on OpenML (licence: Public): https://www.openml.org/d/46929
Original: https://www.kaggle.com/c/GiveMeSomeCredit

    python analysis/credit_risk_validation.py   (downloads the file once if missing)
Outputs: docs/credit_risk_results.json and docs/figures/credit_*.png
"""

import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model import plotting  # noqa: E402
from model.features import SAMA_DEBT_BURDEN_CAP  # noqa: E402

SRC = ROOT / "data" / "external" / "give_me_some_credit" / "GiveMeSomeCredit.arff"
DOWNLOAD = "https://openml.org/data/v1/download/22125240/GiveMeSomeCredit.arff"
OUT = ROOT / "docs" / "credit_risk_results.json"
FIG = ROOT / "docs" / "figures"
COLS = ["distress", "card_use", "age", "late_30_59", "debt_ratio", "income", "open_lines",
        "late_90", "real_estate_loans", "late_60_89", "dependents"]
# Fixed weights, chosen before looking at the outcome, mirroring the tool's Risk score.
WEIGHTS = {"installment_burden": 0.30, "past_late_payment": 0.30, "low_income_per_person": 0.20, "credit_use": 0.20}


def read_arff(path: Path) -> pd.DataFrame:
    text = path.read_text(encoding="utf-8")
    rows = [line.split(",") for line in text.split("@DATA", 1)[1].strip().splitlines()]
    df = pd.DataFrame(rows, columns=COLS).replace("?", np.nan)
    for c in COLS[1:]:
        df[c] = pd.to_numeric(df[c])
    df["y"] = (df.pop("distress") == "Yes").astype(int)
    return df


def clean(raw: pd.DataFrame):
    log = {"rows_in": len(raw)}
    df = raw.copy()
    log["income_missing_dropped"] = int(df["income"].isna().sum())  # debt ratio is meaningless without income
    df = df[df["income"].notna()]
    log["age_zero_dropped"] = int((df["age"] <= 0).sum())
    df = df[df["age"] > 0]
    codes = df["late_30_59"] >= 96  # 96 / 98 are survey codes, not real counts
    log["late_payment_codes_96_98_dropped"] = int(codes.sum())
    df = df[~codes]
    log["dependents_missing_set_0"] = int(df["dependents"].isna().sum())
    df["dependents"] = df["dependents"].fillna(0)
    log["rows_out"] = len(df)
    return df, log


def risk_index(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["installment_burden"] = (df["debt_ratio"].clip(0, 2) / SAMA_DEBT_BURDEN_CAP).clip(0, 1)
    out["past_late_payment"] = ((df["late_30_59"] + df["late_60_89"] + df["late_90"]) > 0).astype(float)
    per_person = df["income"] / (df["dependents"] + 1)
    out["low_income_per_person"] = 1 - per_person.rank(pct=True)
    out["credit_use"] = df["card_use"].clip(0, 1)
    out["risk"] = 100 * sum(WEIGHTS[k] * out[k] for k in WEIGHTS)
    return out


def main() -> None:
    if not SRC.exists():
        SRC.parent.mkdir(parents=True, exist_ok=True)
        print("downloading", DOWNLOAD)
        urllib.request.urlretrieve(DOWNLOAD, SRC)
    df, log = clean(read_arff(SRC))
    r = risk_index(df)
    y = df["y"]
    k = int(round(0.25 * len(df)))
    top = r["risk"].nlargest(k).index
    over_cap = df["debt_ratio"] > SAMA_DEBT_BURDEN_CAP
    decile = pd.qcut(r["risk"].rank(method="first"), 10, labels=False) + 1
    old = df["age"] >= 60
    young = df["age"] < 35
    def auc(m): return round(float(roc_auc_score(y[m], r["risk"][m])), 3)
    result = {
        "source": {"name": "Give Me Some Credit (Credit Fusion & Kaggle, 2011), OpenML mirror",
                   "url_openml": "https://www.openml.org/d/46929", "url_kaggle": "https://www.kaggle.com/c/GiveMeSomeCredit"},
        "cleaning_log": log, "weights": WEIGHTS,
        "distress_rate_overall": round(float(y.mean()), 4),
        "auc_risk_index": auc(slice(None)),
        "auc_each_factor": {f: round(float(roc_auc_score(y, r[f])), 3) for f in WEIGHTS},
        "top_quarter": {"people": k, "distress_rate": round(float(y[top].mean()), 3),
                        "share_of_all_distress_caught": round(float(y[top].sum() / y.sum()), 3)},
        "installment_above_sama_cap": {"share_of_people": round(float(over_cap.mean()), 3),
                                       "distress_rate_above": round(float(y[over_cap].mean()), 3),
                                       "distress_rate_below": round(float(y[~over_cap].mean()), 3)},
        "distress_rate_by_decile": {int(d): round(float(v), 3) for d, v in y.groupby(decile).mean().items()},
        "fairness_auc": {"age_under_35": auc(young), "age_60_plus": auc(old),
                         "no_dependents": auc(df["dependents"] == 0), "3_plus_dependents": auc(df["dependents"] >= 3)},
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    figure(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def figure(res: dict) -> None:
    import matplotlib.pyplot as plt
    plotting.setup()
    FIG.mkdir(parents=True, exist_ok=True)
    dec = res["distress_rate_by_decile"]
    fig, ax = plt.subplots(figsize=(6.6, 3.4))
    colors = [plotting.RISK if d >= 8 else plotting.NEED for d in dec]
    ax.bar([str(d) for d in dec], [100 * v for v in dec.values()], color=colors)
    ax.axhline(100 * res["distress_rate_overall"], color=plotting.INK2, ls="--", lw=1)
    ax.text(0, 100 * res["distress_rate_overall"] + 0.6, "المعدل العام", color=plotting.INK2, fontsize=9)
    ax.set_xlabel("مؤشر المخاطر: من الأقل (1) إلى الأعلى (10)، كل عمود 10% من الناس")
    ax.set_ylabel("وقعوا في تعثّر خلال سنتين %")
    ax.set_title("بيانات حقيقية (120 ألف شخص): كلما ارتفع مؤشر المخاطر زاد التعثّر لاحقًا")
    fig.savefig(FIG / "credit_risk_deciles.png"); plt.close(fig)


if __name__ == "__main__":
    main()
