"""
The application's operations as plain Python, shared by the two ways the site runs:

- backend/app.py      Flask server (local / any Python host)
- frontend/index.html in the browser through Pyodide (static hosting, e.g. GitHub Pages),
                      which calls ApiCore.dispatch() below

Both run exactly the same Python: cleaning, scoring, model, allocation and Excel I/O.
Nothing here stores family data.
"""

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from excel_io import ExcelError, build_export, build_template, read_upload  # noqa: E402
from model.cleaning import clean_frame  # noqa: E402
from model.schema import (CHOICES, DECISION_COLUMNS, FIELDS, INPUT_FIELDS, PRIORITY_AR,  # noqa: E402
                          STATUS_AR, ValidationError, validate)
from model.scoring import COMPONENT_LABELS_AR, NEED_WEIGHTS, RISK_WEIGHTS  # noqa: E402
from services import DISCLAIMER, QUADRANTS_AR, Assessor, allocate  # noqa: E402

MAX_BATCH = 5000
EXPORT_ONLY_COLUMNS = {"الترتيب", "درجة الحاجة", "درجة المخاطر", "الأولوية المقترحة", "ثقة النموذج",
                       "التصنيف على خريطة الأولوية", "إجمالي المصروفات", "العجز الشهري",
                       "الدعم المقترح", "المبلغ المخصّص", "ملاحظة التوزيع"}


class ApiCore:
    def __init__(self, assessor: Assessor, demo_csv: Path):
        self.assessor = assessor
        self.demo_csv = Path(demo_csv)

    # ---- helpers -------------------------------------------------------------
    def scored(self, households: list[dict]) -> list[dict]:
        """Attach scores to validated families, keeping decision fields and client ids."""
        results = self.assessor.assess_many([{k: h[k] for k in INPUT_FIELDS} for h in households])
        return [{**h, **r} for h, r in zip(households, results)]

    @staticmethod
    def families_from_body(body) -> list[dict]:
        items = (body or {}).get("households")
        if not isinstance(items, list) or len(items) > MAX_BATCH:
            raise ValidationError({"households": "قائمة الأسر غير صالحة."})
        return items

    # ---- operations ------------------------------------------------------------
    def health(self) -> dict:
        return {"status": "ok", "model": self.assessor.kind, "storage": "none"}

    def schema(self) -> dict:
        return {"fields": [{"name": f.name, "label": f.label, "kind": f.kind, "required": f.required,
                            "min": f.min, "max": f.max, "hint": f.hint, "group": f.group,
                            "choices": CHOICES.get(f.name)} for f in FIELDS],
                "priority": PRIORITY_AR, "status": STATUS_AR, "quadrants": QUADRANTS_AR,
                "disclaimer": DISCLAIMER}

    def model_info(self) -> dict:
        a = self.assessor
        return {"kind": a.kind, "features": a.features, "feature_labels": a.feature_labels,
                "metrics": a.metrics, "need_weights": NEED_WEIGHTS, "risk_weights": RISK_WEIGHTS,
                "component_labels": COMPONENT_LABELS_AR, "disclaimer": DISCLAIMER}

    @staticmethod
    def template() -> bytes:
        return build_template()

    def import_file(self, data: bytes, filename: str) -> dict:
        raw = read_upload(data, filename)  # in memory only
        result = clean_frame(raw)
        known = {f.label for f in FIELDS} | set(DECISION_COLUMNS.values())
        result["summary"]["unknown_columns"] = [c for c in result["summary"]["unknown_columns"]
                                                if c not in known and c not in EXPORT_ONLY_COLUMNS]
        return {"households": self.scored(result["households"]), "issues": result["issues"],
                "summary": result["summary"], "disclaimer": DISCLAIMER}

    def assess(self, body) -> dict:
        return {**self.assessor.assess(validate(body)), "disclaimer": DISCLAIMER}

    def allocate(self, body) -> dict:
        body = body or {}
        try:
            budget = float(body.get("budget"))
        except (TypeError, ValueError):
            raise ValidationError({"budget": "أدخل الميزانية رقمًا."})
        if not 0 < budget <= 100_000_000:
            raise ValidationError({"budget": "الميزانية يجب أن تكون أكبر من صفر."})
        families = self.families_from_body(body)
        for h in families:  # recompute from inputs; never trust client-side scores
            h.update(validate({k: h.get(k) for k in INPUT_FIELDS}))
        return allocate(self.scored(families), budget)

    def export(self, body) -> bytes:
        body = body or {}
        clean = []
        for h in self.families_from_body(body):
            item = validate({k: h.get(k) for k in INPUT_FIELDS})
            item.update({"id": h.get("id"),
                         "final_priority": h.get("final_priority") if h.get("final_priority") in PRIORITY_AR else None,
                         "status": h.get("status") if h.get("status") in STATUS_AR else "new",
                         "note": str(h.get("note") or "")[:500]})
            clean.append(item)
        rescored = self.scored(clean)
        budget = body.get("budget")
        allocation = allocate(rescored, float(budget)) if budget else None
        return build_export(rescored, allocation, float(budget) if budget else None)

    def demo(self, n: int = 30) -> dict:
        n = max(1, min(int(n), 200))
        sample = pd.read_csv(self.demo_csv).sample(n, random_state=7)
        families = []
        for rec in sample.to_dict("records"):
            item = validate({**{k: rec.get(k) for k in INPUT_FIELDS},
                             "ref_code": f"تجريبي-{rec['household_id'][-4:]}"})
            families.append({**item, "final_priority": None, "status": "new", "note": ""})
        return {"households": self.scored(families), "disclaimer": DISCLAIMER}

    # ---- single entry point for the in-browser version --------------------------
    def dispatch(self, path: str, body_json: str = "null", file_bytes=None, filename: str = ""):
        """Route like the REST API and return (status, json_text) or (status, bytes) for files."""
        body = json.loads(body_json) if body_json else None
        try:
            route = path.split("?")[0]
            if route == "/health":
                out = self.health()
            elif route == "/schema":
                out = self.schema()
            elif route == "/model-info":
                out = self.model_info()
            elif route == "/template":
                return 200, self.template()
            elif route == "/import":
                if not file_bytes:
                    raise ExcelError("لم يُرفق ملف.")
                data = file_bytes.to_bytes() if hasattr(file_bytes, "to_bytes") else bytes(file_bytes)
                out = self.import_file(data, filename)
            elif route == "/assess":
                out = self.assess(body)
            elif route == "/allocate":
                out = self.allocate(body)
            elif route == "/export":
                return 200, self.export(body)
            elif route == "/demo":
                n = int(path.split("n=")[1]) if "n=" in path else 30
                out = self.demo(n)
            else:
                return 404, json.dumps({"error": "not_found"})
            return 200, json.dumps(out, ensure_ascii=False)
        except ValidationError as err:
            return 422, json.dumps({"error": "validation", "fields": err.errors}, ensure_ascii=False)
        except ExcelError as err:
            return 422, json.dumps({"error": "file", "message": str(err)}, ensure_ascii=False)
