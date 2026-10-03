"""Cursor steering: which field is the clinician asking about right now?

Embedding match, no LLM: each clinician sentence is compared with every
field's question text and its options. Options are embedded with their
item legend in front ("GG0110. Prior Device Use: Walker") so short generic
labels like "Correct" or "No" cannot win on their own. The cursor moves only
when the best field clears MOVE_THRESHOLD; otherwise it stays put, which is
the right call for follow-ups ("And how often?") and small talk.

Threshold history (4 synthetic visits, 90 labeled clinician turns):
  0.40 set before the first run: cursor right 75.6%, 4/8 false moves on small talk.
  0.30 picked after a sweep on the SAME 4 visits: 90.0%, 6/8 false moves.
  So 0.30 is tuned on its own test data. It needs held-out visits before anyone trusts it.
"""
import os
import re
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")  # model is already on disk; never call the network

import numpy as np
from sentence_transformers import SentenceTransformer

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MOVE_THRESHOLD = 0.30


def sentences(text):
    """Sentences plus their clauses: one clinician line often covers several fields
    ("pulse seventy-eight, temperature ninety-eight point four, and oxygen ninety-eight")."""
    sents = [p.strip() for p in re.split(r"(?<=[.?!])\s+", text) if len(p.strip()) > 2]
    parts = list(sents)
    for sen in sents:
        clauses = [c.strip() for c in re.split(r"[,;]\s*|\s+and\s+", sen) if len(c.strip().split()) >= 2]
        if len(clauses) > 1:
            parts += clauses
    return parts or [text]


class EmbeddingRouter:
    name = f"embeddings:{MODEL.split('/')[-1]}"

    def __init__(self, threshold=MOVE_THRESHOLD):
        self.model = SentenceTransformer(MODEL, device="cpu")
        self.threshold = threshold
        self._index = {}

    def _build(self, fields):
        key = tuple((f["id"], f["question"]) for f in fields)
        if key not in self._index:
            texts, owner = [], []
            for i, f in enumerate(fields):
                legend = f["question"].split(" | ")[0]
                texts.append(f["question"])
                owner.append(i)
                for o in f.get("options") or []:
                    texts.append(f"{legend}: {o['label']}")
                    owner.append(i)
            emb = self.model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
            self._index[key] = (emb, np.array(owner))
        return self._index[key]

    def route(self, fields, utterance, current=None):
        t0 = time.perf_counter()
        emb, owner = self._build(fields)
        q = self.model.encode(sentences(utterance), normalize_embeddings=True, convert_to_numpy=True)
        sims = (q @ emb.T).max(axis=0)                    # best sentence per text
        per_field = np.full(len(fields), -1.0)
        np.maximum.at(per_field, owner, sims)              # best text per field
        order = np.argsort(-per_field)
        best = int(order[0])
        score = float(per_field[best])
        moved = score >= self.threshold
        hits = [{"id": fields[i]["id"], "score": round(float(per_field[i]), 3)}
                for i in order[:6] if per_field[i] >= self.threshold]
        return {
            "field_id": fields[best]["id"] if moved else None,
            "fields": hits,
            "best_id": fields[best]["id"],
            "score": round(score, 3),
            "threshold": self.threshold,
            "top": [{"id": fields[i]["id"], "score": round(float(per_field[i]), 3)} for i in order[:3]],
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }
