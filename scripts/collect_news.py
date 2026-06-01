import json, re, unicodedata
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
HEADERS = {
    "User-Agent": "Mozilla/5.0 PestMegyeiHirlapBot/1.0 (+https://pestmegyeihirlap.hu)"
}

BAD_IMAGE_WORDS = [
    "logo", "favicon", "sprite", "placeholder", "avatar", "icon",
    "banner", "ads", "advert", "reklam"
]

BAD_TITLE_WORDS = [
    "ügyfélfogadás",
    "nyitvatartás",
    "álláshirdetés",
    "álláslehetőség",
    "pályázati felhívás",
    "közbeszerzés",
    "hirdetmény",
    "meghívó",
    "napirend",
    "jegyzőkönyv",
    "rendelet",
    "határozat",
    "áramszünet",
    "testületi ülés",
    "képviselő-testület",
    "pályázat",
    "beszerzés",
    "adatvédelmi",
    "impresszum",
    "kapcsolat",
    "közérdekű adatok",
    "adatkezelés",
    "szervezeti egység",
    "üvegzseb",
    "letölthető",
    "nyomtatvány",
    "fogadóóra"
]

BAD_URL_WORDS = [
    "kapcsolat",
    "impresszum",
    "adatvedelem",
    "adatkezeles",
    "kozbeszerzes",
    "allashirdetes",
    "hirdetmeny",
    "jegyzokonyv",
    "rendelet",
    "hatarozat",
    "palyazat",
    "testuleti",
    "napirend",
    "ugyfelfogadas",
    "nyitvatartas",
    "kozerdeku",
    "uvegzseb",
    "letoltes",
    "letoltheto",
    "nyomtatvany",
    "szervezeti",
    "login",
    "search",
    "kereses",
    "sitemap"
]

GOOD_URL_WORDS = [
    "hir",
    "hirek",
    "aktualis",
    "aktualitas",
    "cikk",
    "news",
    "bejegyzes",
    "post"
]


def norm(text):
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).lower().strip()
    return re.sub(r"\s+", " ", text)


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", norm(text)).strip("-")[:90]


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
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

    if any(ext in low for ext in [".jpg", ".jpeg", ".png", ".webp"]):
        return True

    # Some CMS image handlers serve images without an extension.
    if any(marker in low for marker in ["/image/", "image=", "kep=", "picture=", "photo=", "media/"]):
        return True

    return False


def clean_img_url(src, base):
    if not src:
        return ""

    src = src.strip()

    if src.startswith("data:"):
        return ""

    # srcset: pick the first usable URL
    if "," in src and " " in src:
        src = src.split(",")[0].strip().split(" ")[0]

    return urljoin(base, src)


def extract_image(soup, page_url):
    selectors = [
        ("meta", {"property": "og:image"}, "content"),
        ("meta", {"name": "twitter:image"}, "content"),
        ("meta", {"property": "og:image:secure_url"}, "content"),
        ("meta", {"itemprop": "image"}, "content"),
        ("link", {"rel": "image_src"}, "href"),
    ]

    for tag, attrs, attr in selectors:
        el = soup.find(tag, attrs=attrs)
        img = clean_img_url(el.get(attr), page_url) if el else ""
        if is_probably_image(img):
            return img

    css_candidates = [
        "article img",
        ".post img",
        ".entry-content img",
        ".content img",
        ".news img",
        ".article img",
        ".single img",
        ".main-content img",
        "main img",
        "img"
    ]

    image_attrs = [
        "src",
        "data-src",
        "data-lazy-src",
        "data-original",
        "data-image",
        "data-full",
        "srcset",
        "data-srcset"
    ]

    for css in css_candidates:
        for img_tag in soup.select(css):
            for attr in image_attrs:
                img = clean_img_url(img_tag.get(attr), page_url)
                if is_probably_image(img):
                    return img

    return ""


def extract_text(soup):
    article = (
        soup.find("article")
        or soup.select_one(".entry-content")
        or soup.select_one(".post-content")
        or soup.select_one(".article-content")
        or soup.select_one(".news-content")
        or soup.select_one(".content")
        or soup.find("main")
        or soup.body
    )

    if not article:
        return ""

    for bad in article.select(
        "script, style, nav, header, footer, form, aside, "
        ".menu, .nav, .breadcrumb, .breadcrumbs, .share, .social, "
        ".cookie, .comments, .comment, .related, .sidebar"
    ):
        bad.decompose()

    paras = []
    for el in article.find_all(["p", "li"]):
        txt = el.get_text(" ", strip=True)
        txt = re.sub(r"\s+", " ", txt).strip()

        if len(txt) < 45:
            continue

        low = txt.lower()
        if any(skip in low for skip in [
            "cookie",
            "süti",
            "adatkezelés",
            "facebook",
            "megosztás",
            "tovább olvasom",
            "kapcsolódó cikk",
            "vissza a hírekhez"
        ]):
            continue

        paras.append(txt)

    # Keep substantially more text than before.
    return "\n\n".join(paras[:30])


