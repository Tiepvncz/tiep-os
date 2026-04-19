"""
Czech Real Estate Scanner - Configuration
All tunable parameters for the investment scanning pipeline.
"""

# ---------------------------------------------------------------------------
# Sreality API
# ---------------------------------------------------------------------------
SREALITY_API_BASE = "https://www.sreality.cz/api/cs/v2/estates"
SREALITY_SUGGEST_URL = "https://www.sreality.cz/api/cs/v2/autocomplete/results"

# Category codes
CATEGORY_MAIN = 1       # byt (apartment)
CATEGORY_TYPE_SALE = 1  # prodej (sale)
CATEGORY_TYPE_RENT = 2  # pronajem (rent)

# Disposition codes (category_sub_cb)
# 2=1+kk, 3=1+1, 4=2+kk, 5=2+1, 6=3+kk, 7=3+1, 8=4+kk, 9=4+1
DISPOSITIONS_SALE = [4, 5, 6, 7]   # 2+kk through 3+1
DISPOSITIONS_RENT = [2, 3, 4, 5, 6, 7, 8, 9]  # wider for comps

DISPOSITION_LABELS = {
    2: "1+kk", 3: "1+1", 4: "2+kk", 5: "2+1",
    6: "3+kk", 7: "3+1", 8: "4+kk", 9: "4+1",
    10: "5+kk", 11: "5+1", 12: "6+",  16: "atypicky",
}

# ---------------------------------------------------------------------------
# Investment criteria (Martin Korenek workshop)
# ---------------------------------------------------------------------------
PRICE_MIN_CZK = 800_000
PRICE_MAX_CZK = 2_500_000

# Ownership filter: only osobni (personal), never druzstevni (cooperative)
REQUIRED_OWNERSHIP = "osobni"
BLOCKED_OWNERSHIP = ["druzstevni", "družstevní", "cooperative"]

# Localities to skip (known problem areas - cheap but not investable)
BLOCKED_LOCALITIES = [
    "mojžíř", "mojzir",
    "chanov",
    "janov",  # Most-Janov
]

# Target districts: (district_id, name)
# IDs verified against sreality suggest API
# Secondary cities - law of convergence strategy
TARGET_DISTRICTS = [
    # Northern Bohemia (Ustecky kraj, region 4) - undervalued per workshop
    (27, "Usti nad Labem"),
    (25, "Most"),
    (26, "Teplice"),
    (19, "Decin"),
    (20, "Chomutov"),
    # Moravia-Silesia (Moravskoslezsky kraj, region 12)
    (62, "Karvina"),
    (61, "Frydek-Mistek"),
    (63, "Novy Jicin"),
    (64, "Opava"),
    # Central Bohemia (Stredocesky kraj, region 11)
    (50, "Kladno"),
]

# ---------------------------------------------------------------------------
# API request settings
# ---------------------------------------------------------------------------
SREALITY_PER_PAGE = 60
SREALITY_MAX_PAGES = 5  # deeper search
REQUEST_DELAY_S = 0.8
REQUEST_TIMEOUT_S = 15
MAX_RETRIES = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------
RENTAL_COMP_RADIUS_KM = 5.0
MICRO_LOCAL_RADIUS_M = 800
MIN_RENTAL_COMPS = 2

# ---------------------------------------------------------------------------
# Scoring weights (must sum to 1.0)
# ---------------------------------------------------------------------------
W_YIELD = 0.30
W_PRICE_DISCOUNT = 0.25
W_POPULATION = 0.20
W_INFRASTRUCTURE = 0.15
W_LISTING_QUALITY = 0.10

# Yield scoring thresholds
YIELD_EXCELLENT = 8.0   # score = 100
YIELD_POOR = 3.0        # score = 0

# Novostavby (new builds)
# Sreality building_condition=1 filters for new construction
SREALITY_BUILDING_CONDITION_NEW = 1
# For novostavby mode, widen dispositions to include smaller units (1+kk)
DISPOSITIONS_NOVOSTAVBY = [2, 3, 4, 5, 6, 7, 8, 9]  # 1+kk through 4+1
# Novostavby can be pricier - allow higher range
NOVOSTAVBY_PRICE_MAX_CZK = 4_000_000

# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------
# Claude analysis: analyze all listings scoring >= this threshold
CLAUDE_SCORE_THRESHOLD = 65
# Vision analysis: analyze photos for listings scoring >= this threshold
VISION_SCORE_THRESHOLD = 60

# ---------------------------------------------------------------------------
# Sreality detail field labels (Czech)
# ---------------------------------------------------------------------------
LABEL_USABLE_AREA = "Užitná plocha"
LABEL_FLOOR_AREA = "Plocha podlahová"
LABEL_CONSTRUCTION = "Stavba"
LABEL_CONDITION = "Stav objektu"
LABEL_OWNERSHIP = "Vlastnictví"
LABEL_ENERGY_RATING = "Energetická náročnost budovy"
LABEL_FLOOR = "Podlaží"

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
import os
DIR = os.path.dirname(os.path.abspath(__file__))
