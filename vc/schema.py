"""Form specs: load a form's items.json and expand it into flat fields.

The field text built here (legend + ask + question) is the same text the
extension reads off the page, so the probe and the extension route on
identical strings. Works for any form registered in vc/forms.py.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _spec(form):
    from vc.forms import FORMS
    return json.loads(FORMS[form]["items"].read_text())


def load_fields(form="oasis"):
    data = _spec(form)
    shared = data.get("shared_options", {})
    with_ids = data.get("legend_with_item_id", True)
    fields = []
    for f in data["fields"]:
        f = dict(f)
        f["_with_ids"] = with_ids
        if "options_ref" in f:
            f["options"] = shared[f.pop("options_ref")]
        if "grid" in f:
            g = f["grid"]
            opts, n = [], 1
            for _, row in g["rows"]:
                for col in g["cols"]:
                    opts.append([f"{n:02d}", f"{row} | {col}"])
                    n += 1
            f["options"] = opts
        fields.append(f)
    return fields


def legend(f):
    return f"{f['item']}. {f['title']}" if f.get("_with_ids", True) else f["title"]


def prompt_text(f):
    """Question text as shown on the page, minus the legend."""
    parts = []
    if f.get("ask"):
        parts.append(f"Ask patient: “{f['ask']}”")
    if f.get("instruction"):
        parts.append(f["instruction"])
    parts.append(f["question"])
    return " ".join(parts)


def field_text(f):
    """Full routing text: legend + prompt. Mirrors the DOM extractor."""
    return f"{legend(f)} | {prompt_text(f)}"


def public_schema(fields):
    """Shape the extension sends to the server."""
    out = []
    for f in fields:
        d = {"id": f["id"], "question": field_text(f), "type": f["type"]}
        if f.get("options"):
            d["options"] = [{"value": v, "label": l} for v, l in f["options"]]
        if f["type"] == "number":
            d["step"] = str(f.get("step", "1"))
        out.append(d)
    return out
