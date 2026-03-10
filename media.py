import hashlib
import html
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse, urlencode

import requests

from config import LINK_KEYS, VIDEO_URL_KEYS, PROGRESSIVE_URL_KEYS, BAD_URL_SUBSTR

SIZE_RE = re.compile(r"(?:^|[\\/_\\-_=])(\d{2,4})x(\d{2,4})(?:$|[\\/_\\-_=])")
THUMB_SIZE_RE = re.compile(r"(?:^|[\\/_\\-_=])(?:s|p)(\d{2,4})x(\d{2,4})(?:$|[\\/_\\-_=])")
RESIZE_Q_KEYS = {
    "w", "h", "width", "height", "resize", "crop", "scale", "quality", "q",
    "ow", "oh", "tw", "th", "sx", "sy", "sw", "sh",
}


def normalize_url(u: str) -> str:
    u = html.unescape((u or "").strip())
    if "<" in u:
        u = u.split("<", 1)[0]
    return u.strip()


def is_bad_asset_url(u: str) -> bool:
    ul = (u or "").lower()
    return any(x in ul for x in BAD_URL_SUBSTR)


def classify_media_url(u: str) -> str:
    u = normalize_url(u)
    ul = (u or "").lower()

    if re.search(r"\.(mp4|m4v)(\?|$)", ul):
        return "video"
    if re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", ul):
        return "image"

    if "fbcdn.net" in ul or "scontent-" in ul:
        if any(
            x in ul
            for x in (
                "stp=dst-jpg",
                "stp=cp0_dst-jpg",
                "stp=dst-png",
                "stp=dst-webp",
                "format=jpg",
                "format=jpeg",
                "format=png",
                "format=webp",
                "mime_type=image",
                "mime=image",
            )
        ):
            return "image"

        if any(x in ul for x in ("mime_type=video", "mime=video", "bytestream=true")) and any(
            x in ul for x in ("video", "mp4", "m4v")
        ):
            return "video"

    return ""


def dedupe_keep_order(items):
    out = []
    seen = set()
    for u in items:
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def image_dims_from_url(u: str):
    if not isinstance(u, str):
        return 0, 0
    best = (0, 0)
    best_thumb = (0, 0)

    for w, h in SIZE_RE.findall(u):
        try:
            ww = int(w)
            hh = int(h)
        except Exception:
            continue
        if ww > 20 and hh > 20 and ww * hh > best[0] * best[1]:
            best = (ww, hh)

    for w, h in THUMB_SIZE_RE.findall(u):
        try:
            ww = int(w)
            hh = int(h)
        except Exception:
            continue
        if ww > 20 and hh > 20 and ww * hh > best_thumb[0] * best_thumb[1]:
            best_thumb = (ww, hh)

    try:
        q = parse_qs(urlparse(u).query)
        if "w" in q and "h" in q:
            ww = int(q.get("w", [0])[0] or 0)
            hh = int(q.get("h", [0])[0] or 0)
            if ww > 20 and hh > 20 and ww * hh > best_thumb[0] * best_thumb[1]:
                best_thumb = (ww, hh)
    except Exception:
        pass

    return best if best != (0, 0) else best_thumb


def image_score_from_url(u: str) -> int:
    w, h = image_dims_from_url(u)
    return w * h if w and h else 0


def image_canonical_key(u: str) -> str:
    u = normalize_url(u)
    try:
        p = urlparse(u)
        base = f"{p.netloc}{p.path}"
        base = re.sub(r"/p\\d+x\\d+/", "/", base)
        base = re.sub(r"/[sp]\\d+x\\d+/", "/", base)
        base = re.sub(r"[_-](?:s|p)\\d+x\\d+", "", base)
        q = parse_qs(p.query)
        q = {k: v for k, v in q.items() if k.lower() not in RESIZE_Q_KEYS}
        fmt = (q.get("format") or [""])[0]
        if fmt:
            base += f"|format={fmt}"
        return base
    except Exception:
        return u


