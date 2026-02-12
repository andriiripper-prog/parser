"""
Wait for Facebook content to actually load
"""
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from adspower import get_ws_url
from config import load_config


def wait_for_content():
    cfg = load_config()
    cfg["user_id"] = cfg["mobile_user_id"]
    
    ws = get_ws_url(cfg)
    if not ws:
        print("❌ AdsPower error")
        return
    
    with sync_playwright() as p:
        print("⏳ Connecting...")
        browser = p.chromium.connect_over_cdp(ws)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        
        print(f"📄 URL: {page.url}")
        
        # Try to wait for any div to appear
        print("⏳ Waiting for content to load (up to 30 seconds)...")
        
        try:
            # Wait for ANY div element
            page.wait_for_selector('div', timeout=30000)
            print("✅ Found div elements!")
        except PlaywrightTimeout:
            print("❌ Timeout waiting for divs")
        
        # Count elements
        divs = page.locator('div').all()
        spans = page.locator('span').all()
        
        print(f"\n📊 Current state:")
        print(f"  <div>: {len(divs)}")
        print(f"  <span>: {len(spans)}")
        
        if len(divs) > 0:
            print("\n✅ SUCCESS! Content loaded")
            
            # Try to find "Sponsored"
            try:
                sponsored = page.get_by_text("Sponsored", exact=False).all()
                print(f"  'Sponsored' labels: {len(sponsored)}")
                
                if len(sponsored) > 0:
                    print("\n🎉 Found ads! Saving HTML...")
                    html = page.evaluate("() => document.documentElement.outerHTML")
                    with open("facebook_working.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    print(f"  Saved to facebook_working.html ({len(html):,} chars)")
            except Exception as e:
                print(f"  Error searching for 'Sponsored': {e}")
        else:
            print("\n❌ Still no content")
            print("Trying to reload page...")
            page.reload(wait_until="domcontentloaded")
            time.sleep(5)
            
            divs = page.locator('div').all()
            print(f"  After reload - <div>: {len(divs)}")


if __name__ == "__main__":
    wait_for_content()
