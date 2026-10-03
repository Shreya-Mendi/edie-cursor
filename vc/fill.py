"""Filling a field: the model proposes, deterministic code decides.

The model returns {"answer", "evidence"}. Code then checks that every evidence
quote is grounded in the transcript, that the answer is a legal value for the
field, and does all the arithmetic itself: for numbers the model only writes
down what was said ("5 ft 11 in", "138/86", "37.2 C") and code converts units,
picks systolic or diastolic, and rounds to the field's own precision (its
HTML step). Anything that fails a check becomes an abstain, never a guess.
"""
import datetime as dt
import json
import re
from decimal import Decimal, ROUND_HALF_UP

from vc import spoken

SYSTEM = (
    "You fill one field of a clinical form from the transcript of a visit. "
    "Use only what was said in the transcript. If the transcript does not answer the field, "
    "set answer to null. If someone corrects an earlier answer, use the corrected one. "
    "Do not do arithmetic or unit conversion: write numbers the way they were said, in digits. "
    "Reply with one JSON object and nothing else."
)

FORMATS = {
    "radio": "one option code as a string",
    "select": "one option code as a string",
    "checkbox": "a list of every option code that applies",
    "date": "a date as YYYY-MM-DD",
    "number": 'the number as said, in digits, with its unit if one was said (for example "187.4 lb", "5 ft 11 in", "138/86", "37.2 C")',
    "text": "a short answer in plain words, as said",
    "textarea": "one to three plain sentences summarizing only what was said",
}


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def build_prompt(field, turns, visit=None, form=None):
    """Conversation first, field last: consecutive calls share a long prefix (see llm_mlx cache)."""
    lines = []
    if form:
        lines.append(f"Form: {form}")
    if visit:
        lines.append(f"Visit date: {visit.get('weekday', '')} {visit.get('date', '')}".strip())
    lines.append("Transcript:")
    lines += [f"[{i + 1}] {x['who']} ({x['role']}): {x['text']}" for i, x in enumerate(turns)]
    lines.append("")
    lines.append(f"Field: {field['question']}")
    if field.get("options"):
        lines.append("Options (code: label):")
        lines += [f"  {o['value']}: {o['label']}" for o in field["options"]]
    quotes = {"textarea": "one to four short exact quotes", "checkbox": "one short exact quote for each code you chose"}.get(
        field["type"], "the shortest exact quote")
    lines.append(f'Answer format: {{"answer": {FORMATS.get(field["type"], FORMATS["text"])} or null, '
                 f'"evidence": ["{quotes} from the transcript that support the answer"]}}')
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}]


def parse(raw):
    i = raw.find("{")
    if i < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(raw[i:])
        return obj
    except json.JSONDecodeError:
        return None


# ---------- numbers: code does the arithmetic ----------
NUM = r"(\d+(?:\.\d+)?)"


def _field_unit(question):
    q = question.lower()
    if re.search(r"\binch|\(in\b|\bin\.\)", q):
        return "in"
    if re.search(r"\bcm\b|centimet", q):
        return "cm"
    if re.search(r"\blb|\bpound", q):
        return "lb"
    if re.search(r"\bkg\b|kilogram", q):
        return "kg"
    if "°f" in q or "fahrenheit" in q:
        return "F"
    if "°c" in q or "celsius" in q:
        return "C"
    return None


