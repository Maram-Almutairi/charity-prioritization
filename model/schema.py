"""
Single source of truth for the household intake schema.

Used by: the web form (via /api/schema), Excel template + import cleaning (backend/excel_io.py,
model/cleaning.py), API validation (backend/services.py) and the data generator.
"""

from dataclasses import dataclass, field

# Choice fields: canonical value -> Arabic label shown in the UI and the Excel template.
CHOICES = {
    "housing_status": {"owned": "ملك", "rented": "إيجار", "shelter": "مأوى أو سكن غير مستقر"},
    "income_source": {"stable": "ثابت", "irregular": "متقطع", "none": "لا يوجد"},
    "has_health_or_disability": {"yes": "نعم", "no": "لا"},
    "electricity_access": {"yes": "متوفر", "intermittent": "متقطع", "no": "غير متوفر"},
    "water_access": {"yes": "متوفر", "intermittent": "متقطع", "no": "غير متوفر"},
    "internet_access": {"yes": "متوفر", "intermittent": "متقطع", "no": "غير متوفر"},
}


@dataclass(frozen=True)
class Field:
    name: str
    label: str                 # Arabic header used in the form and the Excel template
    kind: str                  # "int" | "money" | "choice" | "text"
    required: bool = True
    min: float = 0
    max: float = 0
    hint: str = ""
    group: str = ""
    aliases: tuple = field(default_factory=tuple)  # extra header spellings accepted on import


FIELDS = [
    Field("ref_code", "رمز الأسرة", "text", required=False, max=30, group="family",
          hint="رمز داخلي فقط — لا تكتب الاسم أو رقم الهوية", aliases=("الرمز", "رقم الملف", "code")),
    Field("household_size", "عدد أفراد الأسرة", "int", min=1, max=30, group="family",
          aliases=("عدد الافراد", "الأفراد", "household size")),
    Field("children_primary", "طلاب المرحلة الابتدائية", "int", required=False, max=15, group="family",
          aliases=("ابتدائي", "الابتدائي")),
    Field("children_intermediate", "طلاب المرحلة المتوسطة", "int", required=False, max=15, group="family",
          aliases=("متوسط", "المتوسط")),
    Field("children_secondary", "طلاب المرحلة الثانوية", "int", required=False, max=15, group="family",
          aliases=("ثانوي", "الثانوي")),
    Field("children_university", "طلاب الجامعة", "int", required=False, max=15, group="family",
          aliases=("جامعة", "جامعي", "الجامعة")),

    Field("monthly_income", "الدخل الشهري", "money", max=200_000, group="income",
          hint="كل مصادر دخل الأسرة بالريال", aliases=("الدخل", "income")),
    Field("income_source", "مصدر الدخل", "choice", group="income", aliases=("نوع الدخل",)),

    Field("basic_expenses", "المصروفات المعيشية", "money", min=1, max=200_000, group="expenses",
          hint="غذاء، مواصلات، مستلزمات دراسية — بدون الإيجار والفواتير والأقساط",
          aliases=("المصروفات", "المصاريف", "expenses")),
    Field("housing_status", "الحالة السكنية", "choice", group="expenses", aliases=("السكن",)),
    Field("monthly_rent", "الإيجار الشهري", "money", required=False, max=100_000, group="expenses",
          hint="للمستأجرين فقط", aliases=("الإيجار", "الايجار", "rent")),
    Field("utility_bills", "فواتير الخدمات الشهرية", "money", required=False, max=20_000, group="expenses",
          hint="الكهرباء والماء والإنترنت والجوال", aliases=("الفواتير", "فواتير")),

    Field("total_debt", "إجمالي الديون الحالية", "money", required=False, max=5_000_000, group="debt",
          hint="المتبقي من القروض والأقساط والديون الشخصية", aliases=("الديون", "الدين", "debt")),
    Field("monthly_debt_payment", "القسط الشهري للديون", "money", required=False, max=200_000, group="debt",
          aliases=("القسط", "الأقساط", "قسط")),

    Field("has_health_or_disability", "حالة صحية مزمنة أو إعاقة", "choice", group="health",
          aliases=("حالة صحية", "إعاقة", "اعاقة")),
    Field("electricity_access", "الكهرباء", "choice", group="health"),
    Field("water_access", "الماء", "choice", group="health", aliases=("المياه",)),
    Field("internet_access", "الإنترنت", "choice", group="health", aliases=("الانترنت",)),
]
FIELD_BY_NAME = {f.name: f for f in FIELDS}
INPUT_FIELDS = [f.name for f in FIELDS]

