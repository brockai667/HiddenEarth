#!/usr/bin/env python3
"""
FacelessFactory - automaticka tvorba vertikalnych (9:16) faceless videi.

Pipeline:
  1) edge-tts  -> anglicky hlas (MP3) + casovanie slov pre titulky
  2) Pexels    -> stiahne B-roll klipy podla klucovych slov (volitelne)
  3) FFmpeg    -> poskladne video 1080x1920, prida titulky a hudbu
  4) vystup    -> hotove MP4 + textovy subor s popisom/hashtagmi

Pouzitie:
  python make_video.py scripts/sample.json
  python make_video.py scripts/sample.json --open
"""

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))

# tmave farby pre fallback pozadie (ked nie je B-roll)
PALETTE = ["0x0f172a", "0x1e1b4b", "0x172554", "0x3b0764", "0x064e3b", "0x431407"]


# ----------------------------------------------------------------------------- helpers
def load_config():
    import appconfig
    return appconfig.load()


def run(cmd):
    """Spusti prikaz, vyhodi chybu s vystupom ak zlyha."""
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        sys.stderr.write("\n[CHYBA] prikaz zlyhal:\n" + " ".join(str(c) for c in cmd) + "\n")
        sys.stderr.write((p.stderr or "")[-3000:] + "\n")
        raise RuntimeError("prikaz zlyhal")
    return p


def probe_duration(ffprobe, path):
    p = run([ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path])
    return float(p.stdout.strip())


def slugify(text):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return (s or "video")[:50]


