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
        print("❌ Telegram: No bot token configured")
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
        print("❌ Telegram: No chat_id configured or found")
        return False

    try:
        url = f"https://api.telegram.org/bot{token}/{method}"
        resp = requests.post(url, data=data, files=files, timeout=cfg.get("telegram_timeout", 60))
        js = {}
        try:
            js = resp.json()
        except Exception:
            js = {}
        if js.get("ok") is True:
            return True
        
        desc = str(js.get("description") or "")
        if retry_on_fail:
            if "chat not found" in desc.lower() or "bot can't send messages" in desc.lower():
                chat_id = telegram_get_latest_chat_id(cfg)
                if chat_id:
                    cfg["telegram_chat_id"] = chat_id
                    data["chat_id"] = chat_id
                    return telegram_api(cfg, method, data, files=files, retry_on_fail=False)
        
        if desc:
            print(f"❌ TG send failed ({method}): {desc}")
        return False
    except Exception as e:
        print(f"❌ Telegram API error ({method}): {e}")
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

    lines = []
    # Normalized fields (from mobile_main.py)
    clean_text = ad.get("clean_text", "-")
    text_link = ad.get("text_link", "-")
    post_link = ad.get("post_link") or post_url or "-"
    
    # CRITICAL: destination_link должна быть ВНЕШНЯЯ ссылка (не Facebook ссылка)
    # Приоритет: final_external > external_url > redirect_url > первый non-facebook URL
    destination_link = "-"
    if final_external and final_external.startswith("http"):
        destination_link = final_external
    elif external_url and external_url.startswith("http"):
        destination_link = external_url
    elif redirect_url and redirect_url.startswith("http"):
        destination_link = redirect_url
    else:
        # Fallback: найти первый non-facebook URL из urls
        for u in urls:
            if isinstance(u, str) and u.startswith("http"):
                from facebook_links import is_facebookish
                if not is_facebookish(u):
                    destination_link = u
                    break
    
    # --- FILTER EMPTY ADS ---
    # Если нет текста и нет ссылок - скипаем (это "шляпа")
    has_text = clean_text and clean_text != "-" and len(clean_text) > 2
    has_dest = destination_link and destination_link != "-"
    has_post = post_link and post_link != "-"
    
    if not has_text and not has_dest and not has_post:
        print(f"   ⚠️  Skipping empty ad (no text, no links)")
        return False
    # ------------------------
    
    # 1. Name
    if page_name:
        lines.append(f"Name: {page_name}")
        
    # 2. Geo + Account
    geo_label = cfg.get("account_geo", "Canada").title()
    account_id = cfg.get("account_id", "")
    account_name = cfg.get("account_name", "")
    geo_str = f"Geo: {geo_label}"
    if account_name:
        geo_str += f" | Account: {account_name}"
    elif account_id:
        geo_str += f" | Account: {account_id}"
    lines.append(geo_str)
    
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
    post_id = ad.get("post_id")
    if post_id:
        lines.append(f"Post ID: {post_id}")
        
    # Auto-Registration Info
    reg_email = ad.get("reg_email")
    reg_password = ad.get("reg_password")
    
    if reg_email and reg_email != "-":
        lines.append(f"Auto-Reg: ✅ {reg_email}")
        if reg_password and reg_password != "-":
            lines.append(f"Pass: {reg_password}")
    
    lines.append(f"Link in ad: {destination_link}")
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
            # Find the largest image file in media_files
            largest_size = -1
            best_img = None
            
            for p in media_files:
                sp = str(p)
                if not sp.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
                    continue
                ip = Path(p)
                if not ip.exists() or ip.stat().st_size <= 0:
                    continue
                
                size = ip.stat().st_size
                if size > largest_size:
                    largest_size = size
                    best_img = ip
            
            if best_img:
                img_path = best_img
    
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
        print(f"   📹 Found video: {vid_path.name}, sending...")
        ok = telegram_send_video(cfg, vid_path, caption=caption)
        if ok:
            print(f"   ✅ Video sent successfully")
            return True
        print(f"   ⚠️  Video send failed (check logs for reason), trying text fallback")
        return telegram_send_message(cfg, caption)

    if img_path:
        print(f"   🖼️  Found image: {img_path.name}, sending...")
        ok = telegram_send_photo(cfg, img_path, caption=caption)
        if ok:
            print(f"   ✅ Photo sent successfully")
            return True
        print(f"   ⚠️  Photo send failed (check logs for reason), trying text fallback")
        return telegram_send_message(cfg, caption)

    print(f"   📝 No media, sending text-only message")
    return telegram_send_message(cfg, caption)
