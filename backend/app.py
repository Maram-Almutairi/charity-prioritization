"""
REST API (Flask) for the charity prioritization tool. Also serves the frontend.

Privacy by design: the server is stateless. Family data arrives with a request, is scored in
memory and returned; nothing is written to disk or a database. The browser page holds the
working list, and the charity keeps its own records as exported Excel/PDF files.

The operations themselves live in api_core.py, shared with the in-browser (Pyodide) version
of the site, so both run exactly the same Python.

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
GET   /api/demo                   real demo families (open FIES survey), scored (nothing stored)

Run:
    python backend/app.py         -> http://127.0.0.1:5000
"""

import io
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from api_core import ApiCore
from excel_io import ExcelError
from model.schema import ValidationError
from services import Assessor

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
MAX_UPLOAD_MB = 5
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    app.json.ensure_ascii = False
    core = ApiCore(Assessor(), ROOT / "data" / "raw" / "real_demo_families.csv")

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

    def xlsx(data: bytes, name: str):
        return send_file(io.BytesIO(data), mimetype=XLSX_MIME, as_attachment=True, download_name=name)

    # ---- API ---------------------------------------------------------------
    app.get("/api/health")(core.health)
    app.get("/api/schema")(core.schema)
    app.get("/api/model-info")(core.model_info)

    @app.get("/api/template")
    def template():
        return xlsx(core.template(), "قالب-بيانات-الأسر.xlsx")

    @app.post("/api/import")
    def import_file():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise ExcelError("لم يُرفق ملف.")
        return core.import_file(upload.read(), upload.filename)

    @app.post("/api/assess")
    def assess():
        return core.assess(request.get_json(silent=True))

    @app.post("/api/allocate")
    def allocate_budget():
        return core.allocate(request.get_json(silent=True))

    @app.post("/api/export")
    def export():
        return xlsx(core.export(request.get_json(silent=True)), "نتائج-ترتيب-الأسر.xlsx")

    @app.get("/api/demo")
    def demo():
        return core.demo(request.args.get("n", 30))

    # ---- Frontend ------------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(FRONTEND, "index.html")

    @app.get("/<path:filename>")
    def static_files(filename: str):
        return send_from_directory(FRONTEND, filename)

    return app


if __name__ == "__main__":
    create_app().run(debug=False, port=5000)
