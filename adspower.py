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


def stop_browser(ads_power_url: str, user_id: str) -> bool:
    """Stop (close) the AdsPower browser for the given profile.
    
    Returns True if successfully closed, False otherwise.
    """
    try:
        url = f"{ads_power_url}/api/v1/browser/stop?user_id={user_id}"
        resp = requests.get(url, timeout=15).json()
        return resp.get("code") == 0
    except Exception:
        return False
