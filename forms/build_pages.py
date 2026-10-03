"""Render each demo form as a realistic EHR documentation screen: mock/oasis.html, mock/general.html.

Plain and dense like a real assessment screen (slim app bar, patient banner, section
tabs, label/answer rows, standard controls, a vitals flowsheet, a medications table,
a save bar). Standard markup underneath: fieldset/legend, role=group, aria-labelledby,
aria-describedby, aria-label on options. No hints for the extension. The EHR name,
clinicians, and patients are made up.
"""
import html
import json
import re
from collections import OrderedDict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vc.data import load_encounters  # noqa: E402
from vc.forms import FORMS  # noqa: E402
from vc.schema import ROOT, legend, load_fields, prompt_text  # noqa: E402

e = html.escape

PAGES = {
    "oasis": {
        "doc_title": "Start of Care OASIS · Demo Health",
        "activity": "OASIS-E2 Start of Care",
        "role": "Home health RN",
        "foot": "Item wording from CMS OASIS-E2. 17 of 106 items shown.",
        "sections": [
            ("A", "Administrative", ["M0066", "A1005", "A1010", "M0150", "A1110"]),
            ("C", "Cognitive patterns", ["C0200", "C0300"]),
            ("D", "Mood", ["D0700"]),
            ("F", "Living situation", ["M1100"]),
            ("GG", "Functional abilities", ["GG0100", "GG0110"]),
            ("J", "Health conditions", ["M1033", "J0510", "J0530", "M1400"]),
            ("K", "Height and weight", ["M1060"]),
            ("N", "Medications", ["N0415"]),
        ],
        "patients": {
            "enc-01": {"name": "Walter Briggs", "age": 81, "sex": "M", "dob": "02/09/1945", "mrn": "000418", "clinician": "Maya, RN"},
            "enc-02": {"name": "Rosa Delgado", "age": 74, "sex": "F", "dob": "11/03/1951", "mrn": "000533", "clinician": "Ben, PT"},
            "enc-03": {"name": "Harold Kim", "age": 88, "sex": "M", "dob": "07/22/1938", "mrn": "000607", "clinician": "Dana, RN"},
            "enc-04": {"name": "Gloria Turner", "age": 69, "sex": "F", "dob": "04/30/1957", "mrn": "000712", "clinician": "Priya, RN"},
        },
    },
    "general": {
        "doc_title": "New Patient Visit · Demo Health",
        "activity": "New patient intake",
        "role": "Primary care",
        "foot": "Demo form. Structure follows a real new-patient history form.",
        "sections": [
            ("1", "Reason for visit", ["VISIT"]),
            ("2", "Vital signs", ["VITALS"]),
            ("3", "Allergies", ["ALLERGY"]),
            ("4", "Medications", ["MEDS"]),
            ("5", "Medical history", ["PMH", "SURG"]),
            ("6", "Social history", ["TOBACCO", "ALCOHOL", "EXERCISE"]),
            ("7", "Review of systems", ["ROS"]),
            ("8", "Immunizations", ["FLU"]),
        ],
        "patients": {
            "gen-01": {"name": "Daniel Ortiz", "age": 45, "sex": "M", "dob": "03/14/1981", "mrn": "100231", "clinician": "Dr. Lee, MD"},
            "gen-02": {"name": "Linda Park", "age": 62, "sex": "F", "dob": "06/02/1964", "mrn": "100245", "clinician": "Dr. Patel, MD"},
            "gen-03": {"name": "Aisha Bello", "age": 29, "sex": "F", "dob": "01/19/1997", "mrn": "100258", "clinician": "Jordan, PA"},
        },
    },
}
GG_SHORT = {"3": "Independent", "2": "Needed some help", "1": "Dependent", "8": "Unknown", "9": "Not applicable"}


def unit_of(f):
    if f.get("unit"):
        return f["unit"]
    m = re.search(r"\(([^)]*)\)\s*$", f["question"])
    return m.group(1) if m else ""


def codebox(fid):
    return f'<div class="codebox" aria-hidden="true"><span>Enter code</span><b data-code-for="{fid}"></b></div>'


