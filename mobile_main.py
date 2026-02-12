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
from mobile_story_extract import extract_ads_via_js
from story_extract import extract_feed_ads
from telegram_client import send_ad_to_telegram, telegram_dedupe_key


def run_mobile_scraper():
    cfg = load_config()
    
    # Use Mobile Profile ID
    cfg["user_id"] = cfg["mobile_user_id"] 
    # Override debug dump file for mobile
    cfg["debug_dump_file"] = "mobile_debug_dump.jsonl"
    
    print(f"📱 Using Mobile Profile ID: {cfg['user_id']}")
    
    ws = get_ws_url(cfg)
    if not ws:
        print("❌ AdsPower error (ws url not received)")
        return

    if cfg["download_media"]:
        base = Path(cfg["media_dir"])
        (base / "images").mkdir(parents=True, exist_ok=True)
        (base / "videos").mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        print("⏳ Connecting to AdsPower (Mobile)...")
        browser = p.chromium.connect_over_cdp(ws)
        context = browser.contexts[0]
        
        # Find the correct page (not DevTools)
        page = None
        if context.pages:
            # Look for Facebook page first
            for p in context.pages:
                url = p.url
                if "facebook.com" in url:
                    page = p
                    break
            
            # If no Facebook page, take first non-devtools page
            if not page:
                for p in context.pages:
                    url = p.url
                    if not url.startswith("devtools://"):
                        page = p
                        break
            
            # Last resort: take first page
            if not page:
                page = context.pages[0]
        else:
            page = context.new_page()

        # Navigate to Facebook (www.facebook.com) as requested
        # Browser should handle mobile view redirection if needed based on User-Agent
        target_url = "https://www.facebook.com/"
        
        try:
            # Check if page is alive
            if page.is_closed():
                print("❌ Page is already closed!")
                return
                
            current_url = page.url
            print(f"📍 Current URL: {current_url}")
            
            # Navigate if not already on Facebook
            if "facebook.com" not in current_url:
                print(f"🚀 Navigating to {target_url}...")
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)
                print(f"✅ Navigated to: {page.url}")
            else:
                print(f"✅ Already on Facebook")
        except Exception as e:
            print(f"⚠️ Navigation error: {e}")
            # Try to continue anyway if page is still alive
            try:
                if page.is_closed():
                    print("❌ Page closed, cannot continue")
                    return
            except:
                return


        print("🚀 Started. Using hybrid approach (DOM + GraphQL)...")
        time.sleep(random.uniform(6, 10))

        seen_ids = set()
        seen_sigs = set()
        tg_sent = set()

        collected = 0
        processed_graphql = 0
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

        def process_ad_from_dom(ad_data):
            """Process ad extracted from DOM (mobile_story_extract)"""
            nonlocal collected, seen_sigs, tg_sent
            
            # Build signature for deduplication
            page_name = ad_data.get("page_name", "Unknown")
            text = ad_data.get("text", "")
            link = ad_data.get("link", "")
            image_urls = ad_data.get("image_urls", [])
            video_url = ad_data.get("video_url", "")
            
            # Create signature
            sig = (
                link 
                or video_url 
                or (image_urls[0] if image_urls else "")
                or f"{page_name}|{text[:80]}"
            )
            
            if sig in seen_sigs:
                return
            seen_sigs.add(sig)
            
            # Download media
            media_files = []
            if cfg["download_media"] and (image_urls or video_url):
                # Convert to format expected by save_media_for_ad
                media_files = save_media_for_ad(
                    image_urls,
                    [video_url] if video_url else [],
                    None,  # no DASH on mobile
                    cfg,
                    ad_id="",
                    request_ctx=context.request,
                )
            
            # Resolve landing URL (like desktop)
            visited_url = link or ""
            final_external = ""
            vstatus = ""
            verr = ""
            
            if link:
                print(f"      🔗 Found ad link: {link[:100]}")
            
            if link and "l.facebook.com/l.php" in link.lower():
                # Resolve redirect
                print(f"      🔄 Resolving redirect...")
                info = resolve_lphp_to_external_url(context, link, timeout_ms=20000)
                final_external = (info.get("external_url") or "").strip()
                vstatus = info.get("status") or ""
                verr = info.get("error") or ""
                if final_external:
                    print(f"      ✅ Resolved to: {final_external[:100]}")
                else:
                    print(f"      ⚠️  Resolution failed: {vstatus} {verr}")
            else:
                if link and link.startswith("http") and not is_facebookish(link):
                    final_external = link
                    vstatus = "direct_external"
                    print(f"      ✅ Direct external link")
                else:
                    vstatus = "no_external_link"
            
            # Build ads library URL (fallback)
            ads_library_url = ""
            if page_name and page_name != "Unknown":
                from urllib.parse import quote
                encoded_name = quote(page_name)
                ads_library_url = f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL&q={encoded_name}&search_type=page&media_type=all"
            
            # Build ad object
            ad = {
                "ad_id": "",
                "ad_hash": "",
                "ads_library_url": ads_library_url,
                "page_name": page_name,
                "text": text,
                "urls": [link] if link else [],
                "media_urls": media_files,
                "image_urls": image_urls,
                "video_urls": [video_url] if video_url else [],
                "dash_video_url": "",
                "dash_audio_url": "",
                "visited_url": visited_url,
                "final_external_url": final_external,
                "visit_status": vstatus,
                "visit_error": verr,
                "visited_at": datetime.now(timezone.utc).isoformat(),
                "media_files": media_files,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "source": "mobile_dom",
                "ad_label": ad_data.get("ad_label", ""),
            }
            
            # Save to file BEFORE classification (like desktop)
            with open(cfg["output_file"], "a", encoding="utf-8") as f:
                f.write(json.dumps(ad, ensure_ascii=False) + "\n")
            
            # AI Classification check
            should_send, vertical, confidence, matched_filter = should_send_ad(ad, media_files, cfg, classifier)
            
            # Add vertical metadata
            ad["ai_vertical"] = vertical
            ad["ai_confidence"] = confidence
            ad["matched_filter"] = matched_filter
            
            # Telegram deduplication and send (like desktop)
            tg_key = telegram_dedupe_key(ad)
            if tg_key and tg_key in tg_sent:
                pass  # Already sent
            else:
                if tg_key:
                    tg_sent.add(tg_key)
                send_ad_to_telegram(cfg, ad, media_files)
            
            collected += 1
            
            # Show info (like desktop print format)
            vertical_info = ""
            filter_flag = ""
            if vertical:
                vertical_info = f" | {vertical} ({confidence:.0%})"
                if matched_filter:
                    filter_flag = " ✅ MATCHED FILTER"
            
            # Show both URLs
            urls_display = []
            if ads_library_url:
                urls_display.append(f"📚 Ads Library")
            if final_external:
                urls_display.append(f"🔗 {final_external[:60]}")
            
            urls_out = " | ".join(urls_display) if urls_display else (text[:60]).replace("\n", " ") if text else ""
            print(f"   🔥 FOUND (DOM): {page_name}{vertical_info}{filter_flag}")
            if urls_out:
                print(f"      {urls_out}")

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
            """GraphQL response handler - kept for debugging and fallback"""
            nonlocal collected, processed_graphql, debug_dumps, stop_listen
            if stop_listen:
                return
            try:
                if page.is_closed():
                    return
            except Exception:
                return

            if processed_graphql >= cfg["max_graphql_responses"]:
                return

            url = response.url
            if ("/api/graphql/" not in url) and ("/api/graphqlbatch/" not in url):
                return

            processed_graphql += 1

            try:
                text = response.text()
            except BaseException as e:
                return

            # Verbose logging for first 10 responses
            if processed_graphql <= 10:
                print(f"   📊 GraphQL response #{processed_graphql}: {url[:80]}...")

            payloads = parse_graphql_payload(text)
            if not payloads:
                return
            
            # Log payloads
            if processed_graphql <= 10:
                print(f"      Parsed {len(payloads)} payload(s)")

            for payload in payloads:
                if not payload_looks_sponsored(payload):
                    continue
                
                print(f"   ✅ GraphQL: Found sponsored payload!")

                ads = extract_feed_ads(payload, cfg)
                
                if not ads:
                    print(f"   ⚠️  Sponsored payload but NO ads extracted")
                else:
                    print(f"   ✅ Extracted {len(ads)} ad(s) from GraphQL")

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
                    
                    print(f"   🔥 FOUND (GraphQL): {name} | {ad_id_out}{vertical_info}{filter_flag} | {url_out or text_out}")

        def handle_response(response):
            try:
                return _handle_response(response)
            except BaseException:
                pass

        # Start GraphQL listener (for debugging and fallback)
        page.on("response", handle_response)

        for i in range(cfg["scroll_count"]):
            print(f"📜 Scroll {i+1}...")
            human_idle(page, cfg)
            human_scroll(page, cfg)
            time.sleep(random.uniform(2.0, 4.0))
            
            # PRIMARY METHOD: Extract ads from DOM after each scroll
            print("   🔍 Extracting ads from DOM...")
            
            # Check console logs from JavaScript
            console_listener = lambda msg: print(f"      [Browser] {msg.text}")
            page.on("console", console_listener)
            
            try:
                ads_data = extract_ads_via_js(page, debug_all_posts=False)
                print(f"      [Python] JavaScript returned {len(ads_data)} ads")
            except Exception as e:
                print(f"   ⚠️  DOM extraction error: {e}")
                ads_data = [] # Ensure ads_data is defined even on error
            
            if ads_data:
                print(f"   ✅ Found {len(ads_data)} ad(s) via DOM")
                for ad_data_item in ads_data: # Renamed to avoid conflict with ads_data list
                    try:
                        process_ad_from_dom(ad_data_item)
                    except Exception as e:
                        print(f"   ⚠️  Error processing ad: {e}")
            else:
                print("   ℹ️  No ads found via DOM on this scroll")
            
            # Remove console listener
            page.remove_listener("console", console_listener)

        stop_listen = True
        try:
            page.off("response", handle_response)
        except Exception:
            pass

        print(f"🏁 Done. Saved total: {collected}")
        print(f"🧪 Debug saved to: {cfg['debug_dump_file']}")
        print(f"📊 GraphQL responses processed: {processed_graphql}")
        if cfg["download_media"]:
            print(f"📦 Media saved to: {cfg['media_dir']}/images and {cfg['media_dir']}/videos")


if __name__ == "__main__":
    run_mobile_scraper()
