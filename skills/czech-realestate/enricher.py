"""
Czech Real Estate Scanner - Enrichment
Adds rental comps, price context, live CZSO population data, and POI scores.
"""
import io
import json
import math
import os
import re
import statistics
import time
import requests
from datetime import datetime, timezone

from config import (
    SREALITY_API_BASE, HEADERS, CATEGORY_MAIN, CATEGORY_TYPE_RENT,
    DISPOSITIONS_RENT, TARGET_DISTRICTS, SREALITY_PER_PAGE,
    REQUEST_DELAY_S, REQUEST_TIMEOUT_S, DIR,
)
from models import Listing, EnrichedListing
from scanner import _get_json, _extract_poi, fetch_sreality_detail


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Rental comps (batch per district from sreality)
# ---------------------------------------------------------------------------

def fetch_rental_listings_for_district(district_id: int) -> list[dict]:
    """Fetch rental listings in a district for comp analysis."""
    all_rentals = []

    for page in range(1, 4):
        params = {
            "category_main_cb": CATEGORY_MAIN,
            "category_type_cb": CATEGORY_TYPE_RENT,
            "category_sub_cb": "|".join(str(d) for d in DISPOSITIONS_RENT),
            "locality_district_id": district_id,
            "per_page": SREALITY_PER_PAGE,
            "page": page,
            "tms": int(time.time()),
        }
        data = _get_json(SREALITY_API_BASE, params)
        if not data:
            break

        estates = data.get("_embedded", {}).get("estates", [])
        if not estates:
            break

        for e in estates:
            price = e.get("price", 0)
            gps = e.get("gps", {})
            name = e.get("name", "")

            size = None
            m = re.search(r"(\d+)\s*m", name)
            if m:
                try:
                    size = float(m.group(1))
                except ValueError:
                    pass

            if price > 0 and gps.get("lat"):
                all_rentals.append({
                    "price": price,
                    "size_m2": size,
                    "lat": gps["lat"],
                    "lon": gps["lon"],
                })

        if len(estates) < SREALITY_PER_PAGE:
            break
        time.sleep(REQUEST_DELAY_S)

    return all_rentals


def estimate_rent(listing: Listing, rentals: list[dict], radius_km: float = 5.0) -> tuple[float | None, int]:
    """Estimate monthly rent from nearby rental comps."""
    if not listing.gps_lat or not listing.gps_lon:
        # Fallback: use all district rentals
        if not rentals:
            return None, 0
        nearby = rentals
    else:
        nearby = [
            r for r in rentals
            if haversine_km(listing.gps_lat, listing.gps_lon, r["lat"], r["lon"]) <= radius_km
        ]

    if len(nearby) < 2:
        return None, len(nearby)

    # Method 1: rent per m2 (preferred)
    rents_per_m2 = []
    for r in nearby:
        if r.get("size_m2") and r["size_m2"] > 10:
            rents_per_m2.append(r["price"] / r["size_m2"])

    if rents_per_m2 and listing.size_m2 and listing.size_m2 > 10:
        if len(rents_per_m2) >= 4:
            mean = statistics.mean(rents_per_m2)
            stdev = statistics.stdev(rents_per_m2)
            rents_per_m2 = [r for r in rents_per_m2 if abs(r - mean) <= 2 * stdev]

        if rents_per_m2:
            median_rpm2 = statistics.median(rents_per_m2)
            return median_rpm2 * listing.size_m2, len(nearby)

    # Method 2: raw rent median
    raw_rents = [r["price"] for r in nearby]
    return statistics.median(raw_rents), len(nearby)


# ---------------------------------------------------------------------------
# Local price context
# ---------------------------------------------------------------------------

def compute_district_avg_price_per_m2(listings: list[Listing]) -> float | None:
    prices = [l.price_czk / l.size_m2 for l in listings if l.size_m2 and l.size_m2 > 10]
    return statistics.median(prices) if len(prices) >= 3 else None


# ---------------------------------------------------------------------------
# CZSO live population data
# ---------------------------------------------------------------------------

CZSO_XLSX_URL_2023 = "https://csu.gov.cz/docs/107508/ed031eb8-6b7c-d78a-51cc-92510b906e94/1300722303.xlsx?version=1.0"

