"""
A dependency-light copy of the trained logistic-regression pipeline (StandardScaler + LogisticRegression).

model/train.py exports the fitted parameters to model/artifacts/model_params.json. This class
rebuilds the exact same predict_proba with numpy only, so the site can run where scikit-learn
is not available (Python in the browser via Pyodide). tests/test_project.py checks that it
matches the scikit-learn pipeline on the test households.
"""

import json
from pathlib import Path

import numpy as np


class _Scaler:
    def __init__(self, mean, scale):
        self.mean_, self.scale_ = np.asarray(mean, float), np.asarray(scale, float)

    def transform(self, X):
        return (np.asarray(X, float) - self.mean_) / self.scale_


class _LogReg:
    def __init__(self, coef, intercept, classes):
        self.coef_, self.intercept_ = np.asarray(coef, float), np.asarray(intercept, float)
        self.classes_ = np.asarray(classes)


class LightPipeline:
    """Same interface the app uses: classes_, predict_proba(X), named_steps[...]."""

    def __init__(self, params: dict):
        self.named_steps = {
            "standardscaler": _Scaler(params["scaler_mean"], params["scaler_scale"]),
            "logisticregression": _LogReg(params["coef"], params["intercept"], params["classes"]),
        }
        self.classes_ = self.named_steps["logisticregression"].classes_

    def predict_proba(self, X):
        z = self.named_steps["standardscaler"].transform(X)
        lr = self.named_steps["logisticregression"]
        logits = z @ lr.coef_.T + lr.intercept_
        logits -= logits.max(axis=1, keepdims=True)          # numerically stable softmax
        e = np.exp(logits)
        return e / e.sum(axis=1, keepdims=True)


def export_params(pipeline, kind: str, features: list, feature_labels: dict, metrics: dict) -> dict:
    scaler, lr = pipeline.named_steps["standardscaler"], pipeline.named_steps["logisticregression"]
    return {"kind": kind, "features": features, "feature_labels": feature_labels, "metrics": metrics,
            "classes": [str(c) for c in lr.classes_], "scaler_mean": scaler.mean_.tolist(),
            "scaler_scale": scaler.scale_.tolist(), "coef": lr.coef_.tolist(), "intercept": lr.intercept_.tolist()}


def load_bundle(path: Path) -> dict:
    """Read model_params.json into the same bundle shape as model.joblib."""
    params = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"model": LightPipeline(params), "kind": params["kind"], "features": params["features"],
            "feature_labels": params["feature_labels"], "metrics": params["metrics"]}
