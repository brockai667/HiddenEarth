#!/usr/bin/env python3
"""Auto YouTube thumbnail v profi clickbait style:
- vyberie NAJDRAMATICKEJSI frame z videa (najvyssi kontrast/detail = casto realna fotka)
- zvysi jas/kontrast/sytost + vinetacia (subjekt vyskoci z pozadia)
- VELKY kratky 'information gap' hook (3-5 slov) co nuti kliknut
- mensi nazov pripadu pre kontext (max 3 vizualne prvky: foto + hook + nazov)
- silny ciapky/outline + drop shadow, 1280x720, <2MB
"""
import os, subprocess

# kratke 'curiosity gap' hooky - vsetky PRAVDIVE pre historiu miest (neklamu)
HOOKS = [
    "WHAT HAPPENED HERE?",
    "LOST IN TIME",
    "HISTORY FORGOT IT",
    "FROZEN IN TIME",
    "THE FULL STORY",
    "BEFORE IT VANISHED",
    "A WORLD DISAPPEARED",
    "WHAT REMAINS TODAY?",
]


def _font(size):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariblk.ttf",
              "C:/Windows/Fonts/arial.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w, max_lines):
    lines, cur = [], ""
    for w in text.split():
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) > max_w and cur:
            lines.append(cur); cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def _draw_block(draw, lines, font, cx, y, lh, fill, outline=(0, 0, 0),
                ow=5, shadow=(0, 0, 0), sh=7):
    """Centrovany blok: drop shadow + hruby outline + farba."""
    for ln in lines:
        x = cx - draw.textlength(ln, font=font) / 2
        # drop shadow (mäkký posun dole)
        draw.text((x + 3, y + sh), ln, font=font, fill=shadow)
        # outline (ring)
        for dx in range(-ow, ow + 1, 2):
            for dy in range(-ow, ow + 1, 2):
                if dx or dy:
                    draw.text((x + dx, y + dy), ln, font=font, fill=outline)
        draw.text((x, y), ln, font=font, fill=fill)
        y += lh
    return y


def _pick_frame(video, ffmpeg, ffprobe, out_jpg):
    """Naskenuje viacero frameov a vyberie ten s najvyssim kontrastom/detailom."""
    from PIL import Image, ImageStat
    try:
        d = float(subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                                  "-of", "default=nw=1:nk=1", video],
                                 capture_output=True, text=True).stdout.strip())
    except Exception:
        d = 60.0
    best, best_score = None, -1.0
    for frac in (0.12, 0.25, 0.38, 0.5, 0.62, 0.75, 0.88):
        t = max(1.0, d * frac)
        f = f"{out_jpg}.{int(frac * 100)}.jpg"
        subprocess.run([ffmpeg, "-y", "-ss", str(t), "-i", video, "-frames:v", "1",
                        "-q:v", "2", f], capture_output=True)
        if not os.path.exists(f):
            continue
        try:
            im = Image.open(f).convert("RGB")
            score = ImageStat.Stat(im.convert("L")).stddev[0]   # kontrast/detail
            if score > best_score:
                best_score, best = score, im.copy()
        except Exception:
            pass
        try:
            os.remove(f)
        except OSError:
            pass
    return best


def _pick_face(images_dir, H):
    """Najde realnu PORTRAIT fotku (tvar/skica/poster) na bocny panel = silny clickbait.
    Vrati len ak je dost ostra (netreba ju moc zvacsovat)."""
    from PIL import Image
    if not images_dir or not os.path.isdir(images_dir):
        return None
    best, best_score = None, -1e9
    for fn in os.listdir(images_dir):
        if not fn.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        try:
            im = Image.open(os.path.join(images_dir, fn)).convert("RGB")
        except Exception:
            continue
        w, h = im.size
        ar = w / h
        if ar > 1.15 or h < 360:        # chceme portrait/stvorec a slusnu vysku
            continue
        upscale = (H * 0.9) / h          # o kolko ju musime zvacsit na panel
        score = -abs(ar - 0.72) * 2 - max(0, upscale - 1.4) * 3 + min(h, 1200) / 1000.0
        if score > best_score:
            best_score, best = score, im.copy()
    return best


