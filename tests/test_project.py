"""
Tests for the schema, scoring, cleaning pipeline, allocation and the stateless REST API.

Run:  python -m pytest -q
Requires model/artifacts/model.joblib (python model/train.py) and the sample files
(python data/make_sample_files.py); run_all.py does both.
"""

import io
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from app import create_app  # noqa: E402
from model.cleaning import clean_frame, normalize_text, parse_number  # noqa: E402
from model.features import add_engineered, build_features, SELECTED  # noqa: E402
from model.schema import FIELDS, ValidationError, validate  # noqa: E402
from model.scoring import NEED_WEIGHTS, RISK_WEIGHTS, need_score, risk_score  # noqa: E402
from services import allocate, quadrant, support_target  # noqa: E402

BASE = dict(ref_code="T-1", household_size=6, children_primary=2, children_intermediate=1,
            children_secondary=0, children_university=0, monthly_income=3000, income_source="irregular",
            basic_expenses=4200, housing_status="rented", monthly_rent=1800, utility_bills=600,
            total_debt=20000, monthly_debt_payment=900, has_health_or_disability="yes",
            electricity_access="intermittent", water_access="yes", internet_access="yes")
COMFORTABLE = dict(BASE, ref_code="T-2", monthly_income=15000, income_source="stable",
                   housing_status="owned", monthly_rent=0, total_debt=0, monthly_debt_payment=0,
                   has_health_or_disability="no", electricity_access="yes", children_primary=1,
                   children_intermediate=0)


def frame(*rows):
    return pd.DataFrame([validate(r) for r in rows])


# ---------- scoring ----------
def test_weights_sum_to_one():
    assert sum(NEED_WEIGHTS.values()) == pytest.approx(1)
    assert sum(RISK_WEIGHTS.values()) == pytest.approx(1)


def test_scores_bounded_and_ordered():
    df = frame(BASE, COMFORTABLE)
    need, risk = need_score(df), risk_score(df)
    assert need.between(0, 100).all() and risk.between(0, 100).all()
    assert need[0] > need[1] and risk[0] > risk[1]


def test_risk_is_proactive():
    """Same living costs, but a heavy installment and unstable income must raise risk."""
    calm = frame(COMFORTABLE)
    fragile = frame(dict(COMFORTABLE, income_source="irregular", total_debt=90000, monthly_debt_payment=4500))
    assert risk_score(fragile)[0] > risk_score(calm)[0] + 25


def test_debt_service_uses_sama_cap():
    """An installment at 33.33% of income earns the full debt-service points."""
    h = frame(dict(COMFORTABLE, monthly_income=9000, total_debt=60000, monthly_debt_payment=3000))
    from model.scoring import risk_components
    assert risk_components(h)["debt_service"][0] == pytest.approx(1.0)


def test_education_load_weights_later_stages_more():
    e = add_engineered(frame(dict(COMFORTABLE, children_primary=0, children_university=1),
                             dict(COMFORTABLE, children_primary=1, children_university=0)))
    assert e.education_load[0] > e.education_load[1]


def test_zero_income_does_not_crash():
    df = frame(dict(BASE, monthly_income=0, income_source="none"))
    X = build_features(df)
    assert list(X.columns) == SELECTED and X.notna().all().all()


# ---------- schema validation ----------
@pytest.mark.parametrize("patch, field", [
    ({"household_size": 0}, "household_size"),
    ({"household_size": 2.5}, "household_size"),
    ({"monthly_income": "abc"}, "monthly_income"),
    ({"monthly_income": -10}, "monthly_income"),
    ({"housing_status": "castle"}, "housing_status"),
    ({"has_health_or_disability": "maybe"}, "has_health_or_disability"),
    ({"children_primary": 6}, "children_primary"),          # students >= household size
    ({"monthly_rent": 0}, "monthly_rent"),                  # renter without rent
    ({"total_debt": 0}, "total_debt"),                      # installment without a debt
    ({"monthly_debt_payment": 30000}, "monthly_debt_payment"),  # installment > debt
    ({"ref_code": "x" * 31}, "ref_code"),
])
def test_validation_rejects(patch, field):
    with pytest.raises(ValidationError) as exc:
        validate({**BASE, **patch})
    assert field in exc.value.errors


