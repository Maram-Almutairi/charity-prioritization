"""
Validate the method on REAL household data.

Saudi household microdata is confidential, so we test the approach on a real, open, labelled
household survey: the Costa Rica household poverty data published by the Inter-American
Development Bank (IDB) on Kaggle (9,557 people, 2,988 households, poverty level set by IDB).

    1. Download train.csv from https://www.kaggle.com/c/costa-rican-household-poverty-prediction/data
    2. Put it at data/external/costa_rica/train.csv   (the folder is git-ignored)
    3. python analysis/real_data_validation.py

What it does, with every cleaning step logged:
  - cleaning: "yes"/"no" text inside numeric columns, missing rent / tablets / education,
    households whose members disagree on the poverty level (we use the head's), households
    with no head (dropped), columns with >80% missing (dropped).
  - household features that mirror the tool's Need / Risk dimensions (housing, services,
    education and dependents, assets, health / disability).
  - Test A: a transparent index (fixed weights, no learning) -> does it find the poor?
  - Test B: logistic regression on the real labels -> accuracy, F1, confusion matrix.
  - Test C: fairness by urban / rural, female head, disability.
Outputs: docs/real_data_results.json and docs/figures/real_*.png
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, recall_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model import plotting  # noqa: E402

SRC = ROOT / "data" / "external" / "costa_rica" / "train.csv"
OUT_JSON = ROOT / "docs" / "real_data_results.json"
FIG = ROOT / "docs" / "figures"
SEED = 42
LEVEL = {1: "high", 2: "high", 3: "medium", 4: "low"}  # extreme / moderate poverty, vulnerable, not vulnerable

# Transparent index: same spirit as the tool's Need score, weights sum to 1.
INDEX_WEIGHTS = {
    "housing": 0.25,      # precarious / rented / paying installments, overcrowding, poor materials
    "services": 0.15,     # no electricity, water outside the house, no toilet
    "education": 0.25,    # low adult education, many dependents
    "assets": 0.20,       # no fridge / computer / TV, few phones per person
    "health": 0.15,       # members with disability, old-age dependents
}


def clean(raw: pd.DataFrame):
    log = []
    df = raw.copy()
    log.append(("rows_in", len(df)))
    log.append(("households_in", int(df["idhogar"].nunique())))

    # "yes"/"no" mixed into numeric columns (codebook: yes = 1, no = 0)
    for col in ["dependency", "edjefe", "edjefa"]:
        n = int(df[col].isin(["yes", "no"]).sum())
        df[col] = pd.to_numeric(df[col].replace({"yes": 1, "no": 0}), errors="coerce")
        log.append((f"yes_no_fixed_{col}", n))

    # Missing values that mean "none" by the codebook
    n = int(df["v2a1"].isna().sum())
    df["v2a1"] = df["v2a1"].fillna(0)  # no monthly rent payment (owners, precarious, other)
    log.append(("rent_missing_set_0", n))
    n = int(df["v18q1"].isna().sum())
    df["v18q1"] = df["v18q1"].fillna(0)  # missing tablet count when the household has no tablet
    log.append(("tablets_missing_set_0", n))
    n = int(df["meaneduc"].isna().sum())
    df["meaneduc"] = df["meaneduc"].fillna(df["escolari"])  # 5 rows: use the person's own schooling
    log.append(("meaneduc_missing_filled", n))

    # Columns mostly empty or derived squares
    drop = ["rez_esc"] + [c for c in df.columns if c.startswith("SQB") or c == "agesq"]
    df = df.drop(columns=drop)
    log.append(("columns_dropped", len(drop)))

    # One poverty level per household: the head's
    heads = df[df["parentesco1"] == 1].set_index("idhogar")
    inconsistent = int((df.groupby("idhogar")["Target"].nunique() > 1).sum())
    log.append(("households_inconsistent_target_fixed", inconsistent))
    no_head = int(df["idhogar"].nunique() - heads.index.nunique())
    log.append(("households_without_head_dropped", no_head))
    df = df[df["idhogar"].isin(heads.index)].copy()
    df["Target"] = df["idhogar"].map(heads["Target"])
    dup = int(df.duplicated("Id").sum())
    log.append(("duplicate_person_ids", dup))
    return df, heads, log


def household_features(df: pd.DataFrame, heads: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("idhogar")
    h = pd.DataFrame(index=heads.index)
    h["size"] = heads["hhsize"]
    h["children"] = heads["hogar_nin"]
    h["elderly"] = heads["hogar_mayor"]
    h["dependency_ratio"] = (heads["hogar_nin"] + heads["hogar_mayor"]) / heads["hogar_adul"].replace(0, np.nan)
    h["dependency_ratio"] = h["dependency_ratio"].fillna(h["dependency_ratio"].max())
    h["adult_education"] = heads["meaneduc"]
    h["head_education"] = heads["escolari"]
    h["overcrowding"] = heads["overcrowding"]
    h["rented"] = heads["tipovivi3"]
    h["paying_installments"] = heads["tipovivi2"]
    h["precarious_housing"] = heads["tipovivi4"]
    h["owned"] = heads["tipovivi1"]
    h["rent_per_person"] = heads["v2a1"] / heads["hhsize"]
    h["bad_walls"], h["bad_roof"], h["bad_floor"] = heads["epared1"], heads["etecho1"], heads["eviv1"]
    h["no_floor"] = heads["pisonotiene"]
    h["no_electricity"] = heads["noelec"]
    h["water_outside"] = 1 - heads["abastaguadentro"]
    h["no_toilet"] = heads["sanitario1"]
    h["no_fridge"] = 1 - heads["refrig"]
    h["no_computer"] = 1 - heads["computer"]
    h["no_tv"] = 1 - heads["television"]
    h["phones_per_person"] = heads["qmobilephone"] / heads["hhsize"]
    h["disabled_members"] = g["dis"].sum()
    h["female_head"] = heads["female"]
    h["urban"] = heads["area1"]
    h["target"] = heads["Target"].astype(int)
    h["level"] = h["target"].map(LEVEL)
    return h


def pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, method="average")


def transparent_index(h: pd.DataFrame) -> pd.Series:
    housing = (2 * h["precarious_housing"] + h["rented"] + h["paying_installments"] + pct_rank(h["overcrowding"])
               + (h["bad_walls"] + h["bad_roof"] + h["bad_floor"] + h["no_floor"]) / 2) / 6.5
    services = (h["no_electricity"] + h["water_outside"] + h["no_toilet"]) / 3
    education = (pct_rank(-h["adult_education"]) + pct_rank(h["dependency_ratio"]) + pct_rank(h["children"])) / 3
    assets = (h["no_fridge"] + h["no_computer"] + h["no_tv"] + pct_rank(-h["phones_per_person"])) / 4
    health = ((h["disabled_members"] > 0).astype(float) + pct_rank(h["elderly"]) * (h["elderly"] > 0)) / 2
    parts = dict(housing=housing, services=services, education=education, assets=assets, health=health)
    return 100 * sum(INDEX_WEIGHTS[k] * parts[k].clip(0, 1) for k in parts)


MODEL_FEATURES = ["children", "elderly", "dependency_ratio", "adult_education", "head_education",
                  "overcrowding", "rented", "paying_installments", "precarious_housing", "rent_per_person",
                  "bad_walls", "bad_roof", "bad_floor", "no_floor", "no_electricity", "water_outside",
                  "no_toilet", "no_fridge", "no_computer", "no_tv", "phones_per_person", "disabled_members", "urban"]
FEATURE_AR = {
    "adult_education": "متوسط تعليم البالغين", "phones_per_person": "الجوالات لكل فرد", "no_computer": "لا يوجد حاسب",
    "overcrowding": "الازدحام في السكن", "children": "عدد الأطفال", "dependency_ratio": "نسبة الإعالة",
    "head_education": "تعليم رب الأسرة", "bad_floor": "أرضية سيئة", "bad_walls": "جدران سيئة", "bad_roof": "سقف سيئ",
    "rented": "سكن بالإيجار", "precarious_housing": "سكن غير مستقر", "no_fridge": "لا توجد ثلاجة", "no_tv": "لا يوجد تلفاز",
    "urban": "حضري", "size": "حجم الأسرة", "elderly": "كبار السن", "rent_per_person": "الإيجار لكل فرد",
    "paying_installments": "مسكن بأقساط", "no_floor": "بلا أرضية", "no_electricity": "بلا كهرباء",
    "water_outside": "الماء خارج المسكن", "no_toilet": "بلا دورة مياه", "disabled_members": "أفراد ذوو إعاقة",
}


def main() -> None:
    if not SRC.exists():
        sys.exit(f"Real data not found: {SRC}\nDownload train.csv from "
                 "https://www.kaggle.com/c/costa-rican-household-poverty-prediction/data")
    raw = pd.read_csv(SRC)
    df, heads, log = clean(raw)
    h = household_features(df, heads)
    counts = h["target"].value_counts().sort_index().to_dict()

    # ---- Test A: transparent index, no learning
    h["index"] = transparent_index(h)
    k = int(round(0.25 * len(h)))
    top = h.nlargest(k, "index")
    poor = h["level"] == "high"
    vulnerable = h["level"] == "medium"
    test_a = {
        "households": len(h), "top_k": k,
        "share_poor_overall": round(float(poor.mean()), 3),
        "share_poor_in_top": round(float((top["level"] == "high").mean()), 3),
        "poor_found": int((top["level"] == "high").sum()), "poor_total": int(poor.sum()),
        "vulnerable_found": int((top["level"] == "medium").sum()), "vulnerable_total": int(vulnerable.sum()),
        "random_expected_poor": round(float(poor.mean() * k), 1),
        "mean_index_by_level": {str(t): round(float(v), 1) for t, v in h.groupby("target")["index"].mean().items()},
        "spearman_index_vs_level": round(float(h["index"].corr(-h["target"], method="spearman")), 3),
    }
    test_a["lift"] = round(test_a["share_poor_in_top"] / test_a["share_poor_overall"], 2)

    # ---- Test B: logistic regression on the real labels
    X, y = h[MODEL_FEATURES], h["level"]
    Xtr, Xte, ytr, yte, _, ite = train_test_split(X, y, h.index, test_size=0.2, stratify=y, random_state=SEED)
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced", random_state=SEED))
    model.fit(Xtr, ytr)
    pred = pd.Series(model.predict(Xte), index=ite)
    labels = plotting.ORDER
    cm = confusion_matrix(yte, pred, labels=labels)
    rec = recall_score(yte, pred, labels=labels, average=None)
    coefs = model[-1].coef_[list(model[-1].classes_).index("high")]
    top_feat = sorted(zip(MODEL_FEATURES, coefs), key=lambda t: -abs(t[1]))[:8]
    test_b = {
        "train": len(Xtr), "test": len(Xte),
        "accuracy": round(float(accuracy_score(yte, pred)), 3),
        "macro_f1": round(float(f1_score(yte, pred, average="macro")), 3),
        "recall": {l: round(float(r), 3) for l, r in zip(labels, rec)},
        "high_as_low": int(cm[0, 2]), "low_as_high": int(cm[2, 0]),
        "confusion": {"labels": labels, "matrix": cm.tolist()},
        "baseline_majority_accuracy": round(float((yte == ytr.mode()[0]).mean()), 3),
        "baseline_majority_macro_f1": round(float(f1_score(yte, [ytr.mode()[0]] * len(yte), average="macro")), 3),
        "top_features_high": [{"feature": f, "label_ar": FEATURE_AR[f], "coef": round(float(c), 2)} for f, c in top_feat],
    }

    # ---- Test C: fairness (recall of the "high" class by group, test set)
    te = h.loc[ite].assign(pred=pred)
    groups = {
        "urban": te["urban"] == 1, "rural": te["urban"] == 0,
        "female_head": te["female_head"] == 1, "male_head": te["female_head"] == 0,
        "disability": te["disabled_members"] > 0, "no_disability": te["disabled_members"] == 0,
    }
    test_c = {}
    for name, m in groups.items():
        sub = te[m & (te["level"] == "high")]
        test_c[name] = {"n_high": len(sub), "recall_high": round(float((sub["pred"] == "high").mean()), 3) if len(sub) else None}

    result = {
        "source": {
            "name": "Costa Rican Household Poverty Level Prediction (Inter-American Development Bank)",
            "url": "https://www.kaggle.com/c/costa-rican-household-poverty-prediction/data",
            "rows": int(len(raw)), "households": int(raw["idhogar"].nunique()), "columns": int(raw.shape[1]),
        },
        "cleaning_log": dict(log),
        "households_used": len(h), "target_counts": {str(k_): int(v) for k_, v in counts.items()},
        "index_weights": INDEX_WEIGHTS,
        "test_a_transparent_index": test_a, "test_b_logistic_regression": test_b, "test_c_fairness": test_c,
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    figures(h, cm, labels, top_feat)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def figures(h, cm, labels, top_feat) -> None:
    import matplotlib.pyplot as plt
    plotting.setup()
    FIG.mkdir(parents=True, exist_ok=True)
    names = {1: "فقر مدقع", 2: "فقر متوسط", 3: "معرّضة للفقر", 4: "غير معرّضة"}
    colors = {1: plotting.PRIORITY_COLORS["high"], 2: plotting.PRIORITY_COLORS["high"],
              3: plotting.PRIORITY_COLORS["medium"], 4: plotting.PRIORITY_COLORS["low"]}

    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    means = h.groupby("target")["index"].mean()
    ax.bar([names[t] for t in means.index], means.values, color=[colors[t] for t in means.index], width=0.6)
    for i, v in enumerate(means.values):
        ax.text(i, v + 0.6, f"{v:.0f}", ha="center", color=plotting.INK)
    ax.set_title("بيانات حقيقية: متوسط مؤشر الحاجة حسب مستوى الفقر الفعلي")
    ax.set_ylabel("مؤشر الحاجة (0–100)")
    fig.savefig(FIG / "real_index_by_level.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(4.6, 3.8))
    ax.imshow(cm, cmap="Blues"); ax.grid(False)
    ar = [plotting.PRIORITY_AR[l] for l in labels]
    ax.set_xticks(range(3), ar); ax.set_yticks(range(3), ar)
    ax.set_xlabel("توقّع النموذج", labelpad=8); ax.set_ylabel("المستوى الفعلي", labelpad=14)
    for i in range(3):
        for j in range(3):
            ax.text(j, i, cm[i, j], ha="center", va="center", color="white" if cm[i, j] > cm.max() / 2 else plotting.INK)
    ax.set_title("بيانات حقيقية: مصفوفة الالتباس (أسر الاختبار)")
    fig.savefig(FIG / "real_confusion.png"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    feats = list(reversed(top_feat))
    ax.barh([FEATURE_AR[f] for f, _ in feats], [c for _, c in feats],
            color=[plotting.RISK if c > 0 else plotting.NEED for _, c in feats])
    ax.axvline(0, color=plotting.INK2, linewidth=0.8)
    ax.set_title("بيانات حقيقية: أقوى العوامل في التنبؤ بالأولوية العالية")
    ax.set_xlabel("الأثر (موجب = يرفع الأولوية)")
    fig.savefig(FIG / "real_top_features.png"); plt.close(fig)


if __name__ == "__main__":
    main()
