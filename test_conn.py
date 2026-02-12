from playwright.sync_api import sync_playwright
import adspower
from config import load_config

def test_conn():
    cfg = load_config()
    cfg["user_id"] = "k1951qx9"
    ws_url = adspower.get_ws_url(cfg)
    print(f"WS: {ws_url}")
    
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws_url)
        print("Connected.")
        if browser.contexts:
            page = browser.contexts[0].pages[0]
        else:
            page = browser.new_page()
            
        print(f"Current URL: {page.url}")
        print("Navigating to google...")
        try:
            page.goto("https://www.google.com", timeout=30000)
            print(f"New URL: {page.url}")
            page.screenshot(path="google_test.png")
            print("Screenshot taken.")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    test_conn()
