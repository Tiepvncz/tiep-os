# Czech Real Estate Data Sources - Research

Last updated: 2026-04-06

---

## 1. sreality.cz

**Status: Has a public REST API - best data source**

### API Endpoint
- Base: `https://www.sreality.cz/api/cs/v2/estates`
- Single listing: `https://www.sreality.cz/api/cs/v2/estates/{hash_id}`
- Count: `https://www.sreality.cz/api/cs/v2/estates/count`
- RSS: `https://www.sreality.cz/api/cs/v2/estates/rss`
- Clusters/map: `https://www.sreality.cz/api/cs/v2/clusters`

### Filter Parameters
| Parameter | Values |
|-----------|--------|
| `category_main_cb` | 1=byt (apartment), 2=dum (house), 3=pozemek (land), 4=komercni (commercial), 5=ostatni (other) |
| `category_type_cb` | 1=prodej (sale), 2=pronajem (rent), 3=drazba (auction), 4=prodej podilu (share sale) |
| `category_sub_cb` | 2=1+kk, 3=1+1, 4=2+kk, 5=2+1, 6=3+kk, 7=3+1, 8=4+kk, 9=4+1, 10=5+kk, 11=5+1, 12=6+, 16=atypicky, 37=rodinny dum, 39=vila, 47=pokoj |
| `locality_country_id` | 112=Czech Republic |
| `locality_region_id` | Region ID (e.g., 10=Prague) |
| `locality_district_id` | District ID (e.g., 72=Brno-mesto, 73=Brno-venkov) |
| `czk_price_summary_order2` | Price range (from/to) |
| `per_page` | Results per page |
| `page` | Page number |

### Data Fields - List View
Per listing in search results:
- `hash_id` - unique listing ID
- `name` - title (e.g., "Pronajem bytu 2+kk 50 m2")
- `price` - numeric price
- `price_czk` - structured: `value_raw`, `unit` ("za mesic" for rent), `name`
- `locality` - human-readable address
- `gps` - `lat`, `lon`
- `category` - property category code
- `type` - listing type code
- `seo` - contains `category_main_cb`, `category_sub_cb`, `category_type_cb`, `locality` slug
- `labels` - array of tags (e.g., "Po rekonstrukci", "Tramvaj 5 min. pesky")
- `labelsAll` - detailed label arrays (property features + nearby amenities)
- `has_video`, `has_panorama`, `has_floor_plan`, `has_matterport_url`
- `advert_images_count`
- `new` - boolean, is new listing
- `exclusively_at_rk` - exclusive to agency
- `region_tip` - region suggestion ID

### Data Fields - Detail View
Rich detail per listing:
- `items` array with named attributes:
  - Celkova cena (total price), Poznamka k cene (price note)
  - ID zakazky (order ID), Aktualizace (last update)
  - Stavba (construction type: Cihlova/brick, panelova/panel, etc.)
  - Stav objektu (condition: Po rekonstrukci, nova stavba, etc.)
  - Vlastnictvi (ownership: Osobni/personal, druzstevni/cooperative)
  - Podlazi (floor info)
  - Uzitna plocha (usable area in m2)
  - Sklep (cellar), Parkovani (parking)
  - Rok rekonstrukce (renovation year)
  - Utilities: Voda, Plyn, Odpad, Elektrina, Komunikace
  - Energeticka narocnost budovy (energy rating)
- `text` - full description
- `locality` - detailed location info
- `map` - GPS coordinates
- POI data: `poi_doctors`, `poi_transport`, `poi_school_kindergarten`, `poi_grocery`, `poi_restaurant`, `poi_leisure_time` - each with nearby places, distances, ratings
- `_embedded.company` - listing agency info
- Image URLs via `_links`

### Anti-scraping Measures
- No authentication required
- User-Agent header recommended
- No known strict rate limiting, but reasonable request pacing advised
- Multiple open-source scrapers exist and work reliably

### Open-Source Scrapers
- github.com/adamhosticka/Sreality-Scraper (Scrapy)
- github.com/karlosmatos/sreality-scraper
- github.com/mecv01/sreality-client (JS API client)
- github.com/JirkaZelenka/Sreality (Python)
- Multiple Apify actors available

