"""
Czech Real Estate Scanner - Multi-Portal Listing Fetcher
Sources: sreality.cz (primary), bezrealitky.cz (owner-direct), bazos.cz (supplementary)
"""
import json
import os
import re
import sys
import time
import requests
from datetime import datetime, timezone

from config import (
    SREALITY_API_BASE, HEADERS, CATEGORY_MAIN, CATEGORY_TYPE_SALE,
    DISPOSITIONS_SALE, PRICE_MIN_CZK, PRICE_MAX_CZK,
    TARGET_DISTRICTS, SREALITY_PER_PAGE, SREALITY_MAX_PAGES,
    REQUEST_DELAY_S, REQUEST_TIMEOUT_S, MAX_RETRIES,
    DISPOSITION_LABELS, LABEL_USABLE_AREA, LABEL_FLOOR_AREA,
    LABEL_CONSTRUCTION, LABEL_CONDITION, LABEL_OWNERSHIP,
    LABEL_ENERGY_RATING, LABEL_FLOOR, DIR,
    BLOCKED_OWNERSHIP, BLOCKED_LOCALITIES,
    SREALITY_BUILDING_CONDITION_NEW, DISPOSITIONS_NOVOSTAVBY,
    NOVOSTAVBY_PRICE_MAX_CZK,
)
from models import Listing


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_json(url: str, params: dict = None, headers: dict = None) -> dict | None:
    """GET request with retry logic."""
    hdrs = headers or HEADERS
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=REQUEST_TIMEOUT_S)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            if attempt == MAX_RETRIES:
                print(f"  HTTP {resp.status_code} for {url}")
            return None
        except requests.RequestException as e:
            if attempt < MAX_RETRIES:
                time.sleep(2 * (attempt + 1))
            else:
                print(f"  Request failed: {e}")
                return None
    return None


def _post_json(url: str, payload: dict, headers: dict = None) -> dict | None:
    """POST request with retry logic."""
    hdrs = headers or HEADERS
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, headers=hdrs, timeout=REQUEST_TIMEOUT_S)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if attempt == MAX_RETRIES:
                print(f"  HTTP {resp.status_code} for {url}")
            return None
        except requests.RequestException as e:
            if attempt < MAX_RETRIES:
                time.sleep(2 * (attempt + 1))
            else:
                print(f"  Request failed: {e}")
                return None
    return None


# ===========================================================================
# SREALITY.CZ
# ===========================================================================

def _sreality_search_params(district_id: int, page: int = 1) -> dict:
    return {
        "category_main_cb": CATEGORY_MAIN,
        "category_type_cb": CATEGORY_TYPE_SALE,
        "category_sub_cb": "|".join(str(d) for d in DISPOSITIONS_SALE),
        "locality_district_id": district_id,
        "czk_price_summary_order2": f"{PRICE_MIN_CZK}|{PRICE_MAX_CZK}",
        "per_page": SREALITY_PER_PAGE,
        "page": page,
        "tms": int(time.time()),
    }


def fetch_sreality_page(district_id: int, page: int = 1) -> list[dict]:
    params = _sreality_search_params(district_id, page)
    data = _get_json(SREALITY_API_BASE, params)
    if not data:
        return []
    return data.get("_embedded", {}).get("estates", [])


def fetch_sreality_detail(hash_id: int) -> dict | None:
    url = f"{SREALITY_API_BASE}/{hash_id}"
    return _get_json(url)


def _extract_item(items: list[dict], label: str) -> str | None:
    for item in items:
        if item.get("name") == label:
            val = item.get("value")
            if isinstance(val, list):
                return ", ".join(str(v.get("value", v)) for v in val if isinstance(v, dict))
            return str(val) if val is not None else None
    return None


def _extract_area(items: list[dict]) -> float | None:
    for label in [LABEL_USABLE_AREA, LABEL_FLOOR_AREA]:
        val = _extract_item(items, label)
        if val:
            try:
                return float(str(val).replace("\xa0", "").replace("m2", "").replace(",", ".").strip())
            except (ValueError, TypeError):
                continue
    return None


def _extract_poi(detail: dict) -> tuple[float, dict]:
    poi = detail.get("poi", [])
    if not poi:
        return 0.0, {}

    categories = {}
    for group in poi:
        name = group.get("name", "unknown")
        items = group.get("poi", [])
        categories[name] = len(items)

    total = 0.0
    counted = 0
    for cat_name, count in categories.items():
        for key in ["Doprava", "Obchody", "Skoly", "Zdravi", "Transport", "Shops", "Schools", "Health"]:
            if key.lower() in cat_name.lower():
                score = min(count / 3, 1.0) * 2.5
                total += score
                counted += 1
                break

    if counted > 0 and counted < 4:
        total = total * 4 / counted

    return min(total, 10.0), categories


