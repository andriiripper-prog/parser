from pathlib import Path
import requests

from facebook_links import pick_post_page_redirect
from media import resolve_saved_image_path, resolve_saved_video_path


def telegram_get_latest_chat_id(cfg: dict) -> str:
    token = cfg.get("telegram_bot_token") or ""
    if not token:
        return ""
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        resp = requests.get(url, timeout=cfg.get("telegram_timeout", 20))
        js = resp.json() if resp is not None else {}
        if not js or not js.get("ok"):
            return ""
        results = js.get("result") or []
        for upd in reversed(results):
            msg = (
                upd.get("message")
                or upd.get("edited_message")
                or upd.get("channel_post")
                or upd.get("edited_channel_post")
            )
            if not isinstance(msg, dict):
                continue
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id:
                return str(chat_id)
    except Exception:
        return ""
    return ""


def telegram_api(cfg: dict, method: str, data: dict, files=None, retry_on_fail: bool = True) -> bool:
    token = cfg.get("telegram_bot_token") or ""
    if not token:
        return False

    if "chat_id" not in data or not data.get("chat_id"):
        chat_id = cfg.get("telegram_chat_id") or ""
        if not chat_id:
            chat_id = telegram_get_latest_chat_id(cfg)
            if chat_id:
                cfg["telegram_chat_id"] = chat_id
        if chat_id:
            data["chat_id"] = chat_id

    if not data.get("chat_id"):
        return False

    try:
        url = f"https://api.telegram.org/bot{token}/{method}"
        resp = requests.post(url, data=data, files=files, timeout=cfg.get("telegram_timeout", 20))
        js = {}
        try:
            js = resp.json()
        except Exception:
            js = {}
        if js.get("ok") is True:
            return True
        if retry_on_fail:
            desc = str(js.get("description") or "")
            if "chat not found" in desc.lower() or "bot can't send messages" in desc.lower():
                chat_id = telegram_get_latest_chat_id(cfg)
                if chat_id:
                    cfg["telegram_chat_id"] = chat_id
                    data["chat_id"] = chat_id
                    return telegram_api(cfg, method, data, files=files, retry_on_fail=False)
        desc = str(js.get("description") or "")
        if desc:
            print(f"TG send failed ({method}): {desc}")
        return False
    except Exception:
        return False


def telegram_send_message(cfg: dict, text: str) -> bool:
    data = {"text": text}
    if cfg.get("telegram_chat_id"):
        data["chat_id"] = cfg["telegram_chat_id"]
    return telegram_api(cfg, "sendMessage", data)


def telegram_send_photo(cfg: dict, path: Path, caption: str = "") -> bool:
    if not path or not path.exists():
        return False
    try:
        with open(path, "rb") as f:
            files = {"photo": f}
            data = {}
            if cfg.get("telegram_chat_id"):
                data["chat_id"] = cfg["telegram_chat_id"]
            if caption:
                data["caption"] = caption
            return telegram_api(cfg, "sendPhoto", data, files=files)
    except Exception:
        return False


def telegram_send_video(cfg: dict, path: Path, caption: str = "") -> bool:
    if not path or not path.exists():
        return False
    try:
        with open(path, "rb") as f:
            files = {"video": f}
            data = {"supports_streaming": True}
            if cfg.get("telegram_chat_id"):
                data["chat_id"] = cfg["telegram_chat_id"]
            if caption:
                data["caption"] = caption
            return telegram_api(cfg, "sendVideo", data, files=files)
    except Exception:
        return False


