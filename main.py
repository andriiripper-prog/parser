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
from auto_register import try_auto_register # NEW
from config import load_config
from facebook_links import is_facebookish, pick_target_link_for_visit, resolve_lphp_to_external_url, pick_post_page_redirect
from graphql_parser import parse_graphql_payload, payload_looks_sponsored
from human import human_idle, human_scroll, human_like_post
from media import save_media_for_ad
from story_extract import extract_feed_ads
from telegram_client import send_ad_to_telegram, telegram_dedupe_key


def _enrich_cfg_with_account_info(cfg):
    """Read accounts.yaml and inject geo + name into cfg based on current user_id."""
    try:
        import yaml
        accounts_file = Path(__file__).parent / "accounts.yaml"
        if accounts_file.exists():
            with open(accounts_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            for acc in (data.get("accounts") or []):
                if acc.get("id") == cfg.get("user_id"):
                    cfg["account_geo"] = acc.get("geo", "canada")
                    cfg["account_name"] = acc.get("name", "")
                    cfg["account_id"] = acc.get("id", "")
                    break
    except Exception as e:
        print(f"⚠️ Could not load account geo info: {e}")


def run(overrides=None):
    cfg = load_config(overrides)
    _enrich_cfg_with_account_info(cfg)

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
        
        page = None
        for attempt in range(15):
            if context.pages:
                page = context.pages[0]
                break
            try:
                page = context.new_page()
                break
            except Exception as e:
                print(f"⏳ Waiting for browser to be ready for new page ({attempt+1}/15): {e}")
                time.sleep(1)
                
        if not page:
            print("❌ Failed to get or create a page after 15 seconds")
            return

        if "facebook.com" not in page.url:
            page.goto("https://www.facebook.com/")
        else:
            print("🔄 Refreshing feed...")
            try:
                page.reload()
            except:
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
            """Check if ad matches target verticals based on image OCR only"""
            if not classifier or not cfg.get("classify_images"):
                return True, None, None, False  # no classification, send all

            if not media_files:
                return True, None, None, False  # no image — skip classification

            # Classify first image using OCR only (no ad text)
            for media_path in media_files:
                if str(media_path).lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    try:
                        result = classifier.classify_image(str(media_path))
                        vertical = result.get("vertical", "Unknown")
                        confidence = result.get("confidence", 0.0)
                        is_whitelist = result.get("is_whitelist", False)

                        # Белый список — сразу пропускаем
                        if is_whitelist:
                            print(f"   🚫 Белый список: «{vertical}» ({confidence:.0%}) — пропускаем")
                            return True, vertical, confidence, False

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
                            is_whitelist = result.get("is_whitelist", False)

                            # Clean up temporary frame
                            frame_path.unlink()

                            # Белый список — сразу пропускаем
                            if is_whitelist:
                                print(f"   🚫 Белый список: «{vertical}» ({confidence:.0%}) — пропускаем")
                                return True, vertical, confidence, False

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
                
                print(f"   📦 extract_feed_ads returned {len(ads)} ads")
                for ads_idx, ad in enumerate(ads):
                    print(f"   [Ad {ads_idx+1}/{len(ads)}] page={ad.get('page_name', 'N/A')[:30]}, text_len={len(ad.get('text', '')) or 0}, urls={len(ad.get('urls', []))}") 
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

                    # ── ШАГ 1: Классификация картинки ─────────────────────────────────
                    # Делаем это ДО посещения ссылки — не тратим время на нерелевантные объявления
                    should_send, vertical, confidence, matched_filter = should_send_ad(ad, media_files, cfg, classifier)

                    # Добавляем метаданные классификации
                    ad["ai_vertical"] = vertical
                    ad["ai_confidence"] = confidence
                    ad["matched_filter"] = matched_filter

                    # Если классификатор включён и объявление НЕ подошло — пропускаем
                    if classifier and cfg.get("classify_images") and not matched_filter:
                        print(f"      ⏭  Пропускаем: вертикаль «{vertical}» ({confidence:.0%}) не в фильтре")
                        collected += 1
                        continue

                    print(f"      ✅ Вертикаль: «{vertical}» ({confidence:.0%}) — переходим по ссылке")

                    # ── ШАГ 2: Резолвим ссылку ────────────────────────────────────────
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

                    # ── ШАГ 3: Авторегистрация (только для объявлений из фильтра) ─────
                    reg_email = "-"
                    reg_password = "-"
                    reg_status = "-"

                    if final_external and cfg.get("auto_register", False):
                        try:
                            print(f"      🤖 Авторег на: {final_external[:50]}...")
                            reg_result = try_auto_register(context, final_external, timeout=30000)

                            if reg_result.get("success"):
                                reg_email = reg_result.get("email")
                                reg_password = reg_result.get("password")
                                reg_status = "Success"
                                print(f"      ✅ Авторег успешен! {reg_email} : {reg_password}")
                            elif reg_result.get("error"):
                                reg_status = f"Failed: {reg_result['error']}"
                                print(f"      ⚠️  Авторег не удался: {reg_result['error']}")
                            else:
                                reg_status = "Attempted"
                                print(f"      ℹ️  Авторег выполнен")
                        except Exception as e:
                            print(f"      ❌ Авторег — исключение: {e}")
                            reg_status = "Error"
                    elif final_external:
                        print(f"      ℹ️  Авторег отключён")

                    ad["visited_url"] = visited_url
                    ad["final_external_url"] = final_external
                    ad["visit_status"] = vstatus
                    ad["visit_error"] = verr
                    ad["visited_at"] = datetime.now(timezone.utc).isoformat()

                    ad["reg_email"] = reg_email
                    ad["reg_password"] = reg_password
                    ad["reg_status"] = reg_status

                    ad["media_files"] = media_files
                    ad["captured_at"] = datetime.now(timezone.utc).isoformat()
                    ad["source"] = "graphql"

                    with open(cfg["output_file"], "a", encoding="utf-8") as f:
                        f.write(json.dumps(ad, ensure_ascii=False) + "\n")

                    # ── ШАГ 4: Отправка в Telegram ────────────────────────────────────
                    # Pre-fill destination_link and post_link
                    if not ad.get("destination_link"):
                        urls = ad.get("urls") or []
                        post_url, _page_url, redirect_url, external_url = pick_post_page_redirect(urls)

                        final_ext = (ad.get("final_external_url") or "").strip()
                        if final_ext and final_ext.startswith("http"):
                            ad["destination_link"] = final_ext
                        elif external_url and external_url.startswith("http"):
                            ad["destination_link"] = external_url
                        elif redirect_url and redirect_url.startswith("http"):
                            ad["destination_link"] = redirect_url
                        else:
                            for u in urls:
                                if isinstance(u, str) and u.startswith("http") and not is_facebookish(u):
                                    ad["destination_link"] = u
                                    break

                        if not ad.get("destination_link"):
                            ad["destination_link"] = "-"

                    if not ad.get("post_link"):
                        urls = ad.get("urls") or []
                        post_url, _page_url, redirect_url, external_url = pick_post_page_redirect(urls)
                        ad["post_link"] = post_url or (ad.get("ads_library_url") or "-")

                    tg_key = telegram_dedupe_key(ad)
                    if tg_key and tg_key in tg_sent:
                        print("      ⏩ Уже отправлено в Telegram (дедупликация)")
                    else:
                        if tg_key:
                            tg_sent.add(tg_key)
                        print(f"      📤 Отправка в Telegram...")
                        if send_ad_to_telegram(cfg, ad, media_files):
                            print(f"      🚀 Отправлено в Telegram")
                        else:
                            print(f"      ❌ Ошибка отправки в Telegram")

                    collected += 1

                    name = ad.get("page_name") or "Unknown"
                    ad_id_out = ad_id or "no-id"
                    url_out = ad.get("ads_library_url") or url_key or dash_video_url
                    text_out = ((ad.get("text") or "")[:60]).replace("\\n", " ")

                    if vertical:
                        vertical_info = f" | {vertical} ({confidence:.0%})"
                        filter_flag = " ✅ MATCHED" if matched_filter else ""
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

        cycle = 0

        try:
            while True:
                cycle += 1
                print(f"\n🔁 Cycle #{cycle} started...")

                for i in range(cfg["scroll_count"]):
                    print(f"📜 Scroll {i+1}...")
                    human_idle(page, cfg)
                    human_scroll(page, cfg)
                    time.sleep(random.uniform(2.0, 4.0))

                    # Random like (6% chance per scroll — anti-bot humanization)
                    if cfg.get("like_enabled", True) and random.random() < cfg.get("like_chance", 0.06):
                        human_like_post(page, cfg)

                time.sleep(random.uniform(3, 6))

        except KeyboardInterrupt:
            print("\n👋 Stopped by KeyboardInterrupt")
        finally:
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
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=str, help="AdsPower Profile ID")
    args = parser.parse_args()
    
    overrides = {}
    if args.profile:
        overrides["user_id"] = args.profile
        
    run(overrides)
