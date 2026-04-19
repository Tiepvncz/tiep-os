"""
Polymarket Daily Skill — Single entry point
Run this once a day: .venv/bin/python3 polymarket/run.py

What it does:
  1. Fetches top markets from Polymarket
  2. Filters to candidates with meaningful odds
  3. Claude deeply researches each one using live web search
  4. Presents ranked recommendations with full reasoning
  5. Asks you which trades (if any) to place
"""

import os
import sys
import json
import time
import requests
import feedparser
from datetime import datetime, timezone
from dotenv import load_dotenv
import anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
load_dotenv(dotenv_path=_env_path, override=True)

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
MAX_TRADE_USD      = float(os.getenv("POLYMARKET_MAX_TRADE_EUR", 20))
MAX_POSITIONS      = int(os.getenv("POLYMARKET_MAX_OPEN_POSITIONS", 5))
DAILY_LOSS_CAP_USD = float(os.getenv("POLYMARKET_DAILY_LOSS_CAP_EUR", 25))

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# Cast a wide net - Claude's deep research will filter properly
MIN_YES_PRICE = 0.05   # skip if market says <5% chance
MAX_YES_PRICE = 0.95   # skip if market says >95% chance
MIN_VOLUME    = 300_000
MARKET_LIMIT  = 100    # how many to fetch from API

DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Step 1 — Fetch & price markets
# ---------------------------------------------------------------------------

def fetch_markets() -> list[dict]:
    print("  Fetching markets from Polymarket...")
    params = {
        "active": "true",
        "closed": "false",
        "limit":  MARKET_LIMIT,
        "order":  "volume24hr",
        "ascending": "false",
    }
    resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_price(token_id: str) -> float | None:
    try:
        r = requests.get(f"{CLOB_API}/midpoint", params={"token_id": token_id}, timeout=8)
        if r.status_code == 200:
            return float(r.json().get("mid", 0))
    except Exception:
        pass
    return None


