#!/usr/bin/env python3
"""
Nahra hotove dlhe video na YouTube cez Data API (nazov/popis/tagy zo scenara).
Pouzitie: python youtube_upload.py output/video.mp4 scripts/script.json
Potrebuje (env alebo settings.json): YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN
"""
import json, os, sys, requests

ROOT = os.path.dirname(os.path.abspath(__file__))


def _cfg(key):
    v = os.environ.get(key)
    if v:
        return v
    p = os.path.join(ROOT, "settings.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8")).get(key.lower())
    return None


def access_token(cid, csec, rtok):
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": cid, "client_secret": csec, "refresh_token": rtok,
        "grant_type": "refresh_token"})
    r.raise_for_status()
    return r.json()["access_token"]


def set_thumbnail(tok, video_id, jpg):
    with open(jpg, "rb") as f:
        data = f.read()
    r = requests.post(f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={video_id}",
                      headers={"Authorization": f"Bearer {tok}", "Content-Type": "image/jpeg"},
                      data=data, timeout=120)
    r.raise_for_status()


def upload(tok, mp4, title, description, tags, category="27", privacy="public"):
    meta = {"snippet": {"title": title[:100], "description": description[:4900],
                        "tags": tags[:15], "categoryId": category},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/*"},
        data=json.dumps(meta).encode("utf-8"), timeout=60)
    init.raise_for_status()
    up_url = init.headers["Location"]
    with open(mp4, "rb") as f:
        body = f.read()
    put = requests.put(up_url, headers={"Content-Type": "video/*",
                                        "Content-Length": str(len(body))}, data=body, timeout=900)
    put.raise_for_status()
    return put.json()


def main():
    if len(sys.argv) < 3:
        print("Pouzitie: python youtube_upload.py <video.mp4> <script.json>"); sys.exit(1)
    mp4, script = sys.argv[1], sys.argv[2]
    spec = json.load(open(script, encoding="utf-8"))
    title = spec.get("title", "Untitled")
    tags = [h.lstrip("#") for h in spec.get("hashtags", [])]
    hashline = " ".join(spec.get("hashtags", []))
    desc = (spec.get("description", "") + "\n\n" + hashline +
            "\n\nMusic: CO.AG Music - https://www.youtube.com/c/COAGmusic").strip()
    cid, csec, rtok = _cfg("YOUTUBE_CLIENT_ID"), _cfg("YOUTUBE_CLIENT_SECRET"), _cfg("YOUTUBE_REFRESH_TOKEN")
    if not (cid and csec and rtok):
        raise RuntimeError("Chybaju YouTube OAuth udaje (CLIENT_ID/SECRET/REFRESH_TOKEN).")
    tok = access_token(cid, csec, rtok)
    print(f"Nahravam na YouTube: {title}")
    res = upload(tok, mp4, title, desc, tags)
    vid = res.get("id")
    print(f"OK: https://www.youtube.com/watch?v={vid}")
    # auto-thumbnail (nech nezablokuje upload ak zlyha)
    try:
        import appconfig, thumbnail
        cfg = appconfig.load()
        jpg = os.path.join(os.path.dirname(mp4), "_thumb.jpg")
        imgs = os.path.join(ROOT, "assets", "images")
        thumbnail.make_thumbnail(mp4, title, cfg["ffmpeg"], cfg["ffprobe"], jpg, images_dir=imgs)
        set_thumbnail(tok, vid, jpg)
        print("Thumbnail nastaveny.")
    except Exception as e:
        print("Thumbnail preskoceny:", e)


if __name__ == "__main__":
    main()
