"""
Polymarket Market Scanner
Phase 2 - Read-only market analysis with trade recommendations
Run this daily to get Claude's top trade picks. No trades are placed automatically.
"""

import os
import json
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

MAX_TRADE_USD      = float(os.getenv("POLYMARKET_MAX_TRADE_EUR", 20))
MAX_POSITIONS      = int(os.getenv("POLYMARKET_MAX_OPEN_POSITIONS", 5))
DAILY_LOSS_CAP_USD = float(os.getenv("POLYMARKET_DAILY_LOSS_CAP_EUR", 25))


# ---------------------------------------------------------------------------
# Fetch markets
# ---------------------------------------------------------------------------

def fetch_markets(limit: int = 100, min_volume: float = 50_000) -> list[dict]:
    """Fetch active markets with meaningful liquidity."""
    params = {
        "active": "true",
        "closed": "false",
        "limit": limit,
        "order": "volume24hr",
        "ascending": "false",
    }
    resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=15)
    resp.raise_for_status()
    markets = resp.json()

    # Filter: minimum volume, binary markets only (yes/no), not ending too soon
    now = datetime.now(timezone.utc)
    filtered = []
    for m in markets:
        try:
            volume = float(m.get("volume", 0) or 0)
            if volume < min_volume:
                continue

            end_date_str = m.get("endDate") or m.get("end_date_iso")
            if end_date_str:
                end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                days_to_close = (end_date - now).days
                # Skip markets closing in less than 2 days or more than 90 days
                if days_to_close < 2 or days_to_close > 90:
                    continue

            clob_token_ids = m.get("clobTokenIds")
            if isinstance(clob_token_ids, str):
                clob_token_ids = json.loads(clob_token_ids)
            if not clob_token_ids or len(clob_token_ids) < 2:
                continue

            filtered.append(m)
        except Exception:
            continue

    return filtered[:20]  # top 20 by volume


# ---------------------------------------------------------------------------
# Fetch live prices from CLOB
# ---------------------------------------------------------------------------

def fetch_price(token_id: str) -> float | None:
    """Get current mid price for a token (0-1 scale)."""
    try:
        resp = requests.get(
            f"{CLOB_API}/midpoint",
            params={"token_id": token_id},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return float(data.get("mid", 0))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Build candidate list
# ---------------------------------------------------------------------------

def build_candidates(markets: list[dict]) -> list[dict]:
    """Enrich markets with live prices and key metadata."""
    candidates = []

    for m in markets:
        try:
            token_ids = m.get("clobTokenIds", [])
            if isinstance(token_ids, str):
                token_ids = json.loads(token_ids)
            yes_token_id = token_ids[0] if token_ids else None
            no_token_id  = token_ids[1] if len(token_ids) > 1 else None

            yes_price = fetch_price(yes_token_id) if yes_token_id else None
            no_price  = fetch_price(no_token_id)  if no_token_id  else None

            end_date_str = m.get("endDate") or m.get("end_date_iso", "")
            end_date = None
            days_to_close = None
            if end_date_str:
                end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                days_to_close = (end_date - datetime.now(timezone.utc)).days

            candidates.append({
                "id":            m.get("id"),
                "question":      m.get("question"),
                "category":      m.get("category", ""),
                "volume":        float(m.get("volume", 0) or 0),
                "volume_24h":    float(m.get("volume24hr", 0) or 0),
                "liquidity":     float(m.get("liquidity", 0) or 0),
                "yes_price":     yes_price,
                "no_price":      no_price,
                "yes_token_id":  yes_token_id,
                "no_token_id":   no_token_id,
                "days_to_close": days_to_close,
                "end_date":      end_date_str,
                "url":           f"https://polymarket.com/event/{m.get('slug', m.get('id', ''))}",
            })
        except Exception as e:
            print(f"  Skipping market {m.get('id')}: {e}")

    return candidates


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_report(candidates: list[dict]):
    """Print a structured report for Claude to analyse."""
    print("\n" + "=" * 70)
    print(f"POLYMARKET SCAN REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 70)
    print(f"Guardrails: max trade ${MAX_TRADE_USD} | max positions {MAX_POSITIONS} | daily loss cap ${DAILY_LOSS_CAP_USD}")
    print(f"Markets scanned: {len(candidates)}")
    print("=" * 70)

    for i, m in enumerate(candidates, 1):
        yes = m["yes_price"]
        no  = m["no_price"]
        print(f"\n[{i}] {m['question']}")
        print(f"    Category:     {m['category']}")
        print(f"    Closes in:    {m['days_to_close']} days ({m['end_date'][:10]})")
        print(f"    Volume total: ${m['volume']:,.0f} | 24h: ${m['volume_24h']:,.0f}")
        print(f"    Liquidity:    ${m['liquidity']:,.0f}")
        print(f"    YES price:    {f'{yes:.2f} ({yes*100:.1f}%)' if yes else 'N/A'}")
        print(f"    NO price:     {f'{no:.2f} ({no*100:.1f}%)' if no else 'N/A'}")
        print(f"    URL:          {m['url']}")

    print("\n" + "=" * 70)
    print("END OF REPORT — Review above and decide on trades manually.")
    print("=" * 70 + "\n")

    # Also save as JSON for Claude skill to parse
    output_path = os.path.join(os.path.dirname(__file__), "last_scan.json")
    with open(output_path, "w") as f:
        json.dump({
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "guardrails": {
                "max_trade_usd": MAX_TRADE_USD,
                "max_positions": MAX_POSITIONS,
                "daily_loss_cap_usd": DAILY_LOSS_CAP_USD,
            },
            "markets": candidates,
        }, f, indent=2, default=str)
    print(f"Scan saved to: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Fetching markets from Polymarket...")
    markets = fetch_markets(limit=100, min_volume=50_000)
    print(f"Found {len(markets)} qualifying markets. Fetching live prices...")
    candidates = build_candidates(markets)
    print_report(candidates)
