import asyncio
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

from ad_classifier import AdImageClassifier
from adspower import get_ws_url
from config import load_config
from facebook_links import is_facebookish, pick_target_link_for_visit, resolve_lphp_to_external_url
from graphql_parser import parse_graphql_payload, payload_looks_sponsored
from human import human_idle, human_scroll
from media import save_media_for_ad
from story_extract import extract_feed_ads
from telegram_client import send_ad_to_telegram, telegram_dedupe_key


def run():
    cfg = load_config()

    ws = get_ws_url(cfg)
    if not ws:
        print("❌ AdsPower error (ws url not received)")
        return

    if cfg["download_media"]:
        base = Path(cfg["media_dir"])
        (base / "images").mkdir(parents=True, exist_ok=True)
        (base / "videos").mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        print("⏳ Connecting to AdsPower CDP...")
        browser = p.chromium.connect_over_cdp(ws)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        if "facebook.com" not in page.url:
            page.goto("https://www.facebook.com/")

        print("🚀 Started. Listening GraphQL responses...")
        time.sleep(random.uniform(6, 10))

        seen_ids = set()
        seen_sigs = set()
        tg_sent = set()

        collected = 0
        processed = 0
        debug_dumps = 0
        stop_listen = False

        # Initialize AI classifier
        classifier = None
        if cfg.get("classify_images"):
            print("🤖 Initializing AI classifier...")
            try:
                classifier = AdImageClassifier(
                    ocr_languages=cfg.get("ocr_languages", ["en", "ru"]),
                    classification_model=cfg.get("classifier_model", "valhalla/distilbart-mnli-12-1")
                )
            except Exception as e:
                print(f"⚠️  Classifier initialization failed: {e}")
                print("   Continuing without classification...")

        def extract_first_frame(video_path):
            """Extract first frame from video file"""
            if not CV2_AVAILABLE:
                return None
            
            try:
                cap = cv2.VideoCapture(str(video_path))
                ret, frame = cap.read()
                cap.release()
                
                if ret:
                    # Save frame as temporary image
                    frame_path = Path(str(video_path).replace('.mp4', '_frame.jpg').replace('.m4v', '_frame.jpg'))
                    cv2.imwrite(str(frame_path), frame)
                    return frame_path
            except Exception as e:
                print(f"   ⚠️  Failed to extract frame from video: {e}")
            return None

        def should_send_ad(ad, media_files, cfg, classifier):
            """Check if ad matches target verticals and mark it"""
            if not classifier or not cfg.get("classify_images"):
                return True, None, None, False  # no classification, send all

            if not media_files:
                return True, None, None, False  # no media, but still send

            # Classify first image
            for media_path in media_files:
                if str(media_path).lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    try:
                        result = classifier.classify_image(str(media_path))
                        vertical = result.get("vertical", "Unknown")
                        confidence = result.get("confidence", 0.0)

                        # Check if vertical matches filter
                        filter_verticals = cfg.get("filter_verticals", [])
                        min_confidence = cfg.get("min_vertical_confidence", 0.6)

                        matched = vertical in filter_verticals and confidence >= min_confidence
                        return True, vertical, confidence, matched
                    except Exception as e:
                        print(f"   ⚠️  Classification failed: {e}")
                        return True, None, 0.0, False

            # If no image found, try to extract frame from video
            for media_path in media_files:
                if str(media_path).lower().endswith(('.mp4', '.m4v', '.webm', '.avi')):
                    print(f"   🎬 Extracting frame from video: {Path(media_path).name}")
                    frame_path = extract_first_frame(media_path)
                    if frame_path and frame_path.exists():
                        try:
                            result = classifier.classify_image(str(frame_path))
                            vertical = result.get("vertical", "Unknown")
                            confidence = result.get("confidence", 0.0)

                            # Clean up temporary frame
                            frame_path.unlink()

                            # Check if vertical matches filter
                            filter_verticals = cfg.get("filter_verticals", [])
                            min_confidence = cfg.get("min_vertical_confidence", 0.6)

                            matched = vertical in filter_verticals and confidence >= min_confidence
                            return True, vertical, confidence, matched
                        except Exception as e:
                            print(f"   ⚠️  Video classification failed: {e}")
                            if frame_path.exists():
                                frame_path.unlink()
                            return True, None, 0.0, False

            return True, None, None, False  # no valid media, but still send

        def slim(obj, depth=5, max_list=10, max_keys=70):
            if depth <= 0:
                return "<<cut>>"
            if isinstance(obj, dict):
                out = {}
                for k, v in list(obj.items())[:max_keys]:
                    if isinstance(v, (dict, list)):
                        out[k] = slim(v, depth - 1, max_list, max_keys)
                    else:
                        out[k] = v
                return out
            if isinstance(obj, list):
                return [slim(v, depth - 1, max_list, max_keys) for v in obj[:max_list]]
            return obj

        def _handle_response(response):
            nonlocal collected, processed, debug_dumps, stop_listen
            if stop_listen:
                return
            try:
                if page.is_closed():
                    return
            except Exception:
                return

            if processed >= cfg["max_graphql_responses"]:
                return

            url = response.url
            if ("/api/graphql/" not in url) and ("/api/graphqlbatch/" not in url):
                return

            processed += 1

            try:
                text = response.text()
            except BaseException as e:
                if isinstance(e, asyncio.CancelledError):
                    return
                if "TargetClosedError" in str(e) or "Target page, context or browser has been closed" in str(e):
                    return
                return

            payloads = parse_graphql_payload(text)
            if not payloads:
                return

            for payload in payloads:
                if not payload_looks_sponsored(payload):
                    continue

                ads = extract_feed_ads(payload, cfg)

                if not ads:
                    if debug_dumps < cfg["max_debug_dumps"]:
                        with open(cfg["debug_dump_file"], "a", encoding="utf-8") as f:
                            f.write(
                                json.dumps(
                                    {"type": "sponsored_payload_no_ads_slim", "url": url, "payload_slim": slim(payload)},
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                        debug_dumps += 1
                    continue

                for ad in ads:
                    urls = ad.get("urls") or []
                    image_urls = ad.get("image_urls") or []
                    video_urls = ad.get("video_urls") or []
                    dash_video_url = (ad.get("dash_video_url") or "").strip()
                    dash_audio_url = (ad.get("dash_audio_url") or "").strip()

                    if (
                        not (ad.get("page_name") or "").strip()
                        and not (ad.get("text") or "").strip()
                        and not urls
                        and not (image_urls or video_urls or dash_video_url)
                    ):
                        continue

                    ad_id = (ad.get("ad_id") or "").strip()
                    ad_hash = (ad.get("ad_hash") or "").strip()
                    url_key = urls[0] if urls else ""
                    text_snip = ((ad.get("text") or "").strip())[:80]

                    sig = (
                        ad_id
                        or ad_hash
                        or url_key
                        or dash_video_url
                        or (video_urls[0] if video_urls else "")
                        or (image_urls[0] if image_urls else "")
                        or f"{(ad.get('page_name') or '').strip()}|{text_snip}"
                    )
                    if sig in seen_sigs:
                        continue
                    if ad_id and ad_id in seen_ids:
                        continue

                    if ad_id:
                        seen_ids.add(ad_id)
                    seen_sigs.add(sig)

                    media_files = []
                    if cfg["download_media"] and (image_urls or video_urls or dash_video_url):
                        dash_av = None
                        if dash_video_url:
                            dash_av = {
                                "video": {"url": dash_video_url},
                                "audio": {"url": dash_audio_url} if dash_audio_url else None,
                            }
                        media_files = save_media_for_ad(
                            image_urls,
                            video_urls,
                            dash_av,
                            cfg,
                            ad_id=ad_id,
                            request_ctx=context.request,
                        )

                    # resolve landing
                    target_link = pick_target_link_for_visit(ad)
                    visited_url = target_link or ""
                    final_external = ""
                    vstatus = ""
                    verr = ""

                    if target_link and "l.facebook.com/l.php" in target_link.lower():
                        info = resolve_lphp_to_external_url(context, target_link, timeout_ms=20000)
                        final_external = (info.get("external_url") or "").strip()
                        vstatus = info.get("status") or ""
                        verr = info.get("error") or ""
                    else:
                        if target_link and target_link.startswith("http") and not is_facebookish(target_link):
                            final_external = target_link
                            vstatus = "direct_external"
                        else:
                            vstatus = "no_external_link"

                    ad["visited_url"] = visited_url
                    ad["final_external_url"] = final_external
                    ad["visit_status"] = vstatus
                    ad["visit_error"] = verr
                    ad["visited_at"] = datetime.now(timezone.utc).isoformat()

                    ad["media_files"] = media_files
                    ad["captured_at"] = datetime.now(timezone.utc).isoformat()
                    ad["source"] = "graphql"

                    with open(cfg["output_file"], "a", encoding="utf-8") as f:
                        f.write(json.dumps(ad, ensure_ascii=False) + "\n")

                    # AI Classification check
                    should_send, vertical, confidence, matched_filter = should_send_ad(ad, media_files, cfg, classifier)

                    # Add vertical metadata
                    ad["ai_vertical"] = vertical
                    ad["ai_confidence"] = confidence
                    ad["matched_filter"] = matched_filter

                    tg_key = telegram_dedupe_key(ad)
                    if tg_key and tg_key in tg_sent:
                        pass
                    else:
                        if tg_key:
                            tg_sent.add(tg_key)
                        send_ad_to_telegram(cfg, ad, media_files)

                    collected += 1

                    name = ad.get("page_name") or "Unknown"
                    ad_id_out = ad_id or "no-id"
                    url_out = ad.get("ads_library_url") or url_key or dash_video_url
                    text_out = ((ad.get("text") or "")[:60]).replace("\\n", " ")
                    
                    # Show vertical and filter status
                    if vertical:
                        vertical_info = f" | {vertical} ({confidence:.0%})"
                        if matched_filter:
                            filter_flag = " ✅ MATCHED FILTER"
                        else:
                            filter_flag = ""
                    else:
                        vertical_info = ""
                        filter_flag = ""
                    
                    print(f"   🔥 FOUND: {name} | {ad_id_out}{vertical_info}{filter_flag} | {url_out or text_out}")

        def handle_response(response):
            try:
                return _handle_response(response)
            except BaseException as e:
                if isinstance(e, asyncio.CancelledError):
                    return
                if "TargetClosedError" in str(e) or "Target page, context or browser has been closed" in str(e):
                    return
                return

        page.on("response", handle_response)

        for i in range(cfg["scroll_count"]):
            print(f"📜 Scroll {i+1}...")
            human_idle(page, cfg)
            human_scroll(page, cfg)
            time.sleep(random.uniform(2.0, 4.0))

        stop_listen = True
        try:
            page.off("response", handle_response)
        except Exception:
            pass

        print(f"🏁 Done. Saved total: {collected}")
        print(f"🧪 Debug saved to: {cfg['debug_dump_file']}")
        if cfg["download_media"]:
            print(f"📦 Media saved to: {cfg['media_dir']}/images and {cfg['media_dir']}/videos")


if __name__ == "__main__":
    run()