# ----------------------------------------------------------------------------- TTS
async def _tts(text, voice, out_mp3, rate="+0%", pitch="+0Hz"):
    import edge_tts
    words = []
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    with open(out_mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # offset/duration su v jednotkach 100ns (ticks)
                words.append((chunk["offset"] / 1e7, chunk["duration"] / 1e7, chunk["text"]))
    return words


def tts(text, voice, out_mp3, rate="+0%", pitch="+0Hz"):
    return asyncio.run(_tts(text, voice, out_mp3, rate, pitch))


_KOKORO = None


def _kokoro_model_dir(cfg):
    cands = [cfg.get("kokoro_model_dir"), os.path.join(ROOT, "kokoro"), r"C:\Users\damia\kokoro"]
    for c in cands:
        if c and os.path.exists(os.path.join(c, "kokoro-v1.0.onnx")):
            return c
    return os.path.join(ROOT, "kokoro")


def _ensure_kokoro_model(md):
    import ssl as _ssl
    import urllib.request as _u
    os.makedirs(md, exist_ok=True)
    base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
    ctx = _ssl._create_unverified_context()
    for fn in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        p = os.path.join(md, fn)
        if not os.path.exists(p) or os.path.getsize(p) < 1000000:
            sys.stderr.write(f"[kokoro] stahujem {fn}...\n")
            with _u.urlopen(base + "/" + fn, context=ctx, timeout=600) as r, open(p, "wb") as f:
                f.write(r.read())


def _kokoro_chunks(s, limit=280):
    """Rozdel text na vety, pridlhe vety na slova; kazdy kusok <= limit znakov
    (bezpecne pod 510 fonem, aby kokoro-onnx nespadol na IndexError)."""
    import re as _re
    out = []
    for p in _re.split(r"(?<=[.!?])\s+", (s or "").strip()):
        p = p.strip()
        if not p:
            continue
        if len(p) <= limit:
            out.append(p); continue
        cur = ""
        for w in p.split():
            if cur and len(cur) + len(w) + 1 > limit:
                out.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            out.append(cur)
    return out or [(s or ".").strip() or "."]


def kokoro_tts(text, out_mp3, cfg):
    """Ludsky hlas cez Kokoro (kokoro-onnx). Casovanie slov odhadom (proporcne dlzkou slov).
    Dlhy text sa deli na kusky (< ~280 znakov) a audio sa spaja -> kokoro nikdy nepretecie 510 fonem."""
    global _KOKORO
    import soundfile as sf
    import numpy as _np
    if _KOKORO is None:
        from kokoro_onnx import Kokoro
        md = _kokoro_model_dir(cfg)
        _ensure_kokoro_model(md)
        _KOKORO = Kokoro(os.path.join(md, "kokoro-v1.0.onnx"), os.path.join(md, "voices-v1.0.bin"))
    voice = cfg.get("kokoro_voice", "am_puck")
    speed = float(cfg.get("kokoro_speed", 1.0))
    parts, sr = [], 24000
    for ch in _kokoro_chunks(text):
        s, sr = _KOKORO.create(ch, voice=voice, speed=speed, lang="en-us")
        if s is not None and len(s):
            parts.append(s)
    samples = _np.concatenate(parts) if len(parts) > 1 else (parts[0] if parts else _np.zeros(1, dtype="float32"))
    wav = out_mp3 + ".tmp.wav"
    sf.write(wav, samples, sr)
    run([cfg["ffmpeg"], "-y", "-i", wav, "-b:a", "160k", out_mp3])
    try:
        os.remove(wav)
    except OSError:
        pass
    dur = len(samples) / sr
    toks = text.split() or [text]
    wts = [len(w) + 1 for w in toks]
    tot = sum(wts) or 1
    out, t = [], 0.0
    for w, wt in zip(toks, wts):
        d = dur * wt / tot
        out.append((t, d, w))
        t += d
    return out


_WM = None


def align(audio, fallback):
    """Forced alignment cez faster-whisper -> PRESNE casovanie slov (titulky sediace s AI hlasom).
    Kriticke pri dlhych dokumentoch (proporcny odhad inak ujde). Pri chybe vrati fallback."""
    global _WM
    try:
        if _WM is None:
            from faster_whisper import WhisperModel
            _WM = WhisperModel("base.en", device="cpu", compute_type="int8")
        segs, _ = _WM.transcribe(audio, word_timestamps=True, language="en")
        out = []
        for s in segs:
            for w in (s.words or []):
                wt = w.word.strip()
                if wt:
                    out.append((float(w.start), max(0.05, float(w.end) - float(w.start)), wt))
        return out if len(out) >= max(1, len(fallback) // 2) else fallback
    except Exception as e:
        sys.stderr.write("align zlyhal: %s\n" % str(e)[:120])
        return fallback


def openai_tts(text, key, voice, model, instructions, out_mp3, ffprobe):
    """Prirodzeny hlas cez OpenAI TTS. Vrati ODHAD casovania slov (OpenAI ho nedava)."""
    import requests
    body = {"model": model, "voice": voice, "input": text, "response_format": "mp3"}
    if instructions and "gpt-4o" in model:
        body["instructions"] = instructions
    r = requests.post("https://api.openai.com/v1/audio/speech",
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json=body, timeout=120)
    r.raise_for_status()
    with open(out_mp3, "wb") as f:
        f.write(r.content)
    dur = probe_duration(ffprobe, out_mp3)
    # odhad casovania slov podla dlzky slov (na word-pop titulky)
    ws = text.split()
    weights = [max(1, len(w)) for w in ws] or [1]
    total = sum(weights)
    t, out = 0.0, []
    for w, wt in zip(ws, weights):
        d = dur * wt / total
        out.append((t, d, w)); t += d
    return out


def trim_trailing_silence(ff, src, dst, gap=0.12):
    """Odreze dlhe ticho na konci segmentu a necha jednotnu pauzu (gap s)
    -> oddeli vety/fakty ako odseky. Trailing-only (cez areverse),
    takze casovanie slov pre titulky ostava platne."""
    af = ("areverse,silenceremove=start_periods=1:start_duration=0.02:"
          f"start_threshold=-50dB,areverse,apad=pad_dur={gap}")
    try:
        run([ff, "-y", "-i", src, "-af", af, dst])
        return dst
    except Exception:
        import shutil
        shutil.copyfile(src, dst)   # fallback: ak orez zlyha, pouzi povodne audio
        return dst


# ----------------------------------------------------------------------------- B-roll (Pexels)
def get_broll(keywords, cfg, broll_dir, used_ids):
    """Vrati (cesta, clip_id) k B-roll klipu, ktory este nebol pouzity v tomto videu.
    Dedup podla ID klipu zabranuje opakovaniu rovnakeho zaberu. Inak (None, None)."""
    key = cfg.get("pexels_api_key", "").strip()
    if not key or not keywords:
        return None, None
    try:
        import requests

        def search(params):
            r = requests.get("https://api.pexels.com/videos/search", params=params,
                             headers={"Authorization": key}, timeout=30)
            r.raise_for_status()
            return r.json().get("videos", [])

        # klipy v SPRAVNEJ orientacii (docs=landscape 16:9, shorts=portrait) -> minimalny orez, ostre.
        orient = "portrait" if int(cfg.get("height", 1080)) >= int(cfg.get("width", 1920)) else "landscape"
        vids = search({"query": keywords, "per_page": 40, "orientation": orient})
        if not vids:
            vids = search({"query": keywords, "per_page": 40})

        def rank(f):
            h = f.get("height") or 0
            # preferuj co NAJVYSSIE rozlisenie do 2160 (ostry obraz po Ken Burns), potom co najmensie nad 2160
            return (0, -h) if h <= 2160 else (1, h)

        for v in vids:
            vid = v.get("id")
            if vid in used_ids:
                continue
            files = [f for f in v.get("video_files", []) if (f.get("height") or 0) >= 720]
            if not files:
                continue
            files.sort(key=rank)
            cache = os.path.join(broll_dir, f"{vid}.mp4")
            if not os.path.exists(cache):
                data = requests.get(files[0]["link"], timeout=120).content
                with open(cache, "wb") as f:
                    f.write(data)
            return cache, vid
        return None, None
    except Exception as e:
        sys.stderr.write(f"[upozornenie] Pexels zlyhal pre '{keywords}': {e}\n")
        return None, None


def get_image(query, img_dir):
    """Stiahne REALNU/archivnu fotku z Wikimedia Commons (zadarmo, vela public-domain).
    query = vyhladavaci vyraz alebo priamy URL. Vrati cestu k jpg, alebo None."""
    import requests
    UA = {"User-Agent": "FacelessFactory/1.0 (educational documentary)"}
    try:
        if query.startswith("http"):
            url = query
        else:
            r = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=30,
                params={"action": "query", "generator": "search", "gsrsearch": query,
                        "gsrnamespace": "6", "gsrlimit": "12", "prop": "imageinfo",
                        "iiprop": "url|size", "iiurlwidth": "1400", "format": "json"})
            pages = list(r.json().get("query", {}).get("pages", {}).values())
            pages.sort(key=lambda p: p.get("index", 99))
            url = None
            for p in pages:
                ii = p.get("imageinfo")
                title = (p.get("title", "") or "").lower()
                if not ii:
                    continue
                info = ii[0]
                cand = info.get("thumburl") or info.get("url") or ""
                # len skutocne FOTKY/SKENY: jpg/png, ziadne ikony/loga/vlajky
                # (mapy a rytiny NECHAVAME - pre historiu miest su ziadane archivne vizualy)
                if not cand.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png")):
                    continue
                if any(bad in title for bad in ("icon", "logo", "flag", "seal", "coat of arms")):
                    continue
                url = cand; break
            if not url:
                return None
        path = os.path.join(img_dir, re.sub(r"[^a-z0-9]+", "_", query.lower())[:40] + ".jpg")
        data = requests.get(url, headers=UA, timeout=60).content
        if len(data) < 15000:   # < 15 KB = takmer urcite ikona/placeholder -> radsej fallback
            return None
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception as e:
        sys.stderr.write(f"[upozornenie] Wikimedia foto zlyhala pre '{query}': {e}\n")
        return None


# ----------------------------------------------------------------------------- render segment
def render_segment(i, audio_path, duration, broll_path, cfg, tmp, is_image=False):
    ff = cfg["ffmpeg"]
    W, H, FPS = cfg["width"], cfg["height"], cfg["fps"]
    grade = cfg.get("color_grade", "").strip()   # jednotny vzhlad pre vsetky klipy
    out = os.path.join(tmp, f"seg_{i:03d}.mp4")
    common_out = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                  "-pix_fmt", "yuv420p", "-r", str(FPS),
                  "-c:a", "aac", "-ar", "44100", "-b:a", "160k", out]
    motion = cfg.get("motion", True)
    nf = max(1, int(round(duration * FPS)))         # poc. framov -> plynuly pan cez cely zaber

    def broll_cmd(use_motion):
        if is_image:
            # FOTKA: cela viditelna na CIERNOM pozadi (fit, ziadne orezanie) + pomaly zoom
            # (fotku NEpanujeme do strany -> odhalili by sa cierne okraje)
            base = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                    f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
            if use_motion:
                vf = (base + f",zoompan=z='min(zoom+0.0006,1.09)':d=1:"
                             f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS}")
            else:
                vf = base + f",fps={FPS}"
        elif use_motion:
            # Ken Burns na VIDEU so STRIEDANIM pohybu -> kazdy zaber posobi inak (dynamickejsi strih)
            o_w, o_h = int(W * 1.5), int(H * 1.5)
            xc, yc = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            v = i % 3
            if v == 0:
                z, x, y = "min(zoom+0.0012,1.18)", xc, yc                     # zoom-in do stredu
            elif v == 1:
                z, x, y = "min(zoom+0.0010,1.15)", f"(iw-iw/zoom)*on/{nf}", yc   # pan zlava->prava
            else:
                z, x, y = "min(zoom+0.0010,1.15)", xc, f"(ih-ih/zoom)*on/{nf}"   # pan hore->dole
            vf = (f"scale={o_w}:{o_h}:force_original_aspect_ratio=increase,"
                  f"crop={o_w}:{o_h},setsar=1,"
                  f"zoompan=z='{z}':d=1:x='{x}':y='{y}':s={W}x{H}:fps={FPS}")
        else:
            vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H},setsar=1,fps={FPS}")
        if grade:
            vf += "," + grade
        vf += ",format=yuv420p"
        if is_image:   # statocna fotka -> drzime ju a Ken Burns zoomujeme
            in_args = ["-loop", "1", "-framerate", str(FPS), "-i", broll_path]
        else:
            in_args = ["-stream_loop", "-1", "-i", broll_path]
        return [ff, "-y", *in_args, "-i", audio_path,
                "-t", f"{duration:.3f}", "-vf", vf, "-map", "0:v", "-map", "1:a", *common_out]

    if broll_path:
        try:
            run(broll_cmd(motion))
        except Exception:
            if not motion:
                raise
            run(broll_cmd(False))   # fallback bez pohybu, nech sa video vzdy vyrenderuje
    else:
        color = PALETTE[i % len(PALETTE)]
        vf = (grade + "," if grade else "") + "format=yuv420p"
        run([ff, "-y", "-f", "lavfi", "-i", f"color=c={color}:s={W}x{H}:r={FPS}",
             "-i", audio_path, "-t", f"{duration:.3f}", "-vf", vf,
             "-map", "0:v", "-map", "1:a", *common_out])
    return out