def test_optional_numbers_default_to_zero_and_rent_ignored_for_owners():
    clean = validate({**COMFORTABLE, "utility_bills": "", "children_secondary": None})
    assert clean["utility_bills"] == 0 and clean["children_secondary"] == 0
    assert validate({**BASE, "housing_status": "owned"})["monthly_rent"] == 0


# ---------- cleaning ----------
def test_normalizers():
    assert parse_number("٣٬٥٠٠ ريال") == 3500
    assert parse_number("4,300") == 4300
    assert parse_number("") is None
    assert normalize_text(" إيجار ") == normalize_text("ايجار")


def raw_sheet(rows):
    labels = {f.name: f.label for f in FIELDS}
    return pd.DataFrame([{labels[k]: v for k, v in r.items()} for r in rows], dtype=object)


def test_clean_frame_fixes_rejects_and_reports():
    good = {k: str(v) for k, v in BASE.items()}
    messy = dict(good, ref_code="T-9", monthly_income="٣٠٠٠ ريال", housing_status="ايجار",
                 has_health_or_disability="نعم ✓", income_source="راتب", utility_bills="")
    dup = dict(good)                                          # same code as the first row
    bad = dict(good, ref_code="T-8", monthly_income="غير معروف")
    blank = {k: "" for k in BASE}
    res = clean_frame(raw_sheet([good, messy, dup, bad, blank]))
    s = res["summary"]
    assert (s["rows_read"], s["accepted"], s["rejected"], s["blank_rows_skipped"]) == (4, 2, 2, 1)
    fixed = res["households"][1]
    assert fixed["monthly_income"] == 3000 and fixed["housing_status"] == "rented"
    assert fixed["income_source"] == "stable" and fixed["has_health_or_disability"] == "yes"
    actions = {(i["row"], i["action"]) for i in res["issues"]}
    assert (4, "rejected") in actions and (5, "rejected") in actions   # duplicate + not a number


def test_clean_frame_missing_required_columns():
    with pytest.raises(ValidationError):
        clean_frame(pd.DataFrame({"عمود": [1]}))


# ---------- allocation ----------
def _h(i, priority, deficit, risk=10, final=None):
    return {"id": i, "priority": priority, "final_priority": final, "risk_score": risk,
            "combined_score": 50, "monthly_income": 1000, "total_expenses": 1000 + deficit}


def test_allocation_never_exceeds_budget_and_keeps_everyone():
    rows = [_h(1, "high", 3000), _h(2, "medium", 2000), _h(3, "low", 1000), _h(4, "low", 0)]
    result = allocate(rows, 4000)
    by_id = {r["id"]: r for r in result["rows"]}
    assert result["allocated_total"] <= 4000 and len(result["rows"]) == 4
    assert by_id[1]["allocated"] == 3000 and 0 < by_id[2]["allocated"] < 2000
    assert by_id[3]["allocated"] == 0 and "قرار المسؤول" in by_id[3]["allocation_note"]
    assert result["uncovered"] == 1


def test_lower_tier_waits_while_higher_tier_uncovered():
    rows = [_h(1, "high", 10_000), _h(2, "high", 10_001), _h(3, "low", 200)]
    result = allocate(rows, 5_000)
    by_id = {r["id"]: r for r in result["rows"]}
    assert by_id[3]["allocated"] == 0          # no trickle to the low tier
    assert by_id[1]["allocated"] + by_id[2]["allocated"] <= 5_000


def test_decision_override_changes_tier():
    result = allocate([_h(1, "low", 2000, final="high"), _h(2, "high", 2000)], 2000)
    assert all(r["allocated"] == 1000 for r in result["rows"])


def test_preventive_buffer_and_quadrants():
    assert support_target(_h(1, "medium", 0, risk=60)) == pytest.approx(100)
    assert quadrant(60, 70) == "urgent" and quadrant(20, 70) == "prevent"
    assert quadrant(70, 20) == "support" and quadrant(10, 10) == "monitor"


# ---------- API ----------
@pytest.fixture(scope="module")
def client():
    return create_app().test_client()


def test_health_reports_no_storage(client):
    assert client.get("/api/health").get_json()["storage"] == "none"


