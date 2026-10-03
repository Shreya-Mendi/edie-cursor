# Edie Cursor

Edie is an AI cursor that fills a clinical form with you while you talk to your patient.

Page and demo video: https://shreya-mendi.github.io/edie-cursor/

- **Edie's cursor** (light blue) follows the clinician-patient conversation. She moves to the
  question being discussed and leaves a suggested answer on it, with the exact words she heard.
- **Your own mouse** (amber "You" box) can click any question to ask Edie about that one field.
- Click a suggestion and the conversation scrolls to the line it came from, with the words marked.

Nothing is filled until you accept a suggestion, one at a time or with Accept all, and the form
is never submitted. Everything runs on a Mac with open-source models: no API keys, and the
conversation never leaves the machine.

| Job | Model | Where it runs |
|---|---|---|
| Live voice | Whisper large-v3-turbo on MLX (about 1.6 GB) | local server, Apple GPU |
| Finding the question being discussed | all-MiniLM-L6-v2 sentence embeddings | local server, CPU |
| Writing the suggestion | Qwen3-4B, 4-bit, on MLX (about 2.3 GB) | local server, Apple GPU |

Status: a prototype. Every visit in this repo is made up, and it has not been tested with
clinicians, real patient audio or a real EHR. It is not a medical device.

## Run

Needs a Mac with Apple silicon and `ffmpeg` on your PATH (for Live mode).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/hf download mlx-community/Qwen3-4B-4bit
.venv/bin/hf download mlx-community/whisper-large-v3-turbo
.venv/bin/hf download sentence-transformers/all-MiniLM-L6-v2
.venv/bin/uvicorn server.app:app --port 8000
```

The server loads the models from the local Hugging Face cache, which is why the downloads come
first. The conversation is processed on the machine and is never sent anywhere.

- Without installing the extension, open http://localhost:8000/dev/general (primary care new
  patient visit) or http://localhost:8000/dev/oasis (home health OASIS). The server injects the
  same content script and docks the panel on the right.
- To use it as an extension, open `chrome://extensions`, turn on Developer mode, click Load
  unpacked and pick `extension/`. Then open http://localhost:8000/mock/general.html or
  `/mock/oasis.html` and click the Edie Cursor toolbar icon. The panel opens in Chrome's side
  panel.

In the panel, pick a made-up visit and press Play, or switch to Live and use the mic. Finish all
brings in the rest of the conversation at once and asks Edie about every empty field.

## The demo forms

The two mock pages imitate an EHR rooming screen from a made-up vendor ("Demo Health"): patient
banner, section list with live counts, flowsheet tables, Save and Sign. Underneath is plain,
standard form markup (fieldsets, legends, labels, ARIA) with no hints for the extension, and the
extension has no form-specific code. It reads the questions the same way a screen reader does.

- **New patient visit**, 26 fields: reason for visit, onset, pain, vitals, allergies, one
  medication row, history, surgery, tobacco, alcohol, exercise, review of systems and the flu
  shot. Every question is a choice, a number or a short table cell, like a real intake form.
- **OASIS-E2 start of care**, 24 fields with the CMS wording, including one real skip rule
  (J0510 = 0 skips J0530).

Both forms' skip rules apply to Edie too: once "No known allergies" is suggested, she stops
suggesting allergens. The font is Mona Sans (SIL OFL, `extension/fonts/MonaSans-OFL.txt`).

## How a suggestion gets made

1. The cursor: each clinician sentence is matched against every question on the page with
   sentence embeddings, and Edie moves to the best match above a threshold.
2. The answer: the model gets the conversation and one field, and returns an answer plus a
   supporting quote.
3. The checks, in code: every word of the quote must appear in one line of the transcript, and
   the answer must be a legal option. For numbers, the model only writes down what was said
   ("one thirty-eight over eighty-six", "five foot ten"). Code reads the number, converts units,
   picks systolic or diastolic and rounds to the field's precision. Anything that fails a check
   stays empty, so Edie does not guess.
4. Speed: the conversation stays in the model's prompt cache, so each new field reads only the
   new tokens. Answers are also saved in `data/cache/` (not in git). The model decodes greedily,
   so the same field and conversation always give the same answer, and a saved answer is what
   the model would say again. That is what makes Finish all quick on a replayed visit.

## Layout

| Path | What |
|---|---|
| `forms/general/items.json`, `oasis/items.json` | The two forms' fields; OASIS wording is verbatim from the CMS PDF in `oasis/` |
| `forms/build_pages.py` | Builds `mock/general.html` and `mock/oasis.html` |
| `data/synthetic/general/`, `data/synthetic/` | 3 + 4 made-up visits with gold answers and cursor labels |
| `vc/router.py`, `vc/fill.py`, `vc/spoken.py`, `vc/llm_mlx.py`, `vc/asr.py` | Steering, the answer checks, spoken numbers, the local LLM, the local speech model |
| `server/app.py` | Local FastAPI server |
| `extension/` | Manifest V3 extension: the cursors, the panel, replay, live mic, accept |
| `probe/run_probe.py` | Asks the model about every field and scores it against the gold answers |
| `demo/check_demo.py`, `demo/record_demo.py` | Plays visits through the real extension and scores what shows on screen; records the video |

Left out of the OASIS slice for licensing: A1255 (PRAPARE), B1300 (CC BY-NC) and D0150 (PHQ).
The mic recorder is adapted from QuietSky's `voice-glider/frontend/src/hooks/useRecorder.js`.

MIT license. Built by Shreya Mendi.
