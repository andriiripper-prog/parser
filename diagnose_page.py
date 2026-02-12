"""
Detailed diagnostic for Facebook page loading issue
"""
import time
from playwright.sync_api import sync_playwright
from adspower import get_ws_url
from config import load_config


def diagnose():
    cfg = load_config()
    cfg["user_id"] = cfg["mobile_user_id"]
    
    ws = get_ws_url(cfg)
    if not ws:
        print("❌ AdsPower error")
        return
    
    with sync_playwright() as p:
        print("⏳ Connecting to AdsPower...")
        browser = p.chromium.connect_over_cdp(ws)
        
        print(f"\n📊 Browser contexts: {len(browser.contexts)}")
        context = browser.contexts[0]
        
        print(f"📄 Pages in context: {len(context.pages)}")
        
        for idx, pg in enumerate(context.pages):
            print(f"\n--- Page {idx} ---")
            print(f"  URL: {pg.url}")
            print(f"  Title: {pg.title()}")
            
            # Try to get inner text
            try:
                body_text = pg.inner_text('body')
                print(f"  Body text length: {len(body_text)}")
                if len(body_text) < 200:
                    print(f"  Body text: {body_text[:200]}")
            except Exception as e:
                print(f"  Error getting body text: {e}")
            
            # Try to find common elements
            try:
                divs = pg.locator('div').all()
                print(f"  Total <div> elements: {len(divs)}")
            except Exception as e:
                print(f"  Error counting divs: {e}")
            
            # Check for iframes
            try:
                frames = pg.frames
                print(f"  Frames: {len(frames)}")
                for f_idx, frame in enumerate(frames):
                    print(f"    Frame {f_idx}: {frame.url}")
            except Exception as e:
                print(f"  Error checking frames: {e}")
        
        # Try the main page
        page = context.pages[0] if context.pages else None
        if page:
            print("\n\n🔍 DETAILED CHECK ON MAIN PAGE:")
            print(f"URL: {page.url}")
            
            # Wait and scroll
            time.sleep(2)
            print("Scrolling...")
            page.evaluate("window.scrollTo(0, 500)")
            time.sleep(2)
            
            # Try different selectors
            selectors_to_try = [
                'div',
                'body',
                'span',
                '*[data-mcomponent]',
                'a',
            ]
            
            for sel in selectors_to_try:
                try:
                    elements = page.locator(sel).all()
                    print(f"  {sel}: {len(elements)} elements")
                    if len(elements) > 0 and len(elements) < 20:
                        for i, el in enumerate(elements[:3]):
                            try:
                                text = el.text_content()
                                if text and len(text) > 0:
                                    print(f"    [{i}]: {text[:50]}")
                            except:
                                pass
                except Exception as e:
                    print(f"  {sel}: Error - {e}")


if __name__ == "__main__":
    diagnose()
