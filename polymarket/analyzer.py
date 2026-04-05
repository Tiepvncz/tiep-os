"""
Polymarket Market Analyzer
Uses Claude + live web search to deeply research each market and produce
high-confidence trade recommendations. Run after scanner.py.
"""

import os
import json
import time
import feedparser
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
import anthropic

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

ANTHROPIC_API_KEY      = os.getenv("ANTHROPIC_API_KEY")
MAX_TRADE_USD          = float(os.getenv("POLYMARKET_MAX_TRADE_EUR", 20))
MAX_POSITIONS          = int(os.getenv("POLYMARKET_MAX_OPEN_POSITIONS", 5))
DAILY_LOSS_CAP_USD     = float(os.getenv("POLYMARKET_DAILY_LOSS_CAP_EUR", 25))

# Only analyze markets where there's room for an edge
# Skip near-certain outcomes (>93% or <7%)
MIN_INTERESTING_PRICE  = 0.07
MAX_INTERESTING_PRICE  = 0.93

# Only consider markets with enough volume to be credible
MIN_VOLUME             = 500_000

SCAN_FILE = os.path.join(os.path.dirname(__file__), "last_scan.json")
REPORT_FILE = os.path.join(os.path.dirname(__file__), "last_analysis.json")


# ---------------------------------------------------------------------------
# Web research helpers
# ---------------------------------------------------------------------------

def search_google_news(query: str, max_results: int = 8) -> list[dict]:
    """Fetch recent headlines from Google News RSS (no API key required)."""
    url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:max_results]:
            results.append({
                "title":     entry.get("title", ""),
                "summary":   entry.get("summary", "")[:400],
                "published": entry.get("published", ""),
                "source":    entry.get("source", {}).get("title", ""),
                "link":      entry.get("link", ""),
            })
        return results
    except Exception as e:
        return [{"error": str(e)}]


def search_duckduckgo(query: str, max_results: int = 6) -> list[dict]:
    """Search DuckDuckGo for additional context."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [{"title": r.get("title",""), "body": r.get("body","")[:400], "href": r.get("href","")} for r in results]
    except Exception as e:
        return [{"error": str(e)}]


def gather_research(market: dict) -> dict:
    """Gather web research for a single market."""
    question = market["question"]
    print(f"  Researching: {question[:70]}...")

    # Build targeted search queries
    queries = [
        question,
        f"{question} latest news 2026",
        f"{question} prediction odds analysis",
    ]

    all_news    = []
    all_web     = []

    for q in queries[:2]:
        news = search_google_news(q, max_results=6)
        all_news.extend(news)
        time.sleep(0.5)

    web = search_duckduckgo(question, max_results=5)
    all_web.extend(web)

    return {
        "question":    question,
        "yes_price":   market.get("yes_price"),
        "no_price":    market.get("no_price"),
        "volume":      market.get("volume"),
        "volume_24h":  market.get("volume_24h"),
        "liquidity":   market.get("liquidity"),
        "days_to_close": market.get("days_to_close"),
        "end_date":    market.get("end_date"),
        "url":         market.get("url"),
        "yes_token_id": market.get("yes_token_id"),
        "no_token_id":  market.get("no_token_id"),
        "news":        all_news[:10],
        "web_results": all_web[:5],
    }


# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert prediction market analyst with deep knowledge of
geopolitics, finance, sports, and current events. Your job is to analyze Polymarket
prediction markets and identify trades with a genuine edge - where the market price
is meaningfully wrong compared to reality.

You are rigorous, intellectually honest, and conservative. You only recommend trades
when you have strong evidence and clear reasoning. You never recommend a trade just
to have something to say - SKIP is always a valid and often correct answer.

Your analysis framework:
1. ASSESS BASE RATE: What historically happens in situations like this?
2. EVALUATE CURRENT EVIDENCE: What does the latest news actually say?
3. IDENTIFY MARKET BIAS: Is the crowd overreacting to recent news? Underreacting?
4. CONSIDER RESOLUTION RISK: How clearly will this market resolve? Any ambiguity?
5. CALCULATE EDGE: Is the mispricing large enough to justify risk after fees (0.3%)?

Key principles:
- A market at 95% YES is not interesting even if you agree - the payout is too small
- A market at 50% with no clear edge is a coin flip - skip it
- The best trades are where you have specific knowledge the crowd is missing
- Short time horizons (< 7 days) require very high confidence - events can surprise
- Always account for the 0.3% taker fee on each trade

Output ONLY valid JSON matching this exact schema:
{
  "question": "...",
  "market_price_yes": 0.XX,
  "your_probability_yes": 0.XX,
  "edge": 0.XX,
  "recommendation": "BUY_YES" | "BUY_NO" | "SKIP",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "suggested_bet_usd": 0,
  "reasoning": "...",
  "key_evidence": ["...", "..."],
  "risks": ["...", "..."],
  "skip_reason": "..."
}

Rules:
- Only recommend BUY_YES or BUY_NO if edge > 0.08 AND confidence is HIGH or MEDIUM
- suggested_bet_usd must never exceed 20 (hard limit)
- Scale bet size with confidence: HIGH=15-20, MEDIUM=8-14, LOW=0 (skip)
- skip_reason is required when recommendation is SKIP
- reasoning must cite specific evidence from the research provided"""


