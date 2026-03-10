import os
from pathlib import Path

from env_loader import load_dotenv, env_bool, env_int, env_float

# ================= CONFIG DEFAULTS =================
DEFAULT_CONFIG = {
    "ads_power_url": "http://127.0.0.1:50325",
    "user_id": "k1951qx9",

    "scroll_count": 5,

    "output_file": "sponsored_ads_graphql.jsonl",
    "debug_dump_file": "graphql_debug_dump.jsonl",

    # Mobile Config
    "mobile_user_id": "k1951vv8", # ID вашего мобильного профиля в AdsPower
    "mobile_mode": False,   # Включить мобильный режим

    "max_graphql_responses": 900,
    "max_debug_dumps": 20,

    "min_scroll_pause": 0.6,
    "max_scroll_pause": 1.4,
    "scroll_min_px": 180,
    "scroll_max_px": 420,
    "scroll_steps_min": 6,
    "scroll_steps_max": 12,

    "min_idle_pause": 1.5,
    "max_idle_pause": 4.0,
    "min_hover_pause": 0.4,
    "max_hover_pause": 1.2,

    # media save
    "media_dir": "media",
    "download_media": True,
    "download_timeout": 25,
    "max_media_per_ad": 2,
    "max_bytes_per_file": 80 * 1024 * 1024,  # 80MB safety
    "max_images_per_ad": 1,
    "max_videos_per_ad": 1,
    "prefer_dash_full_hd": True,
    "keep_audio_separate_if_no_ffmpeg": True,

    # image quality filters
    "min_image_area": 60000,
    "min_image_width": 300,
    "min_image_height": 200,

    # AI classification
    "classify_images": True,
    "filter_verticals": [
        "Fake News & Media Scandals",
        "Celebrity Tragedy & Legal Drama",
        "Government Payouts & Allowances",
        "Financial Secrets & Wealth Exposé",
        "Crypto & Investment Offers",
    ],
    "min_vertical_confidence": 0.57,   # >= 57% — принимаем
    "classifier_model": "valhalla/distilbart-mnli-12-1",
    "ocr_languages": ["en", "ru"],



    # like behavior (anti-bot humanization)
    "like_enabled": True,    # master switch for random liking
    "like_chance": 0.06,     # probability (0–1) of liking per scroll cycle
    "like_min_delay": 1.2,   # seconds before clicking Like (min)
    "like_max_delay": 3.5,   # seconds before clicking Like (max)

    # auto-registration on ad landing pages
    "auto_register": True,   # enabled by default

    # telegram
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "telegram_send": True,
    "telegram_timeout": 60,
}

# ================= Sponsored variants (EN) =================
SPONSORED_HINTS = (
    "sponsored",
    "promoted",
    "advertisement",
    "advertiser",
    "paid for by",
    "funded by",
    "paid partnership",
    "sponsored by",
    "why am i seeing this ad",
    "learn more about this ad",
)

AD_ID_KEYS = {
    "ad_id",
    "adid",
    "advertisement_id",
    "adarchiveid",
    "ad_archive_id",
}

SPONSORED_NODE_SKIP_KEYS = {
    "__module_operation_cometfeedstorysponsoredlabelstrategy_sponsoredlabel",
    "__module_component_cometfeedstorysponsoredlabelstrategy_sponsoredlabel",
}

BAD_URL_SUBSTR = (
    "scontent-",
    ".xx.fbcdn.net/",
    "fbcdn.net/",
    "/m1/v/",
    "/t6/",
    "/v/t45.",
    "/v/t39.",
    "/v/t51.",
)

LINK_KEYS = {
    "url",
    "uri",
    "href",
    "link",
    "link_url",
    "external_url",
    "destination_url",
    "website_url",
    "target_url",
    "permalink_url",
    "cta_url",
}

VIDEO_URL_KEYS = {
    "playable_url",
    "playable_url_quality_hd",
    "playable_url_quality_sd",
    "browser_native_hd_url",
    "browser_native_sd_url",
    "browser_native_dash_url",
    "base_url",
}

PROGRESSIVE_URL_KEYS = {"progressive_url"}