def _parse_size_from_name(name: str) -> float | None:
    m = re.search(r"(\d+)\s*m", name)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def parse_sreality(raw: dict, detail: dict | None, district_name: str) -> Listing:
    hash_id = raw.get("hash_id", 0)
    name = raw.get("name", "")
    price = raw.get("price", 0)
    locality = raw.get("locality", "")
    labels = [l for l in raw.get("labels", []) if isinstance(l, str)]

    gps = raw.get("gps", {})
    seo = raw.get("seo", {})
    category_sub = seo.get("category_sub_cb")
    disposition = DISPOSITION_LABELS.get(category_sub, None)

    # Extract image URLs from _links
    links = raw.get("_links", {})
    images = [img["href"] for img in links.get("images", []) if "href" in img][:5]

    size_m2 = None
    construction = condition = ownership = energy_rating = floor_str = None

    if detail:
        items = detail.get("items", [])
        size_m2 = _extract_area(items)
        construction = _extract_item(items, LABEL_CONSTRUCTION)
        condition = _extract_item(items, LABEL_CONDITION)
        ownership = _extract_item(items, LABEL_OWNERSHIP)
        energy_rating = _extract_item(items, LABEL_ENERGY_RATING)
        floor_str = _extract_item(items, LABEL_FLOOR)

    if not size_m2:
        size_m2 = _parse_size_from_name(name)

    url = f"https://www.sreality.cz/detail/prodej/byt/{disposition or 'x'}/{seo.get('locality', 'x')}/{hash_id}"

    return Listing(
        source="sreality",
        source_id=f"sr-{hash_id}",
        title=name,
        price_czk=price,
        size_m2=size_m2,
        disposition=disposition,
        disposition_code=category_sub,
        locality=locality,
        district=district_name,
        gps_lat=gps.get("lat"),
        gps_lon=gps.get("lon"),
        construction_type=construction,
        condition=condition,
        ownership=ownership,
        energy_rating=energy_rating,
        floor=floor_str,
        url=url,
        images=images,
        labels=labels,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


# ===========================================================================
# BEZREALITKY.CZ (GraphQL API)
# ===========================================================================

BEZREALITKY_URL = "https://api.bezrealitky.cz/graphql/"
BEZREALITKY_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Origin": "https://www.bezrealitky.cz",
    "Referer": "https://www.bezrealitky.cz/",
}

BEZREALITKY_QUERY = """
query ListAdverts($limit: Int, $offset: Int, $offerType: [OfferType],
  $estateType: [EstateType], $locale: Locale!, $order: ResultOrder,
  $priceFrom: Int, $priceTo: Int) {
  listAdverts(limit: $limit, offset: $offset, offerType: $offerType,
    estateType: $estateType, locale: $locale, order: $order,
    priceFrom: $priceFrom, priceTo: $priceTo) {
    totalCount
    list {
      id uri title price currency surface disposition
      address(locale: $locale) city(locale: $locale)
      cityDistrict(locale: $locale) street zip
      gps { lat lng }
      mainImage { url }
      offerType estateType condition construction etage ownership
    }
  }
}
"""

BEZREALITKY_DISP_MAP = {
    "DISP_1_KK": "1+kk", "DISP_1_1": "1+1",
    "DISP_2_KK": "2+kk", "DISP_2_1": "2+1",
    "DISP_3_KK": "3+kk", "DISP_3_1": "3+1",
    "DISP_4_KK": "4+kk", "DISP_4_1": "4+1",
    "GARSONIERA": "garsoniera",
}

BEZREALITKY_CONSTRUCTION_MAP = {
    "BRICK": "Cihlova", "PANEL": "Panelova", "PREFAB": "Panelova",
    "MIXED": "Smisena", "WOOD": "Drevena", "SKELET": "Skeletova",
}

BEZREALITKY_CONDITION_MAP = {
    "NEW": "Novostavba", "VERY_GOOD": "Velmi dobry",
    "GOOD": "Dobry", "BAD": "Spatny",
    "BEFORE_RECONSTRUCTION": "Pred rekonstrukci",
    "AFTER_RECONSTRUCTION": "Po rekonstrukci",
}

BEZREALITKY_OWNERSHIP_MAP = {
    "OSOBNI": "Osobni", "DRUZSTEVNI": "Druzstevni",
    "OBECNI": "Obecni", "OSTATNI": "Ostatni",
}


