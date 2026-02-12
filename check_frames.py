"""
Check if Facebook content is inside an iframe
"""
import time
from playwright.sync_api import sync_playwright
from adspower import get_ws_url
from config import load_config


def check_frames():
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
        
        print(f"📄 Page URL: {page.url}")
        print(f"📄 Page Title: {page.title()}")
        
        # Wait a bit
        time.sleep(3)
        
        # Check main frame
        main_frame = page.main_frame
        print(f"\n🖼️  MAIN FRAME:")
        print(f"  URL: {main_frame.url}")
        
        try:
            divs_main = main_frame.locator('div').all()
            print(f"  Divs in main frame: {len(divs_main)}")
        except Exception as e:
            print(f"  Error: {e}")
        
        # Check all frames
        frames = page.frames
        print(f"\n🖼️  ALL FRAMES ({len(frames)} total):")
        
        for idx, frame in enumerate(frames):
            print(f"\n  --- Frame {idx} ---")
            print(f"  URL: {frame.url}")
            print(f"  Name: {frame.name}")
            
            try:
                # Count elements
                divs = frame.locator('div').all()
                spans = frame.locator('span').all()
                mcontainers = frame.locator('[data-mcomponent="MContainer"]').all()
                
                print(f"  <div>: {len(divs)}")
                print(f"  <span>: {len(spans)}")
                print(f"  MContainers: {len(mcontainers)}")
                
                # Try to get some text
                if len(divs) > 0:
                    body_text = frame.inner_text('body')
                    print(f"  Body text length: {len(body_text)}")
                    
                    # Check for "Sponsored"
                    if "Sponsored" in body_text:
                        print(f"  ✅ Found 'Sponsored' in frame {idx}!")
                        count = body_text.count("Sponsored")
                        print(f"     Occurrences: {count}")
                    
                    # Sample text
                    if len(body_text) > 100:
                        print(f"  Sample text: {body_text[:200]}")
                        
            except Exception as e:
                print(f"  Error checking frame: {e}")


if __name__ == "__main__":
    check_frames()
