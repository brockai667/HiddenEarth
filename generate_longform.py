#!/usr/bin/env python3
"""
Generator CELEHO 8-10 min dokumentarneho scenara o HISTORII JEDNEHO MIESTA
(styl 'Fall of Civilizations': atmosfera, uzas, tichy smutok; stare mapy a rytiny
z Wikimedia Commons + kinematicky b-roll). Cez GitHub Models (zadarmo).
Pouzitie:
  python generate_longform.py "Pompeii"              # konkretne miesto
  python generate_longform.py                          # vyberie nepouzite z banky
Vystup: scripts_longform/<slug>.json  (potom: python make_longform.py scripts_longform/<slug>.json)
"""
import json, os, re, sys, requests

ROOT = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(ROOT, "longform_topics.json")
STATE = os.path.join(ROOT, "longform_used.json")

MODEL = os.environ.get("MODELS_MODEL", "openai/gpt-4o-mini")
BASE = os.environ.get("MODELS_BASE_URL", "https://models.github.ai/inference")
TOKEN = os.environ.get("MODELS_TOKEN") or os.environ.get("GITHUB_TOKEN")

SYSTEM = ("You are a scriptwriter for a cinematic HISTORY-OF-PLACES YouTube DOCUMENTARY channel, "
          "in the style of 'Fall of Civilizations': calm, atmospheric, full of awe and quiet melancholy. "
          "Each episode tells the FULL story of ONE real place - how it rose, what life there was like, "
          "what happened to it, and what remains today. SAFETY RULES: only real, widely-documented "
          "history; NEVER invent names, dates or numbers; NO pseudo-archaeology (no aliens, no lost "
          "super-civilizations, no invented mysteries) - real history is dramatic enough; be respectful "
          "to the cultures and people involved; where historians disagree, present theories AS theories. "
          "You output strict JSON, nothing else.")


DEFAULT_CHAPTERS = [
    "Cold open: standing in the place today", "Origins: who built it and why",
    "The golden age: the place at its height", "Daily life: what it was like to live there",
    "The turning point", "The fall: how it was lost or abandoned",
    "The silent centuries and the rediscovery", "The place today and its legacy",
]


def outline_prompt(place):
    return (
        f"Plan a 13 to 15 minute cinematic history DOCUMENTARY about this real place: {place}.\n"
        "Return ONLY JSON with this schema:\n"
        '{\n  "title": "Evocative YouTube documentary title (max 70 chars)",\n'
        '  "description": "One sentence ending with \'Follow for more hidden places.\'",\n'
        '  "hashtags": ["#history","#documentary","#lostcity","#archaeology","#ancienthistory","#hiddenearth"],\n'
        '  "chapters": ["Cold open at the place today","Origins","..."]\n}\n'
        "Give 8 chapter titles that, in order, tell the WHOLE story of the place: a haunting cold-open "
        "(the place today, one striking detail), its origins and builders, its golden age, daily life "
        "there, the turning point, how it fell or was abandoned, the silent centuries and rediscovery, "
        "and what remains today plus its legacy. Real, widely-documented history only. "
        "Return ONLY the JSON, no markdown."
    )


def chapter_prompt(place, title, chapter, idx, total, prev_tail):
    return (
        f"Cinematic history documentary titled \"{title}\" about the place: {place}.\n"
        f"Write ONLY chapter {idx} of {total}: \"{chapter}\".\n"
        + (f"The previous chapter ended with: {prev_tail}\n" if prev_tail else "")
        + "Write 9 to 11 segments for THIS chapter only. Each segment = ONE or TWO short spoken "
        "sentences for a calm, deep documentary voiceover. Move the story FORWARD; do NOT repeat "
        "earlier lines; do NOT write any closing or 'subscribe' line.\n"
        + ("Chapter 1 first segment is the HOOK: under 14 words, haunting and concrete, "
           "NEVER start with 'Did you know'.\n" if idx == 1 else "")
        + "SAFETY: only real documented history; never invent names/dates/numbers; no pseudo-archaeology; "
        "disputed points presented AS theories; respectful tone.\n"
        "'keywords' = 1-3 ENGLISH words for concrete cinematic STOCK footage matching the line "
        "(e.g. 'aerial jungle temple','desert canyon sunrise','ancient stone carving','misty mountain ruins',"
        "'old parchment map','archaeologist excavation'). Cinematic and concrete.\n"
        "ARCHIVAL IMAGES ARE THE SOUL OF THIS CHANNEL: you MUST add 'image' to AT LEAST 4 of the "
        "segments in this chapter. For famous places Wikimedia Commons is guaranteed to have: old maps, "
        "antique engravings, 19th-century photographs, excavation photos, frescoes, artifacts, aerial "
        "photographs, historical paintings. Use precise Commons search terms tied to what the line says "
        "(e.g. 'Pompeii old map', 'Pompeii engraving', 'Pompeii excavation photograph', 'Pompeii fresco', "
        "'Vesuvius eruption painting', 'Angkor Wat 19th century photograph'). Vary the type across the "
        "chapter (map / engraving / photo / artifact / painting).\n"
        "Return ONLY JSON: {\"segments\": [ {\"text\": \"...\", \"keywords\": \"...\", \"image\": \"OPTIONAL\"} ] }."
    )


