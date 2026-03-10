import re
from urllib.parse import unquote

from config import AD_ID_KEYS, SPONSORED_NODE_SKIP_KEYS
from graphql_parser import collect_strings, find_first_key
from media import (
    classify_media_url,
    collect_urls_from_keys,
    dedupe_keep_order,
    extract_media_from_story,
    is_bad_asset_url,
)

_WORD_URL_RE = re.compile(r"https?://[^\s\"']+")

# Ключи в GraphQL-объекте story, которые содержат ЧУЖОЙ контент
# (соседние органические посты, прикреплённые истории и т.д.)
_SKIP_STORY_KEYS = {
    "attached_story",
    "attached_story_attachment",
    "adjacent_stories",
    "related_stories",
    "comet_sections",
    "feedback",
    "seen_state",
    "unified_reactors",
    "timeline_context_items",
    "subattachments",        # карусель — берём только основной текст
    "debug_info",
    "contextual_info",
}


def collect_strings_from_story(node, limit=400):
    """
    Собирает строки из story-ноды, ПРОПУСКАЯ ключи,
    которые обычно содержат чужой органический контент.
    """
    strings = []

    def walk(n, depth=0):
        if len(strings) >= limit:
            return
        if isinstance(n, dict):
            for k, v in n.items():
                if str(k).lower() in _SKIP_STORY_KEYS:
                    continue
                walk(v, depth + 1)
        elif isinstance(n, list):
            for v in n:
                walk(v, depth + 1)
        elif isinstance(n, str):
            strings.append(n)

    walk(node)
    return strings

BAD_STRINGS = {
    "sponsored", "suggested for you", "paid for by", "advertisement",
    "like", "comment", "share", "follow", "join", "install", "play", "apply", "sign up",
    "learn more", "shop now", "watch more", "send message", "whatsapp",
    "about this content", "why am i seeing this ad",
    "hide ad", "report ad", "save link", "turn on notifications"
}


def _is_valid_text(text: str) -> bool:
    """
    Validate that text is not garbage (e.g., from malformed GraphQL data).
    Filters out random strings like "nrhKlE0D krhKlE0A lrhKlE0B".
    
    Args:
        text: Text to validate
        
    Returns:
        bool: True if text looks valid, False if it's garbage
    """
    if not text or len(text.strip()) < 3:
        return False
    
    text = text.strip()
    
    # Count different character types
    letters = sum(1 for c in text if c.isalpha())
    digits = sum(1 for c in text if c.isdigit())
    
    # If too few letters, probably garbage
    if letters < 5:
        return False
    
    # Check for random mixed case (like "nrhKlE0D")
    # Count transitions between upper and lower case
    case_transitions = 0
    prev_was_upper = None
    for c in text:
        if c.isalpha():
            is_upper = c.isupper()
            if prev_was_upper is not None and prev_was_upper != is_upper:
                case_transitions += 1
            prev_was_upper = is_upper
    
    # If too many case transitions relative to letters, it's likely garbage
    # Example: "nrhKlE0D" has 4 transitions in 7 letters = 57%
    if letters >= 5 and case_transitions > letters * 0.4:
        return False
    
    # Check for too many digits mixed with letters (like "krhKlE0A")
    if digits > 0 and letters > 0:
        # If more than 20% digits in text with letters, suspicious
        if digits / (letters + digits) > 0.2:
            return False
    
    return True