def build_candidates(markets: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    candidates = []

    for m in markets:
        try:
            volume = float(m.get("volume", 0) or 0)
            if volume < MIN_VOLUME:
                continue

            token_ids = m.get("clobTokenIds", [])
            if isinstance(token_ids, str):
                token_ids = json.loads(token_ids)
            if not token_ids or len(token_ids) < 2:
                continue

            end_str = m.get("endDate") or m.get("end_date_iso")
            days_to_close = None
            if end_str:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                days_to_close = (end_dt - now).days
                if days_to_close < 1 or days_to_close > 90:
                    continue

            yes_price = fetch_price(token_ids[0])
            no_price  = fetch_price(token_ids[1])

            if yes_price is None or no_price is None:
                continue
            if yes_price < MIN_YES_PRICE or yes_price > MAX_YES_PRICE:
                continue

            candidates.append({
                "question":      m.get("question"),
                "category":      m.get("category", ""),
                "volume":        volume,
                "volume_24h":    float(m.get("volume24hr", 0) or 0),
                "liquidity":     float(m.get("liquidity", 0) or 0),
                "yes_price":     yes_price,
                "no_price":      no_price,
                "yes_token_id":  token_ids[0],
                "no_token_id":   token_ids[1],
                "days_to_close": days_to_close,
                "end_date":      end_str or "",
                "url":           f"https://polymarket.com/event/{m.get('slug', m.get('id', ''))}",
            })

        except Exception:
            continue

    # Sort by 24h volume - most active markets first
    candidates.sort(key=lambda x: x["volume_24h"], reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Step 2 — Deep research via Claude + web search
# ---------------------------------------------------------------------------

RESEARCH_SYSTEM = """You are a world-class prediction market analyst. You have access
to web search and must use it aggressively to research the market given to you.

Your research process for each market:
1. Search for the latest news on the topic (use specific, targeted queries)
2. Search for expert opinions, polls, or data relevant to the outcome
3. Search for base rates and historical precedents
4. Search for any structural factors (gerrymandering, injury reports, institutional biases)
5. Search for what sharp bettors or analysts are saying about this market

After researching, produce a structured JSON analysis with this exact schema:
{
  "question": "...",
  "market_price_yes": 0.XX,
  "your_probability_yes": 0.XX,
  "edge": 0.XX,
  "recommendation": "BUY_YES" | "BUY_NO" | "SKIP",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "suggested_bet_usd": 0,
  "one_line_summary": "...",
  "reasoning": "...",
  "key_evidence": ["...", "...", "..."],
  "risks": ["...", "..."],
  "skip_reason": "...",
  "searches_performed": ["query1", "query2", ...]
}

Rules you MUST follow:
- Perform AT LEAST 4 web searches per market before forming a conclusion
- Only recommend BUY_YES or BUY_NO if edge > 0.08 AND confidence is HIGH or MEDIUM
- suggested_bet_usd must NEVER exceed """ + str(int(MAX_TRADE_USD)) + """
- Bet sizing: HIGH confidence = 15-20, MEDIUM = 8-14, LOW = 0
- skip_reason is required when recommendation is SKIP
- your_probability_yes is YOUR estimate, not the market's
- edge = abs(your_probability_yes - market_price_yes)
- reasoning must reference specific things you found in your searches
- one_line_summary must be a single plain sentence anyone can understand

Output ONLY the JSON block. No markdown, no explanation outside the JSON."""


def research_market(client: anthropic.Anthropic, market: dict) -> dict:
    """Let Claude research a market using its own web search."""

    prompt = f"""Research and analyze this Polymarket prediction market thoroughly.

MARKET: {market['question']}
CLOSES IN: {market['days_to_close']} days ({market['end_date'][:10] if market.get('end_date') else 'unknown'})
YES PRICE: {market['yes_price']:.3f} ({market['yes_price']*100:.1f}% implied probability)
NO PRICE:  {market['no_price']:.3f} ({market['no_price']*100:.1f}% implied probability)
VOLUME (total): ${market['volume']:,.0f}
VOLUME (24h):   ${market['volume_24h']:,.0f}
LIQUIDITY:      ${market['liquidity']:,.0f}
URL: {market['url']}

Use web search to research this market deeply, then output your JSON analysis.
Today's date is {datetime.now().strftime('%Y-%m-%d')}."""

    # Retry with exponential backoff on rate limit errors
    for attempt in range(4):
        try:
            response = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=4000,
                system=RESEARCH_SYSTEM,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                }],
                messages=[{"role": "user", "content": prompt}],
            )

        # Extract the final text response (after all tool calls)
        raw = ""
        for block in response.content:
            if hasattr(block, "text"):
                raw = block.text.strip()

        # Strip markdown if present
        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                if part.startswith("json"):
                    raw = part[4:].strip()
                    break
                elif "{" in part:
                    raw = part.strip()
                    break

        # Find JSON object
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]

            result = json.loads(raw)
            result["yes_token_id"]  = market.get("yes_token_id")
            result["no_token_id"]   = market.get("no_token_id")
            result["url"]           = market.get("url")
            result["days_to_close"] = market.get("days_to_close")
            return result

        except anthropic.RateLimitError:
            wait = 30 * (2 ** attempt)
            print(f"          Rate limit hit — waiting {wait}s before retry...")
            time.sleep(wait)
            continue

        except Exception as e:
            return {
                "question":          market["question"],
                "market_price_yes":  market["yes_price"],
                "recommendation":    "SKIP",
                "confidence":        "LOW",
                "suggested_bet_usd": 0,
                "one_line_summary":  f"Analysis failed: {e}",
                "skip_reason":       str(e),
                "reasoning":         str(e),
                "key_evidence":      [],
                "risks":             [],
                "yes_token_id":      market.get("yes_token_id"),
                "no_token_id":       market.get("no_token_id"),
                "url":               market.get("url"),
                "days_to_close":     market.get("days_to_close"),
            }

    return {
        "question":          market["question"],
        "market_price_yes":  market["yes_price"],
        "recommendation":    "SKIP",
        "confidence":        "LOW",
        "suggested_bet_usd": 0,
        "one_line_summary":  "Rate limit exceeded after all retries.",
        "skip_reason":       "Rate limited — try again later.",
        "reasoning":         "",
        "key_evidence":      [],
        "risks":             [],
        "yes_token_id":      market.get("yes_token_id"),
        "no_token_id":       market.get("no_token_id"),
        "url":               market.get("url"),
        "days_to_close":     market.get("days_to_close"),
    }


# ---------------------------------------------------------------------------
# Step 3 — Present report & prompt user
# ---------------------------------------------------------------------------

def print_header():
    print("\n" + "█" * 70)
    print("  POLYMARKET DAILY SKILL")
    print(f"  {datetime.now().strftime('%A, %B %d %Y — %H:%M')} UTC")
    print(f"  Budget: ${MAX_TRADE_USD}/trade | Max {MAX_POSITIONS} positions | ${DAILY_LOSS_CAP_USD} daily cap")
    print("█" * 70)


