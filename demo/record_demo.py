"""Record the Edie Cursor demo video and the landing-page stills.

Drives your installed Chrome (headless) with Playwright on the dev page
http://localhost:8000/dev/general (the extension script plus the docked panel), captures
Chrome's own screencast frames, and assembles them with ffmpeg at 1920x1080, 30 fps.
Captions and the visible mouse pointer are drawn by this script for the video only.
The conversation is a replay of a made-up visit (gen-01), and nothing is ever submitted.

Two passes run the same scenario:
  video  : screencast on, captions on  -> demo/out/edie-cursor-demo.mp4 (+ docs/assets copy and poster)
  stills : no screencast, no captions  -> docs/assets/shot-*.png (1600x900)

Run with the local server up:
  .venv/bin/python demo/record_demo.py              # both passes
  .venv/bin/python demo/record_demo.py --stills-only
  .venv/bin/python demo/record_demo.py --video-only
"""
import argparse
import base64
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo" / "out"
FRAMES = OUT / "frames"
ASSETS = ROOT / "docs" / "assets"
URL = "http://localhost:8000/dev/general"
W, H = 1920, 1080
VISIT = "gen-01"

CAPTIONS = {
    "open": "Edie Cursor fills a clinical form with you while you talk to your patient.",
    "follow": "Edie follows the conversation to the question being discussed.",
    "waits": "Her suggestion waits on the field. Nothing is filled yet.",
    "evidence": "Every suggestion shows the exact words it came from.",
    "accept": "You accept what is right.",
    "point": "Point at any question to ask Edie about it.",
    "finish": "Finish all brings in the whole visit at once.",
    "close": "Nothing is filled until you accept, and nothing is ever submitted. Runs on a laptop with open-source models.",
}

OVERLAY_JS = r"""
(() => {
  if (window.top !== window) return; // not inside the docked panel frame
  addEventListener('DOMContentLoaded', () => {
    const s = document.createElement('style');
    s.textContent = `
      #demo-cap { position: fixed; left: calc(50% - 180px); bottom: 36px; transform: translateX(-50%); z-index: 2147483647;
        background: rgba(20,28,38,.92); color: #fff; padding: 13px 26px; border-radius: 14px; opacity: 0;
        font: 600 27px/1.3 "VC Mona", "Mona Sans", sans-serif; transition: opacity .35s;
        pointer-events: none; width: max-content; max-width: 1080px; text-align: center; }
      #demo-ptr { position: fixed; left: 0; top: 0; z-index: 2147483647; pointer-events: none; width: 30px; height: 30px; }
      #demo-ptr.click { animation: demo-click .35s ease-out; }
      @keyframes demo-click { 0% { transform-origin: 4px 2px; scale: 1 } 40% { scale: .82 } 100% { scale: 1 } }`;
    document.head.appendChild(s);
    const cap = document.createElement('div'); cap.id = 'demo-cap'; document.body.appendChild(cap);
    const ptr = document.createElement('div'); ptr.id = 'demo-ptr';
    ptr.innerHTML = '<svg viewBox="0 0 24 24" width="30" height="30"><path d="M4 2 L4 20 L9 15.5 L12.5 22.5 L15.5 21 L12 14 L19 14 Z" fill="#111" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>';
    document.body.appendChild(ptr);
    let x = innerWidth * 0.55, y = innerHeight * 0.6;
    const put = () => { ptr.style.left = `${x}px`; ptr.style.top = `${y}px`; };
    put();
    window.__demoCap = (t) => { if (t) { cap.textContent = t; cap.style.opacity = 1; } else cap.style.opacity = 0; };
    window.__demoGlide = (tx, ty, ms) => new Promise((done) => {
      const x0 = x, y0 = y, t0 = performance.now();
      const step = (now) => {
        const k = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - k, 3);
        x = x0 + (tx - x0) * e; y = y0 + (ty - y0) * e; put();
        if (k < 1) requestAnimationFrame(step); else done();
      };
      requestAnimationFrame(step);
    });
    window.__demoTap = () => { ptr.classList.remove('click'); void ptr.offsetWidth; ptr.classList.add('click'); };
    // Smooth page scroll with easing (a plain scrollTo per frame, so it never fights the page's own scrolling).
    window.__demoScroll = (y1, ms) => new Promise((done) => {
      const y0 = scrollY, t0 = performance.now();
      const step = (now) => {
        const k = Math.min(1, (now - t0) / ms), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
        window.scrollTo({ top: y0 + (y1 - y0) * e, behavior: 'instant' });
        if (k < 1) requestAnimationFrame(step); else done();
      };
      requestAnimationFrame(step);
    });
  });
})();
"""