def telegram_dedupe_key(ad: dict) -> str:
    """
    Generate a robust deduplication key based on ad content.
    Key components: Page Name + Text Hash + Destination Link + Post Link
    NOTE: Does NOT include image URLs - Facebook shows same ads with different images
    """
    page_name = (ad.get("page_name") or "Unknown").strip()
    
    # Use clean text if available, otherwise raw text
    text = (ad.get("clean_text") or ad.get("text") or "").strip()
    # Hash text to avoid huge keys
    import hashlib
    text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()[:10]
    
    # Links
    dest = (ad.get("destination_link") or "").strip()
    if dest == "-": dest = ""
    
    post = (ad.get("post_link") or "").strip()
    if post == "-": post = ""
    
    # Construct key without image hash
    # Facebook shows same ads with different images (carousel/A-B testing)
    # So we deduplicate based on content only
    parts = [page_name, text_hash, dest, post]
    key = "|".join(parts)
    
    # Return md5 of the whole thing for cleaner storage
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def send_ad_to_telegram(cfg: dict, ad: dict, media_files=None) -> bool:
    if not cfg.get("telegram_send"):
        return False

    page_name = (ad.get("page_name") or "").strip()
    text = (ad.get("text") or "").strip()
    urls = ad.get("urls") or []
    post_url, _page_url, redirect_url, external_url = pick_post_page_redirect(urls)
    ads_lib = (ad.get("ads_library_url") or "").strip()
    final_external = (ad.get("final_external_url") or "").strip()
    attached = final_external or external_url or redirect_url

    lines = []
    # Normalized fields (from mobile_main.py)
    clean_text = ad.get("clean_text", "-")
    text_link = ad.get("text_link", "-")
    post_link = ad.get("post_link") or post_url or "-" 
    destination_link = ad.get("destination_link", "-")
    
    # 1. Name
    if page_name:
        lines.append(f"Name: {page_name}")
        
    # 2. Geo
    lines.append("Geo: Canada")
    
    # 3. Vertical details
    ai_vertical = ad.get("ai_vertical")
    ai_confidence = ad.get("ai_confidence")
    matched_filter = ad.get("matched_filter", False)
    
    if ai_vertical and ai_confidence:
        filter_flag = " ✅" if matched_filter else ""
        lines.append(f"Vertical: {ai_vertical} ({ai_confidence:.0%}){filter_flag}")

    # 4. Text (Cleaned)
    lines.append(f"\nText:\n{clean_text}")

    # 5. Links Section
    # Link in ad: <destination_link or text_link or "-">
    # Post: <post_link or "-">
    # Ads Library: <ads_library_link>
    
    link_in_ad = destination_link if destination_link != "-" else text_link
    
    lines.append(f"Link in ad: {link_in_ad}")
    lines.append(f"Post: {post_link}")
    
    # Ads Library link removed as per user request
    # if ads_lib:
    #     lines.append(f"Ads Library: {ads_lib}")

    caption = "\n".join(lines).strip()
    if not caption:
        return False
    if len(caption) > 900:
        caption = caption[:900] + "..."

    ad_id = (ad.get("ad_id") or "").strip()

    # CRITICAL FIX: Prioritize media_files (already filtered by JavaScript)
    # over resolve_saved_* functions which may incorrectly re-filter and reject valid images
    vid_path = None
    img_path = None
    
    # 1. First, try to use media_files directly (for mobile ads, JS already filtered correctly)
    if media_files:
        # Look for video in media_files first
        for p in media_files:
            if str(p).lower().endswith((".mp4", ".m4v")):
                vp = Path(p)
                if vp.exists() and vp.stat().st_size > 0:
                    vid_path = vp
                    break
        
        # Look for image in media_files first
        if not img_path:
            # For mobile ads, JavaScript already selected the correct image
            # Just take the first valid image file (don't search for largest)
            for p in media_files:
                sp = str(p)
                if not sp.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
                    continue
                ip = Path(p)
                if not ip.exists() or ip.stat().st_size <= 0:
                    continue
                # Found first valid image - use it
                img_path = ip
                break
    
    # 2. Fallback: use resolve_saved_* functions (for GraphQL ads or if media_files is empty)
    if not vid_path:
        vid_path = resolve_saved_video_path(
            ad.get("video_urls") or [],
            cfg,
            (ad.get("dash_video_url") or "").strip(),
            ad_id,
        )
    
    if not img_path:
        img_path = resolve_saved_image_path(ad.get("image_urls") or [], cfg, ad_id)

    if vid_path:
        ok = telegram_send_video(cfg, vid_path, caption=caption)
        if ok:
            return True
        return telegram_send_message(cfg, caption)

    if img_path:
        ok = telegram_send_photo(cfg, img_path, caption=caption)
        if ok:
            return True
        return telegram_send_message(cfg, caption)

    return telegram_send_message(cfg, caption)
