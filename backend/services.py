"""
Business logic behind the API: household assessment and budget allocation.
Kept separate from Flask routes so it can be unit-tested directly. Holds no state.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model.features import build_features  # noqa: E402
from model.schema import PRIORITY_AR, total_expenses  # noqa: E402
from model.scoring import breakdown_many, need_score, risk_score  # noqa: E402

MODEL_PATH = ROOT / "model" / "artifacts" / "model.joblib"

DISCLAIMER = ("هذا التقييم أداة لدعم قرار الجمعية، وليس قرارًا آليًا. لا يُحرم أي أسرة من المساعدة بسببه، "
              "والقرار النهائي دائمًا للمسؤول عن التوزيع بعد مراجعة الحالة.")

PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1}
CONFIDENCE_REVIEW_THRESHOLD = 0.60  # below this the case is flagged for manual review
QUADRANT_SPLIT = 50                 # need/risk >= 50 counts as "high" on the priority map
QUADRANTS_AR = {
    "urgent": "تدخّل عاجل",          # high need, high risk
    "prevent": "وقاية مبكرة",        # lower need, high risk -> reach before the crisis
    "support": "دعم الاحتياج",       # high need, lower risk
    "monitor": "متابعة دورية",       # both lower
}


def quadrant(need: float, risk: float) -> str:
    if risk >= QUADRANT_SPLIT:
        return "urgent" if need >= QUADRANT_SPLIT else "prevent"
    return "support" if need >= QUADRANT_SPLIT else "monitor"


class Assessor:
    """Loads the trained model once and scores households."""

    def __init__(self, model_path: Path = MODEL_PATH, bundle: dict | None = None):
        if bundle is None:
            import joblib  # not needed when a bundle is passed (e.g. the in-browser version)
            bundle = joblib.load(model_path)
        self.model = bundle["model"]
        self.kind = bundle["kind"]
        self.features = bundle["features"]
        self.feature_labels = bundle["feature_labels"]
        self.metrics = bundle["metrics"]

    def _model_factors(self, X: pd.DataFrame, predicted: list[str]) -> list[list[dict]]:
        """Top features that pushed the model toward each household's predicted class."""
        if self.kind == "logreg":
            scaler = self.model.named_steps["standardscaler"]
            lr = self.model.named_steps["logisticregression"]
            rows = [list(lr.classes_).index(p) for p in predicted]
            contrib = lr.coef_[rows] * scaler.transform(X)
        else:  # decision tree: importance of the features used on each decision path
            tree = self.model
            paths = tree.decision_path(X).toarray().astype(bool)
            contrib = np.zeros((len(X), len(self.features)))
            for i, nodes in enumerate(paths):
                for node in np.flatnonzero(nodes):
                    f = tree.tree_.feature[node]
                    if f >= 0:
                        contrib[i, f] = tree.feature_importances_[f]
        out = []
        for row in contrib:
            order = np.argsort(-row)[:4]
            out.append([{"feature": self.features[i], "label": self.feature_labels[self.features[i]],
                         "weight": round(float(row[i]), 3)} for i in order if row[i] > 0])
        return out

    def assess_many(self, households: list[dict]) -> list[dict]:
        """Score a batch in one vectorised pass (an imported sheet can hold thousands of rows)."""
        if not households:
            return []
        df = pd.DataFrame(households)
        X = build_features(df)
        proba = self.model.predict_proba(X)
        classes = list(self.model.classes_)
        pred_idx = proba.argmax(axis=1)
        needs, risks = need_score(df).to_numpy(), risk_score(df).to_numpy()
        predicted = [classes[k] for k in pred_idx]
        factors = self._model_factors(X, predicted)
        breakdowns = breakdown_many(df)

        results = []
        for i, h in enumerate(households):
            priority = predicted[i]
            need, risk = float(needs[i]), float(risks[i])
            expenses = total_expenses(h)
            results.append({
                "need_score": round(need, 1),
                "risk_score": round(risk, 1),
                "combined_score": round(0.5 * need + 0.5 * risk, 1),
                "priority": priority,
                "priority_ar": PRIORITY_AR[priority],
                "confidence": round(float(proba[i, pred_idx[i]]), 3),
                "probabilities": {c: round(float(p), 3) for c, p in zip(classes, proba[i])},
                "needs_review": bool(proba[i, pred_idx[i]] < CONFIDENCE_REVIEW_THRESHOLD),
                "quadrant": quadrant(need, risk),
                "quadrant_ar": QUADRANTS_AR[quadrant(need, risk)],
                "score_breakdown": breakdowns[i],
                "model_factors": factors[i],
                "total_expenses": round(expenses, 0),
                "monthly_deficit": round(max(expenses - h["monthly_income"], 0), 0),
            })
        return results

    def assess(self, household: dict) -> dict:
        return {**self.assess_many([household])[0], "disclaimer": DISCLAIMER}