# District-level fallback data (used if CZSO download fails)
_FALLBACK_POPULATION = {
    "Usti nad Labem": (118_000, -1.3),
    "Most": (64_000, -3.0),
    "Teplice": (127_000, -1.6),
    "Decin": (127_000, -2.3),
    "Chomutov": (122_000, -2.4),
    "Karvina": (230_000, -6.5),
    "Frydek-Mistek": (213_000, 0.0),
    "Novy Jicin": (150_000, -0.7),
    "Opava": (174_000, -1.1),
    "Kladno": (167_000, 1.8),
}


def load_czso_population() -> dict[str, dict]:
    """Download and parse CZSO population XLSX. Returns {district_name: {population, avg_age}}."""
    try:
        import openpyxl
    except ImportError:
        print("    openpyxl not installed, using fallback population data")
        return {}

    print("    Downloading CZSO population data...", end=" ", flush=True)
    try:
        resp = requests.get(CZSO_XLSX_URL_2023, timeout=30,
                          headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            print(f"HTTP {resp.status_code}")
            return {}
    except Exception as e:
        print(f"failed: {e}")
        return {}

    wb = openpyxl.load_workbook(io.BytesIO(resp.content), read_only=True, data_only=True)
    ws = wb.active

    result = {}
    current_district = None

    for row in ws.iter_rows(min_row=5, values_only=True):
        col_a = str(row[0]).strip() if row[0] else ""
        col_b = str(row[1]).strip() if row[1] else ""
        col_c = str(row[2]).strip() if row[2] else ""

        # District header row: "Okres Usti nad Labem" or "Okres Most"
        if col_a.startswith("Okres"):
            current_district = col_a.replace("Okres ", "").strip()
            continue

        # Municipality data row
        if col_a.startswith("CZ") and col_b and row[3]:
            pop = int(row[3]) if row[3] else None
            avg_age = float(row[6]) if row[6] else None

            # Store municipality-level data
            muni_name = col_c.strip()
            if muni_name and pop:
                result[muni_name] = {
                    "population": pop,
                    "avg_age": avg_age,
                    "district": current_district,
                    "municipality_code": col_b,
                }

            # Also accumulate district-level totals
            if current_district:
                district_key = f"__district__{current_district}"
                if district_key not in result:
                    result[district_key] = {"population": 0, "district": current_district}
                if pop:
                    result[district_key]["population"] += pop

    wb.close()
    print(f"{len(result)} entries loaded")
    return result


def get_population_info(district_name: str, czso_data: dict) -> tuple[int | None, float | None]:
    """Get population and trend for a district."""
    # Try CZSO live data first (district-level aggregate)
    district_key = f"__district__{district_name}"

    # Try various name normalizations
    for key_variant in [district_name, district_name.replace("-", " "),
                        district_name.replace(" ", "-")]:
        dk = f"__district__{key_variant}"
        if dk in czso_data:
            pop = czso_data[dk]["population"]
            # Trend from fallback (CZSO snapshot is single-year)
            fallback = _FALLBACK_POPULATION.get(district_name)
            trend = fallback[1] if fallback else None
            return pop, trend

    # Also try matching by partial name in district values
    for key, val in czso_data.items():
        if key.startswith("__district__"):
            stored_name = val.get("district", "")
            if stored_name and _fuzzy_district_match(district_name, stored_name):
                pop = val["population"]
                fallback = _FALLBACK_POPULATION.get(district_name)
                trend = fallback[1] if fallback else None
                return pop, trend

    # Fallback to hardcoded data
    fallback = _FALLBACK_POPULATION.get(district_name)
    if fallback:
        return fallback[0], fallback[1]
    return None, None


def _fuzzy_district_match(target: str, stored: str) -> bool:
    """Fuzzy match district names (handles diacritics vs ascii)."""
    # Simple: check if major parts match
    target_parts = target.lower().replace("-", " ").split()
    stored_lower = stored.lower()
    return all(p in stored_lower for p in target_parts)


# ---------------------------------------------------------------------------
# Enrichment pipeline
# ---------------------------------------------------------------------------

def enrich_all(listings: list[Listing], detail_cache: dict = None) -> list[EnrichedListing]:
    """Enrich all listings with rental comps, price context, population, POI."""
    if detail_cache is None:
        detail_cache = {}

    enriched = []

    # Load CZSO population data
    czso_data = load_czso_population()

    # Group listings by district
    by_district: dict[str, list[Listing]] = {}
    district_id_map: dict[str, int] = {}
    for did, dname in TARGET_DISTRICTS:
        district_id_map[dname] = did

    for l in listings:
        by_district.setdefault(l.district, []).append(l)

    # Process per district
    for district_name, district_listings in by_district.items():
        district_id = district_id_map.get(district_name)
        print(f"\n  Enriching {district_name} ({len(district_listings)} listings)...")

        # 1. Rental comps (batch per district)
        print(f"    Fetching rental comps...", end=" ", flush=True)
        rentals = []
        if district_id:
            rentals = fetch_rental_listings_for_district(district_id)
        print(f"{len(rentals)} found")
        time.sleep(REQUEST_DELAY_S)

        # 2. District average price/m2
        avg_price_m2 = compute_district_avg_price_per_m2(district_listings)
        if avg_price_m2:
            print(f"    Local avg: {avg_price_m2:,.0f} CZK/m2")

        # 3. Population
        pop, pop_trend = get_population_info(district_name, czso_data)
        if pop:
            trend_str = f"{pop_trend:+.1f}%" if pop_trend is not None else "?"
            print(f"    Population: {pop:,} (trend: {trend_str})")

        # 4. Enrich each listing
        for l in district_listings:
            price_m2 = (l.price_czk / l.size_m2) if (l.size_m2 and l.size_m2 > 10) else None

            discount = None
            if price_m2 and avg_price_m2:
                discount = ((price_m2 - avg_price_m2) / avg_price_m2) * 100

            est_rent, comp_count = estimate_rent(l, rentals)

            gross_yield = None
            if est_rent and l.price_czk > 0:
                gross_yield = (est_rent * 12 / l.price_czk) * 100

            poi_score, poi_details = 0.0, {}
            detail = detail_cache.get(l.source_id)
            if detail:
                poi_score, poi_details = _extract_poi(detail)

            enriched.append(EnrichedListing(
                listing=l,
                price_per_m2=price_m2,
                local_avg_price_per_m2=avg_price_m2,
                price_discount_pct=discount,
                estimated_monthly_rent=est_rent,
                rental_comp_count=comp_count,
                gross_annual_yield_pct=gross_yield,
                population=pop,
                population_trend_5y_pct=pop_trend,
                poi_score=poi_score,
                poi_details=poi_details,
            ))

    return enriched


def save_enriched(enriched: list[EnrichedListing], filename: str = "last_enriched.json") -> str:
    path = os.path.join(DIR, filename)
    data = {
        "enriched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(enriched),
        "listings": [e.to_dict() for e in enriched],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def print_enrichment_summary(enriched: list[EnrichedListing]):
    yields = [e.gross_annual_yield_pct for e in enriched if e.gross_annual_yield_pct]
    discounts = [e.price_discount_pct for e in enriched if e.price_discount_pct]
    with_rent = [e for e in enriched if e.estimated_monthly_rent]
    sources = {}
    for e in enriched:
        s = e.listing.source
        sources[s] = sources.get(s, 0) + 1

    print(f"\n{'=' * 65}")
    print(f"  ENRICHMENT RESULTS")
    print(f"{'=' * 65}")
    print(f"  Total: {len(enriched)} ({', '.join(f'{s}={n}' for s, n in sorted(sources.items()))})")
    print(f"  With rent estimate: {len(with_rent)}")
    if yields:
        print(f"  Yield range: {min(yields):.1f}% - {max(yields):.1f}%")
        print(f"  Median yield: {statistics.median(yields):.1f}%")
    if discounts:
        below = [d for d in discounts if d < 0]
        print(f"  Below-average price: {len(below)} listings")
    print(f"{'=' * 65}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    scan_path = os.path.join(DIR, "last_scan.json")
    if not os.path.exists(scan_path):
        print("No last_scan.json found. Run scanner.py first.")
        exit(1)

    with open(scan_path, "r") as f:
        scan_data = json.load(f)

    listings = [Listing.from_dict(d) for d in scan_data["listings"]]
    print(f"Loaded {len(listings)} listings from last scan")

    enriched = enrich_all(listings)
    print_enrichment_summary(enriched)

    path = save_enriched(enriched)
    print(f"Saved to {path}")
