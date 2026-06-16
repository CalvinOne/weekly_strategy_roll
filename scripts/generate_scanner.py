#!/usr/bin/env python3
"""Generate weekly scanner outputs for altcoins and US large-cap stocks."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import generate_signals as signals
import scanner_data as universe


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = {
    "altcoins": ROOT / "data" / "altcoins_signals.json",
    "stocks": ROOT / "data" / "stocks_signals.json",
}


def attach_meta(signal: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    signal["market_cap_rank"] = meta.get("market_cap_rank")
    if meta.get("market_cap") is not None:
        signal["market_cap"] = meta.get("market_cap")
    return signal


def summarize_output(profile: str, instruments: list[dict[str, Any]], errors: list[dict[str, Any]], universe_size: int) -> dict[str, Any]:
    directional = [item for item in instruments if item.get("direction") != "none"]
    actionable = [
        item
        for item in instruments
        if item.get("trade", {}).get("status") in {"active", "waiting_entry", "first_target_hit", "protected_exit"}
    ]
    high_quality = [item for item in instruments if (item.get("signal_quality") or {}).get("score", 0) >= 75]
    return {
        "generated_at": signals.now_utc().isoformat(),
        "timezone": "UTC",
        "profile": profile,
        "universe_size": universe_size,
        "scanned": len(instruments),
        "directional_count": len(directional),
        "actionable_count": len(actionable),
        "high_quality_count": len(high_quality),
        "notes": [
            "Signals are educational and not financial advice.",
            "Scanner pages reuse the same weekly filter and first-24h 4H confirmation rules as the main dashboard.",
            "Altcoin data comes from Bybit linear USDT perpetuals ranked by 24h turnover; US stocks use Yahoo Finance proxies for large-cap names.",
            "Use exchange or broker quotes before placing live orders.",
        ],
        "summary": {
            "long": sum(1 for item in instruments if item.get("direction") == "long"),
            "short": sum(1 for item in instruments if item.get("direction") == "short"),
            "none": sum(1 for item in instruments if item.get("direction") == "none"),
        },
        "instruments": instruments,
        "errors": errors,
    }


def generate_altcoins(limit: int, sleep_seconds: float) -> dict[str, Any]:
    items = universe.altcoin_instruments(limit)
    instruments: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for index, (instrument, meta) in enumerate(items, start=1):
        try:
            weekly, h4 = universe.get_altcoin_market_data(instrument.symbol)
            signal = attach_meta(signals.build_signal_from_data(instrument, weekly, h4), meta)
            instruments.append(signal)
            print(f"[altcoins] {index}/{len(items)} {instrument.key} -> {signal['direction']}")
        except Exception as exc:  # noqa: BLE001 - keep scanner alive per symbol.
            errors.append({"instrument": instrument.key, "error": str(exc)})
            print(f"[altcoins] {index}/{len(items)} {instrument.key} ERROR {exc}")
        time.sleep(sleep_seconds)

    return summarize_output("altcoins", instruments, errors, len(items))


def generate_stocks(limit: int, sleep_seconds: float) -> dict[str, Any]:
    items = universe.stock_instruments(limit)
    instruments: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for index, (instrument, meta) in enumerate(items, start=1):
        try:
            weekly, h4 = universe.get_stock_market_data(instrument.symbol)
            signal = attach_meta(signals.build_signal_from_data(instrument, weekly, h4), meta)
            instruments.append(signal)
            print(f"[stocks] {index}/{len(items)} {instrument.key} -> {signal['direction']}")
        except Exception as exc:  # noqa: BLE001 - keep scanner alive per symbol.
            errors.append({"instrument": instrument.key, "error": str(exc)})
            print(f"[stocks] {index}/{len(items)} {instrument.key} ERROR {exc}")
        time.sleep(sleep_seconds)

    return summarize_output("stocks", instruments, errors, len(items))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate scanner signal files.")
    parser.add_argument("--type", choices=["altcoins", "stocks", "all"], default="all")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--sleep", type=float, default=1.2, help="Default pause between symbols.")
    parser.add_argument("--altcoin-sleep", type=float, default=0.15, help="Pause between altcoin API calls.")
    parser.add_argument("--stock-sleep", type=float, default=0.6, help="Pause between stock API calls.")
    args = parser.parse_args()

    targets = ["altcoins", "stocks"] if args.type == "all" else [args.type]
    wrote = 0
    for target in targets:
        sleep_seconds = args.altcoin_sleep if target == "altcoins" else args.stock_sleep
        output = (
            generate_altcoins(args.limit, sleep_seconds)
            if target == "altcoins"
            else generate_stocks(args.limit, sleep_seconds)
        )
        path = OUTPUTS[target]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"Wrote {path} scanned={output['scanned']} directional={output['directional_count']} "
            f"errors={len(output['errors'])}"
        )
        wrote += 1
    return 0 if wrote else 1


if __name__ == "__main__":
    sys.exit(main())
