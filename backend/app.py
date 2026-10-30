"""
REST API (Flask) for the charity prioritization tool. Also serves the frontend.

Privacy by design: the server is stateless. Family data arrives with a request, is scored in
memory and returned; nothing is written to disk or a database. The browser page holds the
working list, and the charity keeps its own records as exported Excel/PDF files.

Endpoints
---------
GET   /api/health                 liveness check
GET   /api/schema                 intake fields, labels and choices (drives the form)
GET   /api/model-info             model type, metrics, fairness, score weights
GET   /api/template               Excel template to fill (with drop-downs and instructions)
POST  /api/import                 multipart Excel/CSV -> cleaned + scored families + cleaning report
POST  /api/assess                 score one family
POST  /api/allocate               budget split for the families sent {"budget", "households"}
POST  /api/export                 Excel workbook of the families sent (+ allocation)
GET   /api/demo                   synthetic demo families, scored (nothing stored)

Run:
    python backend/app.py         -> http://127.0.0.1:5000
"""

import io
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request, send_file, send_from_directory

from excel_io import ExcelError, build_export, build_template, read_upload
from services import DISCLAIMER, QUADRANTS_AR, Assessor, allocate
from model.cleaning import clean_frame
from model.schema import (CHOICES, DECISION_COLUMNS, FIELDS, INPUT_FIELDS, PRIORITY_AR,
                          STATUS_AR, ValidationError, validate)
