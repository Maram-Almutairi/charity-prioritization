"""
Reproduce every artifact of the project from scratch, in order, with fixed seeds.

    python run_all.py            # data -> model -> samples -> notebook -> tests
    python run_all.py --pdf      # ... and render docs/presentation.pdf

It also runs the validation against official Saudi data (analysis/saudi_validation.py).

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
    ("3/6 ملفات Excel النموذجية (نظيف + بأخطاء)", [sys.executable, "data/make_sample_files.py"]),
    ("4/6 تنفيذ دفتر التحليل الاستكشافي", [sys.executable, "notebooks/build_eda.py"]),
    ("5/6 محاكاة الأثر (docs/impact.json)", [sys.executable, "docs/impact_simulation.py"]),
    ("6/6 الاختبارات", [sys.executable, "-m", "pytest", "-q", "tests"]),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", action="store_true", help="also render the presentation PDF")
    args = parser.parse_args()
    steps = list(STEPS)
    steps[-1:-1] = [("+ التحقق على بيانات سعودية رسمية: مسح الدخل والإنفاق 2023 (docs/saudi_validation.json)",
                     [sys.executable, "analysis/saudi_validation.py"])]  # before the tests
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