def support_target(h: dict) -> float:
    """Monthly amount that would close the family's gap; at-risk families also get a
    preventive buffer (10% of expenses) so a small shock does not become a crisis."""
    expenses = h.get("total_expenses") or total_expenses(h)
    deficit = max(expenses - h["monthly_income"], 0)
    buffer = 0.10 * expenses if h["risk_score"] >= 50 else 0
    return round(deficit + buffer, 0)


def allocate(households: list[dict], budget: float) -> dict:
    """
    Tiered allocation: high -> medium -> low (the responsible member's decision wins over
    the model's priority). Inside a tier, if the remaining budget cannot cover every target
    it is split in proportion to each target and allocation stops there, so rounding
    leftovers never trickle to a lower tier ahead of an uncovered higher one. Families that
    receive nothing stay in the list, flagged for a decision — nobody is removed.
    """
    remaining = float(budget)
    rows = [{"id": h.get("id"), "target": support_target(h), "allocated": 0.0,
             "effective_priority": h.get("final_priority") or h["priority"],
             "combined_score": h["combined_score"]} for h in households]

    for tier in ("high", "medium", "low"):
        members = [r for r in rows if r["effective_priority"] == tier and r["target"] > 0]
        need = sum(r["target"] for r in members)
        if need == 0 or remaining < 50:  # ignore rounding leftovers
            continue
        ratio = min(1.0, remaining / need)
        for r in members:
            r["allocated"] = float(np.floor(r["target"] * ratio))  # floor: never exceed budget
        remaining -= sum(r["allocated"] for r in members)
        if ratio < 1.0:
            break  # a tier not fully covered gets everything left; lower tiers wait for a decision

    for r in rows:
        if r["target"] == 0:
            r["allocation_note"] = "لا يوجد عجز حالي — متابعة دورية"
        elif r["allocated"] >= r["target"]:
            r["allocation_note"] = "مغطاة بالكامل"
        elif r["allocated"] > 0:
            r["allocation_note"] = "مغطاة جزئيًا"
        else:
            r["allocation_note"] = "لم تُغطَّ ضمن الميزانية الحالية — تحتاج قرار المسؤول"

    total_target = sum(r["target"] for r in rows)
    return {
        "budget": budget,
        "allocated_total": round(budget - remaining, 0),
        "remaining": round(remaining, 0),
        "total_target": round(total_target, 0),
        "coverage": round((budget - remaining) / total_target, 3) if total_target else 1.0,
        "uncovered": sum(1 for r in rows if r["target"] > 0 and r["allocated"] == 0),
        "by_tier": {t: {"families": sum(r["effective_priority"] == t for r in rows),
                        "target": round(sum(r["target"] for r in rows if r["effective_priority"] == t), 0),
                        "allocated": round(sum(r["allocated"] for r in rows
                                               if r["effective_priority"] == t), 0)}
                    for t in ("high", "medium", "low")},
        "rows": rows,
        "disclaimer": DISCLAIMER,
    }
