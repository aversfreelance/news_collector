# Pest county news collector. After edits, run: npm run sync-collector-script
import json, re, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
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

BINARY_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".odt", ".ods", ".odp", ".rtf", ".csv",
    ".bin", ".exe", ".dmg",
)

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
    "fogadóóra",
    "dokumentum",
    "melléklet",
    "sablon",
    "formanyomtatvány",
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
    "sitemap",
    "dokumentum",
    "melleklet",
    "attachment",
    "download",
    "forcedownload",
]

GOOD_URL_WORDS = [
    "hir",
    "hirek",
    "aktualis",
    "aktualitas",
    "cikk",
    "news",
    "bejegyzes",
    "post",
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
        content_type = (r.headers.get("content-type") or "").lower()
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            print(f"SKIP non-html {url}: {content_type}")
            return ""
        return r.text
    except Exception as e:
        print(f"FETCH ERROR {url}: {e}")
        return ""


def is_binary_url(url):
    if not url:
        return False

    path = unquote(urlparse(url).path.lower())
    if any(path.endswith(ext) for ext in BINARY_EXTENSIONS):
        return True
    if any(ext + "?" in url.lower() for ext in BINARY_EXTENSIONS):
        return True

    low = url.lower()
    if any(marker in low for marker in ["/letoltes/", "/download/", "forcedownload=", "attachment_id="]):
        if any(ext in path for ext in BINARY_EXTENSIONS):
            return True

    return False


def is_probably_image(url):
    if not url or not url.startswith("http"):
        return False

    if is_binary_url(url):
        return False

    low = url.lower()

    if any(w in low for w in BAD_IMAGE_WORDS):
        return False

    if any(ext in low for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]):
        return True

    if any(marker in low for marker in ["/image/", "image=", "kep=", "picture=", "photo=", "media/"]):
        return True

    return False


def clean_img_url(src, base):
    if not src:
        return ""

    src = src.strip()

    if src.startswith("data:"):
        return ""

    if "," in src and " " in src:
        src = src.split(",")[0].strip().split(" ")[0]

    return urljoin(base, src)


def parse_date_string(raw):
    if not raw:
        return None

    raw = str(raw).strip()
    if not raw:
        return None

    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"

    dotted = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", raw)
    if dotted:
        y, m, d = int(dotted.group(1)), int(dotted.group(2)), int(dotted.group(3))
        if 1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{m:02d}-{d:02d}"

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except ValueError:
        pass

    return None


def extract_json_ld_dates(data):
    if isinstance(data, dict):
        for key in ("datePublished", "uploadDate", "dateCreated"):
            d = parse_date_string(data.get(key))
            if d:
                return d
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                d = extract_json_ld_dates(item)
                if d:
                    return d
    elif isinstance(data, list):
        for item in data:
            d = extract_json_ld_dates(item)
            if d:
                return d
    return None


def extract_published_date(soup):
    meta_specs = [
        ("meta", {"property": "article:published_time"}, "content"),
        ("meta", {"property": "og:article:published_time"}, "content"),
        ("meta", {"name": "pubdate"}, "content"),
        ("meta", {"name": "publish-date"}, "content"),
        ("meta", {"name": "date"}, "content"),
        ("meta", {"itemprop": "datePublished"}, "content"),
        ("meta", {"property": "article:modified_time"}, "content"),
    ]

    for tag, attrs, attr in meta_specs:
        el = soup.find(tag, attrs=attrs)
        if el:
            d = parse_date_string(el.get(attr))
            if d:
                return d

    for time_el in soup.find_all("time"):
        for attr in ("datetime", "content"):
            d = parse_date_string(time_el.get(attr))
            if d:
                return d
        d = parse_date_string(time_el.get_text(" ", strip=True))
        if d:
            return d

    for css in [".date", ".post-date", ".published", ".entry-date", ".news-date", "time"]:
        el = soup.select_one(css)
        if el:
            d = parse_date_string(el.get("datetime") or el.get_text(" ", strip=True))
            if d:
                return d

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or script.get_text() or "")
            d = extract_json_ld_dates(payload)
            if d:
                return d
        except (json.JSONDecodeError, TypeError):
            continue

    return None


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
            "vissza a hírekhez",
            "letöltés",
            "pdf formátumban",
            "dokumentum letöltése",
        ]):
            continue

        paras.append(txt)

    return "\n\n".join(paras[:30])


def rewrite_light(text, max_len=900):
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

    if is_binary_url(url):
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

    if len(title_l.split()) <= 2 and not any(good in url_l for good in GOOD_URL_WORDS):
        return False

    return True


def looks_like_news_link(title, href, city):
    title_l = (title or "").lower().strip()
    href_l = (href or "").lower().strip()

    if len(title_l) < 12:
        return False

    if is_binary_url(href):
        return False

    if any(x in href_l for x in ["#", "mailto:", "tel:", "facebook.com", "javascript:"]):
        return False

    if any(bad in title_l for bad in BAD_TITLE_WORDS):
        return False

    if any(bad in href_l for bad in BAD_URL_WORDS):
        return False

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

        published = extract_published_date(soup)
        if not published:
            print(f"  SKIP no published date: {cand['url']}")
            continue

        image = extract_image(soup, cand["url"])

        excerpt = rewrite_light(body_raw, 350)

        body = body_raw.strip()

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
            "date": published,
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

        if is_binary_url(url):
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