---

## 2. bazos.cz (Reality Section)

**Status: Has an undocumented mobile app API**

### API Endpoints
- Ads list: `https://www.bazos.cz/api/v1/ads.php`
- Ad detail: `https://www.bazos.cz/api/v1/ad-detail-2.php`
- User ratings: `https://www.bazos.cz/api/v1/ratings.php`

### Required Headers
```
User-Agent: bazos/2.12.1 (cz.ackee.bazos; build:3582; android 13; model:Pixel)
x-deviceid: {8-digit number}
```

### Search Parameters
| Parameter | Description |
|-----------|-------------|
| `section` | "RE" for reality/real estate |
| `offset` | Starting position (0, increments of 20, max 200) |
| `limit` | Results per page (must be 20 - other values risk ban) |
| `query` | Search text |
| `price_from` | Minimum price |
| `price_to` | Maximum price |
| `sort` | date, price_asc, price_desc, distance |
| `email` | Filter by seller email |
| `phone` | Filter by seller phone |
| `latitude`, `longitude` | For distance-based sorting |

### Data Fields - List View
- `id` - ad ID
- `title` - listing title
- `price_formatted` - formatted price string
- `currency` - "CZK"
- `image_thumbnail` - thumbnail URL
- `locality` - location name
- `url` - full listing URL
- `from` - posting date
- `topped` - promoted listing flag
- `views` - view count

### Data Fields - Detail View
- `idad` - ad ID
- `status` - active/deleted
- Additional detail fields available for active listings (title, description, price, contact info, images)

### Anti-scraping Measures
- Must use mobile app User-Agent header format
- Must include x-deviceid header
- Limit must be exactly 20 (other values trigger bans)
- Fast repetitive requests cause IP blocking
- Offset max is 200 (limits deep pagination)

### URL Structure (Web)
- `https://reality.bazos.cz/` - reality section homepage
- `https://reality.bazos.cz/inzerat/{id}/{slug}.php` - individual listing

---

## 3. bezrealitky.cz

**Status: No stable public API - requires scraping**

### Access Method
Bezrealitky is a Next.js SPA. It previously had internal API endpoints (e.g., `/api/record/markers`) but these appear to have been restructured or protected. Current approaches:

- **HTML scraping** with headless browser (Puppeteer/Playwright) due to JS-rendered content
- **Apify actors** available as pre-built solutions

### URL Structure (Web)
- Search: `https://www.bezrealitky.cz/vyhledat?offerType=PRONAJEM&estateType=BYT&...`
- English: `https://www.bezrealitky.com/search`
- Offer types in URL: PRONAJEM (rent), PRODEJ (sale)
- Estate types: BYT (apartment), DUM (house), POZEMEK (land)
- Region filtering via OSM relation IDs

### Data Fields (from scraped listings)
- Price (total and per m2)
- Property type, size (m2), layout (disposition)
- Location (address, GPS)
- Description
- Images
- Agency/owner info (this portal is "bez realitky" - without agents, so mostly direct owners)
- Energy rating
- Available from date
- Amenities and features

### Anti-scraping Measures
- JavaScript-rendered content (no simple HTTP scraping)
- API endpoints return HTML pages rather than JSON when called directly
- Likely Cloudflare or similar protection
- Rate limiting probable

### Existing Scrapers
- github.com/Bralor/bezrealitky_scraper (Python + MongoDB)
- github.com/adampeterka/Bezrealitky (API + web scraping combo)
- github.com/Coelurus/bezRealitky-Apify-Scraper (Apify)
- Apify actor: apify.com/bebich/bezrealitky

---

## 4. reality.idnes.cz

**Status: No public API - HTML scraping required**

### URL Structure
- Search base: `https://reality.idnes.cz/s/{type}/{property}/{location}/`
- Examples:
  - Sale apartments Prague: `https://reality.idnes.cz/s/prodej/byty/praha/`
  - Rent houses Brno: `https://reality.idnes.cz/s/pronajem/domy/brno/`
  - Sale with price filter: `https://reality.idnes.cz/s/prodej/domy/cena-do-7000000/`