def load_config(overrides: dict | None = None) -> dict:
    load_dotenv(".env")

    cfg = dict(DEFAULT_CONFIG)

    # ---- env overrides ----
    cfg["ads_power_url"] = os.getenv("ADS_POWER_URL", cfg["ads_power_url"])
    cfg["user_id"] = os.getenv("ADSPOWER_USER_ID", cfg["user_id"])
    cfg["mobile_user_id"] = os.getenv("MOBILE_USER_ID", cfg["mobile_user_id"])
    cfg["mobile_mode"] = env_bool("MOBILE_MODE", cfg["mobile_mode"])

    cfg["output_file"] = os.getenv("OUTPUT_FILE", cfg["output_file"])
    cfg["debug_dump_file"] = os.getenv("DEBUG_DUMP_FILE", cfg["debug_dump_file"])
    cfg["media_dir"] = os.getenv("MEDIA_DIR", cfg["media_dir"])

    cfg["telegram_bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN", cfg["telegram_bot_token"])
    cfg["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID", cfg["telegram_chat_id"])
    cfg["telegram_send"] = env_bool("TELEGRAM_SEND", cfg["telegram_send"])
    cfg["download_media"] = env_bool("DOWNLOAD_MEDIA", cfg["download_media"])
    cfg["auto_register"] = env_bool("AUTO_REGISTER", cfg["auto_register"])

    cfg["scroll_count"] = env_int("SCROLL_COUNT", cfg["scroll_count"])
    cfg["max_graphql_responses"] = env_int("MAX_GRAPHQL_RESPONSES", cfg["max_graphql_responses"])
    cfg["max_debug_dumps"] = env_int("MAX_DEBUG_DUMPS", cfg["max_debug_dumps"])

    cfg["download_timeout"] = env_int("DOWNLOAD_TIMEOUT", cfg["download_timeout"])
    cfg["max_bytes_per_file"] = env_int("MAX_BYTES_PER_FILE", cfg["max_bytes_per_file"])

    cfg["max_media_per_ad"] = env_int("MAX_MEDIA_PER_AD", cfg["max_media_per_ad"])
    cfg["max_images_per_ad"] = env_int("MAX_IMAGES_PER_AD", cfg["max_images_per_ad"])
    cfg["max_videos_per_ad"] = env_int("MAX_VIDEOS_PER_AD", cfg["max_videos_per_ad"])

    cfg["prefer_dash_full_hd"] = env_bool("PREFER_DASH_FULL_HD", cfg["prefer_dash_full_hd"])
    cfg["keep_audio_separate_if_no_ffmpeg"] = env_bool(
        "KEEP_AUDIO_SEPARATE_IF_NO_FFMPEG", cfg["keep_audio_separate_if_no_ffmpeg"]
    )

    cfg["min_image_area"] = env_int("MIN_IMAGE_AREA", cfg["min_image_area"])
    cfg["min_image_width"] = env_int("MIN_IMAGE_WIDTH", cfg["min_image_width"])
    cfg["min_image_height"] = env_int("MIN_IMAGE_HEIGHT", cfg["min_image_height"])

    cfg["min_scroll_pause"] = env_float("MIN_SCROLL_PAUSE", cfg["min_scroll_pause"])
    cfg["max_scroll_pause"] = env_float("MAX_SCROLL_PAUSE", cfg["max_scroll_pause"])
    cfg["scroll_min_px"] = env_int("SCROLL_MIN_PX", cfg["scroll_min_px"])
    cfg["scroll_max_px"] = env_int("SCROLL_MAX_PX", cfg["scroll_max_px"])
    cfg["scroll_steps_min"] = env_int("SCROLL_STEPS_MIN", cfg["scroll_steps_min"])
    cfg["scroll_steps_max"] = env_int("SCROLL_STEPS_MAX", cfg["scroll_steps_max"])

    cfg["min_idle_pause"] = env_float("MIN_IDLE_PAUSE", cfg["min_idle_pause"])
    cfg["max_idle_pause"] = env_float("MAX_IDLE_PAUSE", cfg["max_idle_pause"])
    cfg["min_hover_pause"] = env_float("MIN_HOVER_PAUSE", cfg["min_hover_pause"])
    cfg["max_hover_pause"] = env_float("MAX_HOVER_PAUSE", cfg["max_hover_pause"])

    cfg["classify_images"] = env_bool("CLASSIFY_IMAGES", cfg["classify_images"])
    cfg["min_vertical_confidence"] = env_float("MIN_VERTICAL_CONFIDENCE", cfg["min_vertical_confidence"])

    if overrides:
        cfg.update(overrides)

    # ---- init output files ----
    Path(cfg["output_file"]).write_text("", encoding="utf-8")
    Path(cfg["debug_dump_file"]).write_text("", encoding="utf-8")

    return cfg