def print_report(recommendations: list[dict]) -> list[dict]:
    """Print report and return actionable trades."""
    order     = {"BUY_YES": 0, "BUY_NO": 1, "SKIP": 2}
    conf_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

    ranked = sorted(recommendations,
        key=lambda x: (
            order.get(x.get("recommendation", "SKIP"), 2),
            conf_order.get(x.get("confidence", "LOW"), 2),
            -abs(x.get("edge", 0))
        )
    )

    actionable = [r for r in ranked if r.get("recommendation") in ("BUY_YES", "BUY_NO")]
    skipped    = [r for r in ranked if r.get("recommendation") == "SKIP"]

    # --- Actionable trades ---
    if actionable:
        print(f"\n{'━'*70}")
        print(f"  RECOMMENDED TRADES  ({len(actionable)} found)")
        print(f"{'━'*70}")
        for i, r in enumerate(actionable, 1):
            conf  = r.get("confidence", "?")
            conf_icon = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(conf, "⚪")
            action = r.get("recommendation", "SKIP")
            print(f"\n  [{i}] {r.get('question')}")
            print(f"      {conf_icon} {action}  |  Confidence: {conf}  |  Bet: ${r.get('suggested_bet_usd', 0)}")
            print(f"      Market: {r.get('market_price_yes', 0)*100:.1f}% YES  →  Your estimate: {r.get('your_probability_yes', 0)*100:.1f}% YES  |  Edge: {r.get('edge', 0)*100:.1f}pp")
            print(f"      Summary: {r.get('one_line_summary', '')}")
            print(f"\n      Reasoning:")
            # Word-wrap reasoning at 65 chars
            words = r.get("reasoning", "").split()
            line = "        "
            for w in words:
                if len(line) + len(w) > 70:
                    print(line)
                    line = "        " + w + " "
                else:
                    line += w + " "
            if line.strip():
                print(line)
            if r.get("key_evidence"):
                print(f"\n      Key evidence:")
                for e in r["key_evidence"][:4]:
                    print(f"        • {e[:110]}")
            if r.get("risks"):
                print(f"\n      Risks:")
                for risk in r["risks"][:3]:
                    print(f"        ⚠ {risk[:110]}")
            print(f"\n      URL: {r.get('url')}")
            print(f"      Closes in: {r.get('days_to_close')} days")
    else:
        print(f"\n{'━'*70}")
        print("  NO TRADES RECOMMENDED TODAY")
        print("  Markets are efficiently priced - waiting for better opportunities.")
        print(f"{'━'*70}")

    # --- Skipped markets ---
    if skipped:
        print(f"\n{'━'*70}")
        print(f"  RESEARCHED & SKIPPED  ({len(skipped)} markets)")
        print(f"{'━'*70}")
        for r in skipped:
            reason = r.get("skip_reason") or r.get("one_line_summary") or "No edge found"
            print(f"  ✗ {r.get('question','')[:60]}")
            print(f"    {reason[:100]}")

    # --- Summary ---
    total = sum(r.get("suggested_bet_usd", 0) for r in actionable)
    print(f"\n{'━'*70}")
    print(f"  Total exposure if all trades placed: ${total}")
    print(f"{'━'*70}\n")

    return actionable


def prompt_user(actionable: list[dict]) -> list[dict]:
    """Ask user which trades to approve."""
    if not actionable:
        print("Nothing to approve. Run again tomorrow.\n")
        return []

    print("Which trades do you want to place?")
    print("Enter numbers separated by commas (e.g. 1,2), 'all', or 'none':")
    print()

    try:
        raw = input("  Your choice: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nSkipping — no trades placed.")
        return []

    if raw in ("none", "n", "skip", ""):
        print("No trades placed.")
        return []

    if raw in ("all", "a", "yes", "y"):
        approved = actionable
    else:
        try:
            indices = [int(x.strip()) - 1 for x in raw.split(",")]
            approved = [actionable[i] for i in indices if 0 <= i < len(actionable)]
        except Exception:
            print("Could not parse input. No trades placed.")
            return []

    return approved


# ---------------------------------------------------------------------------
# Step 4 — Place trades (guarded)
# ---------------------------------------------------------------------------

