"""ÉTAPE 1 — Exploration manuelle d'une heure de GH Archive.

Objectif : comprendre la forme réelle des données AVANT d'écrire le pipeline.
Aucune dépendance externe, uniquement la bibliothèque standard Python.

    python scripts/explore.py --date 2026-08-01 --hour 15

Le fichier est mis en cache dans data/sample/ : relancer le script ne
re-télécharge pas.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"

# Heuristique simple pour repérer les bots. On la raffinera plus tard —
# l'objectif ici est juste de mesurer l'ampleur du phénomène.
BOT_MARKERS = ("[bot]", "-bot", "bot-", "dependabot", "renovate",
               "github-actions", "codecov", "greenkeeper")


def is_bot(login: str) -> bool:
    low = (login or "").lower()
    return any(m in low for m in BOT_MARKERS)


def download_sample(base_url: str, day: str, hour: int) -> Path:
    """Télécharge une heure dans data/sample/, avec cache."""
    # RAPPEL : l'heure n'est PAS remplie de zéros dans l'URL.
    #   correct : 2026-08-01-5.json.gz
    #   404     : 2026-08-01-05.json.gz
    name = f"{day}-{hour}.json.gz"
    target = SAMPLE_DIR / name

    if target.exists() and target.stat().st_size > 0:
        print(f"↺ déjà en cache : {target.relative_to(ROOT)}")
        return target

    url = f"{base_url}/{name}"
    print(f"↓ téléchargement de {url}")
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")

    req = Request(url, headers={"User-Agent": "gharchive-explore/0.1"})
    with urlopen(req, timeout=120) as resp, tmp.open("wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    tmp.replace(target)
    return target


def explore(path: Path) -> None:
    compressed = path.stat().st_size

    n = 0
    uncompressed = 0
    types = Counter()
    actors = Counter()
    repos = Counter()
    languages = Counter()
    bots = 0
    payload_keys: dict[str, Counter] = defaultdict(Counter)
    missing = Counter()
    samples: dict[str, dict] = {}

    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            uncompressed += len(line)
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                missing["ligne_json_invalide"] += 1
                continue

            n += 1
            etype = ev.get("type", "INCONNU")
            types[etype] += 1

            # On garde un exemplaire de chaque type pour inspection visuelle
            samples.setdefault(etype, ev)

            login = (ev.get("actor") or {}).get("login", "")
            actors[login] += 1
            if is_bot(login):
                bots += 1

            repo = (ev.get("repo") or {}).get("name", "")
            repos[repo] += 1

            # Quelles clés existent dans le payload, par type d'événement ?
            # C'est ça qui prouve que le schéma est variable.
            payload = ev.get("payload") or {}
            for k in payload:
                payload_keys[etype][k] += 1

            # Champs qu'on croit garantis — vérifions-le
            for field in ("id", "type", "actor", "repo", "created_at"):
                if not ev.get(field):
                    missing[field] += 1

            # La langue du repo n'est présente que sur certains types
            pr = payload.get("pull_request") or {}
            lang = ((pr.get("base") or {}).get("repo") or {}).get("language")
            if lang:
                languages[lang] += 1

    # ---------------------------------------------------------------- sortie
    def title(s: str) -> None:
        print(f"\n{'=' * 62}\n{s}\n{'=' * 62}")

    title("Q1 — VOLUME")
    print(f"événements           : {n:,}")
    print(f"taille compressée    : {compressed / 1e6:.1f} Mo")
    print(f"taille décompressée  : {uncompressed / 1e6:.1f} Mo")
    print(f"ratio de compression : {uncompressed / compressed:.1f}x")
    print(f"poids moyen/événement: {uncompressed / max(n, 1):.0f} octets")
    print(f"\n→ projection sur 6 mois : "
          f"{n * 24 * 182 / 1e6:.0f} M d'événements, "
          f"{uncompressed * 24 * 182 / 1e12:.2f} To décompressés")

    title("Q2 — TYPES D'ÉVÉNEMENTS")
    for t, c in types.most_common():
        print(f"{t:<28} {c:>8,}  {c / n * 100:5.1f} %")

    title("Q4 — VARIABILITÉ DU PAYLOAD  ← le cœur du problème")
    print("Nombre de clés distinctes dans 'payload', par type :\n")
    for t, c in types.most_common(8):
        keys = sorted(payload_keys[t])
        print(f"{t:<28} {len(keys):>2} clés : {', '.join(keys[:6])}"
              f"{'…' if len(keys) > 6 else ''}")
    print("\n→ Conclusion : impossible de mettre ça dans une table plate")
    print("  sans traitement par type. C'est ça, le travail de la couche Silver.")

    title("Q5 — BOTS")
    print(f"événements de bots : {bots:,}  ({bots / max(n, 1) * 100:.1f} %)")
    print("\nTop 10 acteurs :")
    for a, c in actors.most_common(10):
        flag = "  [BOT]" if is_bot(a) else ""
        print(f"  {a:<32} {c:>6,}{flag}")

    title("Q6 — CHAMPS MANQUANTS")
    if missing:
        for f, c in missing.most_common():
            print(f"  {f:<24} {c:,} fois")
    else:
        print("  aucun champ obligatoire manquant sur cette heure ✓")

    title("BONUS — TOP REPOS ET LANGAGES")
    print("Repos les plus actifs :")
    for r, c in repos.most_common(8):
        print(f"  {r:<44} {c:>5,}")
    if languages:
        print("\nLangages (vus dans les PR) :")
        for l, c in languages.most_common(8):
            print(f"  {l:<20} {c:>5,}")

    # Deux exemples contrastés, à lire attentivement
    title("À LIRE — deux événements aux structures opposées")
    for t in ("PullRequestEvent", "WatchEvent"):
        if t in samples:
            print(f"\n--- {t} ---")
            print(json.dumps(samples[t], indent=2, ensure_ascii=False)[:1400])

    # Un dump complet, un exemplaire par type, pour inspection à froid
    out = SAMPLE_DIR / "samples_by_type.json"
    out.write_text(
        json.dumps(samples, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n\n📄 Un exemplaire de chaque type écrit dans "
          f"{out.relative_to(ROOT)}")
    print("   Ouvre-le et compare les structures. C'est le vrai exercice.")


def main() -> int:
    p = argparse.ArgumentParser(description="Exploration d'une heure GH Archive")
    p.add_argument("--date", default="2026-08-01", help="AAAA-MM-JJ")
    p.add_argument("--hour", type=int, default=15, help="0-23")
    p.add_argument("--base-url", default="https://data.gharchive.org")
    p.add_argument("--file", help="explorer un fichier local déjà téléchargé")
    a = p.parse_args()

    path = Path(a.file) if a.file else download_sample(a.base_url, a.date, a.hour)
    explore(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())