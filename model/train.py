"""
Train and evaluate two explainable priority classifiers and keep the better one.

Candidates (both are glass-box, see docs/methodology.md):
- Decision Tree       -> readable if/else rules
- Logistic Regression -> one signed weight per feature

Selection: highest cross-validated macro F1 (every class counts equally). Recall on the
"high" class is reported separately because missing a family in need is the costliest error.

Usage:
    python model/train.py
Outputs:
    model/artifacts/model.joblib     chosen model + metadata (used by backend/app.py)
    model/artifacts/metrics.json     all metrics for both models
    model/artifacts/model_params.json  numpy-only copy of the chosen model (browser version)
    model/artifacts/tree_rules.txt   decision tree as text rules
    model/artifacts/fairness.csv     per-group performance of the chosen model (test set)
    docs/figures/*.png               charts for docs and presentation
"""

import json
import platform
import sys
from pathlib import Path

import joblib
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import (accuracy_score, confusion_matrix,  # noqa: E402
                             precision_recall_fscore_support)
from sklearn.model_selection import GridSearchCV, StratifiedKFold, train_test_split  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402
from sklearn.tree import DecisionTreeClassifier, export_text, plot_tree  # noqa: E402

from model.features import FEATURE_LABELS_AR, SELECTED, build_features  # noqa: E402
from model.light_model import export_params  # noqa: E402

DATA = ROOT / "data" / "raw" / "synthetic_households.csv"
ARTIFACTS = ROOT / "model" / "artifacts"
FIGURES = ROOT / "docs" / "figures"
CLASSES = ["low", "medium", "high"]  # ordinal order
SEED = 42

from model.plotting import INK, INK2, PRIORITY_AR, setup  # noqa: E402

setup()
SERIES = {"tree": "#2a78d6", "logreg": "#eb6834"}
MODEL_AR = {"tree": "شجرة القرار", "logreg": "الانحدار اللوجستي"}

CANDIDATES = {
    "tree": (
        DecisionTreeClassifier(class_weight="balanced", random_state=SEED),
        {"max_depth": [3, 4, 5, 6], "min_samples_leaf": [10, 25, 50]},
    ),
    "logreg": (
        make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=2000)),
        {"logisticregression__C": [0.01, 0.1, 1, 10]},
    ),
}


def evaluate(y_true, y_pred) -> dict:
    p, r, f, _ = precision_recall_fscore_support(y_true, y_pred, labels=CLASSES, zero_division=0)
    pm, rm, fm, _ = precision_recall_fscore_support(y_true, y_pred, average="macro", zero_division=0)
    return {
        "accuracy": round(accuracy_score(y_true, y_pred), 4),
        "precision_macro": round(pm, 4), "recall_macro": round(rm, 4), "f1_macro": round(fm, 4),
        "per_class": {c: {"precision": round(p[i], 4), "recall": round(r[i], 4), "f1": round(f[i], 4)}
                      for i, c in enumerate(CLASSES)},
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=CLASSES).tolist(),
    }


def plot_confusion(cm, name, title):
    cm = np.array(cm)
    labels = [PRIORITY_AR[c] for c in CLASSES]
    fig, ax = plt.subplots(figsize=(4.4, 3.8))
    ax.imshow(cm, cmap="Blues")
    ax.grid(False)
    ax.set_xticks(range(3), labels)
    ax.set_yticks(range(3), labels)
    ax.set_xlabel("ما توقّعه النموذج")
    ax.set_ylabel("الأولوية الفعلية")
    for i in range(3):
        for j in range(3):
            ax.text(j, i, cm[i, j], ha="center", va="center",
                    color="white" if cm[i, j] > cm.max() / 2 else INK, fontsize=12)
    ax.set_title(title, loc="right")
    fig.tight_layout()
    fig.savefig(FIGURES / f"confusion_{name}.png")
    plt.close(fig)