def pick_best_text(strings, exclude_text=None):
    """
    Select the best text from collected strings.
    Improved version with more flexible filtering and exclusion logic.
    Argument 'exclude_text' allows filtering out text that matches page name.
    """
    candidates = []
    
    exclude_lower = exclude_text.lower().strip() if exclude_text else ""
    
    for s in strings:
        if not isinstance(s, str):
            continue
        text = s.strip()
        text_lower = text.lower()
        
        # Filter bad strings (exact match or contained if short)
        is_bad = False
        for bad in BAD_STRINGS:
            if bad == text_lower:
                is_bad = True
                break
            if len(text) < 20 and bad in text_lower:
                is_bad = True
                break
        if is_bad:
            continue
            
        # Filter excluded text (e.g. page name)
        if exclude_lower:
            # Exact match
            if text_lower == exclude_lower:
                continue
            # Contained match for short texts (e.g. "Belfius" in "Belfius Insurance")
            if len(text) < 50 and (text_lower in exclude_lower or exclude_lower in text_lower):
                continue
        
        # Minimum length check (reduced from 20 to 10)
        if len(text) < 10:
            continue
            
        # Maximum length check
        if len(text) > 900:
            continue
        
        # If text contains URL, try to extract text without URL
        if "http" in text.lower():
            # Remove URLs from text
            text_without_urls = _WORD_URL_RE.sub('', text).strip()
            # If there's still meaningful text after removing URLs, use it
            if len(text_without_urls) >= 10 and text_without_urls.count(" ") >= 1:
                text = text_without_urls
            # Otherwise skip this candidate
            elif len(text_without_urls) < 10:
                continue
        
        # Require at least 1 space (reduced from 2) for meaningful text
        if text.count(" ") < 1:
            continue
        
        # Require at least some alphabetic characters
        if not re.search(r"[A-Za-z]", text):
            continue
        
        # Validate text is not garbage (filter random strings from GraphQL)
        if not _is_valid_text(text):
            continue
        
        # Clean up multiple newlines
        text = re.sub(r'\n+', '\n', text)
        text = text.strip()
        
        candidates.append(text)
    
    if not candidates:
        return ""

    # Возвращаем ПЕРВЫЙ достаточно длинный кандидат (не самый длинный).
    # Рекламный текст в GraphQL идёт раньше соседнего органического контента.
    # Исключение: если первый кандидат очень короткий (< 30 символов) —
    # берём следующий длиннее, но не более чем в 3 раза длиннее первого.
    first = candidates[0]
    if len(first) >= 30:
        return first

    # Первый текст очень короткий — ищем немного длиннее, но без фанатизма
    for c in candidates[1:]:
        if len(c) >= 30 and len(c) <= len(first) * 4 + 200:
            return c

    return first


def find_urls(strings, limit=12):
    urls = []
    for s in strings:
        if "http" not in s:
            continue
        if "<MPD" in s or "<BaseURL" in s:
            continue
        for m in _WORD_URL_RE.findall(s):
            if "w3.org/2001/XMLSchema" in m:
                continue
            urls.append(m)
            if len(urls) >= limit:
                return urls
    return urls


def parse_ad_id_from_url(u: str):
    if not u or not isinstance(u, str):
        return "", ""
    try:
        decoded = unquote(u)
    except Exception:
        decoded = u or ""
    ad_id = ""
    ad_hash = ""
    m = re.search(r"(?:^|[?&])ad_id=(\d+)", decoded)
    if m:
        ad_id = m.group(1)
    mh = re.search(r"(?:^|[?&])h=([^&]+)", decoded)
    if mh:
        ad_hash = mh.group(1)
    return ad_id, ad_hash


def extract_ad_id_from_urls(urls):
    ad_hash = ""
    for u in urls or []:
        ad_id, h = parse_ad_id_from_url(u)
        if h and not ad_hash:
            ad_hash = h
        if ad_id:
            return ad_id, ad_hash
    return "", ad_hash


def ads_library_url_from_ad_id(ad_id: str) -> str:
    if not ad_id:
        return ""
    return f"https://www.facebook.com/ads/library/?id={ad_id}"


def story_has_real_content(story: dict, cfg: dict) -> bool:
    strings = collect_strings(story, limit=450)
    text = pick_best_text(strings)
    urls = [u for u in find_urls(strings) if not is_bad_asset_url(u)]
    img_urls, vid_urls, dash_av = extract_media_from_story(story, cfg)
    has_media = bool(img_urls or vid_urls or (dash_av and dash_av.get("video")))
    if text or urls or has_media:
        return True
    if isinstance(story.get("attachments"), list) and story.get("attachments"):
        return True
    return False


