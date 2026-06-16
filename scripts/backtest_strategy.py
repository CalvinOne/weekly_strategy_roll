#!/usr/bin/env python3
"""Compare weekly strategy variants on available historical public data."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import generate_signals as signals


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "backtest_report.json"


@dataclass(frozen=True)
class RuleVariant:
    key: str
    name: str
    entry_rule: str
    stop_rule: str
    description: str


PRODUCTION_VARIANT = RuleVariant(
    "atr_stop",
    "ATR-Aware Stop",
    "first_break",
    "atr",
    "Current production rule: first 24h 4H close through weekly open, stop at least 1.2x 4H ATR.",
)

VARIANTS = [
    RuleVariant(
        "baseline",
        "Baseline",
        "first_break",
        "fixed",
        "Current rule: first 24h 4H close through weekly open, fixed percent stop.",
    ),
    RuleVariant(
        "strict_entry",
        "Stricter 4H Confirmation",
        "two_closes",
        "fixed",
        "Requires two confirming 4H closes in the first 24h.",
    ),
    RuleVariant(
        "pullback_entry",
        "Confirm Then Pullback",
        "confirm_pullback",
        "fixed",
        "Requires first-24h confirmation, then a 48h pullback/reclaim near weekly open.",
    ),
    RuleVariant(
        "atr_stop",
        "ATR-Aware Stop",
        "first_break",
        "atr",
        "Current entry with stop widened to at least 1.2x recent 4H ATR.",
    ),
    RuleVariant(
        "structure_stop",
        "Structure-Aware Stop",
        "first_break",
        "structure",
        "Current entry with stop outside the previous six 4H candle structure.",
    ),
]


def dt(value: dict[str, Any]) -> datetime:
    return value["dt"]


def side_for(direction: str) -> int:
    return 1 if direction == "long" else -1


def is_confirming(direction: str, close: float, week_open: float) -> bool:
    return close > week_open if direction == "long" else close < week_open


def pct_distance(a: float, b: float) -> float:
    return abs(a - b) / b * 100


def group_h4_by_week(h4: list[dict[str, Any]]) -> dict[datetime, list[dict[str, Any]]]:
    grouped: dict[datetime, list[dict[str, Any]]] = {}
    for bar in h4:
        grouped.setdefault(signals.week_start(dt(bar)), []).append(bar)
    return {week: sorted(bars, key=dt) for week, bars in grouped.items()}


def previous_h4_bars(all_h4: list[dict[str, Any]], before: datetime, count: int) -> list[dict[str, Any]]:
    bars = [bar for bar in all_h4 if dt(bar) < before]
    return bars[-count:]


def h4_atr_pct(all_h4: list[dict[str, Any]], before: datetime, period: int = 14) -> float | None:
    bars = previous_h4_bars(all_h4, before, period + 1)
    if len(bars) < period + 1:
        return None

    ranges = []
    for index in range(1, len(bars)):
        bar = bars[index]
        prev_close = bars[index - 1]["close"]
        true_range = max(
            bar["high"] - bar["low"],
            abs(bar["high"] - prev_close),
            abs(bar["low"] - prev_close),
        )
        ranges.append(true_range / bar["close"] * 100)
    return sum(ranges[-period:]) / period


def find_entry(
    variant: RuleVariant,
    direction: str,
    week_open: float,
    week_h4: list[dict[str, Any]],
) -> tuple[int | None, str]:
    first_24h_end = dt(week_h4[0]) + timedelta(hours=24)
    first_48h_end = dt(week_h4[0]) + timedelta(hours=48)

    if variant.entry_rule == "first_break":
        for index, bar in enumerate(week_h4):
            if dt(bar) >= first_24h_end:
                break
            if is_confirming(direction, bar["close"], week_open):
                return index, "entered"
        return None, "missed_entry"

    if variant.entry_rule == "two_closes":
        confirming_count = 0
        for index, bar in enumerate(week_h4):
            if dt(bar) >= first_24h_end:
                break
            if is_confirming(direction, bar["close"], week_open):
                confirming_count += 1
                if confirming_count >= 2:
                    return index, "entered"
            else:
                confirming_count = 0
        return None, "missed_entry"

    if variant.entry_rule == "confirm_pullback":
        confirmed = False
        for index, bar in enumerate(week_h4):
            if dt(bar) >= first_48h_end:
                break
            if not confirmed:
                if dt(bar) < first_24h_end and is_confirming(direction, bar["close"], week_open):
                    confirmed = True
                continue

            if direction == "long" and bar["low"] <= week_open and bar["close"] > week_open:
                return index, "entered"
            if direction == "short" and bar["high"] >= week_open and bar["close"] < week_open:
                return index, "entered"
        return None, "missed_entry"

    raise ValueError(f"Unknown entry rule: {variant.entry_rule}")


def risk_pct_for(
    variant: RuleVariant,
    instrument: signals.Instrument,
    direction: str,
    entry: float,
    entry_time: datetime,
    all_h4: list[dict[str, Any]],
) -> tuple[float, str]:
    fixed = instrument.stop_pct

    if variant.stop_rule == "fixed":
        return fixed, "fixed"

    if variant.stop_rule == "atr":
        atr_pct = h4_atr_pct(all_h4, entry_time)
        if atr_pct is None:
            return fixed, "fixed_fallback"
        return max(fixed, atr_pct * 1.2), f"atr_1.2x={atr_pct * 1.2:.2f}%"

    if variant.stop_rule == "structure":
        structure = previous_h4_bars(all_h4, entry_time, 6)
        if len(structure) < 3:
            return fixed, "fixed_fallback"
        if direction == "long":
            stop_price = min(bar["low"] for bar in structure)
        else:
            stop_price = max(bar["high"] for bar in structure)
        structure_pct = pct_distance(entry, stop_price) + 0.10
        return max(fixed, structure_pct), f"structure={structure_pct:.2f}%"

    raise ValueError(f"Unknown stop rule: {variant.stop_rule}")


def simulate_trade(
    instrument: signals.Instrument,
    variant: RuleVariant,
    direction: str,
    week: datetime,
    week_h4: list[dict[str, Any]],
    all_h4: list[dict[str, Any]],
) -> dict[str, Any]:
    week_open = week_h4[0]["open"]
    entry_index, entry_status = find_entry(variant, direction, week_open, week_h4)
    if entry_index is None:
        return {"week": week, "status": entry_status, "r": 0.0}

    entry_bar = week_h4[entry_index]
    entry = entry_bar["close"]
    side = side_for(direction)
    risk_pct, stop_basis = risk_pct_for(variant, instrument, direction, entry, dt(entry_bar), all_h4)
    stop = entry * (1 - risk_pct / 100) if direction == "long" else entry * (1 + risk_pct / 100)
    target = entry * (1 + risk_pct * instrument.target_r / 100) if direction == "long" else entry * (1 - risk_pct * instrument.target_r / 100)

    target_index = None
    for index, bar in enumerate(week_h4[entry_index + 1 :], start=entry_index + 1):
        hit_stop = bar["low"] <= stop if direction == "long" else bar["high"] >= stop
        hit_target = bar["high"] >= target if direction == "long" else bar["low"] <= target
        if hit_stop and hit_target:
            return {
                "week": week,
                "status": "stopped",
                "r": -1.0,
                "direction": direction,
                "risk_pct": risk_pct,
                "stop_basis": stop_basis,
            }
        if hit_stop:
            return {
                "week": week,
                "status": "stopped",
                "r": -1.0,
                "direction": direction,
                "risk_pct": risk_pct,
                "stop_basis": stop_basis,
            }
        if hit_target:
            target_index = index
            break

    if target_index is None:
        final_close = week_h4[-1]["close"]
        final_r = ((final_close - entry) / entry * 100 * side) / risk_pct
        return {
            "week": week,
            "status": "active_to_week_close",
            "r": final_r,
            "direction": direction,
            "risk_pct": risk_pct,
            "stop_basis": stop_basis,
        }

    breakeven_hit = False
    for bar in week_h4[target_index + 1 :]:
        if direction == "long" and bar["low"] <= entry:
            breakeven_hit = True
            break
        if direction == "short" and bar["high"] >= entry:
            breakeven_hit = True
            break

    if breakeven_hit:
        total_r = instrument.first_take_profit_pct * instrument.target_r
        status = "first_target_then_breakeven"
    else:
        final_close = week_h4[-1]["close"]
        final_r = ((final_close - entry) / entry * 100 * side) / risk_pct
        total_r = instrument.first_take_profit_pct * instrument.target_r + (1 - instrument.first_take_profit_pct) * final_r
        status = "first_target_held"

    return {
        "week": week,
        "status": status,
        "r": total_r,
        "direction": direction,
        "risk_pct": risk_pct,
        "stop_basis": stop_basis,
    }


def max_losing_streak(trades: list[dict[str, Any]]) -> int:
    longest = 0
    current = 0
    for trade in sorted(trades, key=lambda item: item["week"]):
        if trade["status"] == "missed_entry":
            continue
        if trade["r"] < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def summarize(trades: list[dict[str, Any]], weeks_tested: int, directional_weeks: int) -> dict[str, Any]:
    skipped_statuses = {"missed_entry", "no_intraday_data"}
    entered = [trade for trade in trades if trade["status"] not in skipped_statuses]
    missed = [trade for trade in trades if trade["status"] == "missed_entry"]
    no_intraday = [trade for trade in trades if trade["status"] == "no_intraday_data"]
    if not entered:
        return {
            "weeks_tested": weeks_tested,
            "directional_weeks": directional_weeks,
            "trades": 0,
            "missed_entries": len(missed),
            "no_intraday_weeks": len(no_intraday),
            "win_rate": 0,
            "average_r": 0,
            "total_r": 0,
            "first_target_rate": 0,
            "stop_rate": 0,
            "missed_entry_rate": len(missed) / directional_weeks if directional_weeks else 0,
            "no_intraday_rate": len(no_intraday) / directional_weeks if directional_weeks else 0,
            "max_losing_streak": 0,
        }

    wins = [trade for trade in entered if trade["r"] > 0]
    stopped = [trade for trade in entered if trade["status"] == "stopped"]
    first_targets = [trade for trade in entered if trade["status"].startswith("first_target")]
    total_r = sum(trade["r"] for trade in entered)
    return {
        "weeks_tested": weeks_tested,
        "directional_weeks": directional_weeks,
        "trades": len(entered),
        "missed_entries": len(missed),
        "no_intraday_weeks": len(no_intraday),
        "win_rate": len(wins) / len(entered),
        "average_r": total_r / len(entered),
        "total_r": total_r,
        "first_target_rate": len(first_targets) / len(entered),
        "stop_rate": len(stopped) / len(entered),
        "missed_entry_rate": len(missed) / directional_weeks if directional_weeks else 0,
        "no_intraday_rate": len(no_intraday) / directional_weeks if directional_weeks else 0,
        "max_losing_streak": max_losing_streak(entered),
    }


def simulate_instrument(
    instrument: signals.Instrument,
    variant: RuleVariant,
    weekly: list[dict[str, Any]],
    h4: list[dict[str, Any]],
    since: datetime | None = None,
    include_trades: bool = False,
) -> dict[str, Any]:
    grouped_h4 = group_h4_by_week(h4)
    current_week = signals.week_start(datetime.now(timezone.utc))
    weekly_by_start = {dt(bar): bar for bar in weekly}
    complete_weeks = sorted(week for week in grouped_h4 if week < current_week and week in weekly_by_start)
    if since is not None:
        since_week = signals.week_start(since)
        complete_weeks = [week for week in complete_weeks if week >= since_week]

    trades: list[dict[str, Any]] = []
    directional_weeks = 0
    neutral_weeks = 0
    no_intraday_weeks = 0
    for week in complete_weeks:
        previous_weeks = [bar for bar in weekly if dt(bar) < week]
        if not previous_weeks:
            continue
        previous = previous_weeks[-1]
        direction = signals.direction_from_previous_week(previous)
        if direction == "none":
            neutral_weeks += 1
            continue
        directional_weeks += 1
        week_h4 = grouped_h4.get(week, [])
        if not week_h4:
            no_intraday_weeks += 1
            trades.append({"week": week, "status": "no_intraday_data", "r": 0.0, "direction": direction})
            continue
        trades.append(simulate_trade(instrument, variant, direction, week, week_h4, h4))

    result = {
        "instrument": instrument.key,
        "variant": variant.key,
        "variant_name": variant.name,
        "description": variant.description,
        "metrics": summarize(trades, len(complete_weeks), directional_weeks),
        "context": {
            "weeks_in_window": len(complete_weeks),
            "neutral_weeks": neutral_weeks,
            "directional_weeks": directional_weeks,
            "no_intraday_weeks": no_intraday_weeks,
        },
    }
    if include_trades:
        result["trades"] = trades
    return result


def round_floats(value: Any) -> Any:
    if isinstance(value, float):
        if math.isfinite(value):
            return round(value, 4)
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: round_floats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [round_floats(item) for item in value]
    return value


def select_best_variant(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_variant.setdefault(result["variant"], []).append(result)

    aggregated = []
    for variant in VARIANTS:
        items = by_variant.get(variant.key, [])
        trades = sum(item["metrics"]["trades"] for item in items)
        total_r = sum(item["metrics"]["total_r"] for item in items)
        stops = sum(item["metrics"]["stop_rate"] * item["metrics"]["trades"] for item in items)
        first_targets = sum(item["metrics"]["first_target_rate"] * item["metrics"]["trades"] for item in items)
        wins = sum(item["metrics"]["win_rate"] * item["metrics"]["trades"] for item in items)
        average_r = total_r / trades if trades else 0
        stop_rate = stops / trades if trades else 0
        first_target_rate = first_targets / trades if trades else 0
        win_rate = wins / trades if trades else 0
        score = average_r * 100 + first_target_rate * 25 + win_rate * 15 - stop_rate * 35
        aggregated.append(
            {
                "variant": variant.key,
                "variant_name": variant.name,
                "trades": trades,
                "average_r": average_r,
                "total_r": total_r,
                "win_rate": win_rate,
                "first_target_rate": first_target_rate,
                "stop_rate": stop_rate,
                "score": score,
            }
        )

    aggregated.sort(key=lambda item: item["score"], reverse=True)
    return {"selected": aggregated[0] if aggregated else None, "ranking": aggregated}


def main() -> int:
    results = []
    errors = []
    for instrument in signals.INSTRUMENTS:
        try:
            weekly, h4 = signals.get_market_data(instrument)
        except Exception as exc:  # noqa: BLE001 - keep comparison alive per instrument.
            errors.append({"instrument": instrument.key, "variant": "all", "error": str(exc)})
            continue

        for variant in VARIANTS:
            try:
                results.append(simulate_instrument(instrument, variant, weekly, h4))
            except Exception as exc:  # noqa: BLE001 - keep comparison alive per variant.
                errors.append({"instrument": instrument.key, "variant": variant.key, "error": str(exc)})

    report = round_floats(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "notes": [
                "Backtest uses Yahoo public data and simplified intrabar assumptions.",
                "If stop and target are touched in the same 4H candle, the stop is counted first.",
                "Results are research inputs, not financial advice.",
            ],
            "variants": [variant.__dict__ for variant in VARIANTS],
            "selection": select_best_variant(results),
            "results": results,
            "errors": errors,
        }
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    selected = report["selection"]["selected"]
    print(f"Wrote {OUTPUT} with {len(results)} result rows and {len(errors)} errors.")
    if selected:
        print(
            "Selected variant: "
            f"{selected['variant_name']} avgR={selected['average_r']} "
            f"win={selected['win_rate']} stop={selected['stop_rate']}"
        )
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
