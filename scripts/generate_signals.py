#!/usr/bin/env python3
"""Generate weekly trend signals for the static dashboard.

The script intentionally uses only public, unauthenticated data sources so the
first version can run from GitHub Actions without secrets.
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "signals.json"


@dataclass(frozen=True)
class Instrument:
    key: str
    name: str
    source: str
    symbol: str
    stop_pct: float
    target_r: float
    first_take_profit_pct: float = 0.35


INSTRUMENTS = [
    Instrument("BTC", "Bitcoin Perpetual", "bybit", "BTCUSDT", 1.0, 2.5),
    Instrument("XAU", "Gold Futures Proxy", "yahoo", "GC=F", 1.25, 2.5),
    Instrument("NASDAQ", "Nasdaq 100 Futures Proxy", "yahoo", "NQ=F", 1.25, 2.5),
    Instrument("USDJPY", "USD/JPY", "yahoo", "JPY=X", 0.75, 2.0),
    Instrument("EURUSD", "EUR/USD", "yahoo", "EURUSD=X", 0.6, 2.0),
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def week_start(dt: datetime) -> datetime:
    day = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    return day - timedelta(days=day.weekday())


def http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def clean_bars(bars: list[dict[str, Any]], now: datetime, interval_hours: int | None = None) -> list[dict[str, Any]]:
    cleaned = []
    for bar in bars:
        if interval_hours is not None and bar["dt"] + timedelta(hours=interval_hours) > now:
            continue
        if any(not math.isfinite(float(bar[k])) for k in ("open", "high", "low", "close")):
            continue
        cleaned.append(bar)
    return sorted(cleaned, key=lambda item: item["dt"])


def fetch_bybit(interval: str, days: int) -> list[dict[str, Any]]:
    base = "https://api.bybit.com/v5/market/kline"
    end = now_utc()
    start = end - timedelta(days=days)
    interval_ms = {"W": 7 * 24 * 3600 * 1000, "240": 4 * 3600 * 1000}[interval]
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    seen: set[int] = set()
    rows: list[list[str]] = []

    while cursor < end_ms:
        window_end = min(end_ms, cursor + interval_ms * 1000 - 1)
        params = {
            "category": "linear",
            "symbol": "BTCUSDT",
            "interval": interval,
            "start": cursor,
            "end": window_end,
            "limit": 1000,
        }
        data = http_json(base + "?" + urllib.parse.urlencode(params))
        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit error: {data}")
        result = data.get("result", {}).get("list", [])
        if not result:
            cursor = window_end + 1
            continue
        for row in result:
            ts = int(row[0])
            if ts not in seen:
                seen.add(ts)
                rows.append(row)
        cursor = max(int(row[0]) for row in result) + interval_ms
        time.sleep(0.02)

    bars = [
        {
            "dt": datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
        }
        for row in rows
    ]
    return clean_bars(bars, now_utc(), 4 if interval == "240" else None)


def fetch_yahoo(symbol: str, interval: str, range_: str) -> list[dict[str, Any]]:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?"
        + urllib.parse.urlencode({"interval": interval, "range": range_, "includePrePost": "false"})
    )
    data = http_json(url)
    chart = data.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"Yahoo error for {symbol}: {chart['error']}")
    result = chart.get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo returned no data for {symbol}")

    payload = result[0]
    timestamps = payload.get("timestamp") or []
    quote = payload.get("indicators", {}).get("quote", [{}])[0]
    bars = []
    for index, ts in enumerate(timestamps):
        values = [quote.get(field, [None] * len(timestamps))[index] for field in ("open", "high", "low", "close")]
        if any(value is None for value in values):
            continue
        bars.append(
            {
                "dt": datetime.fromtimestamp(ts, timezone.utc),
                "open": float(values[0]),
                "high": float(values[1]),
                "low": float(values[2]),
                "close": float(values[3]),
            }
        )
    return clean_bars(bars, now_utc(), 1 if interval == "1h" else None)


def resample_to_h4(hourly: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[datetime, dict[str, Any]] = {}
    for bar in hourly:
        base = datetime(bar["dt"].year, bar["dt"].month, bar["dt"].day, tzinfo=timezone.utc)
        bucket = base + timedelta(hours=(bar["dt"].hour // 4) * 4)
        existing = buckets.setdefault(
            bucket,
            {"dt": bucket, "open": bar["open"], "high": bar["high"], "low": bar["low"], "close": bar["close"]},
        )
        existing["high"] = max(existing["high"], bar["high"])
        existing["low"] = min(existing["low"], bar["low"])
        existing["close"] = bar["close"]
    return sorted(buckets.values(), key=lambda item: item["dt"])


def daily_to_weekly(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[datetime, dict[str, Any]] = {}
    for bar in daily:
        bucket = week_start(bar["dt"])
        existing = buckets.setdefault(
            bucket,
            {"dt": bucket, "open": bar["open"], "high": bar["high"], "low": bar["low"], "close": bar["close"]},
        )
        existing["high"] = max(existing["high"], bar["high"])
        existing["low"] = min(existing["low"], bar["low"])
        existing["close"] = bar["close"]
    return sorted(buckets.values(), key=lambda item: item["dt"])


def enrich_weekly(weekly: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for index, bar in enumerate(weekly):
        item = dict(bar)
        candle_range = item["high"] - item["low"]
        item["close_pos"] = (item["close"] - item["low"]) / candle_range if candle_range else 0.5
        if index >= 19:
            item["ma20"] = sum(row["close"] for row in weekly[index - 19 : index + 1]) / 20
            prev_ma = sum(row["close"] for row in weekly[index - 20 : index]) / 20 if index >= 20 else None
            item["ma20_slope"] = item["ma20"] - prev_ma if prev_ma is not None else None
        else:
            item["ma20"] = None
            item["ma20_slope"] = None
        enriched.append(item)
    return enriched


def get_market_data(instrument: Instrument) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if instrument.source == "bybit":
        return enrich_weekly(fetch_bybit("W", 365 * 5 + 250)), fetch_bybit("240", 365 * 2 + 30)

    daily = fetch_yahoo(instrument.symbol, "1d", "5y")
    hourly = fetch_yahoo(instrument.symbol, "1h", "730d")
    return enrich_weekly(daily_to_weekly(daily)), resample_to_h4(hourly)


def direction_from_previous_week(prev: dict[str, Any]) -> str:
    if prev.get("ma20") is None or prev.get("ma20_slope") is None:
        return "none"

    above_ma = prev["close"] > prev["ma20"]
    below_ma = prev["close"] < prev["ma20"]
    ma_rising = prev["ma20_slope"] > 0
    strong_close = prev["close_pos"] >= 0.70

    if above_ma and ma_rising and strong_close:
        return "long"
    if below_ma and not strong_close:
        return "short"
    return "none"


def find_current_week_context(weekly: list[dict[str, Any]], h4: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    current_week = week_start(now_utc())
    current_h4 = [bar for bar in h4 if week_start(bar["dt"]) == current_week]

    complete_weekly = [bar for bar in weekly if bar["dt"] < current_week]
    previous = complete_weekly[-1] if complete_weekly else None

    if current_h4:
        current = {
            "dt": current_week,
            "open": current_h4[0]["open"],
            "high": max(bar["high"] for bar in current_h4),
            "low": min(bar["low"] for bar in current_h4),
            "close": current_h4[-1]["close"],
        }
    else:
        current = {"dt": current_week, "open": None, "high": None, "low": None, "close": None}

    return current, current_h4, previous


def analyze_trade(instrument: Instrument, direction: str, current: dict[str, Any], current_h4: list[dict[str, Any]]) -> dict[str, Any]:
    if direction == "none":
        return {"status": "no_trade", "message": "No weekly direction filter is active."}
    if not current_h4 or current["open"] is None:
        return {"status": "waiting_data", "message": "Waiting for current-week 4H candles."}

    week_open = current["open"]
    first_24h_end = current_h4[0]["dt"] + timedelta(hours=24)
    entry_index = None
    for index, bar in enumerate(current_h4):
        if bar["dt"] >= first_24h_end:
            break
        if direction == "long" and bar["close"] > week_open:
            entry_index = index
            break
        if direction == "short" and bar["close"] < week_open:
            entry_index = index
            break

    if entry_index is None:
        status = "waiting_entry" if now_utc() < first_24h_end else "missed_entry"
        return {
            "status": status,
            "message": "Waiting for first 24h 4H confirmation." if status == "waiting_entry" else "No 4H confirmation in the first 24h.",
            "week_open": week_open,
        }

    entry_bar = current_h4[entry_index]
    entry = entry_bar["close"]
    side = 1 if direction == "long" else -1
    stop = entry * (1 - instrument.stop_pct / 100) if direction == "long" else entry * (1 + instrument.stop_pct / 100)
    target = entry * (1 + instrument.stop_pct * instrument.target_r / 100) if direction == "long" else entry * (1 - instrument.stop_pct * instrument.target_r / 100)

    target_hit_at = None
    stop_hit_at = None
    breakeven_hit_at = None
    status = "active"
    for bar in current_h4[entry_index + 1 :]:
        if direction == "long":
            hit_stop = bar["low"] <= stop
            hit_target = bar["high"] >= target
        else:
            hit_stop = bar["high"] >= stop
            hit_target = bar["low"] <= target

        if hit_stop and hit_target:
            stop_hit_at = bar["dt"]
            status = "stopped"
            break
        if hit_stop:
            stop_hit_at = bar["dt"]
            status = "stopped"
            break
        if hit_target and target_hit_at is None:
            target_hit_at = bar["dt"]
            status = "first_target_hit"
            break

    if target_hit_at is not None:
        for bar in current_h4:
            if bar["dt"] <= target_hit_at:
                continue
            if direction == "long" and bar["low"] <= entry:
                breakeven_hit_at = bar["dt"]
                status = "protected_exit"
                break
            if direction == "short" and bar["high"] >= entry:
                breakeven_hit_at = bar["dt"]
                status = "protected_exit"
                break

    latest = current_h4[-1]
    unrealized_r = ((latest["close"] - entry) / entry * 100 * side) / instrument.stop_pct
    add_on = False
    add_on_note = "No add-on setup."
    if status == "first_target_hit" and len(current_h4) >= 3:
        last_three = current_h4[-3:]
        if direction == "long" and latest["close"] == max(bar["close"] for bar in last_three) and latest["close"] > target:
            add_on = True
            add_on_note = "Momentum continuation after first target; consider add-on with floating profit only."
        if direction == "short" and latest["close"] == min(bar["close"] for bar in last_three) and latest["close"] < target:
            add_on = True
            add_on_note = "Momentum continuation after first target; consider add-on with floating profit only."

    return {
        "status": status,
        "message": {
            "active": "Initial position is active.",
            "stopped": "Initial stop was hit.",
            "first_target_hit": "First target hit; partial profit and move remaining stop to breakeven.",
            "protected_exit": "First target hit, then breakeven stop was touched.",
        }.get(status, status),
        "week_open": week_open,
        "entry": entry,
        "entry_time": entry_bar["dt"].isoformat(),
        "stop": stop,
        "first_target": target,
        "stop_pct": instrument.stop_pct,
        "target_r": instrument.target_r,
        "take_profit_pct": instrument.first_take_profit_pct,
        "target_hit_at": target_hit_at.isoformat() if target_hit_at else None,
        "stop_hit_at": stop_hit_at.isoformat() if stop_hit_at else None,
        "breakeven_hit_at": breakeven_hit_at.isoformat() if breakeven_hit_at else None,
        "latest_close": latest["close"],
        "unrealized_r": unrealized_r,
        "add_on": add_on,
        "add_on_note": add_on_note,
    }


def round_floats(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: round_floats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [round_floats(item) for item in value]
    return value


def build_signal(instrument: Instrument) -> dict[str, Any]:
    weekly, h4 = get_market_data(instrument)
    current, current_h4, previous = find_current_week_context(weekly, h4)
    direction = direction_from_previous_week(previous) if previous else "none"
    trade = analyze_trade(instrument, direction, current, current_h4)

    return round_floats(
        {
            "key": instrument.key,
            "name": instrument.name,
            "symbol": instrument.symbol,
            "source": instrument.source,
            "direction": direction,
            "generated_at": now_utc().isoformat(),
            "current_week_start": current["dt"].isoformat(),
            "current_week": current,
            "previous_week": previous,
            "h4_candles_this_week": len(current_h4),
            "strategy": {
                "mode": "aggressive",
                "weekly_filter": "Long: prev close > 20WMA, 20WMA rising, prev close in top 30%. Short: prev close < 20WMA and prev close not in top 30%.",
                "entry": "First 24h 4H close in the weekly direction.",
                "risk": f"{instrument.stop_pct}% initial stop, {instrument.target_r}R first target, partial profit then breakeven trailing.",
            },
            "parameters": {
                "initial_stop_pct": instrument.stop_pct,
                "first_target_r": instrument.target_r,
                "first_take_profit_pct": instrument.first_take_profit_pct,
            },
            "trade": trade,
        }
    )


def main() -> int:
    output = {
        "generated_at": now_utc().isoformat(),
        "timezone": "UTC",
        "notes": [
            "Signals are educational and not financial advice.",
            "Yahoo symbols are proxies for deployability without paid data feeds.",
            "Use broker quotes before placing live orders.",
        ],
        "instruments": [],
        "errors": [],
    }

    for instrument in INSTRUMENTS:
        try:
            output["instruments"].append(build_signal(instrument))
        except Exception as exc:  # noqa: BLE001 - keep dashboard alive per instrument.
            output["errors"].append({"instrument": instrument.key, "error": str(exc)})

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT} with {len(output['instruments'])} instruments and {len(output['errors'])} errors.")
    return 0 if output["instruments"] else 1


if __name__ == "__main__":
    sys.exit(main())