def test_assess_endpoint(client):
    body = client.post("/api/assess", json=BASE).get_json()
    assert body["priority"] in {"high", "medium", "low"} and body["disclaimer"]
    assert 0 <= body["need_score"] <= 100 and body["score_breakdown"]
    assert client.post("/api/assess", json=COMFORTABLE).get_json()["priority"] == "low"
    r = client.post("/api/assess", json={**BASE, "household_size": "x"})
    assert r.status_code == 422 and "household_size" in r.get_json()["fields"]


def test_template_and_import_round_trip(client):
    t = client.get("/api/template")
    assert t.status_code == 200 and t.data[:2] == b"PK"
    data = (ROOT / "data/samples/sample_messy.xlsx").read_bytes()
    r = client.post("/api/import", data={"file": (io.BytesIO(data), "m.xlsx")}, content_type="multipart/form-data")
    body = r.get_json()
    assert r.status_code == 200 and body["summary"]["rejected"] == 4 and body["summary"]["accepted"] == 25
    families = body["households"]
    for i, h in enumerate(families):
        h["id"] = i
    exported = client.post("/api/export", json={"budget": 30000, "households": families})
    assert exported.status_code == 200
    again = client.post("/api/import", data={"file": (io.BytesIO(exported.data), "x.xlsx")},
                        content_type="multipart/form-data").get_json()
    assert again["summary"]["accepted"] == 25 and again["summary"]["rejected"] == 0


def test_import_rejects_bad_files(client):
    for payload, name in [(b"hello", "x.xlsx"), (b"a,b", "x.pdf")]:
        r = client.post("/api/import", data={"file": (io.BytesIO(payload), name)}, content_type="multipart/form-data")
        assert r.status_code == 422 and r.get_json()["error"] == "file"


def test_allocate_recomputes_scores_server_side(client):
    families = client.get("/api/demo?n=10").get_json()["households"]
    for i, h in enumerate(families):
        h.update(id=i, need_score=0, risk_score=0, combined_score=0, priority="low")  # tampered
    a = client.post("/api/allocate", json={"budget": 20000, "households": families}).get_json()
    assert len(a["rows"]) == 10 and a["allocated_total"] <= 20000
    assert client.post("/api/allocate", json={"budget": 0, "households": []}).status_code == 422


def test_no_persistence_endpoints(client):
    assert client.get("/api/households").status_code == 404
    assert not list((ROOT / "backend").glob("*.db"))


def test_privacy_headers(client):
    r = client.get("/api/schema")
    assert r.headers["Cache-Control"] == "no-store"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_frontend_served(client):
    assert client.get("/").status_code == 200 and client.get("/app.js").status_code == 200


# ---------- in-browser version (Pyodide) ----------
def test_light_model_matches_sklearn():
    """The numpy-only model used in the browser gives the same probabilities as scikit-learn."""
    import joblib
    import numpy as np
    from model.light_model import load_bundle
    sk = joblib.load(ROOT / "model/artifacts/model.joblib")["model"]
    light = load_bundle(ROOT / "model/artifacts/model_params.json")["model"]
    df = pd.read_csv(ROOT / "data/raw/synthetic_households.csv").head(500)
    X = build_features(df)
    assert np.allclose(sk.predict_proba(X), light.predict_proba(X), atol=1e-10)
    assert list(sk.classes_) == list(light.classes_)


def test_browser_dispatch_matches_server():
    import json
    from api_core import ApiCore
    from model.light_model import load_bundle
    from services import Assessor
    core = ApiCore(Assessor(bundle=load_bundle(ROOT / "model/artifacts/model_params.json")),
                   ROOT / "data/raw/synthetic_households.csv")
    status, text = core.dispatch("/assess", json.dumps(BASE))
    server = create_app().test_client().post("/api/assess", json=BASE).get_json()
    browser = json.loads(text)
    assert status == 200 and browser["priority"] == server["priority"]
    assert browser["need_score"] == server["need_score"] and browser["confidence"] == server["confidence"]
    status, text = core.dispatch("/assess", json.dumps({**BASE, "household_size": "x"}))
    assert status == 422 and "household_size" in json.loads(text)["fields"]
    status, data = core.dispatch("/import", "null", (ROOT / "data/samples/sample_messy.xlsx").read_bytes(), "m.xlsx")
    assert status == 200 and json.loads(data)["summary"]["accepted"] == 25
    status, data = core.dispatch("/template")
    assert status == 200 and data[:2] == b"PK"
