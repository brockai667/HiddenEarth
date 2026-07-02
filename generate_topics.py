#!/usr/bin/env python3
"""Doplni banku tem cez GitHub Models (zadarmo). Nika: CESTOVANIE / skryte a surrealne miesta."""
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

SYSTEM = ("You are a viral short-form scriptwriter for a travel brand that profiles ONE specific, real, "
          "stunning place on Earth per video — a tiny travel mini-doc. You ALWAYS name the place and say "
          "WHERE it is (region + country), accurately. Only REAL places, REAL locations, REAL facts — "
          "never invent a place, a location, or a statistic. If unsure of any detail, leave it out or pick "
          "a place you are sure about. You output strict JSON, nothing else.")

EXAMPLE = {
    "title": "The Lake That's Naturally Bright Pink",
    "segments": [
        {"text": "This lake is bubblegum pink, and it's completely real.", "keywords": "pink lake aerial", "highlight": "BUBBLEGUM PINK"},
        {"text": "It's called Lake Hillier.", "keywords": "pink lake shore aerial", "highlight": "LAKE HILLIER"},
        {"text": "You'll find it on Middle Island, off Western Australia.", "keywords": "australia island coast aerial", "highlight": "WESTERN AUSTRALIA"},
        {"text": "Its pink comes from salt-loving algae and bacteria.", "keywords": "pink salt lake water", "highlight": "PINK ALGAE"},
        {"text": "And it stays pink even in a glass of its water.", "keywords": "pink water close up", "highlight": "STILL PINK"},
        {"text": "A pink lake hiding at the edge of the world.", "keywords": "pink lake drone aerial", "highlight": "LAKE HILLIER"},
        {"text": "Follow for places that don't feel real.", "keywords": "drone landscape sunset", "highlight": "FOLLOW"},
    ],
    "description": "📍 Lake Hillier, Middle Island — Western Australia. A naturally bubblegum-pink lake. Follow for daily hidden places! 🌍",
    "hashtags": ["#travel", "#lakehillier", "#australia", "#hiddengems", "#places", "#earth", "#shorts", "#fyp"],
}


import random  # CTAS_ROTATE

CTAS = [
    "Follow to see the world's hidden places.",
    "Follow for a new corner of Earth every day.",
    "Follow if you love to travel from your screen.",
    "Follow for the planet's best-kept secrets.",
    "Follow for daily wonders of our world.",
]