def place_trade(trade: dict) -> bool:
    """Place a single trade via Polymarket CLOB API."""
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType

        private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
        if not private_key:
            print("  ERROR: POLYMARKET_PRIVATE_KEY not set.")
            return False

        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=int(os.getenv("POLYMARKET_CHAIN_ID", 137)),
        )

        recommendation = trade.get("recommendation")
        bet_usd        = float(trade.get("suggested_bet_usd", 0))
        question       = trade.get("question", "")

        if bet_usd <= 0:
            print(f"  Skipping {question[:50]} — bet size is $0")
            return False

        if bet_usd > MAX_TRADE_USD:
            print(f"  Capping bet at ${MAX_TRADE_USD} (was ${bet_usd})")
            bet_usd = MAX_TRADE_USD

        if recommendation == "BUY_YES":
            token_id = trade["yes_token_id"]
            price    = trade.get("market_price_yes", 0.5)
        elif recommendation == "BUY_NO":
            token_id = trade["no_token_id"]
            price    = trade.get("market_price_no", trade.get("no_price", 0.5))
        else:
            return False

        # Shares = USD / price per share
        shares = round(bet_usd / price, 2)

        print(f"\n  Placing trade:")
        print(f"    Market:    {question[:60]}")
        print(f"    Action:    {recommendation}")
        print(f"    Price:     {price:.3f} USDC/share")
        print(f"    Shares:    {shares}")
        print(f"    Total:     ${bet_usd:.2f} USDC")

        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 2),
            size=shares,
            side="BUY",
        )
        resp = client.create_and_post_order(order_args)
        print(f"  ✓ Order placed: {resp}")
        return True

    except Exception as e:
        print(f"  ✗ Trade failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print_header()

    if not ANTHROPIC_API_KEY:
        print("\nERROR: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    # Step 1 — Scan
    print("\n[ STEP 1 ] Scanning Polymarket for live markets...")
    try:
        markets = fetch_markets()
    except Exception as e:
        print(f"Failed to fetch markets: {e}")
        sys.exit(1)

    candidates = build_candidates(markets)
    print(f"  Found {len(markets)} total markets.")
    print(f"  {len(candidates)} candidates with odds between {MIN_YES_PRICE*100:.0f}%-{MAX_YES_PRICE*100:.0f}% YES and ${MIN_VOLUME:,}+ volume.\n")

    if not candidates:
        print("  No candidates today. Check back tomorrow.")
        return

    # Show what we'll research
    print("  Markets to research:")
    for i, c in enumerate(candidates, 1):
        print(f"    [{i}] {c['question'][:65]}  ({c['yes_price']*100:.0f}% YES, closes {c['days_to_close']}d)")

    # Step 2 — Deep research
    print(f"\n[ STEP 2 ] Claude is now deeply researching {len(candidates)} markets with live web search...")
    print("  (This takes 1-2 minutes — Claude searches the web for each market)\n")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    recommendations = []

    for i, market in enumerate(candidates, 1):
        print(f"  [{i}/{len(candidates)}] Researching: {market['question'][:60]}...")
        rec = research_market(client, market)
        action = rec.get("recommendation", "SKIP")
        conf   = rec.get("confidence", "?")
        edge   = rec.get("edge", 0)
        searches = len(rec.get("searches_performed", []))
        print(f"          → {action} | {conf} confidence | {edge*100:.1f}pp edge | {searches} searches done")
        recommendations.append(rec)
        time.sleep(15)  # Respect Anthropic rate limits between markets

    # Save results
    output = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "candidates_analyzed": len(candidates),
        "recommendations": recommendations,
    }
    with open(os.path.join(DIR, "last_run.json"), "w") as f:
        json.dump(output, f, indent=2, default=str)

    # Step 3 — Report & prompt
    print(f"\n[ STEP 3 ] Analysis complete. Here are the results:\n")
    actionable = print_report(recommendations)

    # Step 4 — User approval & trade execution
    approved = prompt_user(actionable)

    if approved:
        print(f"\n[ STEP 4 ] Placing {len(approved)} trade(s)...\n")
        placed = 0
        for trade in approved:
            success = place_trade(trade)
            if success:
                placed += 1
            time.sleep(2)
        print(f"\n  Done. {placed}/{len(approved)} trades placed successfully.")
    else:
        print("\n  No trades placed today.")

    print(f"\n  Full report saved to: {os.path.join(DIR, 'last_run.json')}")
    print(f"\n{'█'*70}\n")


if __name__ == "__main__":
    main()
