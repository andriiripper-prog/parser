"""
Save actual ad container HTML for debugging selectors
"""
import time
from playwright.sync_api import sync_playwright
from adspower import get_ws_url
from config import load_config


def save_ad_html():
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
        
        # Reload to load content
        print("🔄 Reloading...")
        page.reload(wait_until="domcontentloaded")
        time.sleep(2)
        
        # Scroll to load ads
        print("📜 Scrolling...")
        page.evaluate("window.scrollBy(0, 500)")
        time.sleep(2)
        page.evaluate("window.scrollBy(0, 500)")
        time.sleep(2)
        
        # Find "Sponsored" labels
        sponsored_labels = page.get_by_text("Sponsored", exact=False).all()
        
        print(f"✅ Found {len(sponsored_labels)} 'Sponsored' labels")
        
        if len(sponsored_labels) > 0:
            # Get first one
            label = sponsored_labels[0]
            
            # Find container
            container = label.locator('xpath=ancestor::div[@data-mcomponent="MContainer"]').first
            
            if container:
                html = container.inner_html()
                
                with open("ad_container_sample.html", "w", encoding="utf-8") as f:
                    f.write(html)
                
                print(f"✅ Saved ad HTML to ad_container_sample.html ({len(html):,} chars)")
            else:
                print("❌ No container found")


if __name__ == "__main__":
    save_ad_html()
