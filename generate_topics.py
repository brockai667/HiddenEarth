#!/usr/bin/env python3
"""Doplni banku tem cez GitHub Models (zadarmo). Nika: CESTOVANIE / skryte a surrealne miesta."""
import json
import os
import re
import sys

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(ROOT, "topics_bank.json")
STATE = os.path.join(ROOT, "used_topics.json")

TARGET = int(os.environ.get("TOPICS_TARGET", "15"))
MODEL = os.environ.get("MODELS_MODEL", "openai/gpt-4o-mini")
BASE = os.environ.get("MODELS_BASE_URL", "https://models.github.ai/inference")
TOKEN = os.environ.get("MODELS_TOKEN") or os.environ.get("GITHUB_TOKEN")

SYSTEM = ("You are a viral short-form scriptwriter for a travel & hidden-places brand. You only feature "
          "REAL places on Earth, described with awe. No invented places, no fake statistics. "
          "You output strict JSON, nothing else.")

EXAMPLE = {
    "title": "3 Places That Don't Look Real",
    "segments": [
        {"text": "This lake is bright pink, and it's completely real.", "keywords": "pink lake aerial", "highlight": "PINK LAKE"},
        {"text": "And it's only the beginning.", "keywords": "aerial turquoise coast", "highlight": "JUST THE START"},
        {"text": "A desert of golden dunes spills straight into the sea.", "keywords": "desert meets ocean", "highlight": "DESERT MEETS SEA"},
        {"text": "A whole forest of stone rises from the ground.", "keywords": "stone forest rocks", "highlight": "STONE FOREST"},
        {"text": "And a waterfall seems to pour into the clouds.", "keywords": "waterfall clouds mountain", "highlight": "INTO THE CLOUDS"},
        {"text": "Earth looks photoshopped, but every bit is real.", "keywords": "aerial mountains drone", "highlight": "ALL REAL"},
        {"text": "Follow for places that don't feel real.", "keywords": "drone landscape sunset", "highlight": "FOLLOW"},
    ],
    "description": "Earth looks photoshopped — but it's all real. Follow for daily hidden places!",
    "hashtags": ["#travel", "#hiddengems", "#wanderlust", "#places", "#earth", "#shorts", "#fyp", "#beautifuldestinations"],
}


def build_prompt(n, existing_titles):
    return (
        f"Generate {n} NEW faceless short-form video topics for a TRAVEL & HIDDEN-PLACES brand "
        "(TikTok / Reels / YouTube Shorts).\n"
        "Niche: real but surreal/hidden places on Earth — strange landscapes, hidden gems, places that look fake.\n"
        "Return ONLY a JSON array (no markdown). Each item EXACTLY this schema:\n"
        f"{json.dumps(EXAMPLE, ensure_ascii=False, indent=2)}\n\n"
        "Rules (make it feel PRO and VIRAL):\n"
        "- title: dreamy/curiosity, like '3 Places That Don't Look Real', 'A City With No Cars', "
        "or 'The Lake That Shouldn't Exist'.\n"
        "- 6 to 9 segments. Segment 1 is THE HOOK: a scroll-stopping curiosity line under 12 words. "
        "Use one of these proven hook shapes: a 'What if...' question, a bold claim, a surprising number, "
        "or naming an unbelievable real place. Never start with 'Did you know'. The hook should make the "
        "viewer NEED to know where it is.\n"
        "- include a 'highlight' field per segment: the 1-3 word KEY phrase from that segment's text to "
        "emphasize on screen (the place name or the striking word), e.g. 'AVORIAZ', 'PINK LAKE', 'NO CARS'.\n"
        "- segment 2 keeps them watching (e.g. 'And it's only the beginning.').\n"
        "- feature ONLY REAL places (you can describe the type without naming the country if unsure). "
        "NO invented places, NO fake statistics. Pure awe and wonder.\n"
        "- write for a calm, awe-filled SPOKEN voiceover: short, vivid, simple sentences.\n"
        "- each segment 'keywords': 1-3 ENGLISH words for real Pexels footage that VISUALLY MATCHES the place "
        "described (e.g. 'pink lake aerial', 'desert meets ocean', 'waterfall clouds mountain', "
        "'drone landscape sunset'). Beautiful and concrete, never abstract.\n"
        "- the SECOND-TO-LAST segment should loop back to the opening hook so a rewatch feels seamless.\n"
        "- the LAST segment text MUST be exactly: 'Follow for places that don't feel real.'\n"
        "- description: one dreamy sentence ending with 'Follow for daily hidden places!'.\n"
        "- hashtags: 6-8 tags including #travel #hiddengems #shorts #fyp.\n"
        f"- Do NOT reuse any of these existing titles: {existing_titles}\n"
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
    items = extract_json(call_model(build_prompt(need + 3, sorted(titles))))
    added = 0
    for t in items:
        if not valid(t) or t["title"] in titles:
            continue
        bank.append(t); titles.add(t["title"]); added += 1
    json.dump(bank, open(BANK, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Pridanych {added} tem. Banka ma {len(bank)} tem.")


if __name__ == "__main__":
    main()
