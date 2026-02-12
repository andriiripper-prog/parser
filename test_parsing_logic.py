
import sys
import os
from playwright.sync_api import sync_playwright

# Add current directory to path to import modules
sys.path.append(os.getcwd())

try:
    from mobile_story_extract import extract_ads_via_js
except ImportError as e:
    print(f"Error importing module: {e}")
    sys.exit(1)

def test_parsing():
    html_file = "mobile_debug.html"
    if not os.path.exists(html_file):
        print(f"File {html_file} not found!")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        
        print("Loading HTML into Playwright...")
        # Load the HTML file content
        # We use 'file://' scheme to load local file properly or just setContent
        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        page.set_content(content)
        
        print("Running JS Extraction (Debug Mode)...")
        # Run with debug_all_posts=True to treat all posts as ads
        ads = extract_ads_via_js(page, debug_all_posts=True)
        
        print(f"\nFound {len(ads)} posts/ads.")
        
        for i, ad in enumerate(ads):
            print(f"\n--- Post {i+1} ---")
            print(f"Label: {ad.get('ad_label')}")
            print(f"Text: {ad.get('text')[:100]}...") # show first 100 chars
            print(f"Link: {ad.get('link')}")
            print(f"Image: {ad.get('image_url')}")
            print(f"Video: {ad.get('video_url')}")
        
        browser.close()

if __name__ == "__main__":
    test_parsing()