# Optional columns carried through an exported file and re-imported later.
DECISION_COLUMNS = {
    "final_priority": "قرار المسؤول",
    "status": "حالة الطلب",
    "note": "ملاحظة المسؤول",
}
PRIORITY_AR = {"high": "عالية", "medium": "متوسطة", "low": "منخفضة"}
STATUS_AR = {"new": "جديدة", "under_review": "قيد المراجعة", "approved": "معتمدة", "deferred": "مؤجلة"}

# Cost weight of each school stage for the education-load feature (reviewable assumption:
# later stages cost more in transport, materials and, for university, fees and commuting).
EDUCATION_WEIGHTS = {
    "children_primary": 1.0,
    "children_intermediate": 1.2,
    "children_secondary": 1.5,
    "children_university": 2.0,
}
STUDENT_FIELDS = list(EDUCATION_WEIGHTS)


class ValidationError(ValueError):
    """Raised with a dict of field -> Arabic message."""

    def __init__(self, errors: dict):
        super().__init__("invalid input")
        self.errors = errors


def _to_number(raw):
    if isinstance(raw, bool):
        raise ValueError
    return float(raw)


def validate(payload) -> dict:
    """Validate canonical values (already normalized) and return a clean household dict."""
    if not isinstance(payload, dict):
        raise ValidationError({"_": "البيانات المرسلة غير صالحة."})
    errors, clean = {}, {}

    for f in FIELDS:
        raw = payload.get(f.name)
        empty = raw is None or (isinstance(raw, str) and raw.strip() == "")
        if f.kind == "text":
            value = "" if empty else str(raw).strip()
            if len(value) > f.max:
                errors[f.name] = f"يجب ألا يتجاوز {int(f.max)} حرفًا."
            clean[f.name] = value
            continue
        if empty:
            if f.required:
                errors[f.name] = "هذا الحقل مطلوب."
            else:
                clean[f.name] = 0
            continue
        if f.kind == "choice":
            if raw not in CHOICES[f.name]:
                errors[f.name] = "اختر قيمة من القائمة."
            else:
                clean[f.name] = raw
            continue
        try:
            value = _to_number(raw)
        except (TypeError, ValueError):
            errors[f.name] = "يجب أن يكون رقمًا."
            continue
        if f.kind == "int" and not float(value).is_integer():
            errors[f.name] = "يجب أن يكون رقمًا صحيحًا."
        elif not f.min <= value <= f.max:
            errors[f.name] = f"يجب أن يكون بين {f.min:,.0f} و {f.max:,.0f}."
        else:
            clean[f.name] = int(value) if f.kind == "int" else float(value)

    # Cross-field rules.
    if not errors.keys() & {"household_size", *STUDENT_FIELDS}:
        students = sum(clean.get(k, 0) for k in STUDENT_FIELDS)
        if students > clean["household_size"] - 1:
            errors["children_primary"] = "مجموع الطلاب يجب أن يكون أقل من عدد أفراد الأسرة."
    if clean.get("housing_status") == "rented" and clean.get("monthly_rent", 0) <= 0:
        errors["monthly_rent"] = "أدخل قيمة الإيجار الشهري للأسرة المستأجرة."
    if clean.get("housing_status") in ("owned", "shelter"):
        clean["monthly_rent"] = 0.0
    if clean.get("monthly_debt_payment", 0) > 0 and clean.get("total_debt", 0) <= 0:
        errors["total_debt"] = "يوجد قسط شهري بدون مبلغ دين. أدخل إجمالي الدين."
    if (clean.get("total_debt", 0) > 0 and clean.get("monthly_debt_payment", 0) > clean["total_debt"]):
        errors["monthly_debt_payment"] = "القسط الشهري أكبر من إجمالي الدين."

    if errors:
        raise ValidationError(errors)
    return clean


def total_expenses(h: dict) -> float:
    return (h["basic_expenses"] + h.get("monthly_rent", 0) + h.get("utility_bills", 0)
            + h.get("monthly_debt_payment", 0))