def rewrite_light(text, max_len=900):
    # Nem AI-átírás; biztonságos tesztimporthoz rövid, forráshű összefoglaló.
    text = re.sub(r"\s+", " ", text or "").strip()

    if len(text) <= max_len:
        return text

    return text[:max_len].rsplit(" ", 1)[0] + "..."


def is_probably_news(title, url, body):
    title_l = (title or "").lower().strip()
    url_l = (url or "").lower().strip()
    body_l = (body or "").lower().strip()

    if not title_l or not url_l:
        return False

    if len(title_l) < 15:
        return False

    if len(body_l) < 300:
        return False

    if title_l in ["hírek", "aktuális", "aktualitások", "közélet", "önkormányzat"]:
        return False

    if any(bad in title_l for bad in BAD_TITLE_WORDS):
        return False

    if any(bad in url_l for bad in BAD_URL_WORDS):
        return False

    # Very menu-like titles are usually not articles.
    if len(title_l.split()) <= 2 and not any(good in url_l for good in GOOD_URL_WORDS):
        return False

    return True


def looks_like_news_link(title, href, city):
    title_l = (title or "").lower().strip()
    href_l = (href or "").lower().strip()

    if len(title_l) < 12:
        return False

    if any(x in href_l for x in ["#", "mailto:", "tel:", "facebook.com", "javascript:"]):
        return False

    if any(bad in title_l for bad in BAD_TITLE_WORDS):
        return False

    if any(bad in href_l for bad in BAD_URL_WORDS):
        return False

    # Prefer URLs that look like news articles, but do not require it,
    # because many Hungarian municipal websites use custom routing.
    return True


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

        if not looks_like_news_link(title, href, city):
            continue

        clean_href = href.split("#")[0].strip()

        if clean_href not in [x["url"] for x in links]:
            links.append({"title": title, "url": clean_href})

    # Keep more candidates because filtering is stricter later.
    return links[:60]


def collect_city(city):
    out = []

    for cand in find_candidate_links(city):
        html = fetch(cand["url"])

        if not html:
            continue

        soup = BeautifulSoup(html, "lxml")
        h1 = soup.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else cand["title"]
        title = re.sub(r"\s+", " ", title).strip()

        body_raw = extract_text(soup)

        if not is_probably_news(title, cand["url"], body_raw):
            continue

        image = extract_image(soup, cand["url"])

        excerpt = rewrite_light(body_raw, 350)

        body = body_raw.strip()

        # Keep long enough for testing and import, but avoid huge pages.
        if len(body) > 10000:
            body = body[:10000].rsplit(" ", 1)[0] + "..."

        body += f"\n\nForrás: {cand['url']}"

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


def similar(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def dedupe(items):
    result = []
    seen_urls = set()
    seen_title_city = set()
    seen_titles = []

    for item in items:
        url = item.get("url", "").split("?")[0].rstrip("/")
        title = item.get("title", "")
        city = item.get("city", "")
        tnorm = norm(title)
        title_city_key = f"{norm(city)}|{tnorm}"

        if not tnorm or not url:
            continue

        if url in seen_urls:
            continue

        if title_city_key in seen_title_city:
            continue

        if any(similar(title, old) >= 0.88 for old in seen_titles):
            continue

        seen_urls.add(url)
        seen_title_city.add(title_city_key)
        seen_titles.append(tnorm)
        result.append(item)

    return result


def main():
    cities = json.loads(CONFIG.read_text(encoding="utf-8"))

    all_items = []

    for city in cities:
        print("Collecting", city["city"])
        city_items = collect_city(city)
        print(f"  Found {len(city_items)} usable news items")
        all_items.extend(city_items)

    final = dedupe(all_items)

    DRAFT.parent.mkdir(parents=True, exist_ok=True)
    DRAFT.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Saved {len(final)} items to {DRAFT}")


if __name__ == "__main__":
    main()
