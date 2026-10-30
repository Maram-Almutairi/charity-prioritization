"""
Cleaning pipeline for household sheets filled by charity staff (Excel / CSV).

Real sheets are messy: Arabic-Indic digits, "3,500 ريال", "نعم ✓", "ايجار" vs "إيجار",
blank rows, repeated family codes. Every step here is deterministic and logged so the
cleaning report can show exactly what was changed, what was accepted and what was rejected.

Used by backend/excel_io.py (import) and notebooks/eda.ipynb (documentation of the steps).
"""

import re

import pandas as pd

from model.schema import (CHOICES, DECISION_COLUMNS, FIELDS, PRIORITY_AR, STATUS_AR,
                          ValidationError, validate)

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789.")
NUMBER_NOISE = re.compile(r"(ريال|ر\.س|sar|rs|﷼|\s|,|،|٬)", re.IGNORECASE)
TATWEEL_DIACRITICS = re.compile(r"[ـً-ْ]")

# Unusual-but-possible values are kept and flagged for a human to double-check.
REVIEW_THRESHOLDS = {"monthly_income": 60_000, "basic_expenses": 40_000, "monthly_rent": 15_000,
                     "utility_bills": 4_000, "total_debt": 1_000_000}


def normalize_text(value) -> str:
    """Lower-case, trim and unify Arabic letter variants so spellings compare equal."""
    s = str(value).strip().lower()
    s = TATWEEL_DIACRITICS.sub("", s)
    s = re.sub("[إأآا]", "ا", s).replace("ة", "ه").replace("ى", "ي")
    s = re.sub(r"[✓✔☑]", "", s)
    return re.sub(r"\s+", " ", s).strip(" .-_")


def _choice_lookup() -> dict:
    """Every accepted spelling (Arabic label, canonical English value, synonyms) per field."""
    synonyms = {
        "housing_status": {"owned": ["تمليك", "ملكيه", "مملوك", "own"],
                           "rented": ["ايجار", "مستاجر", "استئجار", "rent", "rented"],
                           "shelter": ["ماوي", "سكن خيري", "سكن غير مستقر", "غير مستقر", "مع الاقارب"]},
        "income_source": {"stable": ["ثابت", "راتب", "تقاعد", "stable"],
                          "irregular": ["متقطع", "غير ثابت", "عمل حر", "يومي", "irregular"],
                          "none": ["لا يوجد", "لايوجد", "بدون", "ضمان فقط", "none", "no"]},
    }
    yes_no = {"yes": ["نعم", "يوجد", "1", "true", "y", "yes"], "no": ["لا", "لايوجد", "لا يوجد", "0", "false", "n", "no"]}
    access = {"yes": ["متوفر", "متوفره", "نعم", "يوجد", "1", "yes"],
              "intermittent": ["متقطع", "متقطعه", "احيانا", "مفصول احيانا", "intermittent"],
              "no": ["غير متوفر", "غير متوفره", "لا", "مفصول", "مقطوع", "0", "no"]}
    synonyms["has_health_or_disability"] = yes_no
    for f in ("electricity_access", "water_access", "internet_access"):
        synonyms[f] = access

    lookup = {}
    for field, options in CHOICES.items():
        table = {}
        for value, label in options.items():
            for spelling in [value, label, *synonyms.get(field, {}).get(value, [])]:
                table[normalize_text(spelling)] = value
        lookup[field] = table
    return lookup


CHOICE_LOOKUP = _choice_lookup()
HEADER_LOOKUP = {}
for _f in FIELDS:
    for _h in (_f.name, _f.label, *_f.aliases):
        HEADER_LOOKUP[normalize_text(_h)] = _f.name
for _name, _label in DECISION_COLUMNS.items():
    HEADER_LOOKUP[normalize_text(_label)] = _name
    HEADER_LOOKUP[normalize_text(_name)] = _name
PRIORITY_LOOKUP = {normalize_text(v): k for k, v in PRIORITY_AR.items()} | {k: k for k in PRIORITY_AR}
STATUS_LOOKUP = {normalize_text(v): k for k, v in STATUS_AR.items()} | {k: k for k in STATUS_AR}


def is_blank(value) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() in ("", "nan", "-", "—")


def parse_number(value):
    """'٣٬٥٠٠ ريال' -> 3500.0 ; returns None for blanks, raises ValueError for text."""
    if is_blank(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = NUMBER_NOISE.sub("", str(value).translate(ARABIC_DIGITS))
    if s.endswith("k"):
        return float(s[:-1]) * 1000
    return float(s)


def _is_plain_number(value) -> bool:
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value).strip())
        return True
    except ValueError:
        return False


def map_headers(columns) -> tuple[dict, list]:
    """Map sheet headers to schema fields. Returns (column -> field, unknown columns)."""
    mapping, unknown = {}, []
    for col in columns:
        key = HEADER_LOOKUP.get(normalize_text(col))
        if key and key not in mapping.values():
            mapping[col] = key
        elif not str(col).lower().startswith("unnamed"):
            unknown.append(str(col))
    return mapping, unknown


