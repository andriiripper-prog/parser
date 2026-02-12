"""Extract ads from mobile/desktop Facebook DOM"""

def extract_ads_via_js(page, debug_all_posts=False):
    """
    Universal ad extractor for Facebook.
    Supports both Mobile view (MContainer) and Desktop view (role=article).
    """
    return page.evaluate("""([debugAll]) => {
        const ADS_DEBUG_ALL = debugAll;
        const ads = [];
        const processedContainers = new Set();
        
        console.log(`[JS] Starting universal ad extraction. Debug mode: ${ADS_DEBUG_ALL}`);

        // --- Helper: Clean text ---
        function cleanText(text) {
            if (!text) return "";
            return text.replace(/Sponsored/gi, '')
                       .replace(/Like\\s*·\\s*Comment\\s*·\\s*Share/gi, '')
                       .replace(/\\d+\\s*(min|mins|hour|hours|day|days|h|m|d|w)\\s*(ago)?/gi, '')
                       .replace(/\\d+\\s*Like[s]?/gi, '')
                       .replace(/\\d+\\s*Comment[s]?/gi, '')
                       .replace(/\\d+\\s*Share[s]?/gi, '')
                       .replace(/\\n+/g, ' ').replace(/\\s+/g, ' ').trim();
        }

        // --- Strategy 1: Mobile View (MContainer) ---
        const mobileContainers = document.querySelectorAll('div[data-mcomponent="MContainer"]');
        if (mobileContainers.length > 0) {
            console.log(`[JS] Found ${mobileContainers.length} Mobile MContainers`);
            for (const container of mobileContainers) {
                if (processedContainers.has(container)) continue;
                
                const containerText = container.innerText || "";
                const hasSponsored = /Sponsored/i.test(containerText);
                
                if (!hasSponsored && !ADS_DEBUG_ALL) continue;
                processedContainers.add(container);

                const adData = {
                    ad_label: hasSponsored ? "Sponsored" : "Organic",
                    page_name: "Unknown",
                    text: "",
                    link: "",
                    image_urls: [],
                    video_url: ""
                };

                // Mobile Page Name
                const textAreas = container.querySelectorAll('div[data-mcomponent="TextArea"]');
                for (const textArea of textAreas) {
                    const spans = textArea.querySelectorAll('span[data-action-id], span.rtl-ignore');
                    for (const span of spans) {
                        const text = (span.innerText || '').trim();
                        if (text && text.length > 1 && text.length < 100 && !/^sponsored$/i.test(text)) {
                            adData.page_name = text;
                            break;
                        }
                    }
                    if (adData.page_name !== "Unknown") break;
                }
                
                if (adData.page_name === "Unknown") {
                     const links = container.querySelectorAll('a');
                     for (const link of links) {
                        const t = (link.innerText || '').trim();
                        if (t && t.length > 1 && !/^(sponsored|like|comment|share)$/i.test(t)) {
                            adData.page_name = t;
                            break;
                        }
                     }
                }

                // Mobile Text
                let txt = containerText;
                if (adData.page_name !== "Unknown") txt = txt.replace(adData.page_name, '');
                adData.text = cleanText(txt).substring(0, 500);

                // Mobile Images/Links
                extractMediaAndLinks(container, adData);
                
                if (isValidAd(adData)) ads.push(adData);
            }
        }

        // --- Strategy 2: Desktop View (role=article) ---
        const desktopArticles = document.querySelectorAll('div[role="article"], div[data-pagelet^="FeedUnit"]');
        if (desktopArticles.length > 0) {
            console.log(`[JS] Found ${desktopArticles.length} Desktop Articles/FeedUnits`);
            for (const container of desktopArticles) {
                if (processedContainers.has(container)) continue;
                
                // Check specific desktop sponsored indicators
                const hasSponsoredLabel = Array.from(container.querySelectorAll('span, a, div'))
                    .some(el => {
                        const style = window.getComputedStyle(el);
                        return (el.innerText === 'Sponsored' && style.display !== 'none') ||
                               (el.getAttribute('aria-label') === 'Sponsored');
                    });
                
                const containerText = container.innerText || "";
                const hasSponsoredText = /Sponsored/i.test(containerText);
                
                // Stricter check for desktop to avoid false positives
                if (!hasSponsoredLabel && !hasSponsoredText && !ADS_DEBUG_ALL) continue;
                
                processedContainers.add(container);
                
                 const adData = {
                    ad_label: (hasSponsoredLabel || hasSponsoredText) ? "Sponsored" : "Organic",
                    page_name: "Unknown",
                    text: "",
                    link: "",
                    image_urls: [],
                    video_url: ""
                };

                // Desktop Page Name (usually top bold text or h2/h3/h4)
                const strongTags = container.querySelectorAll('strong, h2, h3, h4, span.nc684nl6'); 
                for (const el of strongTags) {
                    const t = (el.innerText || '').trim();
                    if (t && t.length > 1 && t.length < 100 && !/^sponsored$/i.test(t)) {
                        adData.page_name = t;
                        break;
                    }
                }

                // Desktop Text
                let txt = containerText;
                if (adData.page_name !== "Unknown") txt = txt.replace(adData.page_name, '');
                adData.text = cleanText(txt).substring(0, 500);

                 // Desktop Images/Links
                extractMediaAndLinks(container, adData);

                if (isValidAd(adData)) ads.push(adData);
            }
        }

        // --- Shared Helper: Media & Links ---
        function extractMediaAndLinks(container, adData) {
            // Images
            const images = container.querySelectorAll('img');
            const imageUrls = [];
            for (const img of images) {
                let src = img.src || img.getAttribute('src') || '';
                if (img.width < 50 || img.height < 50) continue;
                if (/profile|emoji|static/i.test(src)) continue;
                
                // Try to get high-res from srcset
                 const srcset = img.getAttribute('srcset');
                if (srcset) {
                    const sources = srcset.split(',').map(s => s.trim().split(' '));
                    sources.sort((a, b) => (parseInt(b[1]) || 0) - (parseInt(a[1]) || 0));
                    if (sources[0] && sources[0][0]) src = sources[0][0];
                }
                if (src && src.startsWith('http')) imageUrls.push(src);
            }
            if (imageUrls.length > 0) adData.image_urls = [imageUrls[0]]; // Take best one

            // Links (Universal)
            const links = container.querySelectorAll('a[href]');
            for (const link of links) {
                const href = link.getAttribute('href') || '';
                // 1. Redirects
                if (href.includes('l.facebook.com/l.php')) {
                    try {
                        const url = new URL(href, window.location.origin);
                        const targetUrl = url.searchParams.get('u');
                        if (targetUrl) {
                            adData.link = decodeURIComponent(targetUrl);
                            break;
                        }
                    } catch (e) {}
                }
                // 2. Direct external
                if (href.startsWith('http') && !href.includes('facebook.com')) {
                    adData.link = href;
                    break;
                }
            }
            // Fallback to raw redirect
             if (!adData.link) {
                for (const link of links) {
                    const href = link.getAttribute('href') || '';
                    if (href.includes('l.facebook.com')) {
                        adData.link = href;
                        break;
                    }
                }
            }
        }

        // --- Helper: Validity Check ---
        function isValidAd(ad) {
            return (ad.page_name !== "Unknown" || ad.text.length > 10 || ad.image_urls.length > 0 || ad.link);
        }
        
        console.log(`[JS] Total found: ${ads.length}`);
        return ads;
    }""", [debug_all_posts])