def is_avatar_or_icon_url(u: str) -> bool:
    ul = (u or "").lower()
    bad = (
        "/profilepic/",
        "profile_pic",
        "profilepic",
        "p64x64",
        "p50x50",
        "p32x32",
        "p24x24",
        "p16x16",
        "sticker",
        "emoji",
        "sprite",
        "safe_image.php",
        "rsrc.php",
        "/t39.30808-1/",
    )
    return any(b in ul for b in bad)


def is_cropped_fb_image_url(u: str) -> bool:
    ul = (u or "").lower()

    if "crop=" in ul:
        return True
    if "stp=cp" in ul or "stp=cp0" in ul:
        return True
    if "/t39.30808-1/" in ul:
        return True
    if re.search(r"/[sp]\d+x\d+/", ul):
        return True

    try:
        q = parse_qs(urlparse(ul).query)
        stp = (q.get("stp") or [""])[0].lower()
        if stp:
            if re.search(r"(?:^|[_-])(?:p|s)\d+x\d+", stp):
                return True
            if "cp" in stp:
                return True
            if stp.startswith("c") and "dst-" in stp:
                return True
        if any(k.lower() in RESIZE_Q_KEYS for k in q.keys()):
            return True
    except Exception:
        pass

    if "cropped" in ul:
        return True

    return False


def sanitize_fb_stp_param(stp: str) -> str:
    if not isinstance(stp, str) or not stp:
        return ""
    parts = [p for p in stp.split("_") if p]
    cleaned = []
    for part in parts:
        pl = part.lower()
        if pl.startswith("cp"):
            continue
        if re.search(r"\d+x\d+", pl) and "dst-" not in pl:
            continue
        if pl.startswith("c") and "dst-" not in pl and (re.search(r"\d", pl) or "x" in pl):
            continue
        cleaned.append(part)
    return "_".join(cleaned)


def dedupe_and_sort_image_urls(urls, max_items=None):
    best_by_key = {}
    for u in urls or []:
        u = normalize_url(u)
        if not u or not isinstance(u, str) or not u.startswith("http"):
            continue
        if is_avatar_or_icon_url(u):
            continue
        if is_cropped_fb_image_url(u):
            continue
        key = image_canonical_key(u)
        score = image_score_from_url(u)
        cand = (-score, -len(u), u)
        prev = best_by_key.get(key)
        if prev is None or cand < prev:
            best_by_key[key] = cand
    ordered = sorted(best_by_key.values())
    out = [c[2] for c in ordered]
    if max_items:
        out = out[:max_items]
    return out


def safe_filename_from_url(url: str, fallback_ext: str):
    url = normalize_url(url)
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

    path = urlparse(url).path.lower()
    m = re.search(r"\.(jpg|jpeg|png|webp|gif|mp4|m4v)(?:$)", path, re.I)
    if m:
        ext = "." + m.group(1).lower()
        return f"{h}{ext}"

    q = urlparse(url).query.lower()
    if "format=png" in q or "dst-png" in q:
        return f"{h}.png"
    if "format=webp" in q or "dst-webp" in q:
        return f"{h}.webp"
    if "format=jpeg" in q:
        return f"{h}.jpeg"
    if "format=jpg" in q or "dst-jpg" in q:
        return f"{h}.jpg"
    if "mime_type=video" in q or "mime=video" in q:
        return f"{h}.mp4"

    return f"{h}{fallback_ext}"


def download_file(url: str, out_path: Path, cfg: dict, request_ctx=None) -> bool:
    url = normalize_url(url)
    if not url or not url.startswith("http"):
        return False

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.facebook.com/",
    }

    try:
        with requests.get(url, headers=headers, stream=True, timeout=cfg["download_timeout"]) as r:
            if r.status_code != 200:
                raise RuntimeError("non-200")

            total = 0
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 64):
                    if not chunk:
                        continue
                    f.write(chunk)
                    total += len(chunk)
                    if total > cfg["max_bytes_per_file"]:
                        return False
        return True
    except Exception:
        if not request_ctx:
            return False
        try:
            resp = request_ctx.get(
                url,
                timeout=cfg["download_timeout"] * 1000,
                headers=headers,
            )
            if not resp or not resp.ok:
                return False
            body = resp.body()
            if not body:
                return False
            if len(body) > cfg["max_bytes_per_file"]:
                return False
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(body)
            return True
        except Exception:
            return False