- Detail page: `https://reality.idnes.cz/detail/{type}/{property}/{location-slug}/{id}/`

### Search/Filter Parameters (URL-based)
- Transaction type: `prodej` (sale), `pronajem` (rent)
- Property type: `byty` (apartments), `domy` (houses), `pozemky` (land)
- Location: city/region slug in URL path
- Price filters: `cena-od-X`, `cena-do-X` in URL path
- Additional filters for size, disposition, etc.

### Data Fields (scraped from HTML)
- Price
- Property type and size (m2)
- Layout/disposition
- Location (address)
- Description text
- Images
- Agency info
- Energy class
- Construction type
- Listing date

### Anti-scraping Measures
- Part of the iDNES/MAFRA media group - standard web protection
- Requires cookie acceptance (GDPR consent)
- Server-side rendered HTML (easier to scrape than SPAs)
- Sitemap available for discovery
- No known aggressive anti-bot measures

### Existing Scrapers
- Apify: apify.com/jakubkonecny/idnes-reality-actor (Crawlee-based)
- github.com/supermartzin/real-estates-watcher (C#, multi-portal)

### Import Interface
- reality.idnes.cz has an import/XML interface for agencies to push listings
- Spec document: `http://reality.idnes.cz/doc/reality-specifikace.zip`

---

## 5. Czech Statistical Office (CZSO/CSU)

**Status: Open data with API access**

### Data Portals
- Main open data page: `https://csu.gov.cz/open-data`
- Public database (VDB): `https://vdb.czso.cz/`
- National catalog: `https://data.gov.cz/` (filter by CZSO publisher)
- Local catalog (LKOD): `https://vdb.czso.cz/pll/eweb/`

### API Endpoints
- Dataset metadata: `https://vdb.czso.cz/pll/eweb/lkod_ld.datova_sada?id={dataset_id}`
- Package info: `https://vdb.czso.cz/pll/eweb/package_show?id={dataset_id}`
- Codelists: `http://apl.czso.cz/iSMS/cs/cislist.jsp`

### Available Data
- **Population by municipality** - annual data, updated every April
  - Direct download: `https://www.czso.cz/csu/czso/pocet-obyvatel-v-obcich`
  - Breakdowns by: age (vek), gender (pohlavi), territory (uzemi)
- **Demographics** - birth rates, death rates, migration
- **Housing statistics** - housing stock, construction permits
- **Territorial structure** - regions, districts, municipalities
- **Income and living conditions**
- **Population and Housing Census data**

### Data Formats
- CSV files downloadable directly
- Some datasets in ZIP archives
- Codelist/classification systems available

### Programmatic Access
- R package `czso` - `install.packages("czso")` - provides direct access to all CZSO open datasets
- Python: download CSV files directly from dataset URLs
- No authentication required for open data

---

## 6. valuo.cz

**Status: Paid API available - professional real estate valuation**

### What It Provides
- Market price estimates for 2.5M+ apartments and houses in Czech Republic
- Data from the property cadastre (actual sale prices)
- Active listings and 50,000+ matched historical advertisements
- Purchase agreements and ownership certificates
- Price trends by locality, property condition, and size

### Products
- **Valuo MAPA** - interactive price map
- **Valuo PROFI** - professional tool with PDF reports and listing history
- **Valuo INDEX** - monthly price trend tracking
- **Valuo API** - REST API for integration
- **Valuo WIDGET** - embeddable price calculator
- **Valuo HLIDACI PES** - price monitoring/alerts

### API Details
**Price Estimation Endpoint:**
- URL: `https://api.valuo.cz/v1/calculation`
- Method: POST (JSON body)
- Auth: Bearer token

**Required parameters:**
- `place` - GPS coords or address text
- `kind` - 'sale' or 'lease'
- `property_type` - 'flat', 'house', or 'land'
- `floor_area` or `lot_area` - m2
- `rating` - condition ('bad', 'nothing_much', 'good', 'very_good', 'new', 'excellent')

**Optional parameters:** ownership, disposition, construction material, house type, floor, energy rating, room count, balcony/garden/terrace area, accessibility

**Response fields:**
- `avg_price_m2`, `min_price_m2`, `max_price_m2` with standard deviation
- `avg_price`, `min_price`, `max_price` (total)
- Calculation area and distance metrics
- Geographic data (lat, lon)

**Reproduction Cost Endpoint:**
- URL: `https://api.valuo.cz/v1/reproduction-cost`
- Returns building cost breakdown, JKSO classification, parts composition, reproduction cost

### Pricing
- Pay per calculation (no upfront cost)
- Free registration available
- Used by 50+ companies and 3,500+ professionals including banks
- Backed by Pale Fire Capital

### Documentation
- `https://www.valuo.cz/docs/api`
- `https://api.valuo.cz/docs/reproduction-cost.html`

---

## 7. obyvateleceska.cz

**Status: Simple population data visualization - no API**

### What It Provides
- Population counts per Czech municipality from 2004 to 2025
- Data sourced from CZSO, updated annually in late April

### URL Structure
- `https://obyvateleceska.cz/{district}/{city}/{municipality_code}`
- Example: `https://obyvateleceska.cz/ostrava-mesto/ostrava/554821`
- About page: `https://www.obyvateleceska.cz/aplikace`

### Data Access
- No API available
- Display-only website
- Original source data downloadable from CZSO directly at:
  `https://www.czso.cz/csu/czso/pocet-obyvatel-v-obcich`

### Verdict
Skip this portal - go directly to CZSO for the same data in machine-readable format.

---

## 8. Additional Data Sources

### Deloitte Rent Index
- URL: `https://www.deloitte.com/cz-sk/en/Industries/real-estate/collections/rent-index.html`
- Quarterly data on rental prices by region and regional capitals
- Covers: prefab blocks, brick houses, development projects
- Average monthly rent per m2 by area
- RE Port platform: `https://www.deloitte.cz/report/`
- No public API - data available in published reports/PDFs

### FRED (Federal Reserve Economic Data)
- Harmonized Index of Consumer Prices for Czech housing rentals
- Series: `CP0410CZM086NEST`
- URL: `https://fred.stlouisfed.org/series/CP0410CZM086NEST`
- API available (FRED API key required)
- Monthly data from Dec 1999 onwards

### Statista
- Average rent per m2 by area in Czechia
- Paid access required for full data

### Global Property Guide
- Czech Republic rental yields and price history
- `https://www.globalpropertyguide.com/europe/czech-republic/`
- Free access to summary data

---

## Recommended Architecture for Scraping Skill

### Priority Order (data quality x ease of access)
1. **sreality.cz** - Best first target. Clean JSON API, no auth, rich data including POIs. ~15,000+ sale listings, ~4,000+ rental listings.
2. **valuo.cz API** - For price estimates/valuations. Paid but structured.
3. **bazos.cz** - Second scraping target. Mobile API works but fragile (ban risk).
4. **CZSO open data** - For population/demographics context. Free CSV downloads.
5. **bezrealitky.cz** - Harder to scrape (SPA), but useful for owner-direct listings.
6. **reality.idnes.cz** - HTML scraping, standard difficulty.

### Key Metrics Available Across Sources
| Metric | sreality | bazos | bezrealitky | idnes | valuo | CZSO |
|--------|----------|-------|-------------|-------|-------|------|
| Sale price | Y | Y | Y | Y | Y (estimate) | - |
| Rent price | Y | Y | Y | Y | Y (estimate) | - |
| Size (m2) | Y | - | Y | Y | Y (input) | - |
| GPS coords | Y | - | Y | - | Y (input) | - |
| Construction type | Y | - | Y | Y | - | - |
| Energy rating | Y | - | Y | Y | - | - |
| Nearby amenities/POI | Y | - | - | - | - | - |
| Population data | - | - | - | - | - | Y |
| Price per m2 trend | - | - | - | - | Y | - |
| Historical prices | - | - | - | - | Y | - |