def to_number(ans, field):
    """Turn what the model wrote into a number in the field's unit, or None."""
    if isinstance(ans, (int, float)) and not isinstance(ans, bool):
        return float(ans)
    s = str(ans).strip().lower().replace("’", "'").replace("”", '"')
    q = field["question"].lower()
    want = _field_unit(field["question"])
    if want == "in" and "height" in q:
        m = re.fullmatch(r"\s*([4-7])\s*[\s.']\s*(\d{1,2})\s*", s)
        if m and int(m.group(2)) <= 11:
            return float(int(m.group(1)) * 12 + int(m.group(2)))
    m = re.search(rf"{NUM}\s*/\s*{NUM}", s)  # blood pressure "138/86"
    if m:
        return float(m.group(2) if "diastolic" in q else m.group(1))
    m = re.search(rf"{NUM}\s*(?:ft|feet|foot|')\s*(?:{NUM}\s*(?:in|inches|inch|\")?)?", s)
    if m:
        inches = float(m.group(1)) * 12 + float(m.group(2) or 0)
        return inches * 2.54 if want == "cm" else inches
    m = re.search(rf"{NUM}\s*(kg|kilograms?|lbs?|pounds?|cm|centimeters?|in|inches|°?\s*[cf]\b|degrees?\s*[cf]?)?", s)
    if not m:
        return None
    v, unit = float(m.group(1)), (m.group(2) or "").replace("°", "").replace("degrees", "").strip()
    if unit.startswith("kg") or unit.startswith("kilo"):
        return v * 2.20462 if want == "lb" else v
    if unit.startswith("lb") or unit.startswith("pound"):
        return v / 2.20462 if want == "kg" else v
    if unit.startswith("cm") or unit.startswith("centi"):
        return v / 2.54 if want == "in" else v
    if unit == "c" and want == "F":
        return v * 9 / 5 + 32
    if unit == "f" and want == "C":
        return (v - 32) * 5 / 9
    return v


def round_to_step(v, step):
    """Round half up to the field's precision (OASIS's own rule for whole numbers)."""
    places = Decimal(str(step or "1")).normalize().as_tuple().exponent
    q = Decimal(1).scaleb(min(0, places))
    r = Decimal(str(v)).quantize(q, rounding=ROUND_HALF_UP)
    return int(r) if q >= 1 else float(r)


