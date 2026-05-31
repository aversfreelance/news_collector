import json, re, hashlib, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from difflib import SequenceMatcher

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "city_sources.json"
DRAFT = ROOT / "public" / "import" / "pest-megye-news-draft.json"
MAX_PER_CITY = 5
HEADERS = {"User-Agent": "Mozilla/5.0 PestMegyeiHirlapBot/1.0"}

BAD_IMAGE_WORDS = ["logo", "favicon", "sprite", "placeholder", "avatar", "icon"]


def norm(text):
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).lower().strip()
    return re.sub(r"\s+", " ", text)


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", norm(text)).strip("-")[:90]


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"FETCH ERROR {url}: {e}")
        return ""


def is_probably_image(url):
    if not url or not url.startswith("http"):
        return False
    low = url.lower()
    if any(w in low for w in BAD_IMAGE_WORDS):
        return False
    return any(ext in low for ext in [".jpg", ".jpeg", ".png", ".webp"])


def clean_img_url(src, base):
    if not src:
        return ""
    src = src.strip()
    if src.startswith("data:"):
        return ""
    if "," in src and " " in src:
        # srcset: pick first URL
        src = src.split(",")[0].strip().split(" ")[0]
    return urljoin(base, src)


def extract_image(soup, page_url):
    selectors = [
        ("meta", {"property": "og:image"}, "content"),
        ("meta", {"name": "twitter:image"}, "content"),
        ("meta", {"property": "og:image:secure_url"}, "content"),
    ]
    for tag, attrs, attr in selectors:
        el = soup.find(tag, attrs=attrs)
        img = clean_img_url(el.get(attr), page_url) if el else ""
        if is_probably_image(img):
            return img
    for css in ["article img", ".post img", ".entry-content img", ".content img", "main img", "img"]:
        for img_tag in soup.select(css):
            for attr in ["src", "data-src", "data-lazy-src", "data-original", "data-image", "srcset", "data-srcset"]:
                img = clean_img_url(img_tag.get(attr), page_url)
                if is_probably_image(img):
                    return img
    return ""


def extract_text(soup):
    article = soup.find("article") or soup.find("main") or soup.body
    if not article:
        return ""
    for bad in article.select("script, style, nav, header, footer, form, aside"):
        bad.decompose()
    paras = [p.get_text(" ", strip=True) for p in article.find_all(["p", "li"]) ]
    paras = [p for p in paras if len(p) > 45]
    return "\n\n".join(paras[:8])


def rewrite_light(text, max_len=900):
    # Nem AI-átírás; biztonságos tesztimporthoz rövid, forráshű összefoglaló.
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


def find_candidate_links(city):
    html = fetch(city["news_url"])
    soup = BeautifulSoup(html, "lxml")
    links = []
    base_host = urlparse(city["home_url"]).netloc.replace("www.", "")
    for a in soup.find_all("a", href=True):
        title = a.get_text(" ", strip=True)
        href = urljoin(city["news_url"], a["href"])
        host = urlparse(href).netloc.replace("www.", "")
        if host and base_host not in host:
            continue
        if len(title) < 12:
            continue
        low = href.lower()
        if any(x in low for x in ["#", "mailto:", "tel:", "facebook.com", "login", "kereses"]):
            continue
        if href not in [x["url"] for x in links]:
            links.append({"title": title, "url": href})
    return links[:25]


def collect_city(city):
    out = []
    for cand in find_candidate_links(city):
        html = fetch(cand["url"])
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        h1 = soup.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else cand["title"]
        body_raw = extract_text(soup)
        if len(body_raw) < 80:
            continue
        image = extract_image(soup, cand["url"])
        excerpt = rewrite_light(body_raw, 180)
        body = rewrite_light(body_raw, 1200) + f"\n\nForrás: {cand['url']}"
        out.append({
            "title": title,
            "category": "kozelet",
            "city": city["city"],
            "excerpt": excerpt,
            "body": body,
            "author": "Pest Megyei Hírlap",
            "url": cand["url"],
            "date": datetime.now(timezone.utc).date().isoformat(),
            "image": image if is_probably_image(image) else "",
            "slug": slugify(f"{city['city']} {title}")
        })
        if len(out) >= MAX_PER_CITY:
            break
    return out


def similar(a,b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def dedupe(items):
    result = []
    seen_urls, seen_titles = set(), []
    for item in items:
        url = item.get("url", "").split("?")[0].rstrip("/")
        title = item.get("title", "")
        tnorm = norm(title)
        if not tnorm or url in seen_urls:
            continue
        if tnorm in seen_titles:
            continue
        if any(similar(title, old) >= 0.88 for old in seen_titles):
            continue
        seen_urls.add(url)
        seen_titles.append(tnorm)
        result.append(item)
    return result


def main():
    cities = json.loads(CONFIG.read_text(encoding="utf-8"))
    all_items = []
    for city in cities:
        print("Collecting", city["city"])
        all_items.extend(collect_city(city))
    final = dedupe(all_items)
    DRAFT.parent.mkdir(parents=True, exist_ok=True)
    DRAFT.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(final)} items to {DRAFT}")

if __name__ == "__main__":
    main()