def continue_prompt(title, segs, n):
    recent = " | ".join(s["text"] for s in segs[-6:])
    return (
        f"Continue this history documentary titled \"{title}\".\n"
        f"Recent lines so far: {recent}\n"
        f"Add EXACTLY {n} MORE segments that move the story FORWARD (deeper into the fall, the "
        "rediscovery, the excavations, what the place tells us today, and its legacy). "
        "Do NOT repeat anything already said. Do NOT include any closing or subscribe line.\n"
        "Same rules: each segment = one or two short calm documentary sentences; 'keywords' = 1-3 ENGLISH "
        "words for concrete cinematic stock footage; add 'image' (a precise Wikimedia Commons search term - "
        "old map / engraving / archival photo / artifact / painting) to AT LEAST a third of the new "
        "segments. ACCURACY IS SACRED, only real history.\n"
        "Return ONLY a JSON object: {\"segments\": [ {\"text\": \"...\", \"keywords\": \"...\"} ] }."
    )


import time as _time

# hlavny model + zalozne (ak hlavny je pod limitom / padne, skusi sa dalsi zadarmo)
_MODELS = [MODEL] + [m.strip() for m in os.environ.get(
    "MODELS_FALLBACK", "openai/gpt-4.1-mini,openai/gpt-4o").split(",")
    if m.strip() and m.strip() != MODEL]
_MIN_GAP = float(os.environ.get("MODELS_MIN_GAP", "4"))   # rozostup (s) medzi volaniami -> limit za minutu
_last = [0.0]


def call_model(user_text):
    gap = _MIN_GAP - (_time.time() - _last[0])            # nenaraz na limit poctu volani za minutu
    if gap > 0:
        _time.sleep(gap)
    last = "?"
    for model in _MODELS:                                 # hlavny model, potom zalozne
        for attempt in range(4):                          # opakuj pri 429 / 5xx (rate limit / pretazenie)
            try:
                r = requests.post(BASE.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
                    json={"model": model, "temperature": 0.8, "max_tokens": 12000,
                          "response_format": {"type": "json_object"},
                          "messages": [{"role": "system", "content": SYSTEM},
                                       {"role": "user", "content": user_text}]},
                    timeout=300)
            except Exception as e:
                last = "exc %s" % str(e)[:120]; _time.sleep(6); continue
            if r.status_code == 429 or r.status_code >= 500:      # limit / pretazenie -> pockaj a skus znova
                last = "%s %s" % (r.status_code, r.text[:150])
                wait = 8 * (attempt + 1)
                ra = r.headers.get("Retry-After")                # respektuj Retry-After ak ho server posle
                if ra:
                    try: wait = max(wait, min(90, int(float(ra))))
                    except Exception: pass
                print("[%s pokus %d/4] %s -> cakam %ds" % (model, attempt + 1, r.status_code, wait))
                _time.sleep(wait); continue
            if r.status_code >= 400:                              # ina chyba -> skus dalsi model
                last = "%s %s" % (r.status_code, r.text[:200]); break
            _last[0] = _time.time()
            return r.json()["choices"][0]["message"]["content"]
    _last[0] = _time.time()
    raise RuntimeError("Models API zlyhalo (vsetky modely): %s" % last)


def extract_json(s):
    s = s.strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b != -1:
        s = s[a:b + 1]
    s = re.sub(r",(\s*[}\]])", r"\1", s)   # odstran trailing commas (casta chyba modelu)
    return json.loads(s)