def plot_comparison(metrics):
    keys = ["accuracy", "precision_macro", "recall_macro", "f1_macro", "recall_high"]
    labels = ["الدقة", "الضبط", "الاستدعاء", "F1", "اكتشاف العالية"]
    x = np.arange(len(keys))
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    for k, name in enumerate(["tree", "logreg"]):
        m = metrics[name]["test"]
        vals = [m[key] if key != "recall_high" else m["per_class"]["high"]["recall"] for key in keys]
        bars = ax.bar(x + (k - 0.5) * 0.36, vals, width=0.34, color=SERIES[name], label=MODEL_AR[name])
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.012, f"{v:.0%}", ha="center", fontsize=8, color=INK2)
    ax.set_xticks(x, labels)
    ax.set_ylim(0, 1.18)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(axis="x", visible=False)
    ax.legend(frameon=False, loc="upper right", ncol=2)
    ax.set_title("أداء النموذجين على 600 أسرة اختبار (متوسط الفئات)", loc="right")
    fig.tight_layout()
    fig.savefig(FIGURES / "model_comparison.png")
    plt.close(fig)


def plot_coefficients(pipe):
    lr = pipe.named_steps["logisticregression"]
    coef = pd.Series(lr.coef_[list(lr.classes_).index("high")], index=SELECTED).sort_values()
    fig, ax = plt.subplots(figsize=(7, 4.4))
    colors = [SERIES["logreg"] if v > 0 else SERIES["tree"] for v in coef]
    ax.barh([FEATURE_LABELS_AR[f] for f in coef.index], coef.values, color=colors, height=0.6)
    ax.axvline(0, color=INK2, lw=1)
    ax.grid(axis="y", visible=False)
    ax.set_title("وزن كل ميزة في ترجيح الأولوية العالية (البرتقالي يرفع، الأزرق يخفض)", loc="right")
    fig.tight_layout()
    fig.savefig(FIGURES / "logreg_coefficients.png")
    plt.close(fig)


def plot_fairness(fair: pd.DataFrame):
    d = fair.dropna(subset=["recall_high"])
    fig, ax = plt.subplots(figsize=(7, 4.2))
    y = np.arange(len(d))
    ax.barh(y, d.recall_high, color="#2a78d6", height=0.55)
    for i, (v, n) in enumerate(zip(d.recall_high, d.n_high)):
        ax.text(v + 0.01, i, f"{v:.0%}  (ن={n})", va="center", fontsize=8, color=INK2)
    ax.set_yticks(y, d.label_ar)
    ax.set_xlim(0, 1.15)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.grid(axis="y", visible=False)
    ax.invert_yaxis()
    ax.set_title("فحص العدالة: نسبة اكتشاف الأسر عالية الأولوية في كل فئة", loc="right")
    fig.tight_layout()
    fig.savefig(FIGURES / "fairness.png")
    plt.close(fig)


def plot_tree_fig(tree):
    fig, ax = plt.subplots(figsize=(16, 7))
    plot_tree(tree, feature_names=[FEATURE_LABELS_AR[f] for f in SELECTED],
              class_names=[PRIORITY_AR[c] for c in tree.classes_], filled=True,
              impurity=False, proportion=True, rounded=True, max_depth=3, fontsize=7, ax=ax)
    fig.tight_layout()
    fig.savefig(FIGURES / "decision_tree.png")
    plt.close(fig)


FAIRNESS_GROUPS = {
    "has_health_or_disability": {"yes": "مع إعاقة/حالة صحية", "no": "بدون"},
    "housing_status": {"owned": "ملك", "rented": "إيجار", "shelter": "مأوى"},
    "income_source": {"stable": "دخل ثابت", "irregular": "دخل متقطع", "none": "بلا دخل"},
    "size_band": {"1-3": "1–3 أفراد", "4-6": "4–6 أفراد", "7+": "7 فأكثر"},
}


