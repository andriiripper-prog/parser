"""
Simple script to dump m.facebook.com page HTML for analysis
"""
import time
from playwright.sync_api import sync_playwright
from adspower import get_ws_url
from config import load_config


def dump_page():
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
        
        print(f"📄 Current page: {page.url}")
        
        # Wait a bit for content to load
        print("⏳ Waiting for content...")
        time.sleep(5)
        
        # Get full page HTML
        html = page.content()
        
        # Save to file
        output_file = "facebook_mobile_page.html"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html)
        
        print(f"✅ Saved HTML to {output_file}")
        print(f"📊 HTML size: {len(html)} characters")
        
        # Also check for "Sponsored" in the text
        if "Sponsored" in html:
            print("✅ Found 'Sponsored' in HTML")
            count = html.count("Sponsored")
            print(f"   Found {count} occurrences")
        else:
            print("❌ 'Sponsored' NOT found in HTML")
        
        if "Ad" in html:
            print("✅ Found 'Ad' in HTML")
            # Count only as whole word to avoid false positives
            import re
            matches = re.findall(r'\bAd\b', html)
            print(f"   Found {len(matches)} occurrences")


if __name__ == "__main__":
    dump_page()
