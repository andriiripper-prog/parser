import re
from urllib.parse import parse_qs, urlparse, unquote

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def is_facebookish(url: str) -> bool:
    if not url:
        return False
    ul = url.lower()
    return any(x in ul for x in ("facebook.com", "l.facebook.com", "fb.com", "m.facebook.com"))


def decode_facebook_redirect(u: str) -> str:
    try:
        if not u:
            return ""
        pu = urlparse(u)
        if "l.facebook.com" not in (pu.netloc or ""):
            return ""
        qs = parse_qs(pu.query)
        real = (qs.get("u") or [""])[0]
        real = unquote(real)
        return real if real.startswith("http") else ""
    except Exception:
        return ""


def resolve_lphp_to_external_url(context, lphp_url: str, timeout_ms: int = 20000) -> dict:
    out = {"visited_url": lphp_url or "", "external_url": "", "status": "error", "error": ""}

    if not lphp_url or not lphp_url.startswith("http"):
        out["error"] = "bad_lphp_url"
        return out

    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.facebook.com/",
        }
        r = requests.get(lphp_url, headers=headers, timeout=15, allow_redirects=True)
        final = (getattr(r, "url", "") or "").strip()
        if final and final.startswith("http") and not is_facebookish(final):
            out["external_url"] = final
            out["status"] = "ok_requests"
            return out
    except Exception as e:
        out["error"] = f"requests_failed:{str(e)[:120]}"

    page2 = None
    try:
        page2 = context.new_page()
        page2.goto(lphp_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page2.wait_for_timeout(1200)
        try:
            page2.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        cur = (page2.url or "").strip()
        if cur and cur.startswith("http") and not is_facebookish(cur):
            out["external_url"] = cur
            out["status"] = "ok_pw_redirect"
            return out

        for label in ("Continue", "Продолжить", "Proceed", "Open Link", "Открыть ссылку"):
            try:
                btn = page2.get_by_role("button", name=label)
                if btn:
                    btn.click(timeout=1200)
                    page2.wait_for_timeout(1200)
                    try:
                        page2.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                    cur2 = (page2.url or "").strip()
                    if cur2 and cur2.startswith("http") and not is_facebookish(cur2):
                        out["external_url"] = cur2
                        out["status"] = "ok_pw_button"
                        return out
            except Exception:
                pass

        try:
            hrefs = page2.eval_on_selector_all("a[href]", "els => els.map(e => e.href).filter(Boolean)")
            if isinstance(hrefs, list):
                for h in hrefs:
                    h = (h or "").strip()
                    if h.startswith("http") and (not is_facebookish(h)):
                        out["external_url"] = h
                        out["status"] = "ok_pw_href"
                        return out
        except Exception:
            pass

        try:
            content = page2.content()
            m = re.search(r'https?://[^\s\"<>]+', content or "")
            if m:
                cand = m.group(0).strip()
                if cand.startswith("http") and not is_facebookish(cand):
                    out["external_url"] = cand
                    out["status"] = "ok_pw_html"
                    return out
        except Exception:
            pass

        out["status"] = "pw_no_external"
        out["error"] = "no_external_found"
        return out

    except PlaywrightTimeoutError:
        out["status"] = "timeout"
        out["error"] = "pw_timeout"
        return out
    except Exception as e:
        out["status"] = "error"
        out["error"] = f"pw_error:{str(e)[:180]}"
        return out
    finally:
        try:
            if page2 and not page2.is_closed():
                page2.close()
        except Exception:
            pass


def pick_post_page_redirect(urls: list[str]):
    post_url = ""
    page_url = ""
    redirect_url = ""

    for u in urls or []:
        ul = (u or "").lower()
        if "l.facebook.com/l.php" in ul or ("l.facebook.com" in ul and "u=" in ul):
            if not redirect_url:
                redirect_url = u
            continue
        if "facebook.com" in ul:
            if any(x in ul for x in ("/posts/", "/videos/", "/reel/", "/photo", "story.php", "permalink")):
                if not post_url:
                    post_url = u
                continue
            try:
                p = urlparse(u)
                path = p.path.strip("/")
                if path and not any(
                    seg in path for seg in ("posts", "videos", "reel", "photo", "story.php", "groups", "events", "watch")
                ):
                    if "/" not in path or path.startswith("profile.php"):
                        if not page_url:
                            page_url = u
            except Exception:
                pass
            continue
        if not redirect_url:
            redirect_url = u

    external_url = ""
    if redirect_url:
        external_url = decode_facebook_redirect(redirect_url)
        if not external_url and redirect_url.startswith("http") and not is_facebookish(redirect_url):
            external_url = redirect_url
    return post_url, page_url, redirect_url, external_url


def pick_target_link_for_visit(ad: dict) -> str:
    urls = ad.get("urls") or []
    post_url, _page_url, redirect_url, external_url = pick_post_page_redirect(urls)

    if external_url and external_url.startswith("http"):
        return external_url
    if redirect_url and redirect_url.startswith("http"):
        return redirect_url
    for u in urls:
        if isinstance(u, str) and u.startswith("http") and not is_facebookish(u):
            return u
    if post_url and post_url.startswith("http"):
        return post_url
    return ""