def fetch_bezrealitky(offer_type: str = "PRODEJ", limit: int = 50, offset: int = 0) -> tuple[int, list[dict]]:
    """Fetch listings from bezrealitky GraphQL API."""
    variables = {
        "limit": limit,
        "offset": offset,
        "offerType": [offer_type],
        "estateType": ["BYT"],
        "locale": "CS",
        "order": "TIMEORDER_DESC",
        "priceFrom": PRICE_MIN_CZK,
        "priceTo": PRICE_MAX_CZK,
    }
    payload = {
        "operationName": "ListAdverts",
        "query": BEZREALITKY_QUERY,
        "variables": variables,
    }
    data = _post_json(BEZREALITKY_URL, payload, headers=BEZREALITKY_HEADERS)
    if not data:
        return 0, []
    adverts = data.get("data", {}).get("listAdverts", {})
    return adverts.get("totalCount", 0), adverts.get("list", [])


def _match_district(city: str, address: str) -> str | None:
    """Try to match a bezrealitky listing to one of our target districts."""
    text = f"{city} {address}".lower()
    for _, dname in TARGET_DISTRICTS:
        # Normalize for matching
        normalized = dname.lower().replace("-", " ")
        parts = normalized.split()
        if all(p in text for p in parts):
            return dname
    return None


