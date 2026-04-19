"""
Czech Real Estate Investment Scanner
Run: python3 run.py [--fast] [--no-claude] [--novostavby]

Pipeline:
  1. Scan sreality.cz + bezrealitky.cz + bazos.cz + reality.idnes.cz
  2. Enrich with rental comps, CZSO population data, price context
  3. Score and rank by investment potential
  4. Top candidates get Claude deep analysis (advocate + devil's advocate)
  5. Photo analysis with Claude Vision
  6. Output investment dashboard with verdicts

Modes:
  --fast        Skip detail fetches (faster but less data)
  --no-claude   Skip AI analysis
  --novostavby  Search only for new construction (novostavby)
"""
import os
import sys
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

# Add skill directory to path
DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)

from config import CLAUDE_SCORE_THRESHOLD, VISION_SCORE_THRESHOLD, TARGET_DISTRICTS
from scanner import scan_all_sources, scan_all_districts, scan_all_novostavby, save_scan, print_scan_summary, fetch_novostavby_com_projects, match_developer_projects
from enricher import enrich_all, save_enriched, print_enrichment_summary
from analyzer import rank_all, analyze_with_claude, analyze_photos, print_dashboard, save_analysis

# Load .env from repo root
_env_path = os.path.join(DIR, '..', '..', '.env')
load_dotenv(dotenv_path=_env_path, override=True)


def main():
    fast_mode = "--fast" in sys.argv
    skip_claude = "--no-claude" in sys.argv
    novostavby_mode = "--novostavby" in sys.argv

    mode_label = "novostavby" if novostavby_mode else ("fast" if fast_mode else "full")

    print("=" * 75)
    print("  CZECH REAL ESTATE INVESTMENT SCANNER")
    if novostavby_mode:
        print("  >>> NOVOSTAVBY MODE - New construction only <<<")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Districts: {', '.join(d[1] for d in TARGET_DISTRICTS)}")
    print(f"  Sources: sreality.cz + bezrealitky.cz + bazos.cz + reality.idnes.cz")
    print(f"  Mode: {mode_label}{' (no Claude)' if skip_claude else ''}")
    print("=" * 75)

    # Step 1: Scan
    print(f"\n[STEP 1/5] Scanning real estate portals...")
    start = time.time()

    if novostavby_mode:
        listings = scan_all_novostavby()
    else:
        listings = scan_all_sources(fetch_details=not fast_mode)

    if not listings:
        print("\n  No listings found. Check network connection.")
        return

    save_scan(listings)
    print_scan_summary(listings)
    print(f"  Scan: {time.time() - start:.0f}s")

    # Developer project matching (novostavby.com reference layer)
    if novostavby_mode:
        print(f"\n  --- NOVOSTAVBY.COM (developer projects) ---")
        try:
            projects = fetch_novostavby_com_projects()
            matched = match_developer_projects(listings, projects)
            print(f"  Matched {matched}/{len(listings)} listings to {len(projects)} developer projects")
        except Exception as e:
            print(f"  Developer project matching failed: {e}")

    # Step 2: Enrich
    print(f"\n[STEP 2/5] Enriching with rental comps, population data, price context...")
    start = time.time()
    enriched = enrich_all(listings)
    save_enriched(enriched)
    print_enrichment_summary(enriched)
    print(f"  Enrichment: {time.time() - start:.0f}s")

    # Step 3: Score & Rank
    print(f"\n[STEP 3/5] Scoring and ranking {len(enriched)} properties...")
    scored = rank_all(enriched)

    # Step 4: Claude Analysis (score-based)
    if not skip_claude:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            qualified = sum(1 for s in scored if s.composite_score >= CLAUDE_SCORE_THRESHOLD)
            print(f"\n[STEP 4/5] Claude deep analysis (score >= {CLAUDE_SCORE_THRESHOLD}: {qualified} listings)...")
            print(f"  (advocate + devil's advocate for each)")
            scored = analyze_with_claude(scored, api_key)

            # Step 5: Vision analysis of photos
            vision_qualified = sum(1 for s in scored if s.composite_score >= VISION_SCORE_THRESHOLD and s.listing.images)
            print(f"\n[STEP 5/5] Photo analysis with Claude Vision (score >= {VISION_SCORE_THRESHOLD}: {vision_qualified} with images)...")
            scored = analyze_photos(scored, api_key)
        else:
            print(f"\n[STEP 4/5] No ANTHROPIC_API_KEY set - skipping Claude analysis")
            print(f"\n[STEP 5/5] Skipped (no API key)")
    else:
        print(f"\n[STEP 4/5] Skipped (--no-claude)")
        print(f"\n[STEP 5/5] Skipped (--no-claude)")

    # Dashboard
    print_dashboard(scored)
    path = save_analysis(scored)
    print(f"Full results: {path}")


if __name__ == "__main__":
    main()