def mux_av(video_path: Path, audio_path: Path, out_path: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(video_path),
                "-i",
                str(audio_path),
                "-c",
                "copy",
                str(out_path),
            ],
            check=False,
        )
        return res.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
    except Exception:
        return False


def parse_dash_manifest(manifest_xml: str):
    video_reps = []
    audio_reps = []
    if not manifest_xml:
        return video_reps, audio_reps

    xml = html.unescape(manifest_xml)
    try:
        root = ET.fromstring(xml)
    except Exception:
        return video_reps, audio_reps

    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]

    for adap in root.iter("AdaptationSet"):
        ctype = (adap.attrib.get("contentType") or "").lower()
        for rep in adap.findall("Representation"):
            base = rep.findtext("BaseURL") or ""
            base = normalize_url(base)
            if not base:
                continue
            bw = 0
            w = 0
            h = 0
            try:
                bw = int(rep.attrib.get("bandwidth") or 0)
            except Exception:
                bw = 0
            try:
                w = int(rep.attrib.get("width") or 0)
                h = int(rep.attrib.get("height") or 0)
            except Exception:
                w = 0
                h = 0
            mime = (rep.attrib.get("mimeType") or "").lower()
            item = {"url": base, "width": w, "height": h, "bandwidth": bw}
            if ctype == "audio" or "audio" in mime:
                audio_reps.append(item)
            else:
                video_reps.append(item)

    return video_reps, audio_reps


def pick_best_dash_av(manifest_xmls):
    best = None
    for xml in manifest_xmls:
        videos, audios = parse_dash_manifest(xml)
        if not videos:
            continue
        videos.sort(key=lambda x: (x["width"] * x["height"], x["bandwidth"]), reverse=True)
        audios.sort(key=lambda x: x["bandwidth"], reverse=True)
        v = videos[0]
        a = audios[0] if audios else None
        score = v["width"] * v["height"]
        cand = {"video": v, "audio": a, "score": score}
        if not best or cand["score"] > best["score"]:
            best = cand
    return best


def extract_dash_manifest_xmls(story: dict, limit=4):
    xmls = []
    seen = set()

    def add(x):
        if not isinstance(x, str):
            return
        if "<MPD" not in x:
            return
        if x in seen:
            return
        seen.add(x)
        xmls.append(x)

    def walk(n):
        if len(xmls) >= limit:
            return
        if isinstance(n, dict):
            if "manifest_xml" in n:
                add(n.get("manifest_xml"))
            for v in n.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(story)
    return xmls


def extract_direct_video_urls(story: dict, limit=6):
    items = []
    seen = set()
    pri_map = {
        "playable_url_quality_hd": 5,
        "playable_url": 4,
        "browser_native_hd_url": 3,
        "browser_native_sd_url": 2,
        "browser_native_dash_url": 1,
        "base_url": 0,
    }

    def add(u, pri):
        u = normalize_url(u)
        if not isinstance(u, str) or not u.startswith("http"):
            return
        if classify_media_url(u) != "video":
            return
        if u in seen:
            return
        seen.add(u)
        items.append((pri, u))

    def walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                kl = str(k).lower()
                if kl in VIDEO_URL_KEYS and isinstance(v, str) and v.startswith("http"):
                    add(v, pri_map.get(kl, 0))
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(story)
    items.sort(key=lambda x: x[0], reverse=True)
    return [u for _, u in items[:limit]]


def extract_progressive_video_urls(story: dict, limit=6):
    candidates = []
    seen = set()

    def add(u, quality="", bitrate=0):
        u = normalize_url(u)
        if not u or not u.startswith("http"):
            return
        if classify_media_url(u) != "video":
            return
        if u in seen:
            return
        seen.add(u)
        q = str(quality or "").upper()
        q_rank = 2 if q == "HD" else 1 if q == "SD" else 0
        br = 0
        try:
            br = int(bitrate or 0)
        except Exception:
            br = 0
        candidates.append((q_rank, br, u))

    def walk(n):
        if isinstance(n, dict):
            if "progressive_url" in n and isinstance(n.get("progressive_url"), str):
                url = n.get("progressive_url")
                md = n.get("metadata") or {}
                quality = ""
                bitrate = 0
                if isinstance(md, dict):
                    quality = md.get("quality") or ""
                bitrate = n.get("bitrate") or 0
                add(url, quality=quality, bitrate=bitrate)
            for v in n.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(story)
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [u for _, _, u in candidates[:limit]]