AUDIT_JS = """() => {
  const els = [...document.querySelectorAll('#assessment input, #assessment textarea, #assessment select')];
  const by = {};
  for (const e of els) {
    const k = e.name || e.id;
    if (!k) continue;
    const on = (e.type === 'radio' || e.type === 'checkbox') ? e.checked : !!e.value;
    if (on) (by[k] = by[k] || []).push(e.value);
  }
  const sg = [...window.__vc.state.sugg.values()];
  return { fields: Object.keys(by).length, values: by,
           accepted: sg.filter(s => s.status === 'accepted').length, pending: sg.filter(s => s.status === 'pending').length };
}"""


def run(p, mode):
    video = mode == "video"
    stamps, walls, marks, saved = [], [], {}, []
    errors = {"pageerror": [], "console": [], "http": []}
    if video:
        if FRAMES.exists():
            shutil.rmtree(FRAMES)
        FRAMES.mkdir(parents=True)

    # Without an explicit window size, headless Chrome frames come out 1920x993 (room kept for browser UI).
    browser = p.chromium.launch(channel="chrome", headless=True, args=[f"--window-size={W},{H}"])
    page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
    page.add_init_script(OVERLAY_JS)
    page.on("pageerror", lambda e: errors["pageerror"].append(str(e)))
    page.on("console", lambda m: errors["console"].append(m.text) if m.type == "error" else None)
    page.on("response", lambda r: errors["http"].append(f"{r.status} {r.url}") if r.status >= 400 else None)
    page.goto(URL)
    page.wait_for_function("window.__vc && window.__vc.state.enc && window.__vc.state.visits.length && window.__vc.state.fillReady",
                           timeout=30000)
    panel = page.frame_locator('iframe[title="Edie Cursor panel"]')
    page.wait_for_timeout(900)
    # The extension's overlay host is last in the DOM at the same (maximum) z-index, so it would paint its chips over
    # the captions. Move the caption and the pointer after it, so they stay on top.
    page.evaluate("document.documentElement.append(document.getElementById('demo-cap'), document.getElementById('demo-ptr'))")

    cdp = None
    if video:
        cdp = page.context.new_cdp_session(page)

        def on_frame(params):
            path = FRAMES / f"f{len(stamps):05d}.jpg"
            path.write_bytes(base64.b64decode(params["data"]))
            stamps.append((params["metadata"]["timestamp"], path.name))
            walls.append(time.time())
            cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})

        cdp.on("Page.screencastFrame", on_frame)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 90, "maxWidth": W, "maxHeight": H, "everyNthFrame": 1})
        page.wait_for_timeout(400)

    # ---------- helpers ----------
    def mark(name):
        if stamps:
            marks[name] = round(stamps[-1][0] - stamps[0][0], 1)

    def cap(text, hold=0):
        if video:
            page.evaluate("t => window.__demoCap(t)", text)
        if hold:
            page.wait_for_timeout(hold if video else min(hold, 150))

    def pause(ms):
        page.wait_for_timeout(ms if video else min(ms, 200))

    def shot(name):
        if video:
            return
        page.wait_for_timeout(700)  # let the cursor, scroll and chip animations settle
        raw = OUT / f"_{name}.png"
        page.screenshot(path=str(raw), type="png")
        from PIL import Image
        dest = ASSETS / f"shot-{name}.png"
        Image.open(raw).convert("RGB").resize((1600, 900), Image.LANCZOS).save(dest, optimize=True)
        raw.unlink()
        saved.append(dest.name)

    def glide(x, y, ms=900):
        ms = ms if video else min(ms, 250)
        page.evaluate("([x, y, ms]) => window.__demoGlide(x, y, ms)", [x, y, ms])
        page.wait_for_timeout(ms + 80)

    def center(loc, off=None):
        b = loc.bounding_box()
        return (b["x"] + off[0], b["y"] + off[1]) if off else (b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)

    def tap_only(loc, ms=900):
        # For <select>: show the pointer going there; the value is then set directly (a native popup would not be filmed).
        glide(*center(loc), ms)
        page.evaluate("window.__demoTap()")
        page.wait_for_timeout(250)

    def click_locator(loc, ms=900, off=None):
        # Glide the visible pointer, then let Playwright click the element where it is now
        # (positions can shift while the pointer moves, so never click stale coordinates).
        loc.scroll_into_view_if_needed()
        glide(*center(loc, off), ms)
        glide(*center(loc, off), 120)
        page.evaluate("window.__demoTap()")
        loc.click(position={"x": off[0], "y": off[1]} if off else None)

    def chip_xy(fid, which):
        return page.evaluate(
            """([fid, which]) => { const c = window.__vc.state.chips.get(fid); const b = c && c.querySelector(which);
                if (!b) return null; const r = b.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }""",
            [fid, which])

    def click_chip(fid, which, ms=900):
        # Chips live in the extension's shadow DOM. Find the button, glide there, then click it with the real mouse.
        xy = chip_xy(fid, which)
        if not xy:
            return False
        glide(*xy, ms)
        xy = chip_xy(fid, which)
        glide(*xy, 120)
        page.evaluate("window.__demoTap()")
        page.mouse.click(*xy)
        page.wait_for_timeout(200)
        if which == ".acc" and page.evaluate("(id) => window.__vc.state.sugg.get(id).status", fid) != "accepted":
            page.evaluate("(id) => window.__vc.state.chips.get(id).querySelector('.acc').click()", fid)  # the click missed: fall back
        return True

    def wait_js(js, arg=None, timeout=40):
        t = time.time()
        while time.time() - t < timeout:
            if page.evaluate(js, arg):
                return True
            page.wait_for_timeout(120)
        return False

    def pending(fid):
        return wait_js("(id) => { const s = window.__vc.state.sugg.get(id); return !!s && s.status === 'pending'; }", fid, timeout=25)

    def queue_idle(timeout=30):
        return wait_js("() => !window.__vc.busy()", timeout=timeout)

    def idx_reached(n, timeout=40):
        return wait_js("(n) => window.__vc.state.idx >= n", n, timeout=timeout)

    def bring_into_view(selector, smooth_ms=900):
        # Scroll so the question's container sits mid-screen, unless it is already comfortably visible.
        moved = page.evaluate(
            """([sel, ms]) => { const el = document.querySelector(sel); const c = el.closest('[role="group"], fieldset') || el.parentElement;
                const r = c.getBoundingClientRect(); const ok = r.top > 150 && r.bottom < innerHeight - 140;
                if (ok) return false;
                const y = scrollY + r.top + r.height / 2 - innerHeight / 2;
                window.__demoScroll(Math.max(0, y), ms); return true; }""", [selector, smooth_ms])
        if moved:
            page.wait_for_timeout(smooth_ms + 250)

    def scroll_page(y, ms):
        ms = ms if video else min(ms, 500)
        page.evaluate("([y, ms]) => window.__demoScroll(y, ms)", [y, ms])

    max_scroll = "Math.max(0, document.documentElement.scrollHeight - innerHeight)"

    # ---------- scene 1: the form and the panel ----------
    mark("1 open")
    cap(CAPTIONS["open"], hold=4200)

    # ---------- scene 2: pick the visit, 2x, Play, and let Edie follow ----------
    mark("2 select and play")
    tap_only(panel.locator("#visit"))
    panel.locator("#visit").select_option(VISIT)
    page.wait_for_function(f"window.__vc.state.enc && window.__vc.state.enc.id === '{VISIT}' && window.__vc.state.idx === 0", timeout=15000)
    tap_only(panel.locator("#speed"))
    panel.locator("#speed").select_option("2")
    pause(500)
    click_locator(panel.locator("#play"))
    mark("2 follow")
    if video:
        cap(CAPTIONS["follow"], hold=9500)
        mark("2 waits")
        cap(CAPTIONS["waits"], hold=10500)
    else:
        idx_reached(7)
        click_locator(panel.locator("#play"))  # pause with Edie on the blood pressure row
        page.wait_for_function("!window.__vc.state.playing", timeout=5000)
        pending("BP_SYS")
        queue_idle()
        shot("follow")
        click_locator(panel.locator("#play"))  # play on
    # Let the height answer land ("Five foot ten"), then pause so the page holds still.
    idx_reached(11)

    # ---------- scene 3: the evidence for the pain suggestion ----------
    click_locator(panel.locator("#play"))  # Pause
    page.wait_for_function("!window.__vc.state.playing", timeout=5000)
    pending("PAIN")
    pending("BP_SYS")
    mark("3 evidence")
    cap(CAPTIONS["evidence"])
    click_locator(panel.locator('#sugg li[data-id="PAIN"]'), off=(70, 16))
    wait_js("() => !!document.querySelector('iframe[title=\"Edie Cursor panel\"]').contentDocument.querySelector('#log .evid')", timeout=5)
    pause(1000)
    shot("evidence")
    pause(2600)

    # ---------- scene 4: accept the systolic blood pressure ----------
    mark("4 accept")
    cap(CAPTIONS["accept"])
    bring_into_view('[name="BP_SYS"]')
    pause(300)
    click_chip("BP_SYS", ".acc")
    pause(3000)

    # ---------- scene 5: your cursor asks about a question ----------
    mark("5 point")
    cap(CAPTIONS["point"])
    reached_exercise = page.evaluate("() => window.__vc.state.idx >= 25")
    discussed = ["EXERCISE"] if reached_exercise else []
    discussed += ["HT", "WT", "SPO2", "TEMP", "HR"]
    target = page.evaluate(
        """(ids) => { const a = window.__vc.state.agent && window.__vc.state.agent.id;
            return ids.find((id) => id !== a && !window.__vc.state.sugg.has(id)) || ids.find((id) => id !== a); }""", discussed)
    bring_into_view(f'[name="{target}"]')
    pause(400)
    spot = page.evaluate(
        """(id) => { const el = document.querySelector(`[name="${id}"]`); const c = el.closest('[role="group"], fieldset') || el.parentElement;
            const t = c.querySelector('legend, .lab, th, td') || c; const r = t.getBoundingClientRect();
            return [r.x + Math.min(r.width / 2, 120), r.y + r.height / 2]; }""", target)
    glide(*spot, 1100)
    page.evaluate("window.__demoTap()")
    page.mouse.move(*spot)
    page.mouse.click(*spot)  # a real click on the question text, not on an input
    got = pending(target)
    pause(1000)
    shot("point")
    pause(3300)

    # ---------- scene 6: Finish all, then scroll the whole form ----------
    mark("6 finish all")
    cap(CAPTIONS["finish"])
    click_locator(panel.locator("#finishall"))
    scroll_page(0, 900)
    pause(1100)
    queue_idle(timeout=40)
    panel.locator("#log").evaluate("el => el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })")  # show the end of the visit
    pause(600)
    n_pending = page.evaluate("() => [...window.__vc.state.sugg.values()].filter(s => s.status === 'pending').length")
    mark("6 scroll")
    scroll_page(page.evaluate(f"() => {max_scroll}"), 6000)  # blocks until the scroll has finished
    pause(400)

    # ---------- scene 7: Accept all, scroll back up, close ----------
    mark("7 accept all")
    click_locator(panel.locator("#acceptall"))
    pause(900)
    cap(CAPTIONS["close"])
    scroll_page(0, 6000)  # blocks until the scroll has finished
    shot("filled")
    pause(5000)
    audit = page.evaluate(AUDIT_JS)
    mark("end")

    tail = time.time() - walls[-1] if video and walls else 1.0  # how long the screen stayed as the last frame left it
    if video:
        cdp.send("Page.stopScreencast")
        page.wait_for_timeout(300)
    browser.close()
    return {"stamps": stamps, "tail": tail, "marks": marks, "audit": audit, "errors": errors, "target": target, "got": got,
            "n_pending": n_pending, "reached_exercise": reached_exercise, "saved": saved}


