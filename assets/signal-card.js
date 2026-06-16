import { directionLabels, fmtNumber, fmtTime, statusLabels } from "./common.js";

function qualityScore(item) {
  return item.signal_quality?.score ?? 0;
}

function formatStopSummary(trade, item) {
  const fixed = item.parameters?.initial_stop_pct;
  const atr = trade.atr_stop_pct;
  const effective = Number(trade.effective_stop_pct || trade.stop_pct || item.parameters?.initial_stop_pct) || 0;
  const parts = [`固定 ${fmtNumber(fixed)}%`];
  if (atr !== null && atr !== undefined) parts.push(`ATR ${fmtNumber(atr)}%`);
  parts.push(`有效 ${fmtNumber(effective)}%`);
  return parts.join(" · ");
}

export function renderSignalCard(item, { compact = false, showRank = false } = {}) {
  const trade = item.trade || {};
  const status = trade.status || "no_trade";
  const quality = item.signal_quality || {};
  const score = qualityScore(item);

  const card = document.createElement("article");
  card.className = `card signal-card${compact ? " signal-card-compact" : ""} status-${status} dir-${item.direction || "none"}`;

  const rankHtml = showRank
    ? `<span class="rank-chip">#${item.market_cap_rank ?? "-"}</span>`
    : "";

  card.innerHTML = `
    <div class="card-head">
      <div class="card-title">
        ${rankHtml}
        <div>
          <p class="symbol">${item.key}</p>
          <h2 class="name">${item.name}${item.symbol && item.symbol !== item.key ? ` · ${item.symbol}` : ""}</h2>
        </div>
      </div>
      <span class="badge badge-${item.direction || "none"}">${directionLabels[item.direction] || item.direction}</span>
    </div>
    <div class="status">${statusLabels[status] || status}${trade.message ? ` · ${trade.message}` : ""}</div>
    <dl class="grid">
      <div><dt>周开盘</dt><dd>${fmtNumber(trade.week_open ?? item.current_week?.open)}</dd></div>
      <div><dt>入场</dt><dd>${fmtNumber(trade.entry)}</dd></div>
      <div><dt>止损</dt><dd class="price-stop">${fmtNumber(trade.stop)}</dd></div>
      <div><dt>第一目标</dt><dd class="price-target">${fmtNumber(trade.first_target)}</dd></div>
      <div><dt>最新价</dt><dd>${fmtNumber(trade.latest_close ?? item.current_week?.close)}</dd></div>
      <div><dt>浮动 R</dt><dd class="price-r">${trade.unrealized_r === undefined ? "-" : `${fmtNumber(trade.unrealized_r)}R`}</dd></div>
    </dl>
    <div class="quality">
      <div class="quality-head">
        <span>信号质量</span>
        <strong>${score} / 100 · ${quality.label || "-"}</strong>
      </div>
      <div class="score-bar" aria-hidden="true"><span style="width:${Math.min(100, Math.max(0, score))}%"></span></div>
      <p>${(quality.factors || []).slice(0, 3).map((f) => `${f.name}: ${f.value}`).join(" · ") || "暂无评分因子。"}</p>
      ${quality.warnings?.length ? `<p class="quality-warning">${quality.warnings.join("；")}</p>` : ""}
    </div>
    <div class="meta">${[
      item.h4_candles_this_week !== undefined ? `本周 4H：${item.h4_candles_this_week}` : null,
      `止损：${formatStopSummary(trade, item)}`,
      item.parameters?.first_target_r ? `目标：${item.parameters.first_target_r}R` : null,
      trade.entry_time ? `入场：${fmtTime(trade.entry_time)}` : null,
    ].filter(Boolean).join(" · ")}</div>
  `;

  return card;
}