# ----------------------------------------------------------------------------- captions (ASS)
def secs_to_ass(t):
    if t < 0:
        t = 0
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60); t -= m * 60
    s = int(t)
    cs = int(round((t - s) * 100))
    if cs >= 100:
        cs = 0; s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(all_words, cfg, path):
    W, H = cfg["width"], cfg["height"]
    fs = cfg.get("caption_fontsize", 82)
    per = max(1, cfg.get("caption_words_per_line", 3))
    mv = cfg.get("caption_margin_v", 880)
    mh = cfg.get("caption_margin_h", 150)
    font = cfg.get("caption_font", "Arial")           # Style C: DM Serif Display
    align = int(cfg.get("caption_alignment", 2))      # 5 = stred (jedno velke slovo)
    case = cfg.get("caption_case", "upper")           # asis -> nechaj ako v scenari
    fade_ms = int(cfg.get("caption_fade_ms", 0))
    lead = float(cfg.get("caption_lead", 0.0))

    def _case(s):
        return s.upper() if case == "upper" else (s.lower() if case == "lower" else s)

    if lead:
        all_words = [(max(0.0, s - lead), d, t) for (s, d, t) in all_words]
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{fs},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,6,2,{align},{mh},{mh},{mv},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    hl = cfg.get("caption_highlight_hex", "00F2FF")   # ASS BGR (zlta)
    pop = cfg.get("caption_pop_scale", 116)
    fadt = ("{\\fad(%d,%d)}" % (fade_ms, min(fade_ms, 100))) if fade_ms else ""
    # rozdel slova na frazy po `per`; v ramci frazy sa zvyrazni prave hovorene slovo
    chunks = [all_words[j:j + per] for j in range(0, len(all_words), per)]
    lines = []
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0][0] if ci + 1 < len(chunks) else chunk[-1][0] + chunk[-1][1]
        for wi, (st, du, _t) in enumerate(chunk):
            ev_start = st
            ev_end = chunk[wi + 1][0] if wi + 1 < len(chunk) else next_start
            if ev_end <= ev_start:
                ev_end = ev_start + 0.15
            parts = []
            for k, w in enumerate(chunk):
                word = _case(w[2]).replace("\n", " ").replace("{", "(").replace("}", ")")
                if k == wi:
                    parts.append(f"{{\\c&H{hl}&\\fscx{pop}\\fscy{pop}}}{word}{{\\r}}")
                elif k < wi:
                    parts.append(word)                                # uz povedane -> viditelne
                else:
                    parts.append(f"{{\\alpha&HFF&}}{word}{{\\r}}")     # buduce -> nevidiltelne (drzi sirku, neprezradi)
            text = fadt + " ".join(parts)
            lines.append(f"Dialogue: 0,{secs_to_ass(ev_start)},{secs_to_ass(ev_end)},Default,,0,0,0,,{text}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")


