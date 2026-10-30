"""
Excel in and out, entirely in memory: uploaded files are read from the request stream and
never written to disk; exports are built in a BytesIO and streamed back.
"""

import io
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model.schema import CHOICES, DECISION_COLUMNS, FIELDS, PRIORITY_AR, STATUS_AR  # noqa: E402

DATA_SHEET = "الأسر"
MAX_ROWS = 5000
XLSX_MAGIC = b"PK\x03\x04"

HEADER_FILL = PatternFill("solid", fgColor="0F2A4A")
REQUIRED_FILL = PatternFill("solid", fgColor="16406E")
HEADER_FONT = Font(bold=True, color="FFFFFF", name="Arial", size=11)
THIN = Side(style="thin", color="C9D3E0")
EXAMPLE_ROWS = [  # clearly synthetic examples for the "مثال" sheet only
    {"ref_code": "مثال-1", "household_size": 6, "children_primary": 2, "children_intermediate": 1,
     "children_secondary": 1, "children_university": 0, "monthly_income": 3200, "income_source": "متقطع",
     "basic_expenses": 4300, "housing_status": "إيجار", "monthly_rent": 1800, "utility_bills": 650,
     "total_debt": 18000, "monthly_debt_payment": 900, "has_health_or_disability": "نعم",
     "electricity_access": "متقطع", "water_access": "متوفر", "internet_access": "متوفر"},
    {"ref_code": "مثال-2", "household_size": 4, "children_primary": 1, "children_intermediate": 0,
     "children_secondary": 0, "children_university": 1, "monthly_income": 8500, "income_source": "ثابت",
     "basic_expenses": 3900, "housing_status": "ملك", "monthly_rent": "", "utility_bills": 520,
     "total_debt": 0, "monthly_debt_payment": 0, "has_health_or_disability": "لا",
     "electricity_access": "متوفر", "water_access": "متوفر", "internet_access": "متوفر"},
]


class ExcelError(ValueError):
    pass


def _style_header(ws, headers, required):
    for col, (title, req) in enumerate(zip(headers, required), start=1):
        cell = ws.cell(row=1, column=col, value=title)
        cell.fill = REQUIRED_FILL if req else HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=THIN)
        ws.column_dimensions[get_column_letter(col)].width = max(14, len(title) + 6)
    ws.row_dimensions[1].height = 36
    ws.freeze_panes = "B2"
    ws.sheet_view.rightToLeft = True


def build_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = DATA_SHEET
    headers = [f.label for f in FIELDS]
    _style_header(ws, headers, [f.required for f in FIELDS])

    # Drop-down lists and numeric checks so most mistakes are caught while typing.
    for col, f in enumerate(FIELDS, start=1):
        letter = get_column_letter(col)
        rng = f"{letter}2:{letter}{MAX_ROWS + 1}"
        if f.kind == "choice":
            dv = DataValidation(type="list", formula1='"' + ",".join(CHOICES[f.name].values()) + '"',
                                allow_blank=not f.required, showErrorMessage=True,
                                errorTitle="قيمة غير صحيحة", error="اختر من القائمة المنسدلة.")
        elif f.kind in ("int", "money"):
            dv = DataValidation(type="whole" if f.kind == "int" else "decimal", operator="between",
                                formula1=str(f.min), formula2=str(f.max), allow_blank=True,
                                showErrorMessage=True, errorTitle="رقم غير صحيح",
                                error=f"أدخل رقمًا بين {f.min:,.0f} و {f.max:,.0f}.")
        else:
            continue
        dv.add(rng)
        ws.add_data_validation(dv)

    ex = wb.create_sheet("مثال")
    _style_header(ex, headers, [f.required for f in FIELDS])
    for r, row in enumerate(EXAMPLE_ROWS, start=2):
        for c, f in enumerate(FIELDS, start=1):
            ex.cell(row=r, column=c, value=row.get(f.name, ""))

    guide = wb.create_sheet("تعليمات")
    guide.sheet_view.rightToLeft = True
    guide.column_dimensions["A"].width = 30
    guide.column_dimensions["B"].width = 70
    lines = [
        ("كيف تعبّئ الملف", ""),
        ("1", "عبّئ ورقة «الأسر» فقط: صف لكل أسرة. ورقة «مثال» للتوضيح ولا تُقرأ عند الرفع."),
        ("2", "الأعمدة الداكنة مطلوبة. الخانات الرقمية الفارغة (الأطفال، الفواتير، الديون) تُعتبر صفرًا."),
        ("3", "لا تكتب الاسم أو رقم الهوية أو الجوال. استخدم رمزًا داخليًا للأسرة."),
        ("4", "المبالغ بالريال شهريًا. يُقبل «3,500» أو «٣٥٠٠ ريال» وسيُنظَّف تلقائيًا."),
        ("5", "المصروفات المعيشية لا تشمل الإيجار والفواتير والأقساط؛ فلكل منها عمود."),
        ("الخصوصية", "الملف يُقرأ في الذاكرة لحساب النتائج ثم يُهمل. لا يُخزَّن أي شيء على الخادم."),
        ("", ""),
        ("العمود", "الوصف"),
    ]
    for f in FIELDS:
        options = "، ".join(CHOICES[f.name].values()) if f.kind == "choice" else ""
        detail = " — ".join(x for x in [("مطلوب" if f.required else "اختياري"), f.hint, options] if x)
        lines.append((f.label, detail))
    for r, (a, b) in enumerate(lines, start=1):
        guide.cell(row=r, column=1, value=a).font = Font(bold=True, name="Arial")
        guide.cell(row=r, column=2, value=b).alignment = Alignment(wrap_text=True)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def read_upload(data: bytes, filename: str) -> pd.DataFrame:
    """Parse an uploaded .xlsx or .csv into a raw string DataFrame (no cleaning yet)."""
    name = (filename or "").lower()
    try:
        if name.endswith(".csv"):
            text = data.decode("utf-8-sig", errors="strict")
            df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
        elif name.endswith((".xlsx", ".xlsm")):
            if not data.startswith(XLSX_MAGIC):
                raise ExcelError("الملف ليس ملف Excel صالحًا.")
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            sheet = DATA_SHEET if DATA_SHEET in wb.sheetnames else (
                "النتائج" if "النتائج" in wb.sheetnames else wb.sheetnames[0])
            rows = list(wb[sheet].iter_rows(values_only=True, max_row=MAX_ROWS + 1))
            wb.close()
            if not rows:
                raise ExcelError("الملف فارغ.")
            headers = [("" if h is None else str(h)).strip() for h in rows[0]]
            df = pd.DataFrame(rows[1:], columns=headers)
        else:
            raise ExcelError("الصيغة غير مدعومة. ارفع ملف ‎.xlsx‎ أو ‎.csv‎.")
    except ExcelError:
        raise
    except UnicodeDecodeError:
        raise ExcelError("تعذّر قراءة ملف CSV. احفظه بترميز UTF-8.")
    except Exception:
        raise ExcelError("تعذّر قراءة الملف. تأكد أنه ملف Excel سليم.")
    df = df.loc[:, [c for c in df.columns if c]]
    if len(df) > MAX_ROWS:
        raise ExcelError(f"الحد الأقصى {MAX_ROWS:,} أسرة في الملف الواحد.")
    return df