def gather_media_urls(node, max_urls=20):
    found = []
    seen = set()

    def add(u):
        u = normalize_url(u)
        if not u or not u.startswith("http"):
            return
        if u in seen:
            return
        seen.add(u)
        found.append(u)

    def walk(n):
        if len(found) >= max_urls:
            return
        if isinstance(n, dict):
            for k, v in n.items():
                kl = str(k).lower()
                if kl in VIDEO_URL_KEYS and isinstance(v, str) and v.startswith("http"):
                    add(v)
                if kl in PROGRESSIVE_URL_KEYS and isinstance(v, str) and v.startswith("http"):
                    add(v)
                if isinstance(v, (dict, list)):
                    walk(v)
                elif isinstance(v, str) and "http" in v:
                    if "<MPD" in v or "<BaseURL" in v:
                        continue
                    for m in re.findall(r"https?://[^\s\"']+", v):
                        add(m)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)

    media = []
    for u in found:
        if classify_media_url(u):
            media.append(u)

    return media[:max_urls]


def best_image_from_media(media: dict):
    if not isinstance(media, dict):
        return "", 0

    candidates = []

    def add(uri, w=None, h=None, base_priority=0):
        uri = normalize_url(uri)
        if not isinstance(uri, str) or not uri.startswith("http"):
            return
        if is_avatar_or_icon_url(uri):
            return

        ww = int(w or 0) if isinstance(w, (int, float)) else 0
        hh = int(h or 0) if isinstance(h, (int, float)) else 0

        score = ww * hh if (ww and hh) else image_score_from_url(uri)
        cropped = 1 if is_cropped_fb_image_url(uri) else 0

        candidates.append(
            {"url": uri, "score": score, "cropped": cropped, "priority": base_priority}
        )

    for key in ("original_image", "full_image"):
        val = media.get(key)
        if isinstance(val, dict):
            add(val.get("uri"), val.get("width"), val.get("height"), base_priority=100)

    for key in ("image_versions2", "all_images"):
        container = media.get(key)
        items = []
        if isinstance(container, dict):
            items = container.get("candidates") or []
        elif isinstance(container, list):
            items = container

        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    add(it.get("uri"), it.get("width"), it.get("height"), base_priority=90)

    val = media.get("photo_image")
    if isinstance(val, dict):
        add(val.get("uri"), val.get("width"), val.get("height"), base_priority=60)

    val = media.get("image")
    if isinstance(val, dict):
        add(val.get("uri"), val.get("width"), val.get("height"), base_priority=50)
    elif isinstance(val, str):
        add(val, base_priority=50)

    val = media.get("large_image")
    if isinstance(val, dict):
        add(val.get("uri"), val.get("width"), val.get("height"), base_priority=50)

    if not candidates:
        val = media.get("preferred_thumbnail")
        if isinstance(val, dict):
            add(val.get("uri"), val.get("width"), val.get("height"), base_priority=10)

    if not candidates:
        return "", 0

    # Prioritize:
    # 1. High Priority (original/full image)
    # 2. Score (Resolution)
    # 3. Non-Cropped (only if priority/score are similar)
    candidates.sort(key=lambda x: (x["cropped"], -x["priority"], -x["score"]))
    candidate = candidates[0]
    return candidate["url"], candidate["score"]


