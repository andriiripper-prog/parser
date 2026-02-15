# Image Upload Fix

## Problem
Images were not being sent to Telegram from the mobile parser. The issue was in the image selection logic in `telegram_client.py`.

## Root Cause
1. **JavaScript filtering (mobile_story_extract.py)** correctly extracts and filters images:
   - Filters out small images (< 250x250)
   - Filters out profile pictures and avatars
   - Filters out cropped images
   - Downloads the best quality images

2. **Media saved to disk** via `save_media_for_ad()` - these are the correctly filtered images

3. **Telegram sending** (telegram_client.py) had the wrong priority:
   - First called `resolve_saved_image_path()` which applies ADDITIONAL filtering
   - This function filters out cropped images and avatars AGAIN
   - Many valid images from JavaScript were rejected
   - Only fell back to `media_files` if resolve failed

## Solution
Reversed the priority order in `telegram_client.py`:

**Before (WRONG):**
```python
# First: resolve_saved_image_path (re-filters, may reject valid images)
img_path = resolve_saved_image_path(ad.get("image_urls") or [], cfg, ad_id)

# Fallback: use media_files (already filtered correctly by JS)
if media_files and not img_path:
    # ...find image in media_files
```

**After (FIXED):**
```python
# First: use media_files (already filtered correctly by JS)
if media_files:
    # ...find image in media_files
    
# Fallback: resolve_saved_image_path (for GraphQL ads)
if not img_path:
    img_path = resolve_saved_image_path(ad.get("image_urls") or [], cfg, ad_id)
```

## Result
- ✅ Images correctly filtered by JavaScript are now sent to Telegram
- ✅ No duplicate filtering that rejects valid images
- ✅ Fallback to resolve_saved functions still works for GraphQL ads
- ✅ Same fix applied to both images and videos

---

# Duplicate Ad Detection Fix

## Problem
Same ad was being sent to Telegram multiple times with different images.

## Root Cause
Facebook shows the same ad with different images for:
- **Carousel ads** - multiple images in one ad campaign
- **A/B testing** - testing different creatives for the same ad

The deduplication logic included image URLs in the signature:
- `mobile_main.py`: `sig = f"{page_name}|{text}|{media_sig}"`
- `telegram_client.py`: `parts = [page_name, text_hash, dest, post, img_hash]`

This caused the same ad with different images to be treated as different ads.

## Solution
Removed image URLs from deduplication signatures in both files:

**mobile_main.py:**
```python
# Before: sig = f"{page_name}|{text}|{media_sig}"
# After:
sig = f"{page_name}|{text}"
```

**telegram_client.py:**
```python
# Before: parts = [page_name, text_hash, dest, post, img_hash]
# After:
parts = [page_name, text_hash, dest, post]
```

## Result
- ✅ Same ad with different images is correctly identified as duplicate
- ✅ Only one version sent to Telegram (first occurrence)
- ✅ Deduplication based on content (page name + text + links) only

---

# Wrong Image Selection Fix

## Problem
Wrong images were being sent for ads - e.g., showing image from one ad with text from another ad.

## Root Cause
The image selection logic in `telegram_client.py` was searching for the **largest file** in `media_files`:

```python
best_img = None
for p in media_files:
    cand = (ip.stat().st_size, ip)
    if best_img is None or cand[0] > best_img[0]:
        best_img = cand
if best_img:
    img_path = best_img[1]
```

For mobile ads:
- JavaScript extracts **one correct image** per ad
- `media_files` should contain only files for the current ad
- But searching for "largest file" was unnecessary and could pick wrong image

## Solution
Simplified to use the **first valid image** from media_files:

```python
for p in media_files:
    if not sp.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        continue
    ip = Path(p)
    if not ip.exists() or ip.stat().st_size <= 0:
        continue
    # Found first valid image - use it
    img_path = ip
    break
```

## Result
- ✅ Correct image sent with correct ad text
- ✅ No searching for largest file (unnecessary for mobile ads)
- ✅ Simple, predictable behavior: use first valid image
