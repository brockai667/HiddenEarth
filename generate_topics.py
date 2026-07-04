#!/usr/bin/env python3
"""Doplni banku tem cez GitHub Models (zadarmo). Nika: CESTOVANIE / skryte a surrealne miesta.
NOVY FORMAT (PRO engine, schvaleny 2026-07-04): tema = place + country + 5-6 scen
(hook/map/fact/callout/cta) s presnymi stock queries, sync chipmi a popisom s pinom.
Stare temy bez 'scenes' sa pri generovani z banky vyradia (nepouzite), publikovane ostavaju."""
import json
import os
import re
import sys

import requests
try:
    import trends                      # trend scanner (Reddit + YouTube), volitelny
except Exception:
    trends = None

ROOT = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(ROOT, "topics_bank.json")
STATE = os.path.join(ROOT, "used_topics.json")

TARGET = int(os.environ.get("TOPICS_TARGET", "15"))
MODEL = os.environ.get("MODELS_MODEL", "openai/gpt-4o-mini")
BASE = os.environ.get("MODELS_BASE_URL", "https://models.github.ai/inference")
TOKEN = os.environ.get("MODELS_TOKEN") or os.environ.get("GITHUB_TOKEN")

# Nika: CESTOVANIE / hidden earth -> kde ludia realne diskutuju / co pozeraju
TREND_SUBREDDITS = ['travel', 'geography', 'MostBeautiful', 'EarthPorn', 'AbandonedPorn']
TREND_YT_QUERIES = ['hidden places on earth', 'most beautiful places', 'abandoned places']

SYSTEM = ("You are a scriptwriter for a premium travel brand that profiles ONE specific, real, "
          "stunning place on Earth per video - a tiny cinematic mini-doc. You ALWAYS name the place "
          "and say WHERE it is (region + country), accurately. Only REAL places, REAL locations, REAL "
          "facts - never invent a place, a location, or a statistic. If unsure of any detail, leave it "
          "out or pick a place you are sure about. You output strict JSON, nothing else.")

EXAMPLE = {
    "title": "The Lake That's Naturally Bright Pink",
    "place": "Lake Hillier",
    "country": "Australia",
    "scenes": [
        {"role": "hook", "text": "This lake is bubblegum pink, and it is completely real.",
         "hook_top": "A LAKE THAT IS PINK", "query": "lake hillier pink lake aerial",
         "query2": "pink lake aerial"},
        {"role": "map", "text": "This is Lake Hillier, on Middle Island off Western Australia."},
        {"role": "fact", "text": "The pink comes from salt-loving algae that thrive in water ten times saltier than the ocean.",
         "query": "lake hillier pink water", "query2": "pink salt lake water", "photo_hint": "aerial",
         "chips": [{"t": "10x SALTIER THAN SEA", "on": "saltier", "style": "orange"}], "punch": "algae"},
        {"role": "callout", "text": "And even in a glass, the water stays pink.",
         "query": "pink water close up", "query2": "pink lake shore",
         "label": "STAYS PINK", "sub": "even in a glass", "label_on": "glass", "punch": "pink"},
        {"role": "cta", "text": "Follow for places that don't feel real.",
         "query": "lake hillier aerial sunset", "query2": "drone island coast sunset"}
    ],
    "description": "\U0001F4CD Lake Hillier, Middle Island - Western Australia. A naturally bubblegum-pink lake that keeps its color even in a glass. Follow for daily hidden places! \U0001F30D",
    "hashtags": ["#travel", "#lakehillier", "#australia", "#hiddengems", "#pinklake", "#earth", "#shorts", "#fyp"],
}


import random  # CTAS_ROTATE

CTAS = [
    "Follow to see the world's hidden places.",
    "Follow for a new corner of Earth every day.",
    "Follow if you love to travel from your screen.",
    "Follow for the planet's best-kept secrets.",
    "Follow for places that don't feel real.",
]