def fairness_table(df_test: pd.DataFrame, y_true: pd.Series, y_pred) -> pd.DataFrame:
    """Accuracy and high-priority recall per subgroup: large gaps would signal unequal treatment."""
    d = df_test.assign(y=y_true.values, pred=y_pred,
                       size_band=pd.cut(df_test.household_size, [0, 3, 6, 99], labels=["1-3", "4-6", "7+"]))
    rows = []
    for col, labels in FAIRNESS_GROUPS.items():
        for value, label in labels.items():
            g = d[d[col].astype(str) == value]
            high = g[g.y == "high"]
            rows.append({"group": col, "value": value, "label_ar": label, "n": len(g),
                         "accuracy": round((g.y == g.pred).mean(), 4) if len(g) else None,
                         "n_high": len(high),
                         "recall_high": round((high.pred == "high").mean(), 4) if len(high) else None,
                         "share_predicted_high": round((g.pred == "high").mean(), 4) if len(g) else None})
    return pd.DataFrame(rows)


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(DATA)
    X, y = build_features(df), df.priority_label
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=SEED)
    df_test = df.loc[X_test.index]
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)

    fitted, metrics = {}, {}
    for name, (estimator, grid) in CANDIDATES.items():
        search = GridSearchCV(estimator, grid, scoring="f1_macro", cv=cv, n_jobs=-1)
        search.fit(X_train, y_train)
        fitted[name] = search.best_estimator_
        metrics[name] = {
            "best_params": search.best_params_,
            "cv_f1_macro": round(search.best_score_, 4),
            "train": evaluate(y_train, search.predict(X_train)),
            "test": evaluate(y_test, search.predict(X_test)),
        }
        plot_confusion(metrics[name]["test"]["confusion_matrix"], name, MODEL_AR[name])
        print(f"{name:7s} cv_f1={metrics[name]['cv_f1_macro']:.3f} "
              f"test_f1={metrics[name]['test']['f1_macro']:.3f} "
              f"recall_high={metrics[name]['test']['per_class']['high']['recall']:.3f} "
              f"params={search.best_params_}")

    chosen = max(metrics, key=lambda n: metrics[n]["cv_f1_macro"])
    metrics["chosen"] = chosen
    metrics["n_train"], metrics["n_test"] = len(X_train), len(X_test)
    metrics["features"] = SELECTED
    metrics["environment"] = {"python": platform.python_version(), "pandas": pd.__version__,
                              "scikit-learn": __import__("sklearn").__version__, "seed": SEED}

    fair = fairness_table(df_test, y_test, fitted[chosen].predict(X_test))
    fair.to_csv(ARTIFACTS / "fairness.csv", index=False, encoding="utf-8-sig")
    plot_fairness(fair)
    metrics["fairness"] = fair.to_dict("records")
    metrics[chosen].update(fairness=metrics["fairness"], n_test=len(X_test), n_train=len(X_train))
    print(fair[["group", "value", "n", "accuracy", "recall_high"]].to_string(index=False))

    tree = fitted["tree"]
    (ARTIFACTS / "tree_rules.txt").write_text(
        export_text(tree, feature_names=SELECTED, decimals=2), encoding="utf-8")
    plot_tree_fig(tree)
    plot_coefficients(fitted["logreg"])
    plot_comparison(metrics)

    # Refit the chosen model on all data before saving (hyper-parameters already fixed).
    final = fitted[chosen].fit(X, y)
    joblib.dump({"model": final, "kind": chosen, "features": SELECTED,
                 "feature_labels": FEATURE_LABELS_AR, "metrics": metrics[chosen]},
                ARTIFACTS / "model.joblib")
    (ARTIFACTS / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if chosen == "logreg":  # numpy-only copy for the in-browser (Pyodide) version of the site
        params = export_params(final, chosen, SELECTED, FEATURE_LABELS_AR, metrics[chosen])
        (ARTIFACTS / "model_params.json").write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
    print(f"chosen: {chosen} -> {ARTIFACTS / 'model.joblib'}")


if __name__ == "__main__":
    main()