def make_thumbnail(video, title, ffmpeg, ffprobe, out_jpg, hook=None, images_dir=None):
    from PIL import Image, ImageDraw, ImageOps, ImageEnhance, ImageFilter
    W, H = 1280, 720
    face = _pick_face(images_dir, H)

    # pozadie: PREFERUJ cisty frame z raw b-rollu (_thumbsrc.jpg, BEZ napalenych titulkov);
    # fallback = dramaticky frame z videa (ten moze obsahovat caption -> kolizia textov)
    bg = None
    _src = os.path.join(os.path.dirname(os.path.abspath(video)), "_thumbsrc.jpg")
    if os.path.exists(_src):
        try:
            from PIL import Image as _I
            bg = _I.open(_src).convert("RGB")
        except Exception:
            bg = None
    if bg is None:
        bg = _pick_frame(video, ffmpeg, ffprobe, out_jpg)
        if bg is not None:
            w0, h0 = bg.size
            bg = bg.crop((0, 0, w0, int(h0 * 0.86)))
    if bg is not None:
        img = ImageOps.fit(bg, (W, H), method=Image.LANCZOS)
    else:
        img = Image.new("RGB", (W, H), (16, 16, 22))

    # subjekt nech vyskoci: jas + kontrast + sytost + ostrost
    img = ImageEnhance.Brightness(img).enhance(1.06)
    img = ImageEnhance.Contrast(img).enhance(1.28)
    img = ImageEnhance.Color(img).enhance(1.35)
    img = ImageEnhance.Sharpness(img).enhance(1.6)

    # cinematic vinetacia (stred svetly, kraje tmave)
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse([-W * 0.22, -H * 0.28, W * 1.22, H * 1.28], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(150))
    img = Image.composite(img, ImageEnhance.Brightness(img).enhance(0.4), vig)

    # ak mame realnu tvar/skicu -> bocny panel vpravo (rule of thirds), text dostane lavu cast
    text_w = W                       # sirka dostupna pre text (default cely)
    if face is not None:
        fh = int(H * 0.92)
        fw = int(face.width * fh / face.height)
        fw = min(fw, int(W * 0.46))
        fim = ImageOps.fit(face, (fw, fh), method=Image.LANCZOS)
        fim = ImageEnhance.Contrast(fim).enhance(1.12)
        fim = ImageEnhance.Color(fim).enhance(1.15)
        fim = ImageEnhance.Sharpness(fim).enhance(1.4)
        px = W - fw - 26
        py = (H - fh) // 2
        # tmavsie pozadie pod panelom + biely ramcek + tien
        draw0 = ImageDraw.Draw(img)
        draw0.rectangle([px - 12, py - 12, px + fw + 14, py + fh + 14], fill=(0, 0, 0))
        img.paste(fim, (px, py))
        ImageDraw.Draw(img).rectangle([px - 6, py - 6, px + fw + 6, py + fh + 6],
                                      outline=(255, 255, 255), width=6)
        text_w = px - 20

    # tmavy gradient zdola pre citatelnost nazvu
    grad = Image.new("L", (1, H), 0)
    for y in range(H):
        grad.putpixel((0, y), int(min(235, max(0, (y - H * 0.5) / (H * 0.5) * 235))))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, grad.resize((W, H)))

    draw = ImageDraw.Draw(img)
    cx = text_w // 2

    # --- HOOK: velky, max 5 slov, zlty (information gap) ---
    if not hook:
        hook = HOOKS[sum(map(ord, title or "x")) % len(HOOKS)]
    hf = _font(124 if face is None else 104)
    hlines = _wrap(draw, hook.upper(), hf, text_w - 80, 3)
    longest = lambda ls, f: max((draw.textlength(s, font=f) for s in ls), default=0)
    while hlines and longest(hlines, hf) > text_w - 60 and hf.size > 56:
        hf = _font(hf.size - 8)
        hlines = _wrap(draw, hook.upper(), hf, text_w - 80, 3)
    hlh = int(hf.size * 1.04)
    top = (H - hlh * len(hlines)) // 2 - 30 if face is not None else 70
    # cervena akcentna linka nad hookom
    draw.rectangle([cx - 130, top - 26, cx + 130, top - 16], fill=(214, 40, 40))
    _draw_block(draw, hlines, hf, cx, top, hlh, fill=(255, 221, 0),
                outline=(0, 0, 0), ow=6, shadow=(120, 0, 0), sh=8)

    # --- NAZOV PRIPADU: mensi, dole, pre kontext (biely) ---
    tf = _font(58)
    tlines = _wrap(draw, (title or "").upper(), tf, text_w - 80, 2)
    while tlines and longest(tlines, tf) > text_w - 60 and tf.size > 34:
        tf = _font(tf.size - 6)
        tlines = _wrap(draw, (title or "").upper(), tf, text_w - 80, 2)
    tlh = int(tf.size * 1.12)
    ty = H - 34 - tlh * len(tlines)
    _draw_block(draw, tlines, tf, cx, ty, tlh, fill=(255, 255, 255),
                outline=(0, 0, 0), ow=4, shadow=(0, 0, 0), sh=5)

    img.save(out_jpg, "JPEG", quality=90)
    return out_jpg


if __name__ == "__main__":
    import sys
    v, t = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "COLD CASE"
    out = sys.argv[3] if len(sys.argv) > 3 else "thumb.jpg"
    ff = os.environ.get("FFMPEG", "ffmpeg")
    fp = os.environ.get("FFPROBE", "ffprobe")
    imd = os.environ.get("IMAGES_DIR")
    print(make_thumbnail(v, t, ff, fp, out, images_dir=imd))