def analyze_market(client: anthropic.Anthropic, research: dict) -> dict:
    """Ask Claude to deeply analyze a single market."""

    user_message = f"""Analyze this Polymarket prediction market and give your recommendation.

MARKET: {research['question']}
CLOSES: {research['days_to_close']} days from now ({research['end_date'][:10] if research.get('end_date') else 'unknown'})
CURRENT YES PRICE: {research['yes_price']:.3f} ({research['yes_price']*100:.1f}%)
CURRENT NO PRICE:  {research['no_price']:.3f} ({research['no_price']*100:.1f}%)
TOTAL VOLUME: ${research['volume']:,.0f}
24H VOLUME: ${research['volume_24h']:,.0f}
LIQUIDITY: ${research['liquidity']:,.0f}
URL: {research['url']}

--- RECENT NEWS ---
{json.dumps(research['news'], indent=2)}

--- WEB RESEARCH ---
{json.dumps(research['web_results'], indent=2)}

Based on ALL of this information, provide your analysis as JSON."""

    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        raw = message.content[0].text.strip()

        # Strip markdown code blocks if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        result["yes_token_id"] = research.get("yes_token_id")
        result["no_token_id"]  = research.get("no_token_id")
        result["url"]          = research.get("url")
        return result

    except Exception as e:
        return {
            "question":   research["question"],
            "recommendation": "SKIP",
            "confidence": "LOW",
            "skip_reason": f"Analysis error: {e}",
            "suggested_bet_usd": 0,
            "reasoning":  str(e),
            "yes_token_id": research.get("yes_token_id"),
            "no_token_id":  research.get("no_token_id"),
            "url": research.get("url"),
        }


# ---------------------------------------------------------------------------
# Filter candidates
# ---------------------------------------------------------------------------