def extract_images_from_story(story: dict, cfg: dict, max_items=8):
    best_by_key = {}

    min_area = int(cfg.get("min_image_area") or 0)
    min_w = int(cfg.get("min_image_width") or 0)
    min_h = int(cfg.get("min_image_height") or 0)

    if not isinstance(story, dict):
        return []

    attachments = story.get("attachments")
    if not isinstance(attachments, list):
        if isinstance(story.get("attached_story"), dict):
            attachments = story["attached_story"].get("attachments")

    if not isinstance(attachments, list):
        return []

    def process_media_entry(url, score):
        if not url:
            return
        key = image_canonical_key(url)
        is_cropped = is_cropped_fb_image_url(url)

        existing = best_by_key.get(key)
        cand = {"url": url, "score": score, "cropped": is_cropped, "len": len(url)}

        if existing is None:
            best_by_key[key] = cand
        else:
            if not cand["cropped"] and existing["cropped"]:
                best_by_key[key] = cand
            elif cand["cropped"] == existing["cropped"] and cand["score"] > existing["score"]:
                best_by_key[key] = cand

    def collect_media_from_node(node):
        m = node.get("media")
        if isinstance(m, dict):
            url, score = best_image_from_media(m)
            process_media_entry(url, score)

        sub = node.get("subattachments")
        if isinstance(sub, dict):
            nodes = sub.get("nodes")
            if isinstance(nodes, list):
                for n in nodes:
                    collect_media_from_node(n)
        elif isinstance(sub, list):
            for n in sub:
                collect_media_from_node(n)

    for att in attachments:
        if isinstance(att, dict):
            collect_media_from_node(att)

    final_candidates = list(best_by_key.values())
    if not final_candidates:
        return []

    final_candidates.sort(key=lambda x: (x["cropped"], -x["score"]))

    filtered = []
    for item in final_candidates:
        url = item["url"]
        score = item["score"]

        # Only filter by size if we actually have a score/dim
        if score > 0 and score < min_area:
            continue

        w, h = image_dims_from_url(url)
        if (w and w < min_w) or (h and h < min_h):
            continue

        filtered.append(url)

    if not filtered and final_candidates:
        filtered = [final_candidates[0]["url"]]

    return filtered[:max_items]


def extract_media_from_story(story: dict, cfg: dict):
    image_urls = extract_images_from_story(story, cfg, max_items=cfg.get("max_images_per_ad", 12))
    progressive = extract_progressive_video_urls(story, limit=6)
    direct = extract_direct_video_urls(story, limit=6)
    video_urls = progressive if progressive else direct
    image_urls = dedupe_keep_order([normalize_url(u) for u in image_urls if u])
    video_urls = dedupe_keep_order([normalize_url(u) for u in video_urls if u])

    extra_imgs = []
    extra = gather_media_urls(story, max_urls=60)
    if extra:
        min_area = int(cfg.get("min_image_area") or 0)
        min_w = int(cfg.get("min_image_width") or 0)
        min_h = int(cfg.get("min_image_height") or 0)
        for u in extra:
            if classify_media_url(u) != "image":
                continue
            if is_avatar_or_icon_url(u):
                continue
            score = image_score_from_url(u)
            w, h = image_dims_from_url(u)
            # Allow images with no score (might be clean URLs) if not explicitly cropped
            if score == 0:
                extra_imgs.append(u)
            elif (score and score >= min_area) or (w >= min_w and h >= min_h):
                extra_imgs.append(u)

    if extra_imgs:
        max_images = cfg.get("max_images_per_ad") or 12
        image_urls = dedupe_and_sort_image_urls(image_urls + extra_imgs, max_items=max_images)

    if not image_urls:
        fallback = gather_media_urls(story, max_urls=60)
        min_area = int(cfg.get("min_image_area") or 0)
        min_w = int(cfg.get("min_image_width") or 0)
        min_h = int(cfg.get("min_image_height") or 0)
        soft_area = int(min_area * 0.5)
        soft_w = int(min_w * 0.5)
        soft_h = int(min_h * 0.5)
        imgs = []
        for u in fallback:
            if classify_media_url(u) != "image":
                continue
            if is_avatar_or_icon_url(u):
                continue
            w, h = image_dims_from_url(u)
            score = w * h if w and h else image_score_from_url(u)
            cropped = 1 if is_cropped_fb_image_url(u) else 0
            tier = 2 if ((score and score >= min_area) or (w >= min_w and h >= min_h)) else 1 if (
                (score and score >= soft_area) or (w >= soft_w and h >= soft_h)
            ) else 0
            imgs.append((tier, cropped, score, len(u), u))
        imgs.sort(key=lambda x: (-x[0], x[1], -x[2], -x[3]))
        image_urls = [u for _, _, _, _, u in imgs[: (cfg.get("max_images_per_ad") or 12)]]

    dash_av = pick_best_dash_av(extract_dash_manifest_xmls(story))
    return image_urls, video_urls, dash_av


