#!/usr/bin/env python3
"""Run a 3-year production-strategy backtest per instrument and write a report."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import backtest_strategy as backtest
import generate_signals as signals


ROOT = Path(__file__).resolve().parents[1]
JSON_OUTPUT = ROOT / "data" / "backtest_3y_report.json"
MD_OUTPUT = ROOT / "data" / "backtest_3y_report.md"
BACKTEST_YEARS = 3


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def yearly_breakdown(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    skipped = {"missed_entry", "no_intraday_data"}
    by_year: dict[int, list[dict[str, Any]]] = {}
    for trade in trades:
        if trade["status"] in skipped:
            continue
        by_year.setdefault(trade["week"].year, []).append(trade)

    rows = []
    for year in sorted(by_year):
        items = by_year[year]
        wins = [trade for trade in items if trade["r"] > 0]
        total_r = sum(trade["r"] for trade in items)
        rows.append(
            {
                "year": year,
                "trades": len(items),
                "win_rate": len(wins) / len(items) if items else 0,
                "total_r": total_r,
                "average_r": total_r / len(items) if items else 0,
            }
        )
    return rows


def status_breakdown(trades: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trade in trades:
        counts[trade["status"]] = counts.get(trade["status"], 0) + 1
    return counts


def aggregate_portfolio(results: list[dict[str, Any]]) -> dict[str, Any]:
    trades = 0
    total_r = 0.0
    wins = 0.0
    stops = 0.0
    first_targets = 0.0
    for result in results:
        metrics = result["metrics"]
        count = metrics["trades"]
        trades += count
        total_r += metrics["total_r"]
        wins += metrics["win_rate"] * count
        stops += metrics["stop_rate"] * count
        first_targets += metrics["first_target_rate"] * count
    return {
        "instruments": len(results),
        "trades": trades,
        "total_r": total_r,
        "average_r": total_r / trades if trades else 0,
        "win_rate": wins / trades if trades else 0,
        "stop_rate": stops / trades if trades else 0,
        "first_target_rate": first_targets / trades if trades else 0,
    }


def build_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 三年回测报告（生产策略）",
        "",
        f"- 生成时间（UTC）：{report['generated_at']}",
        f"- 策略：{report['strategy']['name']}",
        f"- 回测窗口：{report['window']['start']} → {report['window']['end']}（约 {BACKTEST_YEARS} 年）",
        f"- 规则：{report['strategy']['description']}",
        "",
        "## 重要说明",
        "",
    ]
    for note in report["notes"]:
        lines.append(f"- {note}")
    lines.extend(["", "## 全品种汇总", ""])

    portfolio = report["portfolio"]
    lines.extend(
        [
            "| 指标 | 数值 |",
            "|---|---:|",
            f"| 有效品种数 | {portfolio['instruments']} |",
            f"| 成交笔数 | {portfolio['trades']} |",
            f"| 总 R | {num(portfolio['total_r'])} |",
            f"| 平均 R | {num(portfolio['average_r'])} |",
            f"| 胜率 | {pct(portfolio['win_rate'])} |",
            f"| 首目标率 | {pct(portfolio['first_target_rate'])} |",
            f"| 止损率 | {pct(portfolio['stop_rate'])} |",
            "",
            "## 分品种摘要",
            "",
            "| 标的 | 4H覆盖 | 有方向周 | 成交 | 胜率 | 均R | 总R | 首目标率 | 止损率 | 错过入场 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )

    for item in report["instruments"]:
        metrics = item["metrics"]
        coverage = item["coverage"]
        lines.append(
            "| {key} | {start} ~ {end} | {directional} | {trades} | {win} | {avg} | {total} | {first} | {stop} | {missed} |".format(
                key=item["instrument"],
                start=coverage.get("hourly_start", "-")[:10],
                end=coverage.get("hourly_end", "-")[:10],
                directional=metrics["directional_weeks"],
                trades=metrics["trades"],
                win=pct(metrics["win_rate"]),
                avg=num(metrics["average_r"]),
                total=num(metrics["total_r"]),
                first=pct(metrics["first_target_rate"]),
                stop=pct(metrics["stop_rate"]),
                missed=metrics["missed_entries"],
            )
        )

    lines.extend(["", "## 分品种详情", ""])
    for item in report["instruments"]:
        metrics = item["metrics"]
        context = item["context"]
        lines.extend(
            [
                f"### {item['instrument']}",
                "",
                f"- 4H 数据覆盖：{item['coverage'].get('hourly_start', '-')} → {item['coverage'].get('hourly_end', '-')}",
                f"- 窗口内完整周：{context['weeks_in_window']}",
                f"- 无方向过滤周：{context['neutral_weeks']}",
                f"- 有方向周：{context['directional_weeks']}",
                f"- 缺 4H 数据周：{context['no_intraday_weeks']}",
                f"- 成交：{metrics['trades']} 笔，胜率 {pct(metrics['win_rate'])}，均 R {num(metrics['average_r'])}，总 R {num(metrics['total_r'])}",
                f"- 首目标率 {pct(metrics['first_target_rate'])}，止损率 {pct(metrics['stop_rate'])}，错过入场 {metrics['missed_entries']}",
                f"- 最大连亏：{metrics['max_losing_streak']}",
                "",
                "**按年拆分**",
                "",
                "| 年份 | 笔数 | 胜率 | 均R | 总R |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        yearly = item.get("yearly") or []
        if yearly:
            for row in yearly:
                lines.append(
                    f"| {row['year']} | {row['trades']} | {pct(row['win_rate'])} | {num(row['average_r'])} | {num(row['total_r'])} |"
                )
        else:
            lines.append("| - | 0 | - | - | - |")

        statuses = item.get("status_breakdown") or {}
        if statuses:
            lines.extend(["", "**结果分布**", ""])
            for status, count in sorted(statuses.items()):
                lines.append(f"- {status}: {count}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def serialize(value: Any) -> Any:
    return backtest.round_floats(value)


def main() -> int:
    since = datetime.now(timezone.utc) - timedelta(days=BACKTEST_YEARS * 365)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for instrument in signals.INSTRUMENTS:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                weekly, h4, coverage = signals.get_market_data_for_backtest(instrument, years=BACKTEST_YEARS)
                simulated = backtest.simulate_instrument(
                    instrument,
                    backtest.PRODUCTION_VARIANT,
                    weekly,
                    h4,
                    since=since,
                    include_trades=True,
                )
                trades = simulated.pop("trades")
                simulated["coverage"] = coverage
                simulated["yearly"] = yearly_breakdown(trades)
                simulated["status_breakdown"] = status_breakdown(trades)
                results.append(simulated)
                time.sleep(0.5)
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - keep report generation alive per instrument.
                last_error = exc
                time.sleep(2)
        if last_error is not None:
            errors.append({"instrument": instrument.key, "error": str(last_error)})

    report = serialize(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window": {
                "years": BACKTEST_YEARS,
                "start": since.date().isoformat(),
                "end": datetime.now(timezone.utc).date().isoformat(),
            },
            "strategy": {
                "key": backtest.PRODUCTION_VARIANT.key,
                "name": backtest.PRODUCTION_VARIANT.name,
                "description": backtest.PRODUCTION_VARIANT.description,
            },
            "notes": [
                "Uses the current production rule: weekly filter + first 24h 4H confirmation + ATR-aware stop + 2.5R/2R first target model.",
                "Yahoo free hourly history is shorter than 3 years for some symbols; weeks before 4H coverage are marked as no_intraday_data.",
                "If stop and target touch in the same 4H candle, the stop is counted first.",
                "Results are research inputs, not financial advice.",
            ],
            "portfolio": aggregate_portfolio(results),
            "instruments": results,
            "errors": errors,
        }
    )

    JSON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    MD_OUTPUT.write_text(build_markdown(report), encoding="utf-8")

    print(f"Wrote {JSON_OUTPUT}")
    print(f"Wrote {MD_OUTPUT}")
    print(
        "Portfolio: "
        f"trades={report['portfolio']['trades']} "
        f"avgR={report['portfolio']['average_r']} "
        f"win={report['portfolio']['win_rate']}"
    )
    if errors:
        print(f"Errors: {len(errors)}")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