def filter_candidates(markets: list[dict]) -> list[dict]:
    """Keep only markets worth analyzing - exclude near-certain outcomes."""
    candidates = []
    for m in markets:
        yes = m.get("yes_price")
        no  = m.get("no_price")
        vol = m.get("volume", 0) or 0

        if yes is None or no is None:
            continue
        if vol < MIN_VOLUME:
            continue
        # Must have a meaningful price range (not already decided)
        if yes > MAX_INTERESTING_PRICE or yes < MIN_INTERESTING_PRICE:
            continue
        if m.get("days_to_close") is None:
            continue

        candidates.append(m)

    return candidates


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_analysis_report(recommendations: list[dict]):
    """Print final ranked recommendations."""
    print("\n" + "=" * 70)
    print(f"POLYMARKET ANALYSIS REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 70)

    # Sort: actionable first, then by confidence
    order = {"BUY_YES": 0, "BUY_NO": 1, "SKIP": 2}
    conf_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    ranked = sorted(recommendations,
                    key=lambda x: (order.get(x.get("recommendation","SKIP"), 2),
                                   conf_order.get(x.get("confidence","LOW"), 2)))

    actionable = [r for r in ranked if r.get("recommendation") in ("BUY_YES", "BUY_NO")]
    skipped    = [r for r in ranked if r.get("recommendation") == "SKIP"]

    if actionable:
        print(f"\n{'='*70}")
        print(f"  RECOMMENDED TRADES ({len(actionable)} found)")
        print(f"{'='*70}")
        for r in actionable:
            print(f"\n  MARKET:      {r.get('question')}")
            print(f"  ACTION:      {r.get('recommendation')}  |  Confidence: {r.get('confidence')}")
            print(f"  Market yes:  {r.get('market_price_yes', 'N/A')}")
            print(f"  Your prob:   {r.get('your_probability_yes', 'N/A')}")
            print(f"  Edge:        {r.get('edge', 'N/A')}")
            print(f"  Bet size:    ${r.get('suggested_bet_usd', 0)}")
            print(f"  Reasoning:   {r.get('reasoning','')[:300]}")
            if r.get("key_evidence"):
                print(f"  Evidence:")
                for e in r["key_evidence"][:3]:
                    print(f"    - {e[:120]}")
            if r.get("risks"):
                print(f"  Risks:")
                for risk in r["risks"][:2]:
                    print(f"    - {risk[:120]}")
            print(f"  URL:         {r.get('url')}")
    else:
        print("\n  No actionable trades found. All markets SKIPPED.")
        print("  This is a valid outcome - waiting for better opportunities.")

    if skipped:
        print(f"\n{'='*70}")
        print(f"  SKIPPED MARKETS ({len(skipped)})")
        print(f"{'='*70}")
        for r in skipped:
            print(f"  - {r.get('question','')[:65]}")
            print(f"    Reason: {r.get('skip_reason','')[:120]}")

    print(f"\n{'='*70}")
    total_exposure = sum(r.get("suggested_bet_usd", 0) for r in actionable)
    print(f"  Total exposure if all trades placed: ${total_exposure}")
    print(f"  Daily loss cap: ${DAILY_LOSS_CAP_USD}")
    print(f"  Max per trade: ${MAX_TRADE_USD}")
    print(f"{'='*70}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set in .env file.")
        print("Add: ANTHROPIC_API_KEY=sk-ant-... to your .env")
        return

    # Load last scan
    if not os.path.exists(SCAN_FILE):
        print("No scan file found. Run scanner.py first.")
        return

    with open(SCAN_FILE) as f:
        scan = json.load(f)

    markets = scan.get("markets", [])
    print(f"Loaded {len(markets)} markets from last scan.")

    candidates = filter_candidates(markets)
    print(f"Filtered to {len(candidates)} markets with meaningful odds (between {MIN_INTERESTING_PRICE*100:.0f}%-{MAX_INTERESTING_PRICE*100:.0f}% YES).\n")

    if not candidates:
        print("No candidates worth analyzing right now.")
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    recommendations = []
    for i, market in enumerate(candidates, 1):
        print(f"[{i}/{len(candidates)}] Analyzing...")
        research = gather_research(market)
        time.sleep(1)
        rec = analyze_market(client, research)
        recommendations.append(rec)
        action = rec.get("recommendation", "SKIP")
        conf   = rec.get("confidence", "")
        print(f"  -> {action} | {conf} | ${rec.get('suggested_bet_usd',0)} suggested")
        time.sleep(1)

    print_analysis_report(recommendations)

    with open(REPORT_FILE, "w") as f:
        json.dump({
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "recommendations": recommendations,
        }, f, indent=2, default=str)
    print(f"Full analysis saved to: {REPORT_FILE}")


if __name__ == "__main__":
    main()
