"""Step 1 probe: score cursor steering and value filling on the synthetic visits.

Routing: replay each visit; after every clinician turn the cursor either moves
(router returned a field) or stays. Score whether it sits on a gold field.
Filling (only if the local LLM is installed): at the end of each visit, ask
for every field and compare with gold, including false fills on fields that
were never discussed.

Usage: .venv/bin/python -m probe.run_probe [--form oasis|general] [--no-fill]
"""
import argparse
import csv
import datetime as dt
import json
import statistics as st

import re

from vc.data import load_encounters, validate
from vc.fill import load_filler
from vc.forms import FORMS
from vc.router import EmbeddingRouter
from vc.schema import ROOT, load_fields, public_schema


def pct(a, b):
    return f"{a}/{b} = {100 * a / b:.1f}%" if b else "n/a"


def p50_p95(xs):
    xs = sorted(xs)
    return (st.median(xs), xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]) if xs else (0, 0)


def run_routing(router, schema, encs, rows):
    labeled = correct = false_moves = unlabeled = 0
    ms = []
    for enc in encs:
        cursor = None
        for i, t in enumerate(enc["turns"]):
            if enc["speakers"][t["who"]] != "clinician":
                continue
            r = router.route(schema, t["text"], cursor)
            ms.append(r["ms"])
            prev = cursor
            if r["field_id"]:
                cursor = r["field_id"]
            gold = t.get("route", [])
            if gold:
                labeled += 1
                ok = cursor in gold
                correct += ok
            else:
                unlabeled += 1
                ok = cursor == prev
                false_moves += not ok
            rows.append({"enc": enc["id"], "turn": i, "text": t["text"], "gold": "|".join(gold) or "(stay)",
                         "cursor": cursor or "", "moved": bool(r["field_id"]), "best": r["best_id"],
                         "score": r["score"], "ok": ok})
    return {"labeled_turns": labeled, "cursor_correct": correct, "small_talk_turns": unlabeled,
            "false_moves": false_moves, "latency_ms_p50_p95": p50_p95(ms)}


def _n(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def same(got, gold, field):
    """Is the filled value right? Numbers within the field's precision; text by keywords."""
    t = field["type"]
    if got is None:
        return False
    if t == "checkbox":
        return sorted(got) == sorted(gold)
    if t == "number":
        step = float(field.get("step", "1"))
        return abs(float(got) - float(gold)) <= step / 2 + 1e-9
    if t in ("text", "textarea"):
        g = _n(got)
        if isinstance(gold, str):
            return _n(gold) in g
        groups = [[k] if isinstance(k, str) else k for k in gold["keywords"]]
        return all(any(_n(a) in g for a in grp) for grp in groups) and not any(_n(x) in g for x in gold.get("not", []))
    return got == gold


def run_fill(filler, fields, schema, encs, rows, form_title=None):
    by_id = {s["id"]: s for s in schema}
    c = dict(correct=0, wrong=0, missed=0, false_fill=0, right_abstain=0)
    ms = []
    for enc in encs:
        turns = [{"who": t["who"], "role": enc["speakers"][t["who"]], "text": t["text"]} for t in enc["turns"]]
        for f in fields:
            res = filler.fill(by_id[f["id"]], turns, enc["visit"], form_title)
            ms.append(res["ms"])
            gold = enc["gold"].get(f["id"])
            got = res.get("value")
            if gold is None:
                outcome = "false_fill" if res["status"] == "ok" else "right_abstain"
            elif res["status"] != "ok":
                outcome = "missed"
            else:
                outcome = "correct" if same(got, gold, by_id[f["id"]]) else "wrong"
            c[outcome] += 1
            rows.append({"enc": enc["id"], "field": f["id"], "gold": json.dumps(gold), "got": json.dumps(got),
                         "outcome": outcome, "reason": res.get("reason", ""), "quotes": " || ".join(res.get("quotes") or [])})
    c["latency_ms_p50_p95"] = p50_p95(ms)
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fill", action="store_true")
    ap.add_argument("--form", default="oasis", choices=list(FORMS))
    args = ap.parse_args()

    fields = load_fields(args.form)
    schema = public_schema(fields)
    encs = load_encounters(args.form)
    for e in encs:
        assert not validate(e, fields), validate(e, fields)

    out = ROOT / "runs" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-probe-{args.form}"
    out.mkdir(parents=True)
    router = EmbeddingRouter()
    route_rows, fill_rows = [], []
    routing = run_routing(router, schema, encs, route_rows)
    filler = None if args.no_fill else load_filler()
    filling = run_fill(filler, fields, schema, encs, fill_rows, FORMS[args.form]["title"]) if filler else None

    for name, rows in (("routing.csv", route_rows), ("filling.csv", fill_rows)):
        if rows:
            with open(out / name, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)

    r = routing
    lines = [
        f"# Probe {out.name}",
        f"Form: {args.form}. Data: {len(encs)} synthetic visits written by Claude, {len(fields)} fields. Text only, speakers labeled, so these are best-case numbers. n is tiny: do not quote.",
        "",
        f"## Cursor steering ({router.name}, move threshold {router.threshold})",
        f"- Cursor on a gold field after the question: {pct(r['cursor_correct'], r['labeled_turns'])}",
        f"- False moves on small talk: {r['false_moves']} of {r['small_talk_turns']} turns",
        f"- Router latency p50/p95: {r['latency_ms_p50_p95'][0]:.1f} / {r['latency_ms_p50_p95'][1]:.1f} ms (server side, no network)",
    ]
    if filling:
        f = filling
        filled = f["correct"] + f["wrong"] + f["false_fill"]
        present = f["correct"] + f["wrong"] + f["missed"]
        absent = f["false_fill"] + f["right_abstain"]
        lines += [
            "",
            f"## Filling ({filler.name})",
            f"- Right when it fills: {pct(f['correct'], filled)}",
            f"- Coverage of discussed fields: {pct(f['correct'] + f['wrong'], present)} filled, {f['missed']} left empty",
            f"- False fills on fields never discussed: {pct(f['false_fill'], absent)}",
            f"- Latency p50/p95: {f['latency_ms_p50_p95'][0]:.0f} / {f['latency_ms_p50_p95'][1]:.0f} ms per field",
        ]
    else:
        lines += ["", "## Filling", "- Not run: local LLM not installed."]
    lines += ["", "Per-turn and per-field rows: routing.csv, filling.csv"]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    (out / "results.json").write_text(json.dumps({"routing": routing, "filling": filling}, indent=2))
    print("\n".join(lines))
    print(f"\nwritten to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