def sanitize_text(txt):
    """Ochrana proti degenerovanemu LLM textu (opakovane slova/bigramy): zbali opakovania,
    orez na max 2 vety; ak je text stale degenerovany, zahod cely segment (inak na nom
    spadne TTS a s nim CELY 8-10 min render)."""
    txt = re.sub(r"\s+", " ", str(txt)).strip()
    def key(w):
        return re.sub(r"[^a-z0-9']", "", w.lower())
    out = []
    for w in txt.split():
        if out and key(w) and key(w) == key(out[-1]):
            continue                                     # "x x x" -> "x"
        if len(out) >= 3 and key(w) == key(out[-2]) and key(out[-1]) == key(out[-3]):
            continue                                     # "a b a b a b" -> "a b a"
        out.append(w)
    txt = " ".join(out)
    sents = re.split(r"(?<=[.!?])\s+", txt)
    txt = " ".join(sents[:2]).strip()                    # segment = 1-2 kratke vety
    if len(txt) > 400:
        txt = txt[:400].rsplit(" ", 1)[0].rstrip(",;:") + "."
    ws = [key(w) for w in txt.split() if key(w)]
    if len(ws) >= 8 and len(set(ws)) * 2 < len(ws):      # <50% unikatnych slov = degenerovane
        return ""
    return txt


def slug(t):
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")[:50] or "doc"


