// Runs the project's own Python in the browser (Pyodide) when no Flask server is available,
// e.g. on GitHub Pages. The same files as the server are loaded — cleaning, scoring, model,
// allocation, Excel I/O — and called through backend/api_core.py: ApiCore.dispatch().
// Family data never leaves the visitor's device in this mode.
"use strict";

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/";
const PY_SOURCES = [
  "model/__init__.py", "model/schema.py", "model/cleaning.py", "model/features.py", "model/scoring.py",
  "model/light_model.py", "backend/services.py", "backend/excel_io.py", "backend/api_core.py",
];
const PY_DATA = ["model/artifacts/model_params.json", "data/raw/synthetic_households.csv"];

const PyEngine = {
  ready: null,

  // Is the Flask API reachable? (true locally, false on a static host)
  async hasServer() {
    if (location.hostname.endsWith(".github.io")) return false;  // static hosting: no API
    try {
      const res = await fetch("/api/health", { cache: "no-store" });
      if (!res.ok) return false;
      return (await res.json()).storage === "none";
    } catch {
      return false;
    }
  },

  boot(onStep = () => {}) {
    this.ready ||= (async () => {
      onStep("تحميل Python…");
      await new Promise((resolve, reject) => {
        const s = Object.assign(document.createElement("script"), { src: PYODIDE_URL + "pyodide.js" });
        s.onload = resolve;
        s.onerror = () => reject(new Error("تعذّر تحميل Python. تحقق من الاتصال بالإنترنت."));
        document.head.append(s);
      });
      const py = await loadPyodide({ indexURL: PYODIDE_URL });
      onStep("تحميل pandas وnumpy وopenpyxl…");
      await py.loadPackage(["numpy", "pandas", "micropip"]);
      // openpyxl is pure Python and not bundled with Pyodide: install the same version as requirements.txt.
      await py.runPythonAsync('import micropip\nawait micropip.install("openpyxl==3.1.5")');

      onStep("تحميل ملفات المشروع…");
      const root = new URL("../", location.href);  // the site serves the repository root
      for (const rel of [...PY_SOURCES, ...PY_DATA]) {
        const res = await fetch(new URL(rel, root));
        if (!res.ok) throw new Error(`تعذّر تحميل ${rel}`);
        const dir = "/app/" + rel.split("/").slice(0, -1).join("/");
        py.FS.mkdirTree(dir);
        py.FS.writeFile("/app/" + rel, new Uint8Array(await res.arrayBuffer()));
      }
      onStep("تجهيز النموذج…");
      py.runPython(`
import sys
sys.path[:0] = ["/app", "/app/backend"]
from api_core import ApiCore
from services import Assessor
from model.light_model import load_bundle
core = ApiCore(Assessor(bundle=load_bundle("/app/model/artifacts/model_params.json")),
               "/app/data/raw/synthetic_households.csv")
`);
      return py;
    })();
    return this.ready;
  },

  // Same contract as the REST API: returns {status, json} or {status, bytes}.
  async call(path, body = null, file = null) {
    const py = await this.ready;
    const dispatch = py.globals.get("core").dispatch;
    const bytes = file ? new Uint8Array(await file.arrayBuffer()) : null;
    const result = dispatch(path, JSON.stringify(body), bytes, file ? file.name : "");
    const [status, payload] = result.toJs();
    result.destroy();
    dispatch.destroy();
    return typeof payload === "string" ? { status, json: JSON.parse(payload) } : { status, bytes: payload };
  },
};