def build_prompt(n, existing_titles, existing_places, trending=None):
    trend_block = ""
    if trending:
        joined = chr(10).join("- " + t for t in trending)
        trend_block = (
            " WHAT REAL PEOPLE DISCUSS AND WATCH THIS WEEK (live headlines from Reddit communities and "
            "top YouTube videos in this niche - what the audience actually cares about right now): " + joined +
            " Let at least HALF of the new topics be directly inspired by a SPECIFIC item above, turned "
            "into a strong hook that STILL follows the style and safety rules described. Do NOT copy any "
            "headline word-for-word, and NEVER mention Reddit or YouTube. "
        )
    return (
        f"Generate {n} NEW faceless short-form video topics for a premium TRAVEL brand. Each video is a "
        "cinematic MICRO-DOC of ONE specific, real, jaw-dropping place on Earth (TikTok / Reels / Shorts).\n"
        "Return ONLY a JSON array (no markdown). Each item EXACTLY this schema:\n"
        f"{json.dumps(EXAMPLE, ensure_ascii=False, indent=2)}\n\n"
        "Rules (PRO editing pipeline depends on these):\n"
        "- Pick a SPECIFIC, REAL, visually unreal place (e.g. Zhangye Danxia, Pamukkale, Socotra, "
        "Lencois Maranhenses, Cano Cristales, Fly Geyser, Antelope Canyon, Spotted Lake, Deadvlei, "
        "Giant's Causeway). REAL name + REAL location only. ONE place per video.\n"
        "- 'place' = official short name, 'country' = country (both REQUIRED, used for the map pin and "
        "geocoding - must be findable on OpenStreetMap).\n"
        "- EXACTLY 5 or 6 scenes in this order: hook, map, fact, (optional second fact or callout), "
        "callout, cta. Each scene 'text' = 1-2 short spoken sentences (calm, awe-filled voiceover).\n"
        "- hook: the most unreal TRUE thing about the place, under 14 words. 'hook_top' = the same idea "
        "compressed to MAX 6 punchy words (big kinetic text on screen). Never start with 'Did you know'.\n"
        "- map scene 'text' MUST say where it is: region + country, accurately.\n"
        "- fact scenes: ONE fascinating TRUE fact each (why it looks like that / how it formed). "
        "'chips' = 1-2 short TRUE fact-chips shown on screen: {'t': 'MAX 22 CHARS TEXT', 'on': 'the spoken "
        "word that triggers it', 'style': 'white'|'orange'}. NEVER invent numbers for chips - only widely "
        "documented ones; if no reliable number exists, use a word chip (e.g. 'STILL GROWING').\n"
        "- callout scene: 'label' = 2-4 word on-screen label of WHAT we point at (e.g. 'THE MIRROR EFFECT'), "
        "'sub' = short sub-line (max 34 chars), 'label_on' = spoken word that triggers it.\n"
        "- 'punch' (optional, fact/callout scenes): ONE spoken word where the shot subtly zooms.\n"
        "- EVERY scene except map needs 'query' = Pexels search that STARTS with the place's proper name "
        "(e.g. 'salar de uyuni reflection') and 'query2' = visual fallback WITHOUT the name describing how "
        "it LOOKS (e.g. 'salt flat mirror reflection'). Optional 'photo_hint' for archival photo fallback.\n"
        "- the LAST scene text MUST be exactly: 'Follow for places that don't feel real.'\n"
        "- ACCURACY IS CRITICAL: the place, its location, and every fact must be REAL and correct. "
        "No invented places, no fake numbers. If unsure, choose a place you are certain about.\n"
        "- description: MUST begin with a location pin: '\U0001F4CD <Place>, <Region> - <Country>.' then 1-2 "
        "intriguing sentences about what makes it unreal, then 'Follow for daily hidden places!' "
        "(optionally ONE emoji at the very end). Emoji/pin ONLY in the description, never in scene texts.\n"
        "- hashtags: 6-9 tags: #travel #hiddengems #shorts #fyp + 2-3 specific to the place/country.\n"
        "- VARY THE TITLE FORMAT: mix a bold claim, a question, a 'why/how' angle and a curiosity gap; "
        "do NOT start more than one in five titles with a number.\n"
        f"- Do NOT reuse any of these existing titles: {existing_titles}\n"
        f"- Do NOT use any of these already-covered places (no repeats, not even from a new angle): {existing_places}\n"
        + trend_block +
        "Return ONLY the JSON array."
    )