def parse_bezrealitky(raw: dict) -> Listing | None:
    """Parse a bezrealitky listing into our model."""
    city = raw.get("city", "") or ""
    address = raw.get("address", "") or ""
    district = _match_district(city, address)

    # We only want listings in our target districts
    if not district:
        return None

    gps = raw.get("gps") or {}
    disp_raw = raw.get("disposition", "")
    disposition = BEZREALITKY_DISP_MAP.get(disp_raw, disp_raw)

    construction = raw.get("construction")
    condition = raw.get("condition")
    ownership = raw.get("ownership")

    uri = raw.get("uri", "")
    url = f"https://www.bezrealitky.cz/nemovitosti-byty-domy/{uri}"

    # Extract main image
    images = []
    main_img = raw.get("mainImage")
    if main_img and isinstance(main_img, dict) and main_img.get("url"):
        images = [main_img["url"]]

    return Listing(
        source="bezrealitky",
        source_id=f"br-{raw.get('id', '')}",
        title=raw.get("title", ""),
        price_czk=int(raw.get("price", 0)),
        size_m2=float(raw["surface"]) if raw.get("surface") else None,
        disposition=disposition,
        disposition_code=None,
        locality=address,
        district=district,
        gps_lat=gps.get("lat"),
        gps_lon=gps.get("lng"),
        construction_type=BEZREALITKY_CONSTRUCTION_MAP.get(construction, construction),
        condition=BEZREALITKY_CONDITION_MAP.get(condition, condition),
        ownership=BEZREALITKY_OWNERSHIP_MAP.get(ownership, ownership),
        energy_rating=None,
        floor=str(raw["etage"]) if raw.get("etage") else None,
        url=url,
        images=images,
        labels=["owner-direct"],
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def scan_bezrealitky(max_pages: int = 10) -> list[Listing]:
    """Scan bezrealitky for listings in target districts."""
    listings = []
    seen = set()
    page_size = 50

    total, first_page = fetch_bezrealitky(limit=page_size, offset=0)
    if not first_page:
        return []

    print(f"    bezrealitky.cz: {total} total sale apartments nationwide")

    all_raw = first_page
    for page in range(1, max_pages):
        offset = page * page_size
        if offset >= total:
            break
        time.sleep(REQUEST_DELAY_S)
        _, more = fetch_bezrealitky(limit=page_size, offset=offset)
        if not more:
            break
        all_raw.extend(more)

    for raw in all_raw:
        listing = parse_bezrealitky(raw)
        if listing and listing.source_id not in seen:
            seen.add(listing.source_id)
            listings.append(listing)

    return listings


# ===========================================================================
# BAZOS.CZ (API + HTML scraping)
# ===========================================================================

BAZOS_API_URL = "https://www.bazos.cz/api/v1/ads.php"

def fetch_bazos_api() -> list[dict]:
    """Fetch from bazos API (limited to ~30 results, no real pagination)."""
    data = _get_json(BAZOS_API_URL, params={"section": "reality", "rubrika": "prodej-bytu"})
    if isinstance(data, list):
        return data
    return []


def _parse_bazos_price(price_str: str) -> int:
    """Parse '1 200 000 Kc' into integer."""
    cleaned = re.sub(r"[^\d]", "", price_str.split("K")[0])
    try:
        return int(cleaned)
    except ValueError:
        return 0


def _match_bazos_district(locality: str) -> str | None:
    """Match bazos locality to target district."""
    text = locality.lower()
    for _, dname in TARGET_DISTRICTS:
        normalized = dname.lower().replace("-", " ")
        parts = normalized.split()
        if all(p in text for p in parts):
            return dname
    return None


def parse_bazos(raw: dict) -> Listing | None:
    """Parse a bazos API listing."""
    locality = raw.get("locality", "")
    district = _match_bazos_district(locality)
    if not district:
        return None

    title = raw.get("title", "")
    price = _parse_bazos_price(raw.get("price_formatted", "0"))
    if price < PRICE_MIN_CZK or price > PRICE_MAX_CZK:
        return None

    size_m2 = _parse_size_from_name(title)

    # Detect disposition from title
    disposition = None
    for pattern, disp in [
        (r"3\+kk", "3+kk"), (r"3\+1", "3+1"),
        (r"2\+kk", "2+kk"), (r"2\+1", "2+1"),
        (r"4\+kk", "4+kk"), (r"4\+1", "4+1"),
        (r"1\+kk", "1+kk"), (r"1\+1", "1+1"),
    ]:
        if re.search(pattern, title, re.IGNORECASE):
            disposition = disp
            break

    return Listing(
        source="bazos",
        source_id=f"bz-{raw.get('id', '')}",
        title=title,
        price_czk=price,
        size_m2=size_m2,
        disposition=disposition,
        disposition_code=None,
        locality=locality,
        district=district,
        gps_lat=None,
        gps_lon=None,
        construction_type=None,
        condition=None,
        ownership=None,
        energy_rating=None,
        floor=None,
        url=raw.get("url", ""),
        labels=["bazos"],
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def scan_bazos() -> list[Listing]:
    """Scan bazos for listings in target districts."""
    raw_listings = fetch_bazos_api()
    if not raw_listings:
        return []

    print(f"    bazos.cz: {len(raw_listings)} listings from API")

    listings = []
    for raw in raw_listings:
        listing = parse_bazos(raw)
        if listing:
            listings.append(listing)

    return listings


# ===========================================================================
# REALITY.IDNES.CZ (HTML scraping)
# ===========================================================================

IDNES_BASE = "https://reality.idnes.cz"

# Map our target districts to idnes URL slugs
IDNES_DISTRICT_SLUGS = {
    "Usti nad Labem": "usti-nad-labem",
    "Most": "most",
    "Teplice": "teplice",
    "Decin": "decin",
    "Chomutov": "chomutov",
    "Karvina": "karvina",
    "Frydek-Mistek": "frydek-mistek",
    "Novy Jicin": "novy-jicin",
    "Opava": "opava",
    "Kladno": "kladno",
}


def _parse_idnes_price(text: str) -> int:
    """Parse '6 999 900 Kc' -> int."""
    cleaned = re.sub(r"[^\d]", "", text.split("K")[0].split("€")[0])
    try:
        return int(cleaned)
    except ValueError:
        return 0


def _parse_idnes_listing(container, district_name: str) -> Listing | None:
    """Parse one listing from idnes HTML."""
    try:
        from bs4 import Tag
    except ImportError:
        return None

    link = container.find("a", class_="c-products__link")
    if not link:
        return None

    url = link.get("href", "")
    if not url.startswith("http"):
        url = IDNES_BASE + url

    title_el = container.find("h2", class_="c-products__title")
    title = title_el.get_text(strip=True) if title_el else ""

    info_el = container.find("p", class_="c-products__info")
    locality = info_el.get_text(strip=True) if info_el else ""

    price_el = container.find("p", class_="c-products__price")
    price = _parse_idnes_price(price_el.get_text() if price_el else "0")

    if price < PRICE_MIN_CZK or price > PRICE_MAX_CZK:
        return None

    # Extract image
    images = []
    img_el = container.find("img")
    if img_el:
        img_url = img_el.get("data-src") or img_el.get("src", "")
        if img_url and "http" in img_url:
            images = [img_url]
    # Also check background-image in span
    if not images:
        span_img = container.find("span", class_="c-products__img")
        if span_img and span_img.get("style"):
            m = re.search(r"url\(['\"]?(https?://[^'\")]+)", span_img["style"])
            if m:
                images = [m.group(1)]

    # Parse size and disposition from title: "prodej bytu 2+1 62 m2"
    size_m2 = _parse_size_from_name(title)

    disposition = None
    for pattern, disp in [
        (r"3\+kk", "3+kk"), (r"3\+1", "3+1"),
        (r"2\+kk", "2+kk"), (r"2\+1", "2+1"),
        (r"4\+kk", "4+kk"), (r"4\+1", "4+1"),
        (r"1\+kk", "1+kk"), (r"1\+1", "1+1"),
    ]:
        if re.search(pattern, title, re.IGNORECASE):
            disposition = disp
            break

    return Listing(
        source="idnes",
        source_id=f"id-{url.rstrip('/').split('/')[-1][:12]}",
        title=title,
        price_czk=price,
        size_m2=size_m2,
        disposition=disposition,
        disposition_code=None,
        locality=locality,
        district=district_name,
        gps_lat=None,
        gps_lon=None,
        construction_type=None,
        condition=None,
        ownership=None,
        energy_rating=None,
        floor=None,
        url=url,
        images=images,
        labels=["idnes"],
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def scan_idnes(max_pages: int = 3) -> list[Listing]:
    """Scrape reality.idnes.cz for listings in target districts."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("    beautifulsoup4 not installed, skipping idnes")
        return []

    all_listings = []
    seen_ids = set()

    for _, district_name in TARGET_DISTRICTS:
        slug = IDNES_DISTRICT_SLUGS.get(district_name)
        if not slug:
            continue

        for page in range(1, max_pages + 1):
            url = f"{IDNES_BASE}/s/prodej/byty/{slug}/?page={page}"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_S)
                if resp.status_code != 200:
                    break
            except requests.RequestException:
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            containers = soup.find_all("div", class_="c-products__inner")

            if not containers:
                break

            count = 0
            for container in containers:
                listing = _parse_idnes_listing(container, district_name)
                if listing and listing.source_id not in seen_ids:
                    seen_ids.add(listing.source_id)
                    all_listings.append(listing)
                    count += 1

            if page == 1:
                print(f"    {district_name}: page 1 -> {count} listings")

            time.sleep(REQUEST_DELAY_S)

    return all_listings


# ===========================================================================
# NOVOSTAVBY (NEW BUILDS) - dedicated scan across portals
# ===========================================================================

def scan_novostavby_sreality() -> list[Listing]:
    """Scan sreality for novostavby (new construction) in target districts."""
    all_listings: list[Listing] = []
    seen_ids: set[str] = set()

    for district_id, district_name in TARGET_DISTRICTS:
        print(f"  {district_name}...", end=" ", flush=True)
        params = {
            "category_main_cb": CATEGORY_MAIN,
            "category_type_cb": CATEGORY_TYPE_SALE,
            "category_sub_cb": "|".join(str(d) for d in DISPOSITIONS_NOVOSTAVBY),
            "building_condition": SREALITY_BUILDING_CONDITION_NEW,
            "locality_district_id": district_id,
            "czk_price_summary_order2": f"{PRICE_MIN_CZK}|{NOVOSTAVBY_PRICE_MAX_CZK}",
            "per_page": SREALITY_PER_PAGE,
            "page": 1,
            "tms": int(time.time()),
        }
        data = _get_json(SREALITY_API_BASE, params)
        if not data:
            print("0")
            continue

        estates = data.get("_embedded", {}).get("estates", [])
        count = 0
        for raw in estates:
            sid = f"sr-{raw.get('hash_id', '')}"
            if sid in seen_ids:
                continue
            seen_ids.add(sid)

            listing = parse_sreality(raw, None, district_name)
            if listing.price_czk > 0:
                listing.labels.append("novostavba")
                all_listings.append(listing)
                count += 1

        print(f"{count}")

        # Paginate if needed
        total = data.get("result_size", 0)
        for page in range(2, SREALITY_MAX_PAGES + 1):
            if len(estates) < SREALITY_PER_PAGE or page * SREALITY_PER_PAGE > total:
                break
            time.sleep(REQUEST_DELAY_S)
            params["page"] = page
            data = _get_json(SREALITY_API_BASE, params)
            if not data:
                break
            for raw in data.get("_embedded", {}).get("estates", []):
                sid = f"sr-{raw.get('hash_id', '')}"
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)
                listing = parse_sreality(raw, None, district_name)
                if listing.price_czk > 0:
                    listing.labels.append("novostavba")
                    all_listings.append(listing)

        time.sleep(REQUEST_DELAY_S)

    return all_listings


def scan_novostavby_idnes() -> list[Listing]:
    """Scan reality.idnes.cz for novostavby in target districts."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    all_listings = []
    seen_ids = set()

    for _, district_name in TARGET_DISTRICTS:
        slug = IDNES_DISTRICT_SLUGS.get(district_name)
        if not slug:
            continue

        # idnes uses /novostavby/ path segment
        url = f"{IDNES_BASE}/s/prodej/byty/{slug}/novostavby/"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_S)
            if resp.status_code != 200:
                continue
        except requests.RequestException:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        containers = soup.find_all("div", class_="c-products__inner")

        count = 0
        for container in containers:
            listing = _parse_idnes_listing(container, district_name)
            if listing and listing.source_id not in seen_ids:
                # Apply novostavby price range
                if listing.price_czk > NOVOSTAVBY_PRICE_MAX_CZK:
                    continue
                seen_ids.add(listing.source_id)
                listing.labels.append("novostavba")
                all_listings.append(listing)
                count += 1

        if count > 0:
            print(f"    {district_name}: {count} novostavby")

        time.sleep(REQUEST_DELAY_S)

    return all_listings


def scan_novostavby_bezrealitky() -> list[Listing]:
    """Scan bezrealitky for novostavby (condition=NEW)."""
    variables = {
        "limit": 50,
        "offset": 0,
        "offerType": ["PRODEJ"],
        "estateType": ["BYT"],
        "locale": "CS",
        "order": "TIMEORDER_DESC",
        "priceFrom": PRICE_MIN_CZK,
        "priceTo": NOVOSTAVBY_PRICE_MAX_CZK,
    }

    # Add condition filter for new builds
    query = BEZREALITKY_QUERY.replace(
        "$priceTo: Int) {",
        "$priceTo: Int, $condition: [Condition]) {"
    ).replace(
        "priceTo: $priceTo) {",
        "priceTo: $priceTo, condition: $condition) {"
    )
    variables["condition"] = ["NEW"]

    payload = {
        "operationName": "ListAdverts",
        "query": query,
        "variables": variables,
    }
    data = _post_json(BEZREALITKY_URL, payload, headers=BEZREALITKY_HEADERS)
    if not data:
        return []

    adverts = data.get("data", {}).get("listAdverts", {})
    total = adverts.get("totalCount", 0)
    raw_list = adverts.get("list", [])

    listings = []
    for raw in raw_list:
        listing = parse_bezrealitky(raw)
        if listing:
            listing.labels.append("novostavba")
            listings.append(listing)

    return listings


def scan_all_novostavby() -> list[Listing]:
    """Comprehensive novostavby scan across all portals."""
    all_listings = []
    seen_titles: set[str] = set()

    # 1. Sreality novostavby
    print("\n  --- SREALITY.CZ (novostavby) ---")
    sreality = scan_novostavby_sreality()
    sreality = [l for l in sreality if not is_blocked(l)]
    all_listings.extend(sreality)
    for l in sreality:
        seen_titles.add(_normalize_title(l.title))
    print(f"  Sreality novostavby: {len(sreality)}")

    # 2. Reality.idnes.cz novostavby
    print("\n  --- REALITY.IDNES.CZ (novostavby) ---")
    try:
        idnes = scan_novostavby_idnes()
        added = 0
        for l in idnes:
            if is_blocked(l):
                continue
            if _normalize_title(l.title) not in seen_titles:
                all_listings.append(l)
                seen_titles.add(_normalize_title(l.title))
                added += 1
        print(f"  iDNES novostavby: {len(idnes)} scraped, {added} new")
    except Exception as e:
        print(f"  iDNES novostavby failed: {e}")

    # 3. Bezrealitky novostavby
    print("\n  --- BEZREALITKY.CZ (novostavby) ---")
    try:
        bezr = scan_novostavby_bezrealitky()
        added = 0
        for l in bezr:
            if is_blocked(l):
                continue
            if _normalize_title(l.title) not in seen_titles:
                all_listings.append(l)
                seen_titles.add(_normalize_title(l.title))
                added += 1
        print(f"  Bezrealitky novostavby: {len(bezr)} matched, {added} new")
    except Exception as e:
        print(f"  Bezrealitky novostavby failed: {e}")

    return all_listings


# ===========================================================================
# Novostavby.com - Developer project reference layer
# ===========================================================================

# Novostavby.com WP REST API region IDs for our target areas
_NOVOSTAVBY_COM_REGIONS = {
    2766: "Ústecký kraj",
    2315: "Moravskoslezský kraj",
    1387: "Středočeský kraj",
}


def _strip_html(s: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", s).strip()


def fetch_novostavby_com_projects() -> list[dict]:
    """Fetch developer projects from novostavby.com WP REST API for target regions."""
    api_url = "https://novostavby.com/wp-json/wp/v2/property"
    all_projects = []

    for region_id, region_name in _NOVOSTAVBY_COM_REGIONS.items():
        try:
            resp = requests.get(
                api_url,
                params={"per_page": 100, "property_location": region_id},
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT_S,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            for p in data:
                # Extract location info from class_list
                class_list = p.get("class_list", [])
                locations = []
                for c in class_list:
                    if "property_location-" in c:
                        loc = c.replace("property_location-", "").replace("-", " ")
                        if loc not in ("cela cr", "rekreacni lokality") and "kraj" not in loc:
                            locations.append(loc)

                status_tags = [c.replace("property_status-", "").replace("-", " ")
                               for c in class_list if "property_status-" in c]

                title = _strip_html(p.get("title", {}).get("rendered", ""))
                # Stopwords: common Czech location/generic words that cause false matches
                _stop = {"nad", "pod", "okres", "novostavby", "rezidence", "byty",
                         "bydlení", "bydleni", "domy", "nová", "nova", "nové", "nove",
                         "mesto", "město", "kraj", "projekt", "vila", "viladomy",
                         "prodej", "prodej", "apartmány", "apartmany",
                         # District names too broad for matching
                         "karvina", "karviná", "chomutov", "teplice", "most",
                         "opava", "kladno", "ostrava", "frydek", "mistek", "místek",
                         "frýdek", "decin", "děčín", "labem", "jicin", "jičín",
                         "ústí", "usti", "brno"}
                loc_keywords = set()
                for loc in locations:
                    for w in loc.split():
                        if len(w) > 3 and w.lower() not in _stop:
                            loc_keywords.add(w.lower())
                name_keywords = set(
                    w.lower() for w in re.split(r"\s+", title)
                    if len(w) > 3 and w.lower() not in _stop
                )
                all_projects.append({
                    "name": title,
                    "url": p.get("link", ""),
                    "region": region_name,
                    "locations": locations,
                    "status": status_tags[0] if status_tags else "unknown",
                    "name_keywords": name_keywords,
                    "loc_keywords": loc_keywords,
                })
            print(f"    {region_name}: {len(data)} projects")
        except Exception as e:
            print(f"    {region_name}: error - {e}")
        time.sleep(0.5)

    return all_projects


def match_developer_projects(listings: list[Listing], projects: list[dict]) -> int:
    """Match listings to known developer projects by location/name overlap.
    Requires either a project name keyword match OR 2+ location keyword matches.
    Returns count of matched listings."""
    if not projects:
        return 0

    matched = 0
    for listing in listings:
        loc_lower = listing.locality.lower()
        title_lower = listing.title.lower()
        best_match = None
        best_score = 0

        for proj in projects:
            if proj["status"] == "vyprodano":
                continue

            # Score from project name keywords in listing locality/title
            name_hits = sum(1 for kw in proj["name_keywords"]
                           if kw in loc_lower or kw in title_lower)
            # Score from location keywords in listing locality
            loc_hits = sum(1 for kw in proj["loc_keywords"] if kw in loc_lower)

            # Need name match (strong signal) or multiple location matches
            score = name_hits * 3 + loc_hits
            if name_hits == 0 and loc_hits < 2:
                continue  # no name match and weak location match

            if score > best_score and score >= 3:
                best_score = score
                best_match = proj

        if best_match:
            listing.developer_project = best_match["name"]
            listing.developer_url = best_match["url"]
            matched += 1

    return matched


# ===========================================================================
# Filtering
# ===========================================================================

def is_blocked(listing: Listing) -> bool:
    """Filter out druzstevni ownership and known problem localities."""
    # Check ownership (if known)
    if listing.ownership:
        own_lower = listing.ownership.lower()
        for blocked in BLOCKED_OWNERSHIP:
            if blocked in own_lower:
                return True

    # Check locality against blocked list
    loc_lower = (listing.locality or "").lower()
    title_lower = (listing.title or "").lower()
    for blocked_loc in BLOCKED_LOCALITIES:
        if blocked_loc in loc_lower or blocked_loc in title_lower:
            return True

    return False


# ===========================================================================
# Combined scan
# ===========================================================================

def scan_all_districts(fetch_details: bool = True) -> list[Listing]:
    """Scan sreality across all target districts."""
    all_listings: list[Listing] = []
    seen_ids: set[str] = set()

    for district_id, district_name in TARGET_DISTRICTS:
        print(f"\n  [sreality] {district_name} (district {district_id})...")
        district_count = 0

        for page in range(1, SREALITY_MAX_PAGES + 1):
            raw_listings = fetch_sreality_page(district_id, page)
            if not raw_listings:
                break

            for raw in raw_listings:
                sid = f"sr-{raw.get('hash_id', '')}"
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)

                detail = None
                if fetch_details:
                    time.sleep(REQUEST_DELAY_S)
                    detail = fetch_sreality_detail(raw.get("hash_id", 0))

                listing = parse_sreality(raw, detail, district_name)
                if listing.price_czk > 0:
                    all_listings.append(listing)
                    district_count += 1

            print(f"    Page {page}: {len(raw_listings)} raw, {district_count} total")

            if len(raw_listings) < SREALITY_PER_PAGE:
                break
            time.sleep(REQUEST_DELAY_S)

    return all_listings


def scan_all_sources(fetch_details: bool = False) -> list[Listing]:
    """Scan all portals and merge results."""
    all_listings = []
    seen_titles: set[str] = set()

    # 1. Sreality (primary)
    print("\n  --- SREALITY.CZ ---")
    if fetch_details:
        sreality_raw = scan_all_districts(fetch_details=True)
    else:
        sreality_raw = scan_fast()
    # Filter blocked listings
    sreality = [l for l in sreality_raw if not is_blocked(l)]
    blocked_count = len(sreality_raw) - len(sreality)
    all_listings.extend(sreality)
    for l in sreality:
        seen_titles.add(_normalize_title(l.title))
    print(f"  Sreality total: {len(sreality)}" + (f" ({blocked_count} filtered)" if blocked_count else ""))

    # 2. Bezrealitky (owner-direct)
    print("\n  --- BEZREALITKY.CZ ---")
    try:
        bezrealitky = scan_bezrealitky()
        # Filter and deduplicate against sreality
        added = 0
        for l in bezrealitky:
            if is_blocked(l):
                continue
            if _normalize_title(l.title) not in seen_titles:
                all_listings.append(l)
                seen_titles.add(_normalize_title(l.title))
                added += 1
        print(f"  Bezrealitky: {len(bezrealitky)} matched districts, {added} new")
    except Exception as e:
        print(f"  Bezrealitky failed: {e}")

    # 3. Bazos (supplementary)
    print("\n  --- BAZOS.CZ ---")
    try:
        bazos = scan_bazos()
        added = 0
        for l in bazos:
            if _normalize_title(l.title) not in seen_titles:
                all_listings.append(l)
                seen_titles.add(_normalize_title(l.title))
                added += 1
        print(f"  Bazos: {len(bazos)} matched districts, {added} new")
    except Exception as e:
        print(f"  Bazos failed: {e}")

    # 4. Reality.idnes.cz (HTML scraping)
    print("\n  --- REALITY.IDNES.CZ ---")
    try:
        idnes = scan_idnes()
        # Filter and deduplicate
        added = 0
        for l in idnes:
            if is_blocked(l):
                continue
            if _normalize_title(l.title) not in seen_titles:
                all_listings.append(l)
                seen_titles.add(_normalize_title(l.title))
                added += 1
        print(f"  iDNES: {len(idnes)} scraped, {added} new after dedup")
    except Exception as e:
        print(f"  iDNES failed: {e}")

    return all_listings


def _normalize_title(title: str) -> str:
    """Normalize title for dedup."""
    return re.sub(r"\s+", " ", title.lower().strip())


def scan_fast(max_per_district: int = 60) -> list[Listing]:
    """Quick sreality scan without detail fetches."""
    all_listings: list[Listing] = []
    seen_ids: set[str] = set()

    for district_id, district_name in TARGET_DISTRICTS:
        print(f"  {district_name}...", end=" ", flush=True)
        raw_listings = fetch_sreality_page(district_id, page=1)

        count = 0
        for raw in raw_listings[:max_per_district]:
            sid = f"sr-{raw.get('hash_id', '')}"
            if sid in seen_ids:
                continue
            seen_ids.add(sid)

            listing = parse_sreality(raw, None, district_name)
            if listing.price_czk > 0:
                all_listings.append(listing)
                count += 1

        print(f"{count} listings")
        time.sleep(REQUEST_DELAY_S)

    return all_listings


# ---------------------------------------------------------------------------
# Save / print
# ---------------------------------------------------------------------------

def save_scan(listings: list[Listing], filename: str = "last_scan.json") -> str:
    path = os.path.join(DIR, filename)
    by_source = {}
    for l in listings:
        by_source[l.source] = by_source.get(l.source, 0) + 1
    data = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "listing_count": len(listings),
        "by_source": by_source,
        "districts": sorted(set(l.district for l in listings)),
        "listings": [l.to_dict() for l in listings],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def print_scan_summary(listings: list[Listing]):
    by_source: dict[str, int] = {}
    for l in listings:
        by_source[l.source] = by_source.get(l.source, 0) + 1

    print(f"\n{'=' * 65}")
    print(f"  SCAN RESULTS: {len(listings)} listings")
    print(f"  Sources: {', '.join(f'{s}={n}' for s, n in sorted(by_source.items()))}")
    print(f"{'=' * 65}")

    by_district: dict[str, list[Listing]] = {}
    for l in listings:
        by_district.setdefault(l.district, []).append(l)

    for district, items in sorted(by_district.items()):
        prices = [l.price_czk for l in items]
        sizes = [l.size_m2 for l in items if l.size_m2]
        sources = set(l.source for l in items)
        print(f"\n  {district}: {len(items)} listings ({', '.join(sources)})")
        print(f"    Price: {min(prices):,.0f} - {max(prices):,.0f} CZK")
        if sizes:
            print(f"    Size:  {min(sizes):.0f} - {max(sizes):.0f} m2")

    print(f"\n{'=' * 65}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 65)
    print("  CZECH REAL ESTATE SCANNER - Multi-Portal Fetch")
    print("=" * 65)

    fast = "--fast" in sys.argv
    if fast:
        print("\n[Fast mode]\n")
        listings = scan_all_sources(fetch_details=False)
    else:
        print("\n[Full scan with details]\n")
        listings = scan_all_sources(fetch_details=True)

    print_scan_summary(listings)
    path = save_scan(listings)
    print(f"Saved to {path}")