def build_prompt(n, existing_titles, trending=None):
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
        f"Generate {n} NEW faceless short-form video topics for a TRAVEL brand that profiles ONE specific, "
        "real, jaw-dropping place on Earth per video (TikTok / Reels / YouTube Shorts).\n"
        "Each video is a tiny mesmerizing MICRO-DOC of ONE real place: show how unreal it looks, say WHAT it "
        "is, WHERE it is (region + country), and ONE fascinating TRUE fact about it.\n"
        "Return ONLY a JSON array (no markdown). Each item EXACTLY this schema:\n"
        f"{json.dumps(EXAMPLE, ensure_ascii=False, indent=2)}\n\n"
        "Rules (make it feel PRO and VIRAL):\n"
        "- Pick a SPECIFIC, REAL, visually unreal place — e.g. Lake Hillier, Zhangye Danxia rainbow mountains, "
        "Pamukkale, Salar de Uyuni, Socotra, Lencois Maranhenses, Cano Cristales, Fly Geyser, Vaadhoo glowing "
        "beach, Antelope Canyon. REAL name + REAL location only. Each video = ONE place.\n"
        "- title: a curiosity hook about THAT place, e.g. 'The Lake That's Naturally Bright Pink' or "
        "'China's Rainbow Mountains Look Painted'. Never start with 'Did you know'.\n"
        "- 6 to 8 segments forming a mini-doc: (1) HOOK = the most unreal thing about it, scroll-stopping, "
        "under 12 words; (2) NAME the place; (3) WHERE it is — region + country (REQUIRED and accurate); "
        "(4) ONE fascinating TRUE fact (why it looks like that); optionally (5) one more wow detail; "
        "then loop back to the place name; the LAST segment text MUST be exactly "
        "'Follow for places that don't feel real.'\n"
        "- include a 'highlight' field per segment: the 1-3 word KEY phrase to emphasize on screen — the PLACE "
        "NAME, the COUNTRY, or the striking word, e.g. 'LAKE HILLIER', 'CHINA', 'PINK ALGAE'.\n"
        "- 'keywords': 2-4 ENGLISH words describing how the place LOOKS so it matches real Pexels footage "
        "(e.g. 'pink lake aerial', 'rainbow mountains china', 'white travertine terraces', 'salt flat "
        "reflection', 'slot canyon light'). Describe the VISUAL, not just the proper name alone.\n"
        "- ACCURACY IS CRITICAL: the place, its location, and the fact must be REAL and correct. No invented "
        "places, no fake numbers. If unsure, choose a place you are certain about.\n"
        "- write for a calm, awe-filled SPOKEN voiceover: short, vivid, simple sentences.\n"
        "- description: MUST begin with a location pin in this format: '\U0001F4CD <Place>, <Region> — <Country>.' "
        "then one intriguing sentence, then 'Follow for daily hidden places!' (optionally ONE emoji at the very end). "
        "Emoji/pin ONLY in the description, NEVER inside any segment 'text'.\n"
        "- hashtags: 6-8 tags including #travel #hiddengems #shorts #fyp, plus 1-2 specific to the place or country.\n"
        "- VARY THE TITLE FORMAT: do NOT start more than one in five titles with a number "
        "(avoid the repetitive 'N things' pattern). Mix a bold claim, a question, a "
        "'why/how' angle and a curiosity gap so titles never look the same.\n"
        f"- Do NOT reuse any of these existing titles: {existing_titles}\n"
        "- Do NOT repeat the same SUBJECT, fact or concept as any existing title above, even reworded, "
        "renumbered or from a different angle. Every topic must be a genuinely DIFFERENT idea.\n"
        + trend_block +
        "STORYBOARD (visual directing, IMPORTANT): to EVERY segment ADD a field 'visual' = an object choosing HOW to visualize exactly what that line SAYS (never generic): {\"type\":\"kenburns\",\"prompt\":\"LITERAL ENGLISH image prompt naming ONE concrete, instantly recognizable subject/scene that depicts exactly what the line says (a real thing a camera could photograph; NEVER abstract, NEVER metaphors)\"} for normal lines; {\"type\":\"counter\",\"target\":1000,\"suffix\":\"x\",\"label\":\"3-4 WORD CAPTION\"} when the line contains a big number; {\"type\":\"compare\",\"small_prompt\":\"...\",\"big_prompt\":\"...\",\"small_label\":\"X\",\"big_label\":\"Y\",\"stat\":\"300x\"} for size/amount comparisons; {\"type\":\"callouts\",\"prompt\":\"subject image\",\"labels\":[\"SHORT LABEL\"]} to point at parts of a subject; {\"type\":\"lineup\",\"items\":[{\"name\":\"A\",\"prompt\":\"...\"}]} for listing 3-5 things; {\"type\":\"arrow\",\"from_prompt\":\"...\",\"to_prompt\":\"...\",\"label\":\"WHAT MOVES\"} for movement/flow. First segment gets {\"type\":\"hook\",\"prompt\":\"dramatic scene image\",\"big\":\"SHORT PUNCHY QUESTION OR CLAIM (max 5 words)\"}; last segment {\"type\":\"cta\",\"prompt\":\"iconic subject of the video\"}. Labels MUST describe what the narration says at that moment - never invent unrelated text. Return ONLY the JSON array."
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


def valid(t):
    if not isinstance(t, dict) or "title" not in t or "segments" not in t:
        return False
    if not isinstance(t["segments"], list) or len(t["segments"]) < 4:
        return False
    for seg in t["segments"]:
        if "text" not in seg or "keywords" not in seg:
            return False
    t.setdefault("description", t["title"] + " Follow for daily hidden places!")
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


def main():
    if not TOKEN:
        print("CHYBA: chyba MODELS_TOKEN/GITHUB_TOKEN"); sys.exit(1)
    bank = json.load(open(BANK, encoding="utf-8"))
    used = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else []
    titles = {t["title"] for t in bank}
    unused = [t for t in bank if t["title"] not in used]
    need = TARGET - len(unused)
    if need <= 0:
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
    items = extract_json(call_model(build_prompt(need + 3, sorted(titles), trending)))
    added = 0
    existing_sigs = [_sig(x) for x in titles]
    for t in items:
        if not valid(t) or t["title"] in titles:
            continue
        _s = _sig(t["title"])
        if _too_similar(_s, existing_sigs):   # ta ista TEMA (iny nazov) -> preskoc (ziadne opakovanie)
            print("  preskocene (podobna tema):", t["title"]); continue
        if t.get("segments"):
            t["segments"][-1]["text"] = random.choice(CTAS)  # CTAS_ROTATE: nie vzdy rovnaka veta
        bank.append(t); titles.add(t["title"]); existing_sigs.append(_s); added += 1
    json.dump(bank, open(BANK, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Pridanych {added} tem. Banka ma {len(bank)} tem.")


if __name__ == "__main__":
    main()
