import requests


def get_ws_url(cfg: dict) -> str | None:
    try:
        url = f"{cfg['ads_power_url']}/api/v1/browser/start?user_id={cfg['user_id']}"
        resp = requests.get(url, timeout=15).json()
        if resp.get("code") == 0:
            return resp["data"]["ws"]["puppeteer"]
        return None
    except Exception:
        return None