def resolve_saved_image_path(image_urls, cfg: dict, ad_id: str):
    if not image_urls:
        return None
    candidates = []
    for u in image_urls or []:
        u = normalize_url(u)
        if not u or not isinstance(u, str) or not u.startswith("http"):
            continue
        if is_avatar_or_icon_url(u):
            continue
        # Allow cropped images if they are in the 'image_urls' list (which is already sorted/filtered)
        score = image_score_from_url(u)
        candidates.append((-score, -len(u), u))

    if not candidates:
        return None

    candidates.sort()
    prefix = (ad_id or "").strip()
    for _score, _ln, u in candidates:
        fname = safe_filename_from_url(u, fallback_ext=".jpg")
        if prefix:
            fname = f"{prefix}_{fname}"
        path = Path(cfg["media_dir"]) / "images" / fname
        if path.exists():
            return path
    return None


def resolve_saved_video_path(video_urls, cfg: dict, dash_video_url, ad_id: str):
    cand_urls = []
    if video_urls:
        cand_urls.extend(video_urls)
    if dash_video_url:
        cand_urls.append(dash_video_url)
    for u in cand_urls:
        fname = safe_filename_from_url(u, fallback_ext=".mp4")
        prefix = (ad_id or "").strip()
        if prefix:
            fname = f"{prefix}_{fname}"
        path = Path(cfg["media_dir"]) / "videos" / fname
        if path.exists():
            return path
    return None


