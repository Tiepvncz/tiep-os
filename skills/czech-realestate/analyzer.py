"""
Czech Real Estate Scanner - Scoring, Claude Analysis & Investment Dashboard
Ranks properties, then runs deep Claude analysis as both advocate AND devil's advocate.
"""
import json
import os
import sys
import time
import statistics
from datetime import datetime, timezone

from config import (
    W_YIELD, W_PRICE_DISCOUNT, W_POPULATION, W_INFRASTRUCTURE, W_LISTING_QUALITY,
    YIELD_EXCELLENT, YIELD_POOR, CLAUDE_SCORE_THRESHOLD, VISION_SCORE_THRESHOLD, DIR,
)
from models import Listing, EnrichedListing, ScoredListing


# ---------------------------------------------------------------------------
# Scoring functions (each returns 0-100)
# ---------------------------------------------------------------------------

def score_yield(gross_yield_pct: float | None) -> float:
    if gross_yield_pct is None:
        return 0.0
    if gross_yield_pct >= YIELD_EXCELLENT:
        return 100.0
    if gross_yield_pct <= YIELD_POOR:
        return 0.0
    return (gross_yield_pct - YIELD_POOR) / (YIELD_EXCELLENT - YIELD_POOR) * 100


def score_price_discount(discount_pct: float | None) -> float:
    if discount_pct is None:
        return 50.0
    clamped = max(-20, min(20, discount_pct))
    return (20 - clamped) / 40 * 100


def score_population(trend_pct: float | None) -> float:
    """Moderate decline is normal for secondary cities - don't penalize.
    Only drastic decline (>5%) is a real red flag."""
    if trend_pct is None:
        return 50.0
    # Growing = great, flat/mild decline = fine, drastic decline = concern
    # +3% = 100, -2% = 60 (still good), -5% = 30, -8% = 0
    clamped = max(-8, min(3, trend_pct))
    return (clamped + 8) / 11 * 100


def score_infrastructure(poi_score: float | None) -> float:
    if poi_score is None:
        return 30.0
    return min(poi_score * 10, 100.0)


def _is_ground_floor(floor_str: str | None) -> bool:
    """Detect ground floor (1. podlazi, parter, prizemi, etc.)."""
    if not floor_str:
        return False
    f = floor_str.lower().strip()
    if any(kw in f for kw in ["přízemí", "prizemi", "parter", "suterén", "suteren"]):
        return True
    # "1. podlazi" = ground floor in Czech (floors count from 1)
    if f.startswith("1.") or f.startswith("1 ") or f == "1":
        return True
    return False


def score_listing_quality(listing: Listing) -> float:
    # Most listings are incomplete - agents don't fill everything.
    # Don't punish too hard for missing fields. Start at 50 (neutral).
    score = 50.0
    if listing.size_m2:
        score += 15
    if listing.gps_lat:
        score += 15
    if listing.source == "bezrealitky":
        score += 15  # owner-direct = no commission premium
    if listing.ownership and "osobn" in listing.ownership.lower():
        score += 5  # confirmed osobni is a plus
    # Ground floor penalty - people prefer privacy, harder to rent
    if _is_ground_floor(listing.floor):
        score -= 20
    return max(0, min(score, 100.0))


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------

def compute_score(enriched: EnrichedListing) -> ScoredListing:
    ys = score_yield(enriched.gross_annual_yield_pct)
    ds = score_price_discount(enriched.price_discount_pct)
    ps = score_population(enriched.population_trend_5y_pct)
    infra = score_infrastructure(enriched.poi_score)
    qs = score_listing_quality(enriched.listing)

    composite = (
        ys * W_YIELD
        + ds * W_PRICE_DISCOUNT
        + ps * W_POPULATION
        + infra * W_INFRASTRUCTURE
        + qs * W_LISTING_QUALITY
    )

    return ScoredListing(
        enriched=enriched,
        composite_score=composite,
        yield_score=ys,
        discount_score=ds,
        population_score=ps,
        infrastructure_score=infra,
        listing_quality_score=qs,
    )