def call_model(user_text):
    r = requests.post(
        BASE.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"model": MODEL, "temperature": 0.95,
              "messages": [{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": user_text}]},
        timeout=180,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Models API {r.status_code}: {r.text[:500]}")
    return r.json()["choices"][0]["message"]["content"]


def extract_json(s):
    s = s.strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    a, b = s.find("["), s.rfind("]")
    if a != -1 and b != -1:
        s = s[a:b + 1]
    return json.loads(s)


def _place_first_query(q, place):
    """Zaisti, ze query zacina menom miesta (presny stock je zaklad pristupu)."""
    q = str(q or "").strip()
    pl = str(place or "").strip()
    if not pl:
        return q
    if pl.lower() not in q.lower():
        return (pl + " " + q).strip()
    return q


def valid(t):
    """Overi + doopravi NOVY format temy (scenes). Stare/nevalidne temy odmietne."""
    if not isinstance(t, dict) or not t.get("title") or not t.get("place") or not t.get("country"):
        return False
    scenes = t.get("scenes")
    if not isinstance(scenes, list) or not (4 <= len(scenes) <= 7):
        return False
    for sc in scenes:
        if not isinstance(sc, dict) or not sc.get("text"):
            return False
        sc.setdefault("role", "fact")
    roles = [sc["role"] for sc in scenes]
    scenes[0]["role"] = "hook"
    scenes[-1]["role"] = "cta"
    if "map" not in roles:
        return False
    for sc in scenes:
        if sc["role"] == "hook":
            top = re.sub(r"[^A-Za-z0-9' ]", "", str(sc.get("hook_top") or sc["text"]))
            sc["hook_top"] = " ".join(top.split()[:6]).upper()
        if sc["role"] != "map":
            sc["query"] = _place_first_query(sc.get("query"), t["place"])
            if not sc.get("query2"):
                sc["query2"] = str(sc.get("query", "")).replace(str(t["place"]), "").strip() or "aerial landscape"
        if sc["role"] == "fact":
            chips = [c for c in (sc.get("chips") or []) if isinstance(c, dict) and c.get("t")]
            for c in chips:
                c["t"] = str(c["t"])[:24]
            sc["chips"] = chips[:2]
    t.setdefault("description", f"\U0001F4CD {t['place']} - {t['country']}. " + t["title"] + " Follow for daily hidden places!")
    t.setdefault("hashtags", ["#travel", "#hiddengems", "#shorts", "#fyp"])
    return True


_STOP = {"why", "your", "the", "is", "a", "of", "you", "that", "are", "and", "to", "in",
         "on", "how", "this", "for", "with", "it", "its", "can", "cant", "not", "be", "do",
         "than", "them", "their", "own", "what", "when", "was", "were", "has", "have", "from",
         "more", "most", "just", "every", "an", "as", "or", "but", "so", "hidden", "secret",
         "surprising", "truth", "facts", "fact", "these", "there", "they"}


def _sig(title):
    return set(w for w in re.findall(r"[a-z]+", str(title).lower()) if len(w) > 2 and w not in _STOP)


def _too_similar(sig, existing_sigs):
    if not sig:
        return False
    for es in existing_sigs:
        if not es:
            continue
        inter = len(sig & es)
        if inter >= 3:
            return True
        if inter >= 2 and inter / (len(sig | es) or 1) >= 0.5:
            return True
    return False


def _place_key(t):
    """Normalizovany kluc miesta - rovnake miesto sa NIKDY neopakuje."""
    return re.sub(r"[^a-z0-9]+", "", str(t.get("place", "") if isinstance(t, dict) else t).lower())



# --- ANTI-OPAKOVANIE (dedup): po behu odstrani z banky NEPOUZITE temy, ktore su subjektom
# prilis podobne inej teme. Signatura = title+description+hook + cisla/roky; caste niche-slova
# sa auto-ignoruju cez frekvenciu (df). Duale pravidlo: rovnaky ROK + prekrytie = dup;
# rozne roky = rozne pripady; bezrocnove niky -> silna slovna zhoda. Publikovane sa NIKDY nemazu.
_DD_STOP = set("""a an the this that these those and or but so of to in on for with at by from as is are was
were be been being it its you your they them their our we he she his her my me i do does did not no can cant
will just every most more than then there here what when why how who which while into over out up down off only
also very much many some any all if thing things way ways get make made youre follow daily wisdom mindset day
today need needs about like want wants nobody tells tell told never ever still story people world reveal
revealed discover""".split())


def _dd_sig(t):
    first = ""
    if t.get("scenes"):
        first = t["scenes"][0].get("text", "")
    elif t.get("segments"):
        first = t["segments"][0].get("text", "")
    txt = (str(t.get("title", "")) + " " + str(t.get("place", "")) + " "
           + str(t.get("description", "")) + " " + str(first))
    low = txt.lower()
    toks = set(w for w in re.findall(r"[a-z]+", low) if len(w) > 2 and w not in _DD_STOP)
    toks |= set("#" + n for n in re.findall(r"\d{2,}", low))
    return toks


def _dd_years(s):
    return set(w for w in s if len(w) == 5 and w[0] == "#" and w[1] in "12")


def _dd_dup(si, sj):
    common = si & sj
    if len(common) < 3:
        return False
    yi, yj = _dd_years(si), _dd_years(sj)
    yc = yi & yj
    if yi and yj and not yc:
        return False                                   # rozne roky = rozne pripady
    jac = len(common) / (len(si | sj) or 1)
    if yc and len(common) >= 3:
        return True                                    # spolocny rok + prekrytie
    if not (yi or yj) and len(common) >= 4 and jac >= 0.5:
        return True                                    # bezrocnove niky -> silna slovna zhoda
    return False


def _clean_bank():
    """Odstrani NEPOUZITE temy prilis podobne inej teme + duplicitne MIESTA (ziadne opakovanie).
    Publikovane (used_topics) sa nikdy nemazu. Best-effort, nikdy nezhodi denny beh."""
    from collections import Counter
    bank = json.load(open(BANK, encoding="utf-8"))
    used = set(json.load(open(STATE, encoding="utf-8"))) if os.path.exists(STATE) else set()
    raws = [_dd_sig(t) for t in bank]
    df = Counter()
    for s in raws:
        for w in s:
            df[w] += 1
    cutoff = max(2, int(len(bank) * 0.25))             # slovo vo >25% tem = niche-filler -> ignoruj
    sigs = [set(w for w in s if df[w] <= cutoff) for s in raws]
    ks = [s for t, s in zip(bank, sigs) if t.get("title") in used]   # seed: vsetky publikovane
    places = {_place_key(t) for t in bank if t.get("title") in used and t.get("place")}
    kept, removed = [], 0
    for t, s in zip(bank, sigs):
        if t.get("title") in used:
            kept.append(t)
            continue
        pk = _place_key(t)
        if (s and any(_dd_dup(s, k) for k in ks)) or (pk and pk in places):
            removed += 1
            continue
        kept.append(t)
        ks.append(s)
        if pk:
            places.add(pk)
    if removed:
        json.dump(kept, open(BANK, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("Dedup: odstranenych %d podobnych/duplicitnych nepouzitych tem." % removed)
    else:
        print("Dedup: ziadne podobne nepouzite temy.")



def main():
    if not TOKEN:
        print("CHYBA: chyba MODELS_TOKEN/GITHUB_TOKEN"); sys.exit(1)
    bank = json.load(open(BANK, encoding="utf-8"))
    used = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else []
    # MIGRACIA na PRO format: nepouzite temy STAREHO formatu (bez 'scenes') vyrad -
    # ale LEN ak uz mame aspon 3 nove PRO temy (poistka: den nikdy neostane bez videi,
    # keby LLM generovanie zlyhalo)
    old = [t for t in bank if not t.get("scenes") and t["title"] not in used]
    new_unused = [t for t in bank if t.get("scenes") and t["title"] not in used]
    if old and len(new_unused) >= 3:
        bank = [t for t in bank if t.get("scenes") or t["title"] in used]
        print(f"Migracia: vyradenych {len(old)} nepouzitych tem stareho formatu.")
    titles = {t["title"] for t in bank}
    unused = [t for t in bank if t["title"] not in used]
    need = TARGET - len(unused)
    if need <= 0:
        json.dump(bank, open(BANK, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"Banka OK: {len(unused)} nepouzitych tem."); return
    print(f"Generujem ~{need} novych tem cez {MODEL}...")
    trending = []
    if trends is not None:
        try:
            trending, meta = trends.gather(TREND_SUBREDDITS, TREND_YT_QUERIES, top=18, return_meta=True)
            if trending:
                print(f"Trendy: {len(trending)} titulkov (Reddit={meta['reddit']}, YouTube={meta['youtube']}) -> temy z realneho dopytu.")
        except Exception as e:
            print("Trendy preskocene:", str(e)[:120])
    places = sorted({_place_key(t) for t in bank if t.get("place")} |
                    {_place_key(u) for u in used})
    items = extract_json(call_model(build_prompt(need + 3, sorted(titles), places, trending)))
    added = 0
    existing_sigs = [_sig(x) for x in titles]
    existing_places = {_place_key(t) for t in bank if t.get("place")}
    for t in items:
        if not valid(t) or t["title"] in titles:
            continue
        _s = _sig(t["title"])
        if _too_similar(_s, existing_sigs):   # ta ista TEMA (iny nazov) -> preskoc (ziadne opakovanie)
            print("  preskocene (podobna tema):", t["title"]); continue
        pk = _place_key(t)
        if pk and pk in existing_places:      # rovnake MIESTO -> preskoc (nikdy 2x to iste miesto)
            print("  preskocene (miesto uz bolo):", t["place"]); continue
        if t.get("scenes"):
            t["scenes"][-1]["text"] = random.choice(CTAS)  # CTAS_ROTATE: nie vzdy rovnaka veta
        bank.append(t); titles.add(t["title"]); existing_sigs.append(_s); added += 1
        if pk:
            existing_places.add(pk)
    json.dump(bank, open(BANK, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Pridanych {added} tem. Banka ma {len(bank)} tem.")


if __name__ == "__main__":
    main()
    try:
        _clean_bank()
    except Exception as _e:
        print("Dedup preskoceny:", str(_e)[:150])
