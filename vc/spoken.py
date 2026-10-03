"""Read numbers the way clinicians say them, deterministically.

"one thirty-eight over eighty-six" -> (138, 86)   "two twelve point six" -> 212.6
"a hundred point six" -> 100.6                    "ninety-eight point four" -> 98.4
"five ten" (height) -> 5 ft 10 in = 70            "one oh five" -> 105

The model quotes the transcript; this module turns the quote into numbers, so a
small model never has to do the conversion (it got "two twelve point six" as 12.6).
"""
import re

ONES = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
NUMWORD = set(ONES) | set(TEENS) | set(TENS) | {"hundred"}


def _tokens(text):
    t = text.lower().replace("-", " ")
    t = re.sub(r"\ba couple of\b", "two", t)  # "a couple of beers" is 2
    return re.findall(r"\d+(?:\.\d+)?|[a-z]+", t)


def _two_digit(ws):
    """Value of a 1 to 2 word number below 100, or None."""
    if len(ws) == 1:
        w = ws[0]
        return ONES.get(w, TEENS.get(w, TENS.get(w)))
    if len(ws) == 2 and ws[0] in TENS and ws[1] in ONES and ONES[ws[1]] > 0:
        return TENS[ws[0]] + ONES[ws[1]]
    return None


def _integer(ws):
    """Integer readings of a run of number words: [value] plus a feet-inches reading if one fits."""
    if not ws:
        return []
    if "hundred" in ws:
        i = ws.index("hundred")
        head = ws[:i]
        hv = 1 if head in ([], ["a"], ["one"]) else _two_digit(head)
        tail = [w for w in ws[i + 1:] if w != "and"]
        tv = _two_digit(tail) if tail else 0
        return [("num", hv * 100 + tv)] if hv is not None and tv is not None else []
    v = _two_digit(ws)
    if v is not None:
        return [("num", v)]
    if ws[0] in ONES and ONES[ws[0]] > 0:  # hundreds shorthand: "one thirty-eight", "two twelve", "one oh five"
        rest = ws[1:]
        tv = ONES[rest[1]] if len(rest) == 2 and rest[0] == "oh" and rest[1] in ONES else _two_digit(rest)
        if tv is not None:
            out = [("num", ONES[ws[0]] * 100 + tv)]
            if 4 <= ONES[ws[0]] <= 7 and tv <= 11:
                out.append(("height", ONES[ws[0]] * 12 + tv))
            return out
    return []


def readings(text):
    """All numbers in a phrase, in order, each as a list of possible readings.

    A reading is ("num", value), ("height", inches) or ("pair", (a, b)) for "X over Y".
    """
    toks = _tokens(text)
    items, i = [], 0
    while i < len(toks):
        w = toks[i]
        if re.fullmatch(r"\d+(?:\.\d+)?", w):
            items.append([("num", float(w))])
            i += 1
            continue
        if w in NUMWORD or (w == "a" and i + 1 < len(toks) and toks[i + 1] == "hundred"):
            run = []
            while i < len(toks) and (toks[i] in NUMWORD or toks[i] in ("a", "and") and run is not None
                                     and (toks[i] != "and" or "hundred" in run)):
                if toks[i] == "a" and not (i + 1 < len(toks) and toks[i + 1] == "hundred"):
                    break
                run.append(toks[i])
                i += 1
            while run and run[-1] == "and":
                run.pop()
            frac = ""
            if i < len(toks) and toks[i] == "point":
                j = i + 1
                while j < len(toks) and toks[j] in ONES:
                    frac += str(ONES[toks[j]])
                    j += 1
                if frac:
                    i = j
            # "five foot ten" / "five feet eleven inches"
            if i < len(toks) and toks[i] in ("foot", "feet") and _two_digit(run) is not None:
                feet = _two_digit(run)
                j = i + 1
                k = j
                while k < len(toks) and toks[k] in NUMWORD:
                    k += 1
                inch = _two_digit(toks[j:k]) if k > j else 0
                items.append([("height", feet * 12 + (inch or 0))])
                i = k
                continue
            vals = _integer(run)
            if frac:
                vals = [(kind, v + float("0." + frac)) for kind, v in vals if kind == "num"]
            if vals:
                items.append(vals)
            continue
        i += 1
    # "X over Y" -> blood pressure pair
    out, toks_text = [], " ".join(toks)
    if " over " in f" {toks_text} " and len(items) >= 2:
        a, b = items[0][0][1], items[1][0][1]
        out.append([("pair", (a, b))])
        out.extend(items[2:])
        return out
    return items