def choices(f, show_codes):
    t = "radio" if f["type"] == "radio" else "checkbox"
    opts = "".join(
        f'<label class="opt"><input type="{t}" name="{f["id"]}" value="{e(c)}" aria-label="{e(l)}">'
        + (f'<span class="code">{e(c)}.</span>' if show_codes else "")
        + f'<span>{e(l)}</span></label>'
        for c, l in f["options"]
    )
    cls = "inline" if f["id"] in ("PAIN", "ALLERGY_STATUS", "SURG_ANY", "ALCOHOL_ANY") else ("list" if show_codes else "grid")
    block = f'<div class="opts {cls}">{opts}</div>'
    if show_codes and t == "radio":
        return f'<div class="answer">{codebox(f["id"])}{block}</div>'
    hint = '<div class="hint">Check all that apply</div>' if t == "checkbox" else ""
    return hint + block


def grid_html(f):
    g = f["grid"]
    head = "".join(f"<th>{e(c)}</th>" for c in g["cols"])
    body, n = [], 1
    for letter, row in g["rows"]:
        cells = []
        for col in g["cols"]:
            code = f"{n:02d}"
            cells.append(f'<td><label class="cell"><input type="radio" name="{f["id"]}" value="{code}" '
                         f'aria-label="{e(row)} | {e(col)}"><span class="code">{code}</span></label></td>')
            n += 1
        body.append(f"<tr><th>{e(letter)}. {e(row)}</th>{''.join(cells)}</tr>")
    return (f'<div class="answer">{codebox(f["id"])}<div class="hint">Check one box only</div></div>'
            f'<table class="tbl grid"><thead><tr><th>Living arrangement</th>{head}</tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table>")


def control(f, show_codes):
    t = f["type"]
    if t in ("radio", "checkbox"):
        return grid_html(f) if "grid" in f else choices(f, show_codes)
    if t == "select":
        opts = "".join(f'<option value="{e(c)}">{e(l)}</option>' for c, l in f["options"])
        return f'<select name="{f["id"]}"><option value="">Select</option>{opts}</select>'
    if t == "date":
        return f'<input type="date" name="{f["id"]}">'
    if t == "number":
        return (f'<span class="num"><input type="number" name="{f["id"]}" min="0" step="{e(str(f.get("step", "1")))}">'
                f'<span class="unit">{e(unit_of(f))}</span></span>')
    return f'<input type="text" name="{f["id"]}" class="txt">'


def row(f, show_codes):
    """A question row: label left, answer right. role=group + aria-labelledby for the reader."""
    return (f'<div class="row" role="group" aria-labelledby="q-{f["id"]}">'
            f'<div class="lab" id="q-{f["id"]}">{e(prompt_text(f))}</div><div class="ctl">{control(f, show_codes)}</div></div>')


def vitals_table(fields):
    rows = "".join(
        f'<tr role="group" aria-labelledby="q-{f["id"]}"><th id="q-{f["id"]}">{e(prompt_text(f))}</th>'
        f'<td><input type="number" name="{f["id"]}" min="0" step="{e(str(f.get("step", "1")))}"></td>'
        f'<td class="u">{e(unit_of(f))}</td></tr>'
        for f in fields
    )
    return f'<table class="tbl flow"><thead><tr><th>Vital</th><th>Value</th><th>Unit</th></tr></thead><tbody>{rows}</tbody></table>'


def meds_table(fields):
    by = {f["id"]: f for f in fields}
    n_rows = sum(1 for f in fields if f["id"].startswith("MED"))
    body = []
    for i in range(1, n_rows + 1):
        cells = []
        for key in ("MED", "DOSE", "FREQ"):
            f = by[f"{key}{i}"]
            cells.append(f'<td role="group" aria-labelledby="q-{f["id"]}"><span class="sr" id="q-{f["id"]}">{e(prompt_text(f))}</span>'
                         f'{control(f, False)}</td>')
        body.append(f'<tr><th class="n">{i}</th>{"".join(cells)}</tr>')
    return ('<table class="tbl meds"><thead><tr><th class="n">#</th><th>Medication</th><th>Dose</th><th>How often</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def gg_table(fields):
    codes = [c for c, _ in fields[0]["options"]]
    head = "".join(f'<th><span class="code">{c}</span><small>{GG_SHORT[c]}</small></th>' for c in codes)
    rows = []
    for f in fields:
        cells = "".join(f'<td><input type="radio" name="{f["id"]}" value="{c}" aria-label="{e(l)}"></td>' for c, l in f["options"])
        rows.append(f'<tr role="group" aria-labelledby="q-{f["id"]}"><th class="rowq" id="q-{f["id"]}">{e(prompt_text(f))}</th>'
                    f"<td>{codebox(f['id'])}</td>{cells}</tr>")
    return f'<table class="tbl gg"><thead><tr><th>Activity</th><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def leg(f):
    text = legend(f)
    if f.get("_with_ids", True):
        item, title = text.split(". ", 1)
        return f'<legend><span class="iid">{e(item)}.</span> {e(title)}</legend>'
    return f"<legend>{e(text)}</legend>"


def item_html(key, fields, show_codes):
    first = fields[0]
    if key == "GG0100":
        body = gg_table(fields)
    elif key == "VITALS":
        body = vitals_table(fields)
    elif key == "MEDS":
        body = meds_table(fields)
    elif len(fields) == 1 and first["id"] == key:  # one OASIS item, question described by the fieldset
        return (f'<fieldset class="item" id="item-{key}" aria-describedby="q-{first["id"]}">{leg(first)}'
                f'<div class="q" id="q-{first["id"]}">{e(prompt_text(first))}</div>{control(first, show_codes)}</fieldset>')
    else:
        body = "".join(row(f, show_codes) for f in fields)
    return f'<fieldset class="item" id="item-{key}">{leg(first)}{body}</fieldset>'


def build(form):
    cfg = PAGES[form]
    spec = json.loads(FORMS[form]["items"].read_text())
    show_codes = spec.get("show_codes", True)
    fields = load_fields(form)
    by_item = OrderedDict()
    for f in fields:
        by_item.setdefault(f["item"], []).append(f)
    tabs, secs = [], []
    for code, name, items in cfg["sections"]:
        n = sum(len(by_item[i]) for i in items)
        tabs.append(f'<li><a href="#sec-{code}" data-sec="{code}"><span class="tname">{e(name)}</span>'
                    f'<span class="count" data-count="{code}">0/{n}</span></a></li>')
        inner = "".join(item_html(i, by_item[i], show_codes) for i in items)
        secs.append(f'<section class="sec" id="sec-{code}" data-sec="{code}"><h2>{e(name)}</h2>{inner}</section>')
    visits = {x["id"]: {**cfg["patients"][x["id"]], **x["visit"]} for x in load_encounters(form) if x["id"] in cfg["patients"]}
    first = next(iter(cfg["patients"].values()))
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="demo-form" content="{form}">
<title>{e(cfg['doc_title'])}</title>
<link rel="stylesheet" href="ehr.css">
</head>
<body>
<header class="appbar">
  <span class="brand"><span class="mark"></span>Demo Health</span>
  <nav class="appnav"><span>Schedule</span><span>Chart Review</span><span class="on">Rooming</span><span>Notes</span><span>Orders</span></nav>
  <span class="user"><span id="me-name">{e(first['clinician'])}</span> · {e(cfg['role'])}</span>
</header>
<section class="banner" aria-label="Patient">
  <div class="who"><b id="pt-name">{e(first['name'])}</b>
    <span><span id="pt-age">{first['age']}</span> y · <span id="pt-sex">{first['sex']}</span> · DOB <span id="pt-dob">{e(first['dob'])}</span> · MRN <span id="pt-mrn">{e(first['mrn'])}</span></span></div>
  <div class="flag" id="pt-allergy">Allergies: not reviewed</div>
  <div class="meta"><span>{e(cfg['activity'])}</span><span id="pt-date">Visit date</span><span id="pt-clin">{e(first['clinician'])}</span><span class="demo">Synthetic demo data</span></div>
</section>
<div class="work">
  <nav class="tabs" aria-label="Sections"><ol>{''.join(tabs)}</ol><p class="tabfoot">{e(cfg['foot'])}</p></nav>
  <main>
    <form id="assessment" autocomplete="off" onsubmit="return false">
{''.join(secs)}
    </form>
  </main>
</div>
<footer class="savebar"><span id="save-state">Draft · all changes saved</span><span id="done-count">0 of {len(fields)} documented</span>
  <span class="grow"></span><button type="button" class="btn">Save</button><button type="button" class="btn primary">Sign</button></footer>
<script>window.DEMO_VISITS = {json.dumps(visits)}; window.SKIP_RULES = {json.dumps(spec.get('skip_rules', []))};</script>
<script src="ehr.js"></script>
</body>
</html>
"""
    out = ROOT / "mock" / FORMS[form]["page"]
    out.write_text(page)
    print(f"wrote {out.relative_to(ROOT)}: {len(fields)} fields, {len(by_item)} items, {len(cfg['sections'])} sections")


if __name__ == "__main__":
    for form in PAGES:
        build(form)