def gate(field, turns, out):
    """Return ("ok", value, quotes) or ("abstain", reason, quotes)."""
    if not isinstance(out, dict):
        return "abstain", "unparseable model output", []
    ans = out.get("answer")
    quotes = out.get("evidence") or []
    if isinstance(quotes, str):
        quotes = [quotes]
    if ans in (None, "", [], "null"):
        return "abstain", "model found no answer", quotes
    # Models often copy the line label too ("[3] Walt (patient): ..."); drop it before matching.
    quotes = [re.sub(r"^\s*(\[\d+\]\s*)?[^:()\[\]]{1,40}\([a-z]+\):\s*", "", str(q)) for q in quotes]
    # ...or the option code they chose it for ("HTN: Just high blood pressure.").
    opt = {o["value"] for o in field.get("options") or []}
    quotes = [re.sub(r"^\s*([A-Za-z0-9_]{1,10})\s*:\s*", lambda m: "" if m.group(1) in opt else m.group(0), q) for q in quotes]
    # Grounding rule: every word of a quote must appear in ONE transcript line (any order).
    lines = [set(_norm(t["text"]).split()) for t in turns]

    def grounded(q):
        words = set(_norm(q).split())
        return bool(words) and any(words <= line for line in lines)

    if not quotes or not all(grounded(q) for q in quotes):
        return "abstain", "evidence not found in any single line of the transcript", quotes
    t = field["type"]
    codes = [o["value"] for o in field.get("options") or []]

    def code_of(a):  # models sometimes answer "06: Patient lives with ..." instead of "06"
        a = str(a).strip()
        for c in sorted(codes, key=len, reverse=True):
            if a == c or re.match(rf"^{re.escape(c)}\s*[:.\-]\s", a):
                return c
        return a

    if t in ("radio", "select"):
        ans = code_of(ans)
        if ans not in codes:
            return "abstain", f"{ans} is not an option", quotes
    elif t == "checkbox":
        ans = [code_of(a) for a in (ans if isinstance(ans, list) else [ans])]
        if not ans or not set(ans) <= set(codes):
            return "abstain", f"{ans} is not a subset of the options", quotes
        ans = sorted(set(ans), key=codes.index)
        none = {o["value"] for o in field["options"] if o["label"].lower().startswith("none of")}
        if len(ans) > 1 and set(ans) & none:
            ans = [a for a in ans if a not in none]
        # Ticking (nearly) everything is the model listing options, not hearing them.
        real = [c for c in codes if c not in none]
        if len(real) > 3 and set(real) <= set(ans):
            return "abstain", "every option was chosen", quotes
        if len(ans) > 3 * max(1, len({_norm(q) for q in quotes})):
            return "abstain", f"{len(ans)} choices but only {len(quotes)} quote(s)", quotes
    elif t == "date":
        try:
            ans = dt.date.fromisoformat(str(ans)).isoformat()
        except ValueError:
            return "abstain", f"{ans} is not a date", quotes
    elif t == "number":
        m = to_number(ans, field)
        heard = spoken.candidates(quotes, field["question"])
        tol = float(field.get("step", "1")) / 2 + 1e-9
        if not heard:
            v = m
        elif m is not None and any(abs(m - h) <= tol for h in heard):
            v = m
        elif len(heard) == 1:
            v = heard[0]  # the quote is verbatim speech; the model misread it
        else:
            tail = [h for h in heard if m is not None and f"{h:g}".endswith(f"{m:g}")]
            v = tail[0] if len(tail) == 1 else None
        if v is None:
            return "abstain", f"{ans} does not match the numbers in the quote", quotes
        ans = round_to_step(v, field.get("step", "1"))
    else:
        ans = str(ans).strip()
        # A dose is a number and a unit. When the model writes the drug name instead, read the dose off the quote.
        if "amount and unit" in field["question"] and not re.search(r"\d", ans):
            UNITS = {"milligram": "mg", "mg": "mg", "microgram": "mcg", "mcg": "mcg", "gram": "g", "unit": "units",
                     "puff": "puffs", "tablet": "tablets", "pill": "pills", "ml": "mL", "milliliter": "mL"}
            dose = None
            for q in quotes:
                nums = [dict(r)["num"] for r in spoken.readings(q) if "num" in dict(r)]
                unit = next((u for w, u in UNITS.items() if re.search(rf"\b{w}s?\b", q.lower())), "")
                if nums:
                    dose = f"{nums[0]:g} {unit}".strip()
                    break
            if dose is None:
                return "abstain", "no dose in the quote", quotes
            ans = dose
        if len(ans) > (700 if t == "textarea" else 120):
            return "abstain", "text answer too long", quotes
    # A 0 to 10 scale answer must point at the line that says the number. Search from the
    # line the model quoted, forward first, so "Maybe a four" wins over the question before it.
    if t == "radio" and all(re.match(rf"{re.escape(o['value'])}(\s|$)", o["label"]) for o in field["options"]):
        n = float(ans)

        def says(text):
            return any(dict(r).get("num") == n for r in spoken.readings(text))

        if not any(says(q) for q in quotes):
            words = set(_norm(quotes[0]).split())
            start = next((i for i, line in enumerate(lines) if words <= line), 0)
            for i in [*range(start, len(turns)), *range(start - 1, -1, -1)]:
                hit = [s for s in re.split(r"(?<=[.?!])\s+", turns[i]["text"]) if says(s)]
                if hit:
                    quotes = hit[:1]
                    break
    return "ok", ans, quotes


def display(field, value):
    if field.get("options"):
        labels = {o["value"]: o["label"] for o in field["options"]}
        vals = value if isinstance(value, list) else [value]
        def one(v):
            label = labels.get(v, "")
            return label if label.startswith(v) else f"{v} {label}".strip()  # "4", not "4 4"
        return "; ".join(one(v) for v in vals)
    return str(value)


class Filler:
    """Wraps a text-generation backend with the prompt, parser, and gate."""

    def __init__(self, backend):
        self.backend = backend
        self.name = backend.name

    def fill(self, field, turns, visit=None, form=None):
        max_tokens = 260 if field["type"] == "textarea" else 120
        raw, ms = self.backend.generate(build_prompt(field, turns, visit, form), max_tokens=max_tokens)
        status, val, quotes = gate(field, turns, parse(raw))
        res = {"status": status, "quotes": quotes, "ms": ms, "raw": raw[-400:]}
        if status == "ok":
            res.update(value=val, display=display(field, val))
        else:
            res["reason"] = val
        return res


def load_filler():
    """Local LLM if installed, else None (the server then reports filling as unavailable)."""
    try:
        from vc.llm_mlx import MLXBackend
    except ImportError:
        return None
    backend = MLXBackend.load_if_present()
    return Filler(backend) if backend else None