def build_export(households: list[dict], allocation: dict | None, budget: float | None) -> bytes:
    """Results workbook: ranked families with inputs, scores, decisions and the budget split."""
    alloc = {r["id"]: r for r in (allocation or {}).get("rows", [])}
    rows = []
    for rank, h in enumerate(households, start=1):
        a = alloc.get(h.get("id"), {})
        row = {"الترتيب": rank}
        for f in FIELDS:
            value = h.get(f.name, "")
            row[f.label] = CHOICES[f.name].get(value, value) if f.kind == "choice" else value
        row.update({
            "درجة الحاجة": h.get("need_score"),
            "درجة المخاطر": h.get("risk_score"),
            "الأولوية المقترحة": PRIORITY_AR.get(h.get("priority"), ""),
            "ثقة النموذج": h.get("confidence"),
            "التصنيف على خريطة الأولوية": h.get("quadrant_ar", ""),
            "إجمالي المصروفات": h.get("total_expenses"),
            "العجز الشهري": h.get("monthly_deficit"),
            DECISION_COLUMNS["final_priority"]: PRIORITY_AR.get(h.get("final_priority") or "", ""),
            DECISION_COLUMNS["status"]: STATUS_AR.get(h.get("status") or "new", ""),
            DECISION_COLUMNS["note"]: h.get("note", ""),
            "الدعم المقترح": a.get("target", ""),
            "المبلغ المخصّص": a.get("allocated", ""),
            "ملاحظة التوزيع": a.get("allocation_note", ""),
        })
        rows.append(row)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="النتائج", index=False)
        ws = writer.sheets["النتائج"]
        _style_header(ws, [c.value for c in ws[1]], [False] * ws.max_column)

        summary = [
            ("تاريخ التصدير", datetime.now().strftime("%Y-%m-%d %H:%M")),
            ("عدد الأسر", len(households)),
            *[(f"أولوية {PRIORITY_AR[p]}", sum((h.get("final_priority") or h.get("priority")) == p
                                               for h in households)) for p in ("high", "medium", "low")],
        ]
        if allocation:
            summary += [("الميزانية", budget), ("إجمالي الاحتياج المقدّر", allocation["total_target"]),
                        ("المخصّص", allocation["allocated_total"]), ("المتبقي", allocation["remaining"]),
                        ("نسبة التغطية", f"{allocation['coverage']:.0%}"),
                        ("أسر لم تُغطَّ", allocation["uncovered"])]
        summary.append(("تنبيه", "التقييم أداة لدعم القرار وليس قرارًا آليًا؛ القرار النهائي للمسؤول عن التوزيع."))
        pd.DataFrame(summary, columns=["البند", "القيمة"]).to_excel(writer, sheet_name="ملخص", index=False)
        sm = writer.sheets["ملخص"]
        _style_header(sm, ["البند", "القيمة"], [False, False])
        sm.column_dimensions["A"].width, sm.column_dimensions["B"].width = 28, 70
    return buf.getvalue()