from model.scoring import COMPONENT_LABELS_AR, NEED_WEIGHTS, RISK_WEIGHTS

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
MAX_UPLOAD_MB = 5
MAX_BATCH = 5000
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    app.json.ensure_ascii = False
    assessor = Assessor()

    @app.after_request
    def security_headers(resp):
        # Family data must not linger in browser or proxy caches.
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        return resp

    @app.errorhandler(ValidationError)
    def on_validation_error(err):
        return jsonify({"error": "validation", "fields": err.errors}), 422

    @app.errorhandler(ExcelError)
    def on_excel_error(err):
        return jsonify({"error": "file", "message": str(err)}), 422

    @app.errorhandler(413)
    def on_too_large(_err):
        return jsonify({"error": "file", "message": f"حجم الملف أكبر من {MAX_UPLOAD_MB} ميجابايت."}), 413

    @app.errorhandler(404)
    def on_not_found(_err):
        return jsonify({"error": "not_found"}), 404

    @app.errorhandler(500)
    def on_server_error(_err):
        return jsonify({"error": "server", "message": "حدث خطأ غير متوقع."}), 500

    def scored(households: list[dict]) -> list[dict]:
        """Attach scores to validated families, keeping decision fields and client ids."""
        results = assessor.assess_many([{k: h[k] for k in INPUT_FIELDS} for h in households])
        return [{**h, **r} for h, r in zip(households, results)]

    def families_from_body(body) -> list[dict]:
        items = (body or {}).get("households")
        if not isinstance(items, list) or len(items) > MAX_BATCH:
            raise ValidationError({"households": "قائمة الأسر غير صالحة."})
        return items

    # ---- API ---------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"status": "ok", "model": assessor.kind, "storage": "none"}

    @app.get("/api/schema")
    def schema():
        return {"fields": [{"name": f.name, "label": f.label, "kind": f.kind, "required": f.required,
                            "min": f.min, "max": f.max, "hint": f.hint, "group": f.group,
                            "choices": CHOICES.get(f.name)} for f in FIELDS],
                "priority": PRIORITY_AR, "status": STATUS_AR, "quadrants": QUADRANTS_AR,
                "disclaimer": DISCLAIMER}

    @app.get("/api/model-info")
    def model_info():
        return {"kind": assessor.kind, "features": assessor.features,
                "feature_labels": assessor.feature_labels, "metrics": assessor.metrics,
                "need_weights": NEED_WEIGHTS, "risk_weights": RISK_WEIGHTS,
                "component_labels": COMPONENT_LABELS_AR, "disclaimer": DISCLAIMER}

    @app.get("/api/template")
    def template():
        return send_file(io.BytesIO(build_template()), mimetype=XLSX_MIME, as_attachment=True,
                         download_name="قالب-بيانات-الأسر.xlsx")

    @app.post("/api/import")
    def import_file():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise ExcelError("لم يُرفق ملف.")
        raw = read_upload(upload.read(), upload.filename)  # read into memory only
        result = clean_frame(raw)
        known = {f.label for f in FIELDS} | set(DECISION_COLUMNS.values())
        result["summary"]["unknown_columns"] = [c for c in result["summary"]["unknown_columns"]
                                                if c not in known and not _is_export_column(c)]
        return {"households": scored(result["households"]), "issues": result["issues"],
                "summary": result["summary"], "disclaimer": DISCLAIMER}

    @app.post("/api/assess")
    def assess():
        body = request.get_json(silent=True)
        return {**assessor.assess(validate(body)), "disclaimer": DISCLAIMER}

    @app.post("/api/allocate")
    def allocate_budget():
        body = request.get_json(silent=True) or {}
        try:
            budget = float(body.get("budget"))
        except (TypeError, ValueError):
            raise ValidationError({"budget": "أدخل الميزانية رقمًا."})
        if not 0 < budget <= 100_000_000:
            raise ValidationError({"budget": "الميزانية يجب أن تكون أكبر من صفر."})
        families = families_from_body(body)
        for h in families:  # recompute from inputs; never trust client-side scores
            h.update(validate({k: h.get(k) for k in INPUT_FIELDS}))
        rescored = scored(families)
        return allocate(rescored, budget)

    @app.post("/api/export")
    def export():
        body = request.get_json(silent=True) or {}
        families = families_from_body(body)
        clean = []
        for h in families:
            item = validate({k: h.get(k) for k in INPUT_FIELDS})
            item.update({"id": h.get("id"), "final_priority": h.get("final_priority") if h.get("final_priority") in PRIORITY_AR else None,
                         "status": h.get("status") if h.get("status") in STATUS_AR else "new",
                         "note": str(h.get("note") or "")[:500]})
            clean.append(item)
        rescored = scored(clean)
        budget = body.get("budget")
        allocation = allocate(rescored, float(budget)) if budget else None
        data = build_export(rescored, allocation, float(budget) if budget else None)
        return send_file(io.BytesIO(data), mimetype=XLSX_MIME, as_attachment=True,
                         download_name="نتائج-ترتيب-الأسر.xlsx")

    @app.get("/api/demo")
    def demo():
        n = max(1, min(int(request.args.get("n", 30)), 200))
        sample = pd.read_csv(ROOT / "data" / "raw" / "synthetic_households.csv").sample(n, random_state=7)
        families = []
        for rec in sample.to_dict("records"):
            item = validate({**{k: rec.get(k) for k in INPUT_FIELDS},
                             "ref_code": f"تجريبي-{rec['household_id'][-4:]}"})
            families.append({**item, "final_priority": None, "status": "new", "note": ""})
        return {"households": scored(families), "disclaimer": DISCLAIMER}

    # ---- Frontend ------------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(FRONTEND, "index.html")

    @app.get("/<path:filename>")
    def static_files(filename: str):
        return send_from_directory(FRONTEND, filename)

    return app


EXPORT_ONLY_COLUMNS = {"الترتيب", "درجة الحاجة", "درجة المخاطر", "الأولوية المقترحة", "ثقة النموذج",
                       "التصنيف على خريطة الأولوية", "إجمالي المصروفات", "العجز الشهري",
                       "الدعم المقترح", "المبلغ المخصّص", "ملاحظة التوزيع"}


def _is_export_column(name: str) -> bool:
    return name in EXPORT_ONLY_COLUMNS


if __name__ == "__main__":
    create_app().run(debug=False, port=5000)
