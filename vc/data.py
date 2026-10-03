"""Load synthetic encounters and check them against their form's fields."""
import datetime as dt
import json

from vc.forms import FORMS
from vc.schema import load_fields


def load_encounters(form="oasis"):
    spec = FORMS[form]
    encs = [json.loads(p.read_text()) for p in sorted(spec["visits"].glob(spec["visit_glob"]))]
    for e in encs:
        e["form"] = form
    return encs


def validate(enc, fields):
    """Return a list of problems; empty means the encounter is consistent."""
    by_id = {f["id"]: f for f in fields}
    errs = []
    v = enc["visit"]
    weekday = dt.date.fromisoformat(v["date"]).strftime("%A")
    if weekday != v["weekday"]:
        errs.append(f"visit {v['date']} is a {weekday}, file says {v['weekday']}")
    for i, t in enumerate(enc["turns"]):
        role = enc["speakers"].get(t["who"])
        if role is None:
            errs.append(f"turn {i}: speaker {t['who']} has no role")
        if role == "clinician" and "route" not in t:
            errs.append(f"turn {i}: clinician turn without route label")
        for fid in t.get("route", []):
            if fid not in by_id:
                errs.append(f"turn {i}: route to unknown field {fid}")
    for fid, val in enc["gold"].items():
        f = by_id.get(fid)
        if f is None:
            errs.append(f"gold: unknown field {fid}")
            continue
        codes = {c for c, _ in f.get("options", [])}
        t = f["type"]
        if t in ("radio", "select") and val not in codes:
            errs.append(f"gold {fid}: {val} is not an option")
        if t == "checkbox" and (not isinstance(val, list) or not set(val) <= codes):
            errs.append(f"gold {fid}: {val} not a subset of options")
        if t == "date":
            dt.date.fromisoformat(val)
        if t == "number" and not isinstance(val, (int, float)):
            errs.append(f"gold {fid}: {val} is not a number")
        if t in ("text", "textarea") and not (isinstance(val, str) or (isinstance(val, dict) and val.get("keywords"))):
            errs.append(f"gold {fid}: text gold must be a string or {{'keywords': [...]}}")
    return errs


if __name__ == "__main__":
    for form in FORMS:
        fields = load_fields(form)
        for enc in load_encounters(form):
            errs = validate(enc, fields)
            clin = [t for t in enc["turns"] if enc["speakers"][t["who"]] == "clinician"]
            print(f"[{form}] {enc['id']}: {len(enc['turns'])} turns, {len(clin)} clinician, "
                  f"{len(enc['gold'])} gold fields, {len(fields) - len(enc['gold'])} should stay empty"
                  f" -> {'OK' if not errs else errs}")
