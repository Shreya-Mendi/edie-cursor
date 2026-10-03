"""Demo check: play visits through the real extension and score what shows up on screen.

Unlike probe/run_probe.py (which asks the model directly), this drives the dev
page in headless Chrome exactly as the demo runs: replay at a set speed, the
cursor steering, the fill queue, the chips. When the replay ends and the queue
drains, every suggestion on screen is compared with the gold answers.

Run with the server up:  python3 demo/check_demo.py [--speed 2] [--visits enc-01 gen-01]
"""
import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from probe.run_probe import same  # noqa: E402
from vc.data import load_encounters  # noqa: E402
from vc.forms import FORMS  # noqa: E402
from vc.schema import load_fields, public_schema  # noqa: E402

BASE = "http://localhost:8000"


def check(page, form, enc, speed):
    by_id = {f["id"]: f for f in public_schema(load_fields(form))}
    page.goto(f"{BASE}/dev/{form}")
    page.wait_for_function("window.__vc && window.__vc.state.visits.length", timeout=30000)
    panel = page.frame_locator('iframe[title="Edie Cursor panel"]')
    panel.locator("#visit").select_option(enc["id"])
    page.wait_for_function(f"window.__vc.state.enc && window.__vc.state.enc.id === '{enc['id']}'", timeout=15000)
    panel.locator("#speed").select_option(str(speed))
    t0 = time.time()
    panel.locator("#play").click()
    page.wait_for_function("window.__vc.state.idx >= window.__vc.state.enc.turns.length", timeout=900000)
    replay_s = time.time() - t0
    page.wait_for_function("!window.__vc.busy()", timeout=900000, polling=500)
    drain_s = time.time() - t0 - replay_s
    sugg = page.evaluate("() => Object.fromEntries([...window.__vc.state.sugg.values()].map(s => [s.id, s.value]))")
    gold = enc["gold"]
    right = [k for k in gold if k in sugg and same(sugg[k], gold[k], by_id[k])]
    wrong = [(k, sugg[k], gold[k]) for k in gold if k in sugg and k not in right]
    missing = [k for k in gold if k not in sugg]
    extra = [(k, sugg[k]) for k in sugg if k not in gold]
    return {"right": right, "wrong": wrong, "missing": missing, "extra": extra, "replay_s": replay_s, "drain_s": drain_s}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", default="2", choices=["1", "2", "4"])
    ap.add_argument("--visits", nargs="*", help="ids like enc-01 gen-01 (default: all)")
    args = ap.parse_args()
    encs = [(f, e) for f in FORMS for e in load_encounters(f) if not args.visits or e["id"] in args.visits]
    tot = {"right": 0, "wrong": 0, "missing": 0, "extra": 0, "gold": 0}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--window-size=1920,1080"])
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        for form, enc in encs:
            r = check(page, form, enc, args.speed)
            n = len(enc["gold"])
            for k in ("right", "wrong", "missing", "extra"):
                tot[k] += len(r[k])
            tot["gold"] += n
            print(f"\n[{form}] {enc['id']} at {args.speed}x: {len(r['right'])}/{n} right on screen, "
                  f"{len(r['wrong'])} wrong, {len(r['missing'])} missing, {len(r['extra'])} on fields nobody discussed "
                  f"| replay {r['replay_s']:.0f} s, queue drained {r['drain_s']:.0f} s later")
            if r["missing"]:
                print(f"   missing: {', '.join(r['missing'])}")
            for k, got, want in r["wrong"]:
                print(f"   wrong:   {k} got {got!r}, gold {want!r}")
            for k, got in r["extra"]:
                print(f"   extra:   {k} = {got!r}")
        browser.close()
    print(f"\nTOTAL: {tot['right']}/{tot['gold']} right on screen, {tot['wrong']} wrong, "
          f"{tot['missing']} missing, {tot['extra']} on fields nobody discussed")


if __name__ == "__main__":
    main()