def rank_all(enriched: list[EnrichedListing]) -> list[ScoredListing]:
    scored = [compute_score(e) for e in enriched]
    scored.sort(key=lambda s: s.composite_score, reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Claude deep analysis (advocate + devil's advocate)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a senior Czech real estate investment analyst with 15 years of experience. You follow the Martin Korenek methodology.

Your job: evaluate each property as an investment for a buyer who wants OSOBNI (personal) ownership apartments in secondary Czech cities at CZK 0.8-2.5M. The investor is practical - speed over perfection. "80% good deal acted on fast beats 100% deal found too late."

IMPORTANT CONTEXT:
- All listings have already been filtered to exclude druzstevni (cooperative) and known problem areas (Mojzir, Chanov, Janov etc.)
- Many listings have incomplete data - Czech real estate agents rarely fill all fields. Missing construction type or energy rating is NORMAL, not suspicious. Do NOT penalize missing fields.
- Panel (panelova) vs brick (cihlova) construction does NOT matter for this investor.
- Ground floor (1. podlazi, prizemi) is a NEGATIVE - tenants dislike lack of privacy. Flag it.
- The investor understands these are secondary cities with declining populations. That's the THESIS - convergence play. Don't flag population decline as a dealbreaker, it's already priced in.

## Your framework:

### AS ADVOCATE:
- Yield potential (rent/price ratio)
- Price discount vs local market - is this genuinely below-average value?
- Convergence potential - will this location appreciate over 5-10 years?
- Rental demand signals
- Quick financing path (osobni ownership = standard mortgage)

### AS DEVIL'S ADVOCATE:
- Is the yield real? Could the rent estimate be inflated vs what you'd actually get?
- Vacancy risk - how easy to find tenants in this specific location?
- Micro-location issues - is this the bad part of an otherwise OK town?
- Hidden costs (SVJ fees, needed reconstruction, old plumbing/wiring)?
- Liquidity - how easy to resell if you need to exit?

### VERDICTS:
- BUY: Solid fundamentals. Good yield, reasonable price, no major red flags. ACT FAST.
- WATCH: Promising but verify 1-2 specific things first (name them).
- SKIP: Material risk that can't be mitigated.

Be specific and practical. This investor wants actionable calls, not academic hedging.

Respond in JSON format ONLY."""

USER_PROMPT_TEMPLATE = """Analyze this property for investment:

LISTING:
  Title: {title}
  Price: {price:,} CZK ({price_m2_str} CZK/m2)
  Size: {size} m2 | Layout: {disposition}
  Location: {locality}, district {district}
  Construction: {construction} | Condition: {condition}
  Ownership: {ownership} | Energy: {energy}
  Floor: {floor}
  Source: {source} | URL: {url}

INVESTMENT METRICS:
  Local avg price/m2: {avg_price_m2} CZK
  Price vs local avg: {discount}
  Estimated monthly rent: {rent} CZK (from {comp_count} comps)
  Gross annual yield: {gross_yield}
  District population: {population} (5yr trend: {pop_trend})
  Infrastructure score: {poi}/10
  Composite score: {score:.1f}/100

  Score breakdown:
    Yield: {yield_score:.0f}/100 (weight 30%)
    Price discount: {discount_score:.0f}/100 (weight 25%)
    Population: {pop_score:.0f}/100 (weight 20%)
    Infrastructure: {infra_score:.0f}/100 (weight 15%)
    Data quality: {quality_score:.0f}/100 (weight 10%)

Respond with this exact JSON:
{{
  "verdict": "BUY" or "WATCH" or "SKIP",
  "confidence": "HIGH" or "MEDIUM" or "LOW",
  "one_liner": "One sentence pitch or warning",
  "bull_case": "2-3 sentences on why to buy",
  "bear_case": "2-3 sentences on why NOT to buy",
  "risks": ["specific risk 1", "specific risk 2", "specific risk 3"],
  "opportunities": ["specific opportunity 1", "specific opportunity 2"],
  "key_question": "The ONE thing the investor should verify before acting",
  "estimated_5yr_appreciation_pct": number,
  "estimated_true_yield_after_costs_pct": number
}}"""


def analyze_with_claude(scored: list[ScoredListing], api_key: str) -> list[ScoredListing]:
    """Deep Claude analysis on candidates scoring >= CLAUDE_SCORE_THRESHOLD."""
    try:
        import anthropic
    except ImportError:
        print("  anthropic package not installed, skipping Claude analysis")
        return scored

    client = anthropic.Anthropic(api_key=api_key)
    candidates = [s for s in scored if s.composite_score >= CLAUDE_SCORE_THRESHOLD]

    if not candidates:
        print(f"  No listings scored >= {CLAUDE_SCORE_THRESHOLD}. Analyzing top 5 instead.")
        candidates = scored[:5]

    print(f"  {len(candidates)} listings qualify for deep analysis (score >= {CLAUDE_SCORE_THRESHOLD})")

    for i, s in enumerate(candidates):
        l = s.listing
        e = s.enriched
        print(f"\n  [{i+1}/{len(candidates)}] {l.disposition or '?'} | {l.locality[:40]} | {l.price_czk:,} CZK (score: {s.composite_score:.1f})")

        prompt = USER_PROMPT_TEMPLATE.format(
            title=l.title,
            price=l.price_czk,
            price_m2_str=f"{e.price_per_m2:,.0f}" if e.price_per_m2 else "N/A",
            size=l.size_m2 or "N/A",
            disposition=l.disposition or "N/A",
            locality=l.locality,
            district=l.district,
            construction=l.construction_type or "unknown",
            condition=l.condition or "unknown",
            ownership=l.ownership or "unknown",
            energy=l.energy_rating or "unknown",
            floor=l.floor or "unknown",
            source=l.source,
            url=l.url,
            avg_price_m2=f"{e.local_avg_price_per_m2:,.0f}" if e.local_avg_price_per_m2 else "N/A",
            discount=f"{e.price_discount_pct:+.1f}%" if e.price_discount_pct is not None else "N/A",
            rent=f"{e.estimated_monthly_rent:,.0f}" if e.estimated_monthly_rent else "N/A",
            comp_count=e.rental_comp_count,
            gross_yield=f"{e.gross_annual_yield_pct:.1f}%" if e.gross_annual_yield_pct else "N/A",
            population=f"{e.population:,}" if e.population else "N/A",
            pop_trend=f"{e.population_trend_5y_pct:+.1f}%" if e.population_trend_5y_pct is not None else "N/A",
            poi=f"{e.poi_score:.1f}" if e.poi_score else "N/A",
            score=s.composite_score,
            yield_score=s.yield_score,
            discount_score=s.discount_score,
            pop_score=s.population_score,
            infra_score=s.infrastructure_score,
            quality_score=s.listing_quality_score,
        )

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=800,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            result = json.loads(text)
            s.claude_verdict = result.get("verdict", "SKIP")
            s.claude_summary = result.get("one_liner", "")
            s.claude_risks = result.get("risks", [])
            s.claude_analysis = result  # Store full analysis
            print(f"    -> {s.claude_verdict} ({result.get('confidence', '?')}) - {s.claude_summary[:60]}")

        except json.JSONDecodeError:
            print(f"    -> Failed to parse Claude response")
            s.claude_verdict = "ERROR"
            s.claude_summary = "Analysis response was not valid JSON"
        except Exception as ex:
            err_str = str(ex)
            print(f"    -> Error: {ex}")
            s.claude_verdict = "ERROR"
            s.claude_summary = err_str[:100]
            # Stop early if credit/auth issue - no point retrying
            if "credit balance" in err_str or "authentication" in err_str.lower():
                print(f"    -> Stopping Claude analysis (API credit/auth issue)")
                break

        time.sleep(1)

    return scored


# ---------------------------------------------------------------------------
# Vision analysis - analyze listing photos
# ---------------------------------------------------------------------------

VISION_SYSTEM = """You are a Czech real estate investment photo analyst. Evaluate apartment photos for investment quality.

Look for:
- Overall condition: walls, floors, windows, bathroom, kitchen
- Renovation level: recently renovated vs needs work vs original state
- Layout quality: room sizes, natural light, storage
- Red flags: mold, water damage, cracked walls, outdated wiring, old pipes visible
- Building exterior: facade condition, common areas, surroundings
- Green flags: new windows, modern bathroom, nice flooring, balcony

Rate the visual condition 1-10 and provide actionable notes for the investor.
Respond in JSON: {"visual_score": number, "condition_summary": "...", "red_flags": [...], "green_flags": [...], "renovation_estimate": "none/cosmetic/partial/full"}"""


def analyze_photos(scored: list[ScoredListing], api_key: str) -> list[ScoredListing]:
    """Analyze photos with Claude Vision for listings scoring >= VISION_SCORE_THRESHOLD."""
    try:
        import anthropic
        import base64
        import requests as req
    except ImportError:
        print("  Missing packages for vision analysis")
        return scored

    client = anthropic.Anthropic(api_key=api_key)
    candidates = [s for s in scored if s.composite_score >= VISION_SCORE_THRESHOLD and s.listing.images]

    if not candidates:
        print(f"  No listings with images scoring >= {VISION_SCORE_THRESHOLD}")
        return scored

    print(f"  {len(candidates)} listings qualify for photo analysis")

    for i, s in enumerate(candidates):
        l = s.listing
        images = l.images[:4]  # Max 4 photos
        if not images:
            continue

        print(f"  [{i+1}/{len(candidates)}] Analyzing {len(images)} photos for {l.disposition or '?'} | {l.locality[:30]}...", end=" ", flush=True)

        # Build image content blocks
        content = []
        for img_url in images:
            content.append({
                "type": "image",
                "source": {"type": "url", "url": img_url},
            })
        content.append({
            "type": "text",
            "text": f"Analyze these photos of a {l.disposition or 'N/A'} apartment in {l.locality}, {l.district}. Price: {l.price_czk:,} CZK. Listed condition: {l.condition or 'unknown'}. Respond in JSON only."
        })

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                system=VISION_SYSTEM,
                messages=[{"role": "user", "content": content}],
            )
            text = response.content[0].text.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            result = json.loads(text)
            # Store vision analysis
            if not s.claude_analysis:
                s.claude_analysis = {}
            s.claude_analysis["vision"] = result
            print(f"-> {result.get('visual_score', '?')}/10 ({result.get('renovation_estimate', '?')})")

        except Exception as ex:
            err_str = str(ex)
            print(f"-> Error: {err_str[:60]}")
            if "credit balance" in err_str or "authentication" in err_str.lower():
                print(f"    -> Stopping vision analysis (API credit/auth issue)")
                break

        time.sleep(0.5)

    return scored


# ---------------------------------------------------------------------------
# Investment Dashboard
# ---------------------------------------------------------------------------

def print_dashboard(scored: list[ScoredListing]):
    """Print a clean investment dashboard."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    has_claude = any(s.claude_verdict for s in scored)

    # Header
    print(f"\n{'=' * 75}")
    print(f"  CZECH REAL ESTATE INVESTMENT DASHBOARD")
    print(f"  Generated: {now}")
    print(f"  Properties analyzed: {len(scored)}")
    print(f"{'=' * 75}")

    # Market overview
    _print_market_overview(scored)

    # Top picks with Claude analysis
    if has_claude:
        _print_top_picks(scored)
        _print_watchlist(scored)
    else:
        _print_ranked_list(scored)

    # Summary stats
    _print_district_heatmap(scored)


def _print_market_overview(scored: list[ScoredListing]):
    """Quick market stats."""
    yields = [s.enriched.gross_annual_yield_pct for s in scored if s.enriched.gross_annual_yield_pct]
    prices_m2 = [s.enriched.price_per_m2 for s in scored if s.enriched.price_per_m2]
    sources = {}
    for s in scored:
        src = s.listing.source
        sources[src] = sources.get(src, 0) + 1

    print(f"\n  MARKET SNAPSHOT")
    print(f"  {'-' * 50}")
    if yields:
        print(f"  Yields:     {min(yields):.1f}% - {max(yields):.1f}% (median {statistics.median(yields):.1f}%)")
    if prices_m2:
        print(f"  Price/m2:   {min(prices_m2):,.0f} - {max(prices_m2):,.0f} CZK (median {statistics.median(prices_m2):,.0f})")
    print(f"  Sources:    {', '.join(f'{s}: {n}' for s, n in sorted(sources.items()))}")
    above_7 = sum(1 for y in yields if y >= 7.0)
    print(f"  Yield > 7%: {above_7} properties ({above_7 * 100 // len(yields) if yields else 0}%)")


def _print_top_picks(scored: list[ScoredListing]):
    """Print BUY verdicts with full analysis."""
    buys = [s for s in scored if s.claude_verdict == "BUY"]

    print(f"\n  {'=' * 70}")
    print(f"  TOP PICKS - BUY ({len(buys)})")
    print(f"  {'=' * 70}")

    if not buys:
        print(f"  No BUY verdicts this scan. Market may be overheated or criteria too strict.")
        return

    for rank, s in enumerate(buys, 1):
        l = s.listing
        e = s.enriched
        a = getattr(s, 'claude_analysis', {}) or {}

        source_tag = f"[{l.source}]"
        if l.source == "bezrealitky":
            source_tag = "[bezrealitky - OWNER DIRECT]"

        print(f"\n  #{rank} {source_tag}")
        print(f"  {'~' * 65}")
        print(f"  {l.title}")
        print(f"  {l.locality}, {l.district}")
        print(f"  {l.url}")
        print()
        print(f"  Price:    {l.price_czk:>12,} CZK  ({e.price_per_m2:,.0f} CZK/m2)" if e.price_per_m2 else f"  Price:    {l.price_czk:>12,} CZK")
        print(f"  Size:     {l.size_m2 or 'N/A':>12} m2   Layout: {l.disposition or 'N/A'}")
        print(f"  Yield:    {e.gross_annual_yield_pct:>11.1f}%   Rent est: {e.estimated_monthly_rent:,.0f} CZK/mo" if e.gross_annual_yield_pct and e.estimated_monthly_rent else f"  Yield:    N/A")
        print(f"  vs Local: {e.price_discount_pct:>+10.1f}%   (avg {e.local_avg_price_per_m2:,.0f} CZK/m2)" if e.price_discount_pct is not None and e.local_avg_price_per_m2 else "")
        print(f"  Build:    {l.construction_type or 'N/A':>12}   Own: {l.ownership or 'N/A'}   Energy: {l.energy_rating or 'N/A'}")
        print(f"  Score:    {s.composite_score:>11.1f}/100")

        print(f"\n  BULL CASE: {a.get('bull_case', 'N/A')}")
        print(f"  BEAR CASE: {a.get('bear_case', 'N/A')}")

        if a.get('risks'):
            print(f"  RISKS: {' | '.join(a['risks'][:3])}")
        if a.get('opportunities'):
            print(f"  OPPS:  {' | '.join(a['opportunities'][:2])}")

        if a.get('key_question'):
            print(f"  >> KEY QUESTION: {a['key_question']}")

        if a.get('estimated_true_yield_after_costs_pct'):
            print(f"  Est. true yield (after costs): {a['estimated_true_yield_after_costs_pct']:.1f}%")
        if a.get('estimated_5yr_appreciation_pct') is not None:
            print(f"  Est. 5yr appreciation: {a['estimated_5yr_appreciation_pct']:+.0f}%")


def _print_watchlist(scored: list[ScoredListing]):
    """Print WATCH verdicts as brief list."""
    watches = [s for s in scored if s.claude_verdict == "WATCH"]

    if not watches:
        return

    print(f"\n  {'=' * 70}")
    print(f"  WATCHLIST ({len(watches)})")
    print(f"  {'=' * 70}")

    for s in watches:
        l = s.listing
        e = s.enriched
        a = getattr(s, 'claude_analysis', {}) or {}
        yield_str = f"{e.gross_annual_yield_pct:.1f}%" if e.gross_annual_yield_pct else "N/A"
        discount_str = f"{e.price_discount_pct:+.1f}%" if e.price_discount_pct is not None else ""

        print(f"\n  {l.disposition or '?':5s} | {l.price_czk:>10,} CZK | {yield_str:>5s} yield | {discount_str:>6s} | {l.district}")
        print(f"  {l.locality[:50]}")
        if a.get('one_liner'):
            print(f"  -> {a['one_liner']}")
        if a.get('key_question'):
            print(f"  ?? {a['key_question']}")
        print(f"  {l.url}")


def _print_ranked_list(scored: list[ScoredListing], limit: int = 25):
    """Print ranked list without Claude analysis."""
    print(f"\n  TOP {min(limit, len(scored))} BY COMPOSITE SCORE")
    print(f"  {'-' * 65}")
    print(f"  {'#':>3s}  {'Score':>5s}  {'Disp':5s}  {'Price':>12s}  {'m2':>5s}  {'Yield':>6s}  {'vs Avg':>7s}  {'District'}")
    print(f"  {'-' * 65}")

    for i, s in enumerate(scored[:limit], 1):
        l = s.listing
        e = s.enriched
        yield_str = f"{e.gross_annual_yield_pct:.1f}%" if e.gross_annual_yield_pct else "  N/A"
        disc_str = f"{e.price_discount_pct:+.1f}%" if e.price_discount_pct is not None else ""

        print(f"  {i:>3d}  {s.composite_score:>5.1f}  {l.disposition or '?':5s}  {l.price_czk:>10,} CZK  {l.size_m2 or 0:>5.0f}  {yield_str:>6s}  {disc_str:>7s}  {l.district}")
        print(f"       [{l.source}] {l.locality[:45]}")
        print(f"       {l.url}")


def _print_district_heatmap(scored: list[ScoredListing]):
    """Show district-level summary."""
    by_district: dict[str, list[ScoredListing]] = {}
    for s in scored:
        by_district.setdefault(s.listing.district, []).append(s)

    print(f"\n  DISTRICT HEATMAP")
    print(f"  {'-' * 65}")
    print(f"  {'District':<20s} {'Count':>5s} {'Avg Score':>9s} {'Med Yield':>9s} {'Med CZK/m2':>10s} {'Pop Trend':>9s}")
    print(f"  {'-' * 65}")

    for district in sorted(by_district.keys()):
        items = by_district[district]
        avg_score = statistics.mean(s.composite_score for s in items)
        yields = [s.enriched.gross_annual_yield_pct for s in items if s.enriched.gross_annual_yield_pct]
        prices = [s.enriched.price_per_m2 for s in items if s.enriched.price_per_m2]
        pop_trend = items[0].enriched.population_trend_5y_pct

        med_yield = f"{statistics.median(yields):.1f}%" if yields else "N/A"
        med_price = f"{statistics.median(prices):,.0f}" if prices else "N/A"
        trend_str = f"{pop_trend:+.1f}%" if pop_trend is not None else "N/A"

        print(f"  {district:<20s} {len(items):>5d} {avg_score:>9.1f} {med_yield:>9s} {med_price:>10s} {trend_str:>9s}")

    print(f"  {'-' * 65}")
    print(f"\n{'=' * 75}\n")


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_analysis(scored: list[ScoredListing], filename: str = "last_analysis.json") -> str:
    path = os.path.join(DIR, filename)

    # Serialize including claude_analysis
    listings_data = []
    for s in scored:
        d = s.to_dict()
        if hasattr(s, 'claude_analysis') and s.claude_analysis:
            d['claude_analysis'] = s.claude_analysis
        listings_data.append(d)

    data = {
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "count": len(scored),
        "listings": listings_data,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    enriched_path = os.path.join(DIR, "last_enriched.json")
    if not os.path.exists(enriched_path):
        print("No last_enriched.json found. Run enricher.py first.")
        exit(1)

    with open(enriched_path, "r") as f:
        data = json.load(f)

    enriched = [EnrichedListing.from_dict(d) for d in data["listings"]]
    print(f"Loaded {len(enriched)} enriched listings")

    scored = rank_all(enriched)
    print_dashboard(scored)

    path = save_analysis(scored)
    print(f"Saved to {path}")
