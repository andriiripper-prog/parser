"""
Dump page HTML using DOM directly (not page.content())
"""
import time
from playwright.sync_api import sync_playwright
from adspower import get_ws_url
from config import load_config


def dump_page_v2():
    cfg = load_config()
    cfg["user_id"] = cfg["mobile_user_id"]
    
    ws = get_ws_url(cfg)
    if not ws:
        print("❌ AdsPower error")
        return
    
    with sync_playwright() as p:
        print("⏳ Connecting to AdsPower...")
        browser = p.chromium.connect_over_cdp(ws)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        
        print(f"📄 Current URL: {page.url}")
        
        # Wait for page to load
        print("⏳ Waiting 3 seconds...")
        time.sleep(3)
        
        # Scroll to load content
        print("📜 Scrolling...")
        for i in range(5):
            page.evaluate("window.scrollBy(0, 400)")
            time.sleep(0.8)
        
        print("⏳ Waiting for dynamic content...")
        time.sleep(2)
        
        # Get HTML directly from DOM
        html = page.evaluate("() => document.documentElement.outerHTML")
        
        # Save to file
        output_file = "facebook_mobile_full.html"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html)
        
        print(f"✅ Saved to {output_file}")
        print(f"📊 Size: {len(html):,} characters")
        
        # Search for ad markers
        for keyword in ["Sponsored", "Ad", "Реклама"]:
            if keyword in html:
                count = html.count(keyword)
                print(f"✅ Found '{keyword}': {count} times")
        
        # Sample some text to see what's there
        print("\n📝 Checking for MContainer elements...")
        try:
            containers = page.locator('div[data-mcomponent="MContainer"]').all()
            print(f"   Found {len(containers)} MContainer elements")
        except Exception as e:
            print(f"   Error: {e}")


if __name__ == "__main__":
    dump_page_v2()
