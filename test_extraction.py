
import sys
import re

def analyze(html_path):
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            html = f.read()
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    print(f"File size: {len(html)} bytes")

    # 1. Broad search for "Sponsored" or variations
    print("\n--- Searching for 'Sponsored' keywords ---")
    keywords = ["Sponsored", "Реклама", "Ad", "Gesponsert", "Publicité"]
    for kw in keywords:
        matches = [m.start() for m in re.finditer(re.escape(kw), html, re.IGNORECASE)]
        print(f"'{kw}': found {len(matches)} matches")
        for pos in matches[:5]: # Show context for first 5 matches
            start = max(0, pos - 50)
            end = min(len(html), pos + 50)
            print(f"  Context: ...{html[start:end].replace(chr(10), ' ')}...")

    # 2. Find Profile Headers to see structure
    print("\n--- Searching for Profile Headers ---")
    # Pattern: aria-label="Name Profile Picture"
    # Regex to capture the name
    pattern = re.compile(r'aria-label="([^"]+) Profile Picture"')
    
    matches = pattern.finditer(html)
    count = 0
    for match in matches:
        count += 1
        name = match.group(1)
        print(f"\n[Post {count}] Name: {name}")
        
        # Look at the text immediately following this match to find the "sub-text" or label
        # The structure in previous dump showed name and then date/label nearby.
        
        pos = match.end()
        # Grab a chunk of text after the profile picture
        chunk = html[pos:pos+1000]
        
        # Try to find visible text in this chunk (very rough approximation)
        # We look for >TEXT< patterns
        text_matches = re.findall(r'>([^<]+)<', chunk)
        visible_text = [t.strip() for t in text_matches if t.strip()]
        
        print(f"  Nearby Visible Text: {visible_text[:10]}")
        
        # specific check for our keywords in this nearby text
        for kw in keywords:
            for t in visible_text:
                if kw.lower() in t.lower():
                     print(f"  !!! SUSPECTED AD LABEL: '{t}' matches '{kw}' !!!")

    if count == 0:
        print("No profile headers found via regex.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 test_extraction.py <html_file>")
        sys.exit(1)
    analyze(sys.argv[1])