def save_media_for_ad(image_urls, video_urls, dash_av, cfg: dict, ad_id: str, request_ctx=None):
    saved = []

    base = Path(cfg["media_dir"])
    img_dir = base / "images"
    vid_dir = base / "videos"
    tmp_dir = vid_dir / "_tmp"

    max_images = cfg.get("max_images_per_ad") or cfg["max_media_per_ad"]
    max_videos = cfg.get("max_videos_per_ad") or 1

    image_urls = (image_urls or [])[:max_images]
    video_urls = (video_urls or [])[:max_videos]

    ffmpeg = shutil.which("ffmpeg")

    sources = []
    if dash_av and ffmpeg and cfg.get("prefer_dash_full_hd"):
        sources.append(("dash", dash_av))
    for u in video_urls:
        sources.append(("url", u))
    if not sources and dash_av:
        sources.append(("dash", dash_av))

    sources = sources[:max_videos]

    for kind, src in sources:
        if kind == "url":
            u = src
            if classify_media_url(u) != "video":
                continue
            fname = safe_filename_from_url(u, fallback_ext=".mp4")
            prefix = (ad_id or "").strip()
            if prefix:
                fname = f"{prefix}_{fname}"
            out_path = vid_dir / fname
            if out_path.exists() and out_path.stat().st_size > 0:
                saved.append(str(out_path))
                continue
            if download_file(u, out_path, cfg, request_ctx=request_ctx):
                saved.append(str(out_path))
        else:
            vurl = (src.get("video") or {}).get("url") if isinstance(src, dict) else ""
            aurl = (src.get("audio") or {}).get("url") if isinstance(src, dict) else ""
            if not vurl:
                continue
            vname = safe_filename_from_url(vurl, fallback_ext=".mp4")
            prefix = (ad_id or "").strip()
            if prefix:
                vname = f"{prefix}_{vname}"
            out_path = vid_dir / vname
            if out_path.exists() and out_path.stat().st_size > 0:
                saved.append(str(out_path))
                continue

            if aurl and ffmpeg:
                tmp_dir.mkdir(parents=True, exist_ok=True)
                vtmp = tmp_dir / f"v_{vname}"
                atmp = tmp_dir / f"a_{vname}"
                ok_v = download_file(vurl, vtmp, cfg, request_ctx=request_ctx)
                ok_a = download_file(aurl, atmp, cfg, request_ctx=request_ctx)
                if ok_v and ok_a and mux_av(vtmp, atmp, out_path):
                    saved.append(str(out_path))
                if vtmp.exists():
                    vtmp.unlink(missing_ok=True)
                if atmp.exists():
                    atmp.unlink(missing_ok=True)
            else:
                if download_file(vurl, out_path, cfg, request_ctx=request_ctx):
                    saved.append(str(out_path))
                if aurl and cfg.get("keep_audio_separate_if_no_ffmpeg"):
                    aname = safe_filename_from_url(aurl, fallback_ext=".m4a")
                    if prefix:
                        aname = f"{prefix}_{aname}"
                    audio_path = vid_dir / aname
                    if not audio_path.exists():
                        if download_file(aurl, audio_path, cfg, request_ctx=request_ctx):
                            saved.append(str(audio_path))

    for u in image_urls:
        if classify_media_url(u) != "image":
            continue
        fname = safe_filename_from_url(u, fallback_ext=".jpg")
        prefix = (ad_id or "").strip()
        if prefix:
            fname = f"{prefix}_{fname}"
        out_path = img_dir / fname
        if out_path.exists() and out_path.stat().st_size > 0:
            saved.append(str(out_path))
            continue
        # Try cleaned/uncropped URL variants first, then fall back to original
        def image_url_variants(orig_url: str):
            u = normalize_url(orig_url)
            variants = []
            try:
                p = urlparse(u)
                path = p.path
                # remove common size markers in path
                new_path = re.sub(r"/p\d+x\d+/", "/", path)
                new_path = re.sub(r"/[sp]\d+x\d+/", "/", new_path)
                new_path = re.sub(r"[_-](?:s|p)\d+x\d+", "", new_path)

                q = parse_qs(p.query)
                q_base = {k: v for k, v in q.items() if k.lower() not in RESIZE_Q_KEYS}

                stp_key = None
                for k in q_base.keys():
                    if k.lower() == "stp":
                        stp_key = k
                        break

                def build_url(qd, path_value=new_path):
                    new_query = urlencode({k: v[0] for k, v in qd.items()}) if qd else ""
                    return urlunparse((p.scheme, p.netloc, path_value, p.params, new_query, p.fragment))

                paths = [new_path]
                if new_path != path:
                    paths.append(path)

                for path_value in paths:
                    if stp_key:
                        stp_val = (q_base.get(stp_key) or [""])[0]
                        stp_clean = sanitize_fb_stp_param(stp_val)
                        if stp_clean and stp_clean != stp_val:
                            q2 = dict(q_base)
                            q2[stp_key] = [stp_clean]
                            variants.append(build_url(q2, path_value=path_value))

                        q3 = dict(q_base)
                        q3.pop(stp_key, None)
                        variants.append(build_url(q3, path_value=path_value))

                    cleaned = build_url(q_base, path_value=path_value)
                    if cleaned and cleaned != u:
                        variants.append(cleaned)
            except Exception:
                pass
            variants.append(u)
            # dedupe while preserving order
            out = []
            seen = set()
            for x in variants:
                if not x or x in seen:
                    continue
                seen.add(x)
                out.append(x)
            return out

        for cand_url in image_url_variants(u):
            if download_file(cand_url, out_path, cfg, request_ctx=request_ctx):
                saved.append(str(out_path))
                break

    return saved


def collect_urls_from_keys(node, limit=30):
    urls = []
    seen = set()

    def add(u):
        u = normalize_url(u)
        if not u or not u.startswith("http"):
            return
        if u in seen:
            return
        seen.add(u)
        urls.append(u)

    def walk(n):
        if len(urls) >= limit:
            return
        if isinstance(n, dict):
            for k, v in n.items():
                kl = str(k).lower()
                if kl in LINK_KEYS:
                    if isinstance(v, str):
                        add(v)
                    elif isinstance(v, dict):
                        for subk in ("url", "uri", "href"):
                            sv = v.get(subk)
                            if isinstance(sv, str):
                                add(sv)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, str):
                                add(item)
                            elif isinstance(item, dict):
                                for subk in ("url", "uri", "href"):
                                    sv = item.get(subk)
                                    if isinstance(sv, str):
                                        add(sv)
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
    return urls[:limit]