def encode(stamps, tail):
    from PIL import Image
    bad = {Image.open(FRAMES / n).size for _, n in stamps[::25]} - {(W, H)}
    if bad:
        raise SystemExit(f"frames are {bad}, not {W}x{H}; refusing to stretch them")
    # Frames arrive when the screen changes, so each frame lasts until the next one.
    lines = []
    for i, (ts, name) in enumerate(stamps):
        dur = stamps[i + 1][0] - ts if i + 1 < len(stamps) else max(0.5, min(tail, 8.0)) / 2  # the repeated last entry below adds this time again
        lines.append(f"file '{name}'\nduration {max(dur, 0.001):.4f}")
    lines.append(f"file '{stamps[-1][1]}'")
    (FRAMES / "list.txt").write_text("\n".join(lines) + "\n")
    out = OUT / "edie-cursor-demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
                    "-vf", "fps=30,scale=in_range=pc:out_range=tv,format=yuv420p", "-c:v", "libx264", "-crf", "20",
                    "-preset", "medium", "-pix_fmt", "yuv420p", "-color_range", "tv", "-movflags", "+faststart", str(out)], cwd=FRAMES, check=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out, ASSETS / "edie-cursor-demo.mp4")
    # Poster: the opening frame once its caption has faded in (same view the video starts on).
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.6", "-i", str(out), "-frames:v", "1",
                    "-vf", "scale=1600:900:flags=lanczos", "-q:v", "2", str(ASSETS / "poster.jpg")], check=True)
    return out


def report(tag, r):
    a = r["audit"]
    print(f"[{tag}] marks (s into the video): {r['marks']}")
    print(f"[{tag}] You pointed at: {r['target']} (conversation reached exercise: {r['reached_exercise']}), suggestion present: {r['got']}")
    print(f"[{tag}] suggestions pending right after Finish all: {r['n_pending']}")
    print(f"[{tag}] after Accept all: {a['fields']} form fields hold values, {a['accepted']} suggestions accepted, {a['pending']} still pending")
    print(f"[{tag}] page errors: {len(r['errors']['pageerror'])} {r['errors']['pageerror']} | console errors: {r['errors']['console']} | http >= 400: {r['errors']['http']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stills-only", action="store_true")
    ap.add_argument("--video-only", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        if not args.video_only:
            r = run(p, "stills")
            report("stills", r)
            print(f"stills written: {r['saved']}")
        if not args.stills_only:
            r = run(p, "video")
            out = encode(r["stamps"], r["tail"])
            span = r["stamps"][-1][0] - r["stamps"][0][0]
            print(f"frames: {len(r['stamps'])} over {span:.1f} s | wrote {out.relative_to(ROOT)}")
            report("video", r)


if __name__ == "__main__":
    main()
