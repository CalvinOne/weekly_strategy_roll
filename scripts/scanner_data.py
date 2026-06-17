"""Universe definitions and market data helpers for scanner pages."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any

import generate_signals as signals


ROOT = Path(__file__).resolve().parents[1]
US_UNIVERSE_FILE = ROOT / "data" / "us_stocks_universe.json"
ALTCOIN_UNIVERSE_FILE = ROOT / "data" / "bybit_altcoin_universe.json"

EXCLUDED_ALTCOIN_SYMBOLS = {
    "BTC",
    "ETH",
    "USDT",
    "USDC",
    "USDE",
    "DAI",
    "FDUSD",
    "TUSD",
    "USDD",
    "PYUSD",
    "FRAX",
    "USDS",
    "BUSD",
    "USD1",
    "WBTC",
    "WETH",
    "STETH",
    "WSTETH",
    "CBETH",
    "RETH",
    "BSC-USD",
    "SUSDE",
    "USDT0",
}

ALTCOIN_STOP_PCT = 2.0
ALTCOIN_TARGET_R = 2.5
STOCK_STOP_PCT = 1.25
STOCK_TARGET_R = 2.5


def fetch_bybit_linear_tickers() -> list[dict[str, Any]]:
    url = "https://api.bybit.com/v5/market/tickers?" + urllib.parse.urlencode({"category": "linear"})
    payload = signals.http_json(url)
    if payload.get("retCode") != 0:
        raise RuntimeError(f"Bybit tickers error: {payload}")
    return payload.get("result", {}).get("list") or []


def bybit_base_symbol(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return symbol[: -len("USDT")]
    return symbol


def is_excluded_altcoin(symbol: str) -> bool:
    if not symbol.endswith("USDT"):
        return True
    base = bybit_base_symbol(symbol)
    return base in EXCLUDED_ALTCOIN_SYMBOLS or symbol in {"BTCUSDT", "ETHUSDT"}


def load_cached_altcoin_markets(limit: int = 100) -> list[dict[str, Any]]:
    payload = json.loads(ALTCOIN_UNIVERSE_FILE.read_text(encoding="utf-8"))
    rows = payload.get("coins") or []
    for index, row in enumerate(rows[:limit], start=1):
        row.setdefault("market_cap_rank", index)
    return rows[:limit]


def save_altcoin_universe(markets: list[dict[str, Any]]) -> None:
    payload = {
        "description": "Cached Bybit linear USDT universe for CI fallback when tickers API is blocked.",
        "updated_at": signals.now_utc().isoformat(),
        "coins": [
            {
                "symbol": row["symbol"],
                "base": row["base"],
                "name": row.get("name") or row["base"],
                "market_cap_rank": row.get("market_cap_rank"),
            }
            for row in markets
        ],
    }
    ALTCOIN_UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ALTCOIN_UNIVERSE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_bybit_altcoin_markets(limit: int = 100) -> list[dict[str, Any]]:
    try:
        tickers = fetch_bybit_linear_tickers()
    except Exception as exc:
        if ALTCOIN_UNIVERSE_FILE.exists():
            print(f"Bybit tickers unavailable ({exc}); using cached universe from {ALTCOIN_UNIVERSE_FILE.name}.")
            return load_cached_altcoin_markets(limit)
        raise

    ranked: list[dict[str, Any]] = []
    for row in tickers:
        symbol = str(row.get("symbol") or "")
        if is_excluded_altcoin(symbol):
            continue
        turnover = float(row.get("turnover24h") or 0)
        if turnover <= 0:
            continue
        ranked.append(
            {
                "symbol": symbol,
                "base": bybit_base_symbol(symbol),
                "name": bybit_base_symbol(symbol),
                "turnover24h": turnover,
                "volume24h": float(row.get("volume24h") or 0),
                "last_price": float(row.get("lastPrice") or 0),
            }
        )

    ranked.sort(key=lambda item: item["turnover24h"], reverse=True)
    for index, row in enumerate(ranked[:limit], start=1):
        row["market_cap_rank"] = index
    selected = ranked[:limit]
    if selected:
        save_altcoin_universe(selected)
    return selected


def get_altcoin_market_data(symbol: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    weekly = signals.enrich_weekly(signals.fetch_bybit(symbol, "W", 365 * 5 + 250))
    h4 = signals.fetch_bybit(symbol, "240", 365 * 2 + 30)
    return weekly, h4


def altcoin_instruments(limit: int = 100) -> list[tuple[signals.Instrument, dict[str, Any]]]:
    markets = fetch_bybit_altcoin_markets(limit)
    items: list[tuple[signals.Instrument, dict[str, Any]]] = []
    for row in markets:
        symbol = str(row["symbol"])
        base = str(row["base"])
        instrument = signals.Instrument(
            key=base,
            name=str(row.get("name") or base),
            source="bybit",
            symbol=symbol,
            stop_pct=ALTCOIN_STOP_PCT,
            target_r=ALTCOIN_TARGET_R,
        )
        meta = {
            "market_cap_rank": row.get("market_cap_rank"),
            "turnover24h": row.get("turnover24h"),
            "volume24h": row.get("volume24h"),
            "last_price": row.get("last_price"),
        }
        items.append((instrument, meta))
    return items


def load_us_stock_universe(limit: int = 100) -> list[dict[str, str]]:
    payload = json.loads(US_UNIVERSE_FILE.read_text(encoding="utf-8"))
    rows = payload.get("stocks") or []
    return rows[:limit]


def stock_instruments(limit: int = 100) -> list[tuple[signals.Instrument, dict[str, Any]]]:
    rows = load_us_stock_universe(limit)
    items: list[tuple[signals.Instrument, dict[str, Any]]] = []
    for index, row in enumerate(rows, start=1):
        symbol = str(row["symbol"]).upper()
        instrument = signals.Instrument(
            key=symbol,
            name=str(row.get("name") or symbol),
            source="yahoo",
            symbol=symbol,
            stop_pct=STOCK_STOP_PCT,
            target_r=STOCK_TARGET_R,
        )
        items.append((instrument, {"market_cap_rank": index}))
    return items


def get_stock_market_data(symbol: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    daily = signals.fetch_yahoo(symbol, "1d", "5y")
    hourly = signals.fetch_yahoo_hourly_extended(symbol, min_days=365)
    return signals.enrich_weekly(signals.daily_to_weekly(daily)), signals.resample_to_h4(hourly)
