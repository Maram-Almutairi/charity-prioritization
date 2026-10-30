"""
Capture the interface screenshots used in the report and presentation.

Starts an isolated in-process server on port 5055 (the app stores nothing anyway), imports
the messy sample file, loads the real demo families (open FIES survey), and saves screenshots to docs/screenshots/.
Requires Playwright and an installed Google Chrome.

    python docs/capture_screenshots.py
"""

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
PORT = 5055
NUMBER_FIELDS = ["household_size", "children_primary", "children_intermediate", "children_secondary",
                 "monthly_income", "basic_expenses", "utility_bills"]
CHOICE_FIELDS = ["income_source", "housing_status", "has_health_or_disability",
                 "electricity_access", "water_access", "internet_access"]


def real_family() -> dict:
    """A typical high-scoring real demo family (open FIES survey): the 90th percentile of the
    combined score, not the most extreme one, typed in by hand."""
    import pandas as pd
    from model.schema import validate
    from services import Assessor
    df = pd.read_csv(ROOT / "data" / "raw" / "real_demo_families.csv")
    a = Assessor()
    keep = [c for c in df.columns if c not in ("household_id", "head_marital_status", "source_row")]
    scores = [a.assess(validate(r))["combined_score"] for r in df[keep].to_dict("records")]
    order = sorted(range(len(df)), key=scores.__getitem__)
    rec = df.iloc[order[int(0.9 * (len(df) - 1))]].to_dict()
    return {k: (int(v) if isinstance(v, float) else v) for k, v in rec.items()}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    srv = make_server("127.0.0.1", PORT, create_app())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}/"

    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        pg.goto(url); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(600)
        pg.screenshot(path=OUT / "empty.png")

        pg.set_input_files("#file-input", str(ROOT / "data/samples/sample_messy.xlsx"))
        pg.wait_for_selector(".report"); pg.wait_for_timeout(4500)
        pg.locator(".report").screenshot(path=OUT / "import_report.png")

        pg.evaluate("loadDemo()"); pg.wait_for_timeout(4500)
        pg.click(".segmented [data-view=dashboard]")
        pg.fill("#budget-form [name=budget]", "60000")
        pg.click("#budget-form button[type=submit]"); pg.wait_for_selector(".alloc__big"); pg.wait_for_timeout(1500)
        pg.locator(".pt-hit").nth(12).hover(); pg.wait_for_timeout(400)
        pg.screenshot(path=OUT / "dashboard.png")
        pg.mouse.move(2, 2)

        pg.locator("#toplist [data-open]").first.click(); pg.wait_for_selector("#sheet[open]"); pg.wait_for_timeout(500)
        pg.screenshot(path=OUT / "sheet.png")
        pg.click("[data-action=close-sheet]")

        pg.click(".segmented [data-view=families]")
        pg.evaluate("state.lastReport=null; renderReport()")
        row = pg.locator("#table tbody tr", has=pg.locator(".badge--medium")).first
        row.locator("select[data-field=final_priority]").select_option("high"); pg.wait_for_timeout(1200)
        pg.locator("#table tbody tr.is-overridden").first.locator("input[data-field=note]").fill("زيارة ميدانية: فقد العائل عمله")
        pg.mouse.move(2, 2); pg.wait_for_timeout(300)
        pg.screenshot(path=OUT / "families.png")

        pg.click(".segmented [data-view=form]")
        f = "#family-form "
        fam = real_family()
        for name in NUMBER_FIELDS:
            if fam[name] or name == "monthly_income":
                pg.fill(f + f"[name={name}]", str(fam[name]))
        pg.fill(f + "[name=ref_code]", fam["household_id"])
        for name in CHOICE_FIELDS:
            pg.click(f + f"label:has(input[name={name}][value={fam[name]}])")
        if fam["housing_status"] == "rented":
            pg.fill(f + "[name=monthly_rent]", str(fam["monthly_rent"]))
        pg.click("button[data-mode=preview]"); pg.wait_for_selector("#result .assessment"); pg.wait_for_timeout(500)
        pg.evaluate("window.scrollTo(0, 0)")
        pg.screenshot(path=OUT / "form.png")

        pg.click(".segmented [data-view=method]"); pg.wait_for_selector(".fair tbody tr"); pg.wait_for_timeout(400)
        pg.screenshot(path=OUT / "method.png")

        m = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        m.goto(url); m.wait_for_load_state("networkidle"); m.evaluate("loadDemo()"); m.wait_for_timeout(4800)
        m.screenshot(path=OUT / "mobile.png")
        b.close()
    srv.shutdown()
    print("screenshots ->", sorted(p.name for p in OUT.glob("*.png")))


if __name__ == "__main__":
    main()