def extract_feed_ads(payload, cfg: dict):
    ads = []

    def get_page_name_from_story(story: dict) -> str:
        actors = story.get("actors")
        if isinstance(actors, list):
            for a in actors:
                if isinstance(a, dict):
                    name = a.get("name")
                    if isinstance(name, str) and 1 < len(name) < 120:
                        return name.strip()
        owner = story.get("owner")
        if isinstance(owner, dict):
            nm = owner.get("name")
            if isinstance(nm, str) and nm.strip():
                return nm.strip()
        return ""

    def story_is_ad_by_fields(story: dict) -> bool:
        if not isinstance(story, dict):
            return False
        if story.get("is_sponsored") is True:
            return True
        if story.get("sponsored_data") is not None:
            return True
        if find_first_key(story, AD_ID_KEYS) is not None:
            return True
        for k in story.keys():
            if str(k).lower() in SPONSORED_NODE_SKIP_KEYS:
                return True
        return False

    def extract_one(story: dict):
        # 1. Extract Page Name FIRST
        page_name = get_page_name_from_story(story)
        
        # 2. Collect strings and pick best text, EXCLUDING page name
        # Используем collect_strings_from_story (пропускает ключи с органикой)
        strings = collect_strings_from_story(story, limit=400)
        text = pick_best_text(strings, exclude_text=page_name)
        
        # Clean text (remove URLs, emojis) - similar to mobile_main.py
        import re
        clean_text = text
        if clean_text:
            # Remove emojis and special unicode
            clean_text = re.sub(r'[\ue000-\uf8ff]|[\U000f0000-\U000ffffd]|[\U00100000-\U0010fffd]|\ufffd', '', clean_text)
            # Remove URLs
            clean_text = re.sub(r'https?://\S+', '', clean_text).strip()
            # Normalize whitespace
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        
        if not clean_text:
            clean_text = "-"

        urls_all = find_urls(strings, limit=20)
        urls_all += collect_urls_from_keys(story, limit=40)
        urls_deduped = dedupe_keep_order(urls_all)

        image_urls, video_urls, dash_av = extract_media_from_story(story, cfg)

        ad_id = find_first_key(story, AD_ID_KEYS)
        ad_id = str(ad_id) if ad_id else ""
        ad_hash = ""
        if not ad_id:
            ad_id, ad_hash = extract_ad_id_from_urls(urls_deduped)
        else:
            _, ad_hash = extract_ad_id_from_urls(urls_deduped)

        landing_urls = []
        for u in urls_deduped:
            if is_bad_asset_url(u):
                continue
            if classify_media_url(u):
                continue
            landing_urls.append(u)

        max_images = cfg.get("max_images_per_ad") or cfg["max_media_per_ad"]
        max_videos = cfg.get("max_videos_per_ad") or 1
        image_urls = (image_urls or [])[:max_images]
        video_urls = (video_urls or [])[:max_videos]

        dash_video = ""
        dash_audio = ""
        if isinstance(dash_av, dict):
            dv = dash_av.get("video") or {}
            da = dash_av.get("audio") or {}
            dash_video = dv.get("url") or ""
            dash_audio = da.get("url") or ""

        media_urls = (video_urls + image_urls)[: cfg["max_media_per_ad"]]

        ad = {
            "ad_id": ad_id,
            "ad_hash": ad_hash,
            "ads_library_url": ads_library_url_from_ad_id(ad_id),
            "page_name": page_name,
            "text": text,
            "clean_text": clean_text,
            "urls": landing_urls[:12],
            "media_urls": media_urls,
            "image_urls": image_urls,
            "video_urls": video_urls,
            "dash_video_url": dash_video,
            "dash_audio_url": dash_audio,
            "raw_keys": list(story.keys())[:20],
        }

        if (
            not ad["page_name"]
            and len((ad["text"] or "").strip()) < 25
            and not ad["urls"]
            and not ad["media_urls"]
            and not ad.get("dash_video_url")
        ):
            return

        if not (ad["page_name"] or ad["text"] or ad["urls"] or ad["media_urls"] or ad.get("dash_video_url")):
            return

        ads.append(ad)

    def walk(n):
        if isinstance(n, dict):
            story = n.get("story")
            if isinstance(story, dict):
                if story_is_ad_by_fields(story) and story_has_real_content(story, cfg):
                    extract_one(story)

            node = n.get("node")
            if isinstance(node, dict):
                st2 = node.get("story")
                if isinstance(st2, dict):
                    if story_is_ad_by_fields(st2) and story_has_real_content(st2, cfg):
                        extract_one(st2)

            tn = n.get("__typename")
            if isinstance(tn, str) and "story" in tn.lower():
                if story_is_ad_by_fields(n) and story_has_real_content(n, cfg):
                    extract_one(n)

            for v in n.values():
                if isinstance(v, (dict, list)):
                    walk(v)

        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(payload)
    
    # Deduplicate ads by content signature to prevent duplicate extraction
    # (can happen if same story matches multiple extraction patterns)
    deduped = []
    seen_sigs = set()
    for ad in ads:
        # Build signature from page_name, text, and urls
        sig_parts = [
            (ad.get("page_name") or "").strip(),
            (ad.get("text") or "").strip()[:100],
            str(ad.get("ad_id", ""))
        ]
        sig = "|".join(sig_parts)
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            deduped.append(ad)
    
    return deduped