def refill_bank(min_unused=4, target=8):
    """Ak v banke ostava malo nepouzitych miest, AI dogeneruje dalsie REALNE slavne
    stratene/opustene/premenene miesta (dedup). Vdaka tomu napady nikdy nedojdu."""
    bank = json.load(open(BANK, encoding="utf-8")) if os.path.exists(BANK) else []
    used = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else []
    unused = [c for c in bank if c not in used]
    if len(unused) >= min_unused or not TOKEN:
        return bank
    have_lc = {c.strip().lower() for c in bank}
    avoid = "; ".join(bank[-25:])
    prompt = (
        f"List {target} REAL, famous, widely-documented PLACES with a dramatic history, suitable for a "
        "cinematic history documentary: lost or abandoned cities, buried or sunken settlements, sealed-off "
        "towns, once-great capitals that fell, places rediscovered by archaeologists. Each must be a REAL, "
        "well-documented place (no legends like Atlantis, no fiction). Prefer variety across continents "
        "and eras (ancient + modern). Do NOT include any of these already-used ones: " + avoid + ".\n"
        'Return ONLY JSON: {"places": ["Pompeii, the Roman city buried by Vesuvius (Italy)", "..."]}. '
        "Use the format 'Name, one-line hook (Country)' for each."
    )
    try:
        data = extract_json(call_model(prompt))
        new = data.get("places", []) if isinstance(data, dict) else []
    except Exception as e:
        print(f"[refill zlyhal: {e}]"); return bank
    added = 0
    for c in new:
        c = str(c).strip()
        if 6 <= len(c) <= 110 and c.lower() not in have_lc:
            bank.append(c); have_lc.add(c.lower()); added += 1
    if added:
        json.dump(bank, open(BANK, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[refill] pridanych {added} novych miest do banky (spolu {len(bank)})")
    return bank


def ensure_images(segs, place):
    """POISTKA: archivne obrazky su dusa kanala - ak ich model dal malo, dopln vlastne
    Wikimedia vyrazy tak, aby mal obrazok aspon kazdy ~4. segment. Vyrazy sa tocia cez
    rozne typy archivov (mapa/rytina/foto/artefakt/malba); ak sa na Commons nenajdu,
    make_longform.py automaticky spadne na stock b-roll, takze horsie to byt nemoze."""
    short = re.split(r"[,(]", str(place))[0].strip() or str(place).strip()
    kinds = ["old map", "antique map", "engraving", "19th century photograph",
             "ruins photograph", "excavation photograph", "archaeological site",
             "artifact", "fresco", "painting", "aerial photograph", "historical photograph"]
    target = max(8, len(segs) // 4)
    have = sum(1 for s in segs if s.get("image"))
    if have >= target:
        return 0
    ki, added = 0, 0
    idxs = [i for i in range(1, len(segs) - 1) if not segs[i].get("image")]
    gap = max(1, len(idxs) // max(1, target - have))
    for i in idxs[::gap]:
        if have + added >= target or ki >= len(kinds):
            break
        segs[i]["image"] = f"{short} {kinds[ki]}"
        ki += 1; added += 1
    return added


def pick_place():
    bank = refill_bank()
    used = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else []
    used_lc = {u.strip().lower() for u in used}
    left = [c for c in bank if c.strip().lower() not in used_lc]
    return left[0] if left else (bank[0] if bank else None)


def main():
    if not TOKEN:
        print("CHYBA: chyba MODELS_TOKEN/GITHUB_TOKEN (lokalne daj PAT s 'models' scope do MODELS_TOKEN)")
        sys.exit(1)
    place = " ".join(sys.argv[1:]).strip() or pick_place()
    if not place:
        print("CHYBA: ziadne miesto (zadaj argument alebo napln longform_topics.json)"); sys.exit(1)
    print(f"Generujem 8-10 min scenar o mieste: {place}  (model {MODEL}, po kapitolach)...")

    def add_segments(raw, clean, have):
        added = 0
        for s in raw:
            if not (isinstance(s, dict) and s.get("text") and s.get("keywords")):
                continue
            txt = sanitize_text(s["text"])
            if not txt or txt.lower() in have:
                continue
            seg = {"text": txt, "keywords": str(s["keywords"]).strip()}
            if s.get("image"):
                seg["image"] = str(s["image"]).strip()
            clean.append(seg); have.add(txt.lower()); added += 1
        return added

    # 1) osnova (titulok, popis, hashtagy, kapitoly)
    try:
        plan = extract_json(call_model(outline_prompt(place)))
    except Exception as e:
        print(f"[osnova zlyhala: {e}] pouzivam default kapitoly")
        plan = {}
    spec = {
        "title": (plan.get("title") or place).strip(),
        "description": (plan.get("description") or f"The full story of {place}. Follow for more hidden places.").strip(),
        "hashtags": plan.get("hashtags") or ["#history", "#documentary", "#lostcity", "#archaeology", "#ancienthistory", "#hiddenearth"],
    }
    chapters = [c for c in (plan.get("chapters") or []) if isinstance(c, str) and c.strip()] or DEFAULT_CHAPTERS
    chapters = chapters[:12]
    print(f"Titulok: {spec['title']}  |  {len(chapters)} kapitol")

    # 2) generuj kazdu kapitolu zvlast (spolahlivo nazbiera ~100-140 segmentov)
    clean, have = [], set()
    for i, ch in enumerate(chapters, 1):
        tail = " ".join(s["text"] for s in clean[-3:])
        try:
            part = extract_json(call_model(chapter_prompt(place, spec["title"], ch, i, len(chapters), tail)))
        except Exception as e:
            print(f"[kapitola {i} '{ch[:30]}' zlyhala: {e}]"); continue
        a = add_segments(part.get("segments", []), clean, have)
        print(f"[kapitola {i}/{len(chapters)}] +{a}  spolu {len(clean)}")

    # 3) poistka na dlzku: ak je malo, doziadaj este (continuation)
    tries = 0
    while len(clean) < 72 and tries < 3:
        tries += 1
        try:
            more = extract_json(call_model(continue_prompt(spec["title"], clean, min(30, 85 - len(clean)))))
        except Exception as e:
            print(f"[continuation {tries}] {e}"); break
        a = add_segments(more.get("segments", []), clean, have)
        print(f"[continuation {tries}] +{a}  spolu {len(clean)}")
        if a == 0:
            break

    # 4) zaveracia (subscribe) veta
    closing = "Subscribe to uncover the places history forgot."
    if not clean or clean[-1]["text"] != closing:
        clean.append({"text": closing, "keywords": "aerial ancient ruins sunset"})

    if len(clean) < 40:
        print(f"CHYBA: po vsetkych pokusoch len {len(clean)} segmentov."); sys.exit(1)
    extra = ensure_images(clean, place)
    if extra:
        print(f"[poistka] doplnenych {extra} archivnych obrazkov (Wikimedia)")
    spec["segments"] = clean
    spec.setdefault("title", place)
    spec.setdefault("hashtags", ["#history", "#documentary", "#lostcity", "#archaeology", "#hiddenearth"])
    os.makedirs(os.path.join(ROOT, "scripts_longform"), exist_ok=True)
    path = os.path.join(ROOT, "scripts_longform", slug(place) + ".json")
    json.dump(spec, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    imgs = sum(1 for s in clean if s.get("image"))
    print(f"OK: {len(clean)} segmentov ({imgs} s archivnym obrazkom) -> {path}")
    # zaznam do banky-stavu
    if place:
        used = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else []
        if place not in used:
            used.append(place)
            json.dump(used, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