STOP = {"what", "when", "with", "that", "this", "your", "have", "from", "patient", "does", "right", "scale", "minute", "per", "beats"}


def candidates(quotes, question):
    """Numbers the quotes support for this field, already in the field's terms.

    A blood pressure field takes the "X over Y" pair when there is one. When a quote
    holds several numbers, the one right after the field's own keyword wins
    ("temperature ninety-eight point four" for a temperature field).
    """
    q = question.lower()
    bp = "systolic" in q or "diastolic" in q or "blood pressure" in q
    height_in = "height" in q and ("inch" in q or "(in" in q)
    label = q.split("|")[-1]
    keys = {w for w in re.findall(r"[a-z]+", label) if len(w) >= 4 and w not in STOP}
    found = []
    for quote in quotes:
        quote = str(quote)
        values = []
        for opts in readings(quote):
            kinds = dict(opts)
            if "pair" in kinds:
                if bp:
                    a, b = kinds["pair"]
                    return [b if "diastolic" in q else a]
            elif height_in and "height" in kinds:
                values.append(kinds["height"])
            elif "num" in kinds:
                values.append(kinds["num"])
        if len(values) > 1 and keys:
            # Clause after the field keyword: "temperature ninety-eight point four, ..."
            low = quote.lower()
            hits = [m.end() for k in keys for m in re.finditer(rf"\b{k}", low)]
            if hits:
                after = readings(re.split(r"[,;]| and ", low[min(hits):], maxsplit=1)[0])
                vals = [dict(o).get("height" if height_in else "num") for o in after]
                vals = [v for v in vals if v is not None]
                if vals:
                    values = vals[:1]
        found.extend(values)
    return found


if __name__ == "__main__":
    tests = [
        ("Blood pressure is one thirty-eight over eighty-six.", "Blood pressure, systolic (mmHg)", [138]),
        ("Blood pressure is one thirty-eight over eighty-six.", "Blood pressure, diastolic (mmHg)", [86]),
        ("Two twelve point six.", "Weight (lb)", [212.6]),
        ("Your temperature is a hundred point six.", "Temperature (°F)", [100.6]),
        ("temperature ninety-eight point four", "Temperature (°F)", [98.4]),
        ("And you're about five ten?", "Height (inches)", [70]),
        ("Five six.", "Height (inches)", [66]),
        ("And he's five foot five?", "A. Height (in inches)", [65]),
        ("One eighty-seven point four.", "B. Weight (in pounds)", [187.4]),
        ("the scale says one forty-two point five", "B. Weight (in pounds)", [142.5]),
        ("one eighteen over seventy-four, heart rate ninety-six", "Blood pressure, systolic (mmHg)", [118]),
        ("one eighteen over seventy-four, heart rate ninety-six", "Vital signs | Heart rate (beats per minute)", [96]),
        ("your pulse is seventy-eight", "Heart rate (beats per minute)", [78]),
        ("one oh five", "Heart rate (beats per minute)", [105]),
    ]
    ok = 0
    for text, question, want in tests:
        got = candidates([text], question)
        ok += got == want
        print(f"{'ok ' if got == want else 'BAD'} {text!r} [{question}] -> {got} (want {want})")
    print(f"{ok}/{len(tests)} spoken readings right")
