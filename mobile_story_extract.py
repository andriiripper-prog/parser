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
                       .replace(/See more/gi, '')
                       .replace(/Learn more/gi, '')
                       .replace(/Shop now/gi, '')
                       .replace(/Sign up/gi, '')
                       .replace(/\\n+/g, ' ').replace(/\\s+/g, ' ').trim();
        }

        // --- Strategy 1: Mobile View (MContainer) ---
        try {
            const mobileContainers = document.querySelectorAll('div[data-mcomponent="MContainer"]');
            if (mobileContainers.length > 0) {
                console.log(`[JS] Found ${mobileContainers.length} Mobile MContainers`);
                for (const container of mobileContainers) {
                    try {
                        if (processedContainers.has(container)) continue;
                        
                        const containerText = container.innerText || "";
                        const hasSponsored = /Sponsored/i.test(containerText);
                        
                        // STRICT FILTER: If it doesn't say "Sponsored", it is NOT an ad.
                        if (!hasSponsored) continue;
                        
                        // NEW APPROACH: Look for individual post sub-containers within MContainer
                        // Try to find article, section, or div elements that represent individual posts
                        let postContainers = [];
                        
                        // Strategy A: Look for article tags (common in Facebook feed)
                        const articles = container.querySelectorAll('article');
                        if (articles.length > 0) {
                            console.log(`[JS] Found ${articles.length} article elements in MContainer`);
                            postContainers = Array.from(articles).filter(art => /Sponsored/i.test(art.innerText || ""));
                        }
                        
                        // Strategy B: If no articles, look for direct children with "Sponsored"
                        if (postContainers.length === 0) {
                            const directChildren = Array.from(container.children);
                            console.log(`[JS] Checking ${directChildren.length} direct children for "Sponsored"`);
                            postContainers = directChildren.filter(child => {
                                const text = child.innerText || "";
                                return /Sponsored/i.test(text) && text.length > 50; // Has content, not just label
                            });
                        }
                        
                        // Strategy C: Fallback - treat entire container as one post
                        if (postContainers.length === 0) {
                            console.log(`[JS] No sub-containers found, treating entire MContainer as one post`);
                            postContainers = [container];
                        }
                        
                        console.log(`[JS] Processing ${postContainers.length} post(s) from this MContainer`);
                        processedContainers.add(container);
                        
                        // Process each post container separately
                        for (const postContainer of postContainers) {
                            // Skip if we've already processed this exact element
                            if (postContainer !== container && processedContainers.has(postContainer)) {
                                continue;
                            }
                            if (postContainer !== container) {
                                processedContainers.add(postContainer);
                            }
                            
                            const adData = {
                                ad_label: "Sponsored",
                                page_name: "Unknown",
                                text: "",
                                link: "",
                                post_link: "",
                                image_urls: [],
                                video_url: ""
                            };

                            // Mobile Page Name
                            const textAreas = postContainer.querySelectorAll('div[data-mcomponent="TextArea"]');
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
                                 const links = postContainer.querySelectorAll('a');
                                 for (const link of links) {
                                    const t = (link.innerText || '').trim();
                                    if (t && t.length > 1 && !/^(sponsored|like|comment|share)$/i.test(t)) {
                                        adData.page_name = t;
                                        break;
                                    }
                                 }
                            }

                            // Mobile Text
                            let postText = "";
                            const allTextAreas = Array.from(postContainer.querySelectorAll('div[data-mcomponent="TextArea"]'));
                            
                            for (let i = 0; i < allTextAreas.length; i++) {
                                const textArea = allTextAreas[i];
                                const txt = (textArea.innerText || '').trim();
                                
                                if (txt === adData.page_name || txt.length < 10) continue;
                                if (/^(Like|Comment|Share|Sponsored|See more|Learn more)$/i.test(txt)) continue;
                                if (/^\d+\s*(Like|Comment|Share)s?$/i.test(txt)) continue;
                                
                                if (txt.length > postText.length) {
                                    postText = txt;
                                }
                            }
                            
                            adData.text = cleanText(postText).substring(0, 500);

                            // Mobile Images/Links - NOW USES postContainer, not full container
                            extractMediaAndLinks(postContainer, adData);
                            
                            console.log(`[JS] Extracted post: ${adData.page_name}, images: ${adData.image_urls.length}, link: ${adData.link ? 'yes' : 'no'}`);
                            
                            if (isValidAd(adData)) ads.push(adData);
                        }
                    } catch (err) {
                        console.error(`[JS] Error processing Mobile container: ${err.message}`);
                    }
                }
            }
        } catch (e) {
            console.error(`[JS] Strategy 1 Error: ${e.message}`);
        }

        // --- Strategy 2: Desktop View (role=article) ---
        try {
            const desktopArticles = document.querySelectorAll('div[role="article"], div[data-pagelet^="FeedUnit"]');
            if (desktopArticles.length > 0) {
                console.log(`[JS] Found ${desktopArticles.length} Desktop Articles/FeedUnits`);
                for (const container of desktopArticles) {
                    try {
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
                        // STRICT FILTER: If it doesn't say "Sponsored", it is NOT an ad.
                        if (!hasSponsoredLabel && !hasSponsoredText) continue;
                        
                        processedContainers.add(container);
                        
                         const adData = {
                            ad_label: (hasSponsoredLabel || hasSponsoredText) ? "Sponsored" : "Organic",
                            page_name: "Unknown",
                            text: "",
                            link: "",
                            post_link: "",
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
                    } catch (err) {
                         console.error(`[JS] Error processing Desktop container: ${err.message}`);
                    }
                }
            }
        } catch (e) {
             console.error(`[JS] Strategy 2 Error: ${e.message}`);
        }

        // --- Shared Helper: Media & Links ---
        function extractMediaAndLinks(container, adData) {
            try {
                // Collect ALL images with their metadata
                const images = container.querySelectorAll('img');
                const candidates = [];
                
                console.log(`[JS] Scanning ${images.length} images in container for ${adData.page_name}`);
                
                for (const img of images) {
                    let src = img.src || img.getAttribute('src') || '';
                    if (!src) continue;
                    
                    const w = img.naturalWidth || img.width || 0;
                    const h = img.naturalHeight || img.height || 0;
                    
                    // Safety Filter
                    if (!container.contains(img)) continue;
                    
                    // Size Filter: Skip very small images
                    if (w < 250 || h < 250) continue; 
                    
                    // URL Pattern Filter
                    if (/profile|avatar|picture|profile\.php/i.test(src)) continue;
                    if (/_n\.jpg/i.test(src) && (w < 300 || h < 300)) continue;
                    if (/svg|data:image|sprite|emoji|static\.xx\.fbcdn\.net/i.test(src)) continue;
                    
                    // Filter scontent patterns
                    if (/\/s\d+x\d+\//.test(src) || /\/p\d+x\d+\//.test(src)) {
                         if (/stp=c/i.test(src)) continue;
                    }
                    if (img.closest('div[role="banner"]')) continue;
                    
                    // Try to get high-res from srcset
                    const srcset = img.getAttribute('srcset');
                    if (srcset) {
                        const sources = srcset.split(',').map(s => s.trim().split(' '));
                        sources.sort((a, b) => (parseInt(b[1]) || 0) - (parseInt(a[1]) || 0));
                        if (sources[0] && sources[0][0]) src = sources[0][0];
                    }
                    
                    if (src && src.startsWith('http')) {
                        candidates.push({ src: src, area: w * h, w: w, h: h });
                        console.log(`[JS]   Candidate: ${w}x${h} = ${w*h} | ${src.substring(0, 80)}...`);
                    }
                }
                
                console.log(`[JS] Found ${candidates.length} valid image candidates`);
                
                // Sort by area descending
                candidates.sort((a, b) => b.area - a.area);
                
                // Take the largest image (we're now in a localized post container)
                if (candidates.length > 0) {
                    adData.image_urls = [candidates[0].src];
                    console.log(`[JS] Selected largest image: ${candidates[0].w}x${candidates[0].h}`);
                }

                // Videos
                const videos = container.querySelectorAll('video');
                for (const video of videos) {
                    if (!container.contains(video)) continue;
                    let src = video.src || video.getAttribute('src');
                    if (!src) {
                        const sources = video.querySelectorAll('source');
                        for (const source of sources) {
                             src = source.src || source.getAttribute('src');
                             if (src) break;
                        }
                    }
                    if (src && src.startsWith('http')) {
                         adData.video_url = src;
                         console.log(`[JS] Found video URL: ${src.substring(0, 80)}`);
                         break; // Take first video
                    }
                }

                // Links (Universal)
                const links = container.querySelectorAll('a[href]');
                let ctaLink = "";
                let postLink = "";
                
                console.log(`[JS] Found ${links.length} links in container`);

                for (const link of links) {
                    const href = link.getAttribute('href') || '';
                    const role = link.getAttribute('role');
                    const ariaLabel = (link.getAttribute('aria-label') || "").toLowerCase();
                    const text = (link.innerText || "").toLowerCase();
                    
                    // Debug: show first few links
                    if (links.length <= 10) {
                        console.log(`[JS] Link: ${href.substring(0, 80)}`);
                    }
                    
                    // 1. Post Link Detection (Permalink)
                    // Looks for /posts/, /videos/, /watch/, /story.php, or permlink structures
                    if (!postLink) {
                        if (href.includes('/posts/') || 
                            href.includes('/videos/') || 
                            href.includes('/watch/') || 
                            href.includes('story.php') ||
                            href.includes('/permalink.php') ||
                            (href.includes('/photo.php') && href.includes('&set='))) {
                            
                            // Avoid share/comment links if possible, but usually these are distinct
                            if (!href.includes('sharer') && !href.includes('comment')) {
                                // Convert relative URL to absolute
                                if (href.startsWith('/')) {
                                    postLink = window.location.origin + href;
                                } else if (href.startsWith('http')) {
                                    postLink = href;
                                } else {
                                    postLink = window.location.origin + '/' + href;
                                }
                                console.log(`[JS] Found post link: ${postLink.substring(0, 100)}`);
                            }
                        }
                    }

                    // 2. External Link (CTA) Detection
                    // Priority: CTA buttons
                    const isCTA = ariaLabel.includes('learn more') || ariaLabel.includes('shop now') || 
                                  ariaLabel.includes('sign up') || ariaLabel.includes('install') ||
                                  ariaLabel.includes('download') || ariaLabel.includes('apply now') ||
                                  ariaLabel.includes('subscribe') || ariaLabel.includes('book now') ||
                                  ariaLabel.includes('contact us') || ariaLabel.includes('get quote') ||
                                  ariaLabel.includes('watch more') ||
                                  text.includes('learn more') || text.includes('shop now') ||
                                  text.includes('sign up') || text.includes('install') ||
                                  text.includes('download');

                    if (isCTA && !ctaLink) {
                        // Handle both l.facebook.com and lm.facebook.com (mobile)
                        if (href.includes('l.facebook.com/l.php') || href.includes('lm.facebook.com/l.php')) {
                            try {
                                const url = new URL(href, window.location.origin);
                                const targetUrl = url.searchParams.get('u');
                                if (targetUrl) {
                                    ctaLink = decodeURIComponent(targetUrl);
                                }
                            } catch (e) {}
                        }
                        // Handle relative /l.php paths
                        if (!ctaLink && href.includes('/l.php?u=')) {
                            try {
                                const fullUrl = href.startsWith('http') ? href : window.location.origin + href;
                                const url = new URL(fullUrl);
                                const targetUrl = url.searchParams.get('u');
                                if (targetUrl) {
                                    ctaLink = decodeURIComponent(targetUrl);
                                }
                            } catch (e) {}
                        }
                        if (!ctaLink && href.startsWith('http') && !href.includes('facebook.com')) {
                            ctaLink = href;
                        }
                        if (!ctaLink && (href.includes('l.facebook.com') || href.includes('lm.facebook.com'))) { 
                            // Convert to absolute if relative
                            ctaLink = href.startsWith('http') ? href : window.location.origin + href;
                        }
                    }
                }

                // Fallback for External Link if no clear CTA found
                if (!ctaLink) {
                    for (const link of links) {
                        const href = link.getAttribute('href') || '';
                        if (href === postLink) continue; // Skip post link
                        if (href.includes('facebook.com') && !href.includes('l.facebook.com') && !href.includes('lm.facebook.com')) continue; 
                        
                        // 1. Redirects (both l.facebook.com and lm.facebook.com)
                        if (href.includes('l.facebook.com/l.php') || href.includes('lm.facebook.com/l.php') || href.includes('/l.php?u=')) {
                            try {
                                const fullUrl = href.startsWith('http') ? href : window.location.origin + href;
                                const url = new URL(fullUrl);
                                const targetUrl = url.searchParams.get('u');
                                if (targetUrl) {
                                    ctaLink = decodeURIComponent(targetUrl);
                                    break;
                                }
                            } catch (e) {}
                            // If we couldn't decode, keep the redirect URL
                            if (!ctaLink) {
                                ctaLink = href.startsWith('http') ? href : window.location.origin + href;
                                break;
                            }
                        }
                        // 2. Direct external
                        if (href.startsWith('http') && !href.includes('facebook.com')) {
                            ctaLink = href;
                            break;
                        }
                         // Fallback to raw redirect
                        if (href.includes('l.facebook.com') || href.includes('lm.facebook.com')) {
                            ctaLink = href.startsWith('http') ? href : window.location.origin + href;
                            break; 
                        }
                    }
                }

                // 3. SPECIAL: Display Link (domain text below image)
                // <div data-mcomponent="TextArea"> ... <span class="f5">marketika.co</span> ... </div>
                if (!ctaLink) {
                     const textAreas = container.querySelectorAll('div[data-mcomponent="TextArea"] span.f5');
                     for (const span of textAreas) {
                         const txt = (span.innerText || "").trim();
                         // Check if it looks like a domain using simple regex
                         if (/^[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(\/[a-zA-Z0-9-._]+)*$/.test(txt)) {
                             ctaLink = txt; // Treat as link
                             break;
                         }
                     }
                }
                
                adData.link = ctaLink;
                adData.post_link = postLink;
            } catch (e) {
                console.error(`[JS] Error in extractMediaAndLinks: ${e.message}`);
                adData.error = e.message;
            }
        }

        // --- Helper: Validity Check ---
        function isValidAd(ad) {
            // Require meaningful content: link OR image/video
            const hasLink = ad.link && ad.link.length > 0;
            const hasPostLink = ad.post_link && ad.post_link.length > 0;
            const hasImage = ad.image_urls && ad.image_urls.length > 0;
            const hasVideo = ad.video_url && ad.video_url.length > 0;
            
            // Must have SOME content (link or media)
            if (!hasLink && !hasPostLink && !hasImage && !hasVideo) {
                return false;
            }
            
            // Must also have identification (page name OR meaningful text)
            const hasPageName = ad.page_name && ad.page_name !== "Unknown";
            const hasMeaningfulText = ad.text && ad.text.length > 20;
            
            return (hasPageName || hasMeaningfulText);
        }
        
        console.log(`[JS] Total found: ${ads.length}`);
        return ads;
    }""", [debug_all_posts])
