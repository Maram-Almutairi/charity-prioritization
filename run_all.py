"""
Reproduce every artifact of the project from scratch, in order, with fixed seeds.

    python run_all.py            # data -> model -> samples -> notebook -> tests
    python run_all.py --pdf      # ... and render docs/presentation.pdf

The Saudi 2023 survey context always runs (the file is in data/raw). The two real-data tests
run when their files are present in data/external (see analysis/*.py for the download links).

Each step is a separate process so a failure stops the run with a clear message.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEPS = [
    ("1/6 توليد البيانات التجريبية (seed=42)", [sys.executable, "data/generate_synthetic.py", "--n", "3000", "--seed", "42"]),
    ("2/6 تدريب النموذجين وتقييمهما وفحص العدالة", [sys.executable, "model/train.py"]),
    ("3/6 ملفات Excel النموذجية من أسر حقيقية (نظيف + بأخطاء)", [sys.executable, "data/make_sample_files.py"]),
    ("4/6 تنفيذ دفتر التحليل الاستكشافي", [sys.executable, "notebooks/build_eda.py"]),
    ("5/6 محاكاة الأثر (docs/impact.json)", [sys.executable, "docs/impact_simulation.py"]),
    ("6/6 الاختبارات", [sys.executable, "-m", "pytest", "-q", "tests"]),
]
REAL_DATA = ROOT / "data" / "external" / "costa_rica" / "train.csv"
FIES_DATA = ROOT / "data" / "external" / "fies_philippines" / "fies.arff"
CREDIT_DATA = ROOT / "data" / "external" / "give_me_some_credit" / "GiveMeSomeCredit.arff"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", action="store_true", help="also render the presentation PDF")
    args = parser.parse_args()
    steps = list(STEPS)
    if FIES_DATA.exists():  # real demo families for the site, before the sample Excel files
        steps.insert(2, ("+ أسر حقيقية للموقع من مسح FIES المفتوح (data/raw/real_demo_families.csv)",
                         [sys.executable, "-I", "data/real_families.py"]))
    analyses = [("+ السياق السعودي من مسح الدخل والإنفاق 2023 (docs/saudi_context.json)",
                 [sys.executable, "analysis/saudi_context.py"])]
    for path, title, script in [
        (REAL_DATA, "+ التحقق على أسر حقيقية من كوستاريكا (docs/real_data_results.json)", "analysis/real_data_validation.py"),
        (CREDIT_DATA, "+ اختبار درجة المخاطر على 120 ألف شخص حقيقي (docs/credit_risk_results.json)", "analysis/credit_risk_validation.py"),
    ]:
        if path.exists():
            analyses.append((title, [sys.executable, "-I", script]))
        else:
            print(f"تنبيه: {path.relative_to(ROOT)} غير موجود، تخطّينا «{title}» (رابط التنزيل في {script})")
    steps[-1:-1] = analyses  # before the tests
    steps += ([("PDF العرض التقديمي", [sys.executable, "docs/build_pdfs.py"])] if args.pdf else [])

    for title, cmd in steps:
        print(f"\n=== {title} ===", flush=True)
        start = time.time()
        if subprocess.run(cmd, cwd=ROOT).returncode != 0:
            sys.exit(f"توقف التشغيل عند: {title}")
        print(f"--- تم في {time.time() - start:.1f} ث")
    print("\nاكتمل كل شيء. شغّل الموقع: python backend/app.py")


if __name__ == "__main__":
    main()