def clean_frame(raw: pd.DataFrame, first_row_number: int = 2) -> dict:
    """
    Clean a raw sheet. Returns:
      households: list of validated households (+ optional decision fields),
      issues: list of {row, field, value, action, message} (action: fixed | review | rejected),
      summary: counts for the cleaning report.
    `first_row_number` is the spreadsheet row of the first data row (2 when row 1 is headers).
    """
    mapping, unknown = map_headers(raw.columns)
    missing = [f.label for f in FIELDS if f.required and f.name not in mapping.values()]
    if missing:
        raise ValidationError({"_columns": "أعمدة مطلوبة غير موجودة في الملف: " + "، ".join(missing)})

    df = raw.rename(columns=mapping)[list(mapping.values())]
    issues, households, seen_codes = [], [], {}
    blank_rows = 0

    def log(row, field, value, action, message):
        issues.append({"row": row, "field": field, "value": "" if is_blank(value) else str(value),
                       "action": action, "message": message})

    for offset, record in enumerate(df.to_dict("records")):
        row = first_row_number + offset
        if all(is_blank(v) for v in record.values()):
            blank_rows += 1
            continue

        candidate, row_errors = {}, {}
        for f in FIELDS:
            value = record.get(f.name)
            if f.kind == "text":
                candidate[f.name] = "" if is_blank(value) else str(value).strip()
            elif f.kind == "choice":
                if is_blank(value):
                    candidate[f.name] = None
                    continue
                canon = CHOICE_LOOKUP[f.name].get(normalize_text(value))
                if canon is None:
                    row_errors[f.name] = f"قيمة غير معروفة «{value}». القيم المقبولة: " + "، ".join(CHOICES[f.name].values())
                else:
                    candidate[f.name] = canon
                    if str(value).strip() not in (canon, CHOICES[f.name][canon]):
                        log(row, f.name, value, "fixed", f"وُحّدت إلى «{CHOICES[f.name][canon]}»")
            else:
                try:
                    number = parse_number(value)
                except ValueError:
                    row_errors[f.name] = f"«{value}» ليس رقمًا."
                    continue
                if number is not None and not _is_plain_number(value):
                    log(row, f.name, value, "fixed", f"حُوّلت إلى الرقم {number:,.0f}")
                if number is None and not f.required:
                    if f.name == "monthly_rent" and normalize_text(record.get("housing_status", "")) not in ("ايجار", "rented", "مستاجر"):
                        pass  # rent blank for non-renters is expected
                    elif f.name in ("utility_bills", "total_debt", "monthly_debt_payment"):
                        log(row, f.name, value, "fixed", "خانة فارغة اعتُبرت صفرًا")
                candidate[f.name] = number
                if number is not None and number > REVIEW_THRESHOLDS.get(f.name, float("inf")):
                    log(row, f.name, value, "review", "قيمة مرتفعة بشكل غير معتاد — يُرجى التحقق منها")

        if not row_errors:
            try:
                clean = validate(candidate)
            except ValidationError as err:
                row_errors = err.errors
        if row_errors:
            for field, message in row_errors.items():
                log(row, field, record.get(field), "rejected", message)
            continue

        code = clean["ref_code"]
        if code:
            if code in seen_codes:
                log(row, "ref_code", code, "rejected", f"الرمز مكرر (ورد أولًا في الصف {seen_codes[code]}). حُذف الصف المكرر.")
                continue
            seen_codes[code] = row
        else:
            clean["ref_code"] = f"F-{row:04d}"
            log(row, "ref_code", "", "fixed", f"لا يوجد رمز — أُعطي الرمز {clean['ref_code']}")

        # Optional decision columns from a previously exported file.
        decision = record.get("final_priority")
        clean["final_priority"] = None if is_blank(decision) else PRIORITY_LOOKUP.get(normalize_text(decision))
        status = record.get("status")
        clean["status"] = STATUS_LOOKUP.get(normalize_text(status), "new") if not is_blank(status) else "new"
        clean["note"] = "" if is_blank(record.get("note")) else str(record.get("note"))[:500]
        clean["_row"] = row
        households.append(clean)

    rejected_rows = {i["row"] for i in issues if i["action"] == "rejected"}
    fixed_rows = {i["row"] for i in issues if i["action"] == "fixed"} - rejected_rows
    return {
        "households": households,
        "issues": issues,
        "summary": {
            "rows_read": len(df) - blank_rows,
            "blank_rows_skipped": blank_rows,
            "accepted": len(households),
            "accepted_with_fixes": len(fixed_rows),
            "rejected": len(rejected_rows),
            "flagged_for_review": len({i["row"] for i in issues if i["action"] == "review"} - rejected_rows),
            "unknown_columns": unknown,
        },
    }
