"""The demo forms: field spec, synthetic visits, and mock page for each."""
from vc.schema import ROOT

FORMS = {
    "oasis": {
        "title": "Start of Care OASIS",
        "items": ROOT / "oasis" / "items.json",
        "visits": ROOT / "data" / "synthetic",
        "visit_glob": "enc-*.json",
        "page": "oasis.html",
    },
    "general": {
        "title": "New Patient Visit",
        "items": ROOT / "forms" / "general" / "items.json",
        "visits": ROOT / "data" / "synthetic" / "general",
        "visit_glob": "gen-*.json",
        "page": "general.html",
    },
}