# ----------------------------------------------------------------------------- assembly
def concat_segments(seg_files, cfg, tmp):
    ff = cfg["ffmpeg"]
    listfile = os.path.join(tmp, "concat.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for s in seg_files:
            f.write(f"file '{s.replace(os.sep, '/')}'\n")
    out = os.path.join(tmp, "concat.mp4")
    run([ff, "-y", "-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", out])
    return out


def add_sfx(ff, video, cut_times, tmp):
    """Pridá RÔZNE jemne prechodove zvuky na strihy (striedaju sa -> profi pocit).
    Plne chranene -> pri chybe vrati povodne video."""
    if not cut_times:
        return video
    try:
        # 4 varianty prechodov: vzdusny swoosh, hlboky brum, jemny shimmer, nizky 'impact'
        variants = [
            ("anoisesrc=d=0.28:c=pink:a=0.06",  "highpass=f=450,lowpass=f=5200,afade=t=in:d=0.05,afade=t=out:st=0.11:d=0.16,volume=0.16"),
            ("anoisesrc=d=0.30:c=white:a=0.04", "highpass=f=650,lowpass=f=7500,afade=t=in:d=0.04,afade=t=out:st=0.12:d=0.16,volume=0.12"),
        ]
        wfiles = []
        for vi, (src, af) in enumerate(variants):
            w = os.path.join(tmp, f"_sfx{vi}.wav")
            run([ff, "-y", "-f", "lavfi", "-i", src, "-af", af, w])
            wfiles.append(w)
        inputs = ["-i", video]
        fc, labels = [], ["[0:a]"]
        for n, t in enumerate(cut_times):
            inputs += ["-i", wfiles[n % len(wfiles)]]   # striedanie variantov
            ms = int(t * 1000)
            fc.append(f"[{n+1}:a]adelay={ms}|{ms}[s{n}]")
            labels.append(f"[s{n}]")
        fc.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0[a]")
        out = os.path.join(tmp, "with_sfx.mp4")
        run([ff, "-y", *inputs, "-filter_complex", ";".join(fc),
             "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", out])
        return out
    except Exception:
        return video   # ak SFX zlyha, video ostane bez nich (nic sa nerozbije)


def add_music(video, music, cfg, tmp):
    """Podloz hudbu pod hlas: hudba sa UHYBA pod hlasom (sidechain duck) + fade in/out.
    Plne chranene -> pri chybe spadne na jednoduchy mix (povodne spravanie)."""
    ff = cfg["ffmpeg"]
    vol = cfg.get("music_volume", 0.12)
    out = os.path.join(tmp, "with_music.mp4")
    try:
        dur = probe_duration(cfg["ffprobe"], video)
        fade = float(cfg.get("music_fade", 2.0))
        fin = min(fade, 1.2)
        fout = max(0.1, dur - fade)
        fc = (f"[1:a]volume={vol},afade=t=in:st=0:d={fin:.2f},"
              f"afade=t=out:st={fout:.2f}:d={fade:.2f}[m];"
              f"[m][0:a]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=260[mduck];"
              f"[0:a][mduck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
        run([ff, "-y", "-i", video, "-stream_loop", "-1", "-i", music,
             "-filter_complex", fc,
             "-map", "0:v", "-map", "[a]", "-c:v", "copy",
             "-c:a", "aac", "-ar", "44100", "-b:a", "160k", "-shortest", out])
        return out
    except Exception:
        run([ff, "-y", "-i", video, "-stream_loop", "-1", "-i", music,
             "-filter_complex",
             f"[1:a]volume={vol}[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
             "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", out])
        return out


def _ensure_cinematic_music(music_dir, cfg):
    """Stiahne par CINEMATIC/dramatickych trackov (Mixkit, volna licencia) ak chybaju. Chranene."""
    try:
        os.makedirs(music_dir, exist_ok=True)
        if sum(1 for m in os.listdir(music_dir) if m.startswith("cine_")) >= 2:
            return
        import ssl as _ssl, urllib.request as _u
        ctx = _ssl._create_unverified_context()
        ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        for tid in cfg.get("cinematic_music_ids", [720, 892, 871, 616]):
            p = os.path.join(music_dir, f"cine_{tid}.mp3")
            if os.path.exists(p) and os.path.getsize(p) > 100000:
                continue
            try:
                data = _u.urlopen(_u.Request(f"https://assets.mixkit.co/music/{tid}/{tid}.mp3", headers=ua),
                                  context=ctx, timeout=120).read()
                with open(p, "wb") as f:
                    f.write(data)
            except Exception:
                pass
    except Exception:
        pass


def burn_captions(video, ass_path, out_path, cfg, tmp):
    ff = cfg["ffmpeg"]
    # subtitles filter ma problem s ':' vo Windows ceste -> spustime s cwd=tmp a relativnym menom
    ass_rel = os.path.basename(ass_path)
    vid_rel = os.path.relpath(video, tmp).replace(os.sep, "/")
    run_in([ff, "-y", "-i", vid_rel, "-vf", f"subtitles={ass_rel}:fontsdir=../assets/fonts",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-maxrate", "8M", "-bufsize", "16M",
            "-pix_fmt", "yuv420p",
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:a", "aac", "-ar", "44100", "-b:a", "192k", "-movflags", "+faststart", out_path], cwd=tmp)


def run_in(cmd, cwd):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd)
    if p.returncode != 0:
        sys.stderr.write("\n[CHYBA]\n" + (p.stderr or "")[-3000:] + "\n")
        raise RuntimeError("prikaz zlyhal")
    return p


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script", help="cesta k JSON scenaru")
    ap.add_argument("--open", action="store_true", help="otvor hotove video")
    ap.add_argument("--no-captions", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    ff = cfg["ffmpeg"]
    with open(args.script, "r", encoding="utf-8") as f:
        spec = json.load(f)

    voice = spec.get("voice") or cfg["voice"]
    segments = spec["segments"]
    tmp = os.path.join(ROOT, "temp")
    broll_dir = os.path.join(ROOT, "assets", "broll")
    img_dir = os.path.join(ROOT, "assets", "images")
    out_dir = os.path.join(ROOT, "output")
    for d in (tmp, broll_dir, img_dir, out_dir):
        os.makedirs(d, exist_ok=True)
    # vycisti temp
    for fn in os.listdir(tmp):
        try:
            os.remove(os.path.join(tmp, fn))
        except OSError:
            pass

    print(f"== Generujem video: {spec.get('title','(bez nazvu)')} ==")
    print(f"   hlas: {voice} | segmentov: {len(segments)}")

    all_words = []
    seg_files = []
    cursor = 0.0
    cuts = []           # casy strihov -> jemne zvukove efekty
    first_broll = None  # loop-bookend: posledny zaber = prvy -> plynuly loop
    first_is_image = False
    used_ids = set()    # ID klipov uz pouzitych v tomto videu -> ziadne opakovanie
    loop_end = cfg.get("loop_bookend", True)
    last_i = len(segments) - 1
    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        print(f"  [{i+1}/{len(segments)}] TTS: {text[:55]}...")
        raw_audio = os.path.join(tmp, f"seg_{i:03d}_raw.mp3")
        audio = os.path.join(tmp, f"seg_{i:03d}.mp3")
        def _tts_one(t):
            if cfg.get("tts_engine") == "kokoro":
                return kokoro_tts(t, raw_audio, cfg)
            elif cfg.get("openai_api_key"):
                return openai_tts(t, cfg["openai_api_key"],
                                  cfg.get("openai_voice", "onyx"),
                                  cfg.get("openai_tts_model", "gpt-4o-mini-tts"),
                                  cfg.get("openai_instructions",
                                          "Speak in a deep, serious, suspenseful documentary narration style, measured and ominous."),
                                  raw_audio, cfg["ffprobe"])
            else:
                return tts(t, voice, raw_audio,
                           cfg.get("tts_rate", "+0%"), cfg.get("tts_pitch", "+0Hz"))
        try:
            words = _tts_one(text)
        except Exception as e:              # jeden zly segment nesmie zabit cely render
            print(f"       [TTS zlyhalo: {str(e)[:80]}] retry so zbalenym/skratenym textom")
            text = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", text, flags=re.IGNORECASE)[:280].strip() or "..."
            try:
                words = _tts_one(text)
            except Exception as e2:
                print(f"       [segment {i+1} preskoceny: {str(e2)[:80]}]")
                continue
        trim_trailing_silence(cfg["ffmpeg"], raw_audio, audio, cfg.get("segment_gap", 0.12))
        words = align(audio, words)                      # presne casovanie titulkov (sync s hlasom)
        dur = probe_duration(cfg["ffprobe"], audio)
        vid = None
        is_image = False
        img_q = seg.get("image")
        if loop_end and i == last_i and first_broll:
            broll, is_image = first_broll, first_is_image   # bookend: koniec = zaciatok
        elif img_q:                                          # REALNA archivna fotka
            broll = get_image(img_q, img_dir)
            if broll:
                is_image = True
                print(f"       foto: {img_q}")
            else:                                            # fallback na stock video
                broll, vid = get_broll(seg.get("keywords", ""), cfg, broll_dir, used_ids)
        else:
            broll, vid = get_broll(seg.get("keywords", ""), cfg, broll_dir, used_ids)
        # cisty zdroj pre thumbnail: frame z RAW b-rollu (bez napalenych titulkov)
        if (not is_image) and broll and str(broll).lower().endswith(".mp4") and i >= 2:
            _ts = os.path.join(out_dir, "_thumbsrc.jpg")
            if not os.path.exists(_ts):
                try:
                    run([cfg["ffmpeg"], "-y", "-ss", "1", "-i", broll,
                         "-frames:v", "1", "-q:v", "2", _ts])
                except Exception:
                    pass
        if i == 0:
            first_broll, first_is_image = broll, is_image
        if vid is not None:
            used_ids.add(vid)
        if not broll and seg.get("keywords"):
            print(f"       (bez B-roll -> fallback pozadie)")
        render_segment(i, audio, dur, broll, cfg, tmp, is_image=is_image)
        for (o, d, txt) in words:
            all_words.append((cursor + o, d, txt))
        if i > 0:
            cuts.append(cursor)                 # strih na zaciatku tohto segmentu
        cursor += dur
        seg_files.append(os.path.join(tmp, f"seg_{i:03d}.mp4"))

    print(f"  Skladam {len(seg_files)} segmentov (dlzka ~{cursor:.1f}s)...")
    video = concat_segments(seg_files, cfg, tmp)

    # hudba (ak je nejaka v assets/music)
    music_dir = os.path.join(ROOT, "assets", "music")
    musics = [os.path.join(music_dir, m) for m in os.listdir(music_dir)
              if m.lower().endswith((".mp3", ".m4a", ".wav"))] if os.path.isdir(music_dir) else []
    _ensure_cinematic_music(music_dir, cfg)          # cinematic/dramaticke tracky ak chybaju
    musics = [os.path.join(music_dir, m) for m in os.listdir(music_dir)
              if m.lower().endswith((".mp3", ".m4a", ".wav"))] if os.path.isdir(music_dir) else []
    if musics:
        cine = [m for m in musics if os.path.basename(m).startswith("cine_")]
        track = random.choice(cine if cine else musics)   # preferuj cinematic; kazde video ine
        print(f"  Pridavam hudbu: {os.path.basename(track)}")
        video = add_music(video, track, cfg, tmp)

    if cfg.get("sfx", True):
        print("  Pridavam jemne zvukove efekty na strihy...")
        video = add_sfx(cfg["ffmpeg"], video, cuts, tmp)

    slug = slugify(spec.get("title", "video"))
    final = os.path.join(out_dir, slug + ".mp4")

    if args.no_captions:
        run([ff, "-y", "-i", video, "-c", "copy", final])
    else:
        print("  Vypaľujem titulky...")
        ass_path = os.path.join(tmp, "subs.ass")
        build_ass(all_words, cfg, ass_path)
        # presun finalny vstup do tmp aby cwd trik fungoval
        burn_captions(video, ass_path, final, cfg, tmp)

    # metadata subor (popis + hashtagy zladene so znackou MindBlownDaily)
    meta_path = os.path.join(out_dir, slug + ".txt")
    desc = (spec.get("description", "") or "").strip()
    cta = cfg.get("brand_cta", "").strip()
    if cta and cta.lower() not in desc.lower():
        desc = (desc + "\n" + cta).strip()
    # zluc hashtagy z temy + znackove, bez duplicit, max 12
    seen, tags = set(), []
    for t in list(spec.get("hashtags", [])) + cfg.get("brand_hashtags", []):
        t = t.strip()
        t = t if t.startswith("#") else "#" + t
        if len(t) > 1 and t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(spec.get("title", "") + "\n\n")
        f.write(desc + "\n\n")
        f.write(" ".join(tags[:12]) + "\n")
        credit = cfg.get("music_credit", "")
        if musics and credit:
            f.write("\n" + credit + "\n")

    print(f"\nHOTOVO: {final}")
    print(f"Popis/hashtagy: {meta_path}")

    if args.open:
        os.startfile(final)


if __name__ == "__main__":
    main()
