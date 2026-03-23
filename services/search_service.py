import os
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID", "")

async def _scrape_url(client: httpx.AsyncClient, link: str, title: str, snippet: str) -> dict:
    """Scrape a URL for content, extracting paragraphs and headings for richer context."""
    try:
        page_resp = await client.get(link, timeout=6.0, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (Research Bot)"})
        soup = BeautifulSoup(page_resp.text, "html.parser")
        # Extract headings for structure
        headings = [h.get_text(strip=True) for h in soup.find_all(['h1','h2','h3']) if h.get_text(strip=True)]
        # Extract paragraphs
        paragraphs = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 40]
        # Extract list items for bullet-heavy pages
        list_items = [li.get_text(strip=True) for li in soup.find_all('li') if len(li.get_text(strip=True)) > 20]

        heading_text = ' | '.join(headings[:8])
        body_text = ' '.join(paragraphs + list_items)
        combined = f"[Headings: {heading_text}]\n{body_text}" if heading_text else body_text
        
        # Extract images from <img> tags
        import urllib.parse
        scraped_images = []
        for img in soup.find_all('img'):
            src = img.get('src')
            if not src: continue
            
            # Convert relative to absolute
            abs_url = urllib.parse.urljoin(link, src)
            
            # Simple metadata extraction
            alt = img.get('alt', '').strip()
            # If alt is empty, try to get nearby sibling text or skip
            if not alt:
                continue
                
            # Filter obvious small icons/trackers (just heuristic check)
            if 'icon' in src.lower() or 'logo' in src.lower() or 'pixel' in src.lower():
                continue
                
            scraped_images.append({
                "url": abs_url,
                "title": alt or title
            })
            
        return {
            "url": link,
            "title": title,
            "snippet": snippet,
            "content": combined[:8000],   # 8k chars per source for richer context
            "scraped_images": scraped_images[:3] # Keep top 3 images from this page
        }
    except Exception as scrape_err:
        print(f"Scrape failed for {link}: {scrape_err}")
        return {"url": link, "title": title, "snippet": snippet, "content": snippet}

async def _search_duckduckgo(query: str, num_results: int) -> list[dict]:
    """
    Fallback search using DuckDuckGo Instant Answer API (no API key needed).
    Returns limited but usable results when Google is unavailable.
    """
    print("⚠️ Google Search failed — falling back to DuckDuckGo.")
    results = []
    try:
        async with httpx.AsyncClient() as client:
            params = {"q": query, "format": "json", "no_redirect": "1", "no_html": "1"}
            resp = await client.get("https://api.duckduckgo.com/", params=params, timeout=5.0)
            data = resp.json()

            # DuckDuckGo Instant Answer
            abstract = data.get("Abstract", "")
            abstract_url = data.get("AbstractURL", "")
            abstract_source = data.get("AbstractSource", "Unknown")

            if abstract and abstract_url:
                results.append({
                    "url": abstract_url,
                    "title": abstract_source,
                    "snippet": abstract,
                    "content": abstract
                })

            # Related topics
            for topic in data.get("RelatedTopics", [])[:num_results - 1]:
                if isinstance(topic, dict) and topic.get("FirstURL"):
                    results.append({
                        "url": topic.get("FirstURL"),
                        "title": topic.get("Text", "")[:80],
                        "snippet": topic.get("Text", ""),
                        "content": topic.get("Text", "")
                    })
    except Exception as e:
        print(f"DuckDuckGo fallback also failed: {e}")

    return results

async def search_google(query: str, num_results: int = 10) -> list[dict]:
    """
    Primary: Google Custom Search with full page scraping.
    Fallback 1: DuckDuckGo Instant Answers (no API key required).
    Fallback 2: Hardcoded mock (dev mode).
    """
    if not GOOGLE_API_KEY or not SEARCH_ENGINE_ID:
        # Development mock fallback if API keys are not provided
        print("⚠️ No Google API key — using dev mock fallback.")
        return [{
            "url": "https://example.com/mock",
            "title": f"Mock result for {query}",
            "snippet": f"This is a mocked snippet about {query}.",
            "content": f"Full body text about {query}. This data would normally be scraped from the URL."
        }]

    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": SEARCH_ENGINE_ID,
        "q": query,
        "num": num_results
    }

    results = []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=8.0)
            if response.status_code != 200:
                raise Exception(f"Google Search API returned {response.status_code}: {response.text[:200]}")

            data = response.json()
            items = data.get("items", [])

            if not items:
                raise Exception("Google returned 0 results.")

            # Scrape all pages concurrently for speed
            import asyncio
            scrape_tasks = [
                _scrape_url(client, item.get("link"), item.get("title"), item.get("snippet", ""))
                for item in items if item.get("link")
            ]
            results = await asyncio.gather(*scrape_tasks)

    except Exception as api_err:
        print(f"Google Search failed: {api_err}")
        # Fallback 1: DuckDuckGo
        results = await _search_duckduckgo(query, num_results)

    # Fallback 2: ensure we never return empty
    if not results:
        print("⚠️ All search providers failed — returning structured empty fallback.")
        results = [{
            "url": "https://fallback.local",
            "title": f"No live results for: {query}",
            "snippet": f"Search providers unavailable. Query: {query}",
            "content": f"Unable to retrieve live data for '{query}'. Please retry later."
        }]

    return results

async def fetch_images(query: str, num_results: int = 5) -> list[dict]:
    """
    Search for relevant images using Google Custom Search API.
    Returns: list of {"url": image_url, "title": image_title}
    """
    if not GOOGLE_API_KEY or not SEARCH_ENGINE_ID:
        return []

    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": SEARCH_ENGINE_ID,
        "q": query,
        "searchType": "image",
        "num": num_results,
        "safe": "active"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=6.0)
            if response.status_code != 200:
                print(f"Image search API returned {response.status_code}")
                return []
            
            data = response.json()
            items = data.get("items", [])
            return [
                {
                    "url": item.get("link"),
                    "title": item.get("title", "")
                }
                for item in items if item.get("link")
            ]
    except Exception as e:
        print(f"Image search failed: {e}")
        return []

def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
