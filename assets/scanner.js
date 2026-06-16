import {
  fmtTime,
  initializeAuthGate,
  markActiveNav,
  setRefreshState,
  updateDataFreshness,
} from "./common.js";
import { renderSignalCard } from "./signal-card.js";

let latestData = null;
let lastLoadedGeneratedAt = null;

const state = {
  direction: "all",
  status: "all",
  sort: "score-desc",
  query: "",
};

function qualityScore(item) {
  return item.signal_quality?.score ?? 0;
}

function tradeStatus(item) {
  return item.trade?.status || "no_trade";
}

function matchesFilters(item) {
  if (state.direction !== "all" && item.direction !== state.direction) return false;
  if (state.status === "actionable") {
    const actionable = new Set(["active", "waiting_entry", "first_target_hit", "protected_exit"]);
    if (!actionable.has(tradeStatus(item))) return false;
  } else if (state.status === "high_quality") {
    if (qualityScore(item) < 75) return false;
  } else if (state.status !== "all" && tradeStatus(item) !== state.status) {
    return false;
  }

  const query = state.query.trim().toLowerCase();
  if (!query) return true;
  return (
    String(item.key).toLowerCase().includes(query) ||
    String(item.name).toLowerCase().includes(query) ||
    String(item.symbol).toLowerCase().includes(query)
  );
}

function sortItems(items) {
  const sorted = [...items];
  sorted.sort((a, b) => {
    switch (state.sort) {
      case "rank-asc":
        return (a.market_cap_rank ?? 9999) - (b.market_cap_rank ?? 9999);
      case "symbol-asc":
        return String(a.key).localeCompare(String(b.key));
      case "score-desc":
      default:
        return qualityScore(b) - qualityScore(a);
    }
  });
  return sorted;
}

function renderSummary(data) {
  const summary = document.getElementById("scanSummary");
  if (!summary) return;
  const cards = [
    ["扫描成功", data.scanned ?? 0],
    ["有方向", data.directional_count ?? 0],
    ["可跟踪", data.actionable_count ?? 0],
    ["高质量", data.high_quality_count ?? 0],
    ["做多", data.summary?.long ?? 0],
    ["做空", data.summary?.short ?? 0],
  ];
  summary.replaceChildren(
    ...cards.map(([label, value]) => {
      const item = document.createElement("div");
      item.className = "stat-chip";
      item.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
      return item;
    }),
  );
}

function renderCards(data) {
  const container = document.getElementById("scanCards");
  const empty = document.getElementById("scanEmpty");
  const count = document.getElementById("scanCount");
  const filtered = sortItems((data.instruments || []).filter(matchesFilters));

  container.replaceChildren(
    ...filtered.map((item) => renderSignalCard(item, { compact: true, showRank: true })),
  );
  empty.hidden = filtered.length > 0;
  count.textContent = `显示 ${filtered.length} / ${data.instruments?.length || 0}`;
}

async function load(isManual = false) {
  const dataUrl = document.body.dataset.dataUrl;
  if (isManual) setRefreshState(true, "正在重新读取 GitHub Pages 上的已部署数据...");
  const response = await fetch(`${dataUrl}?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`数据加载失败：${response.status}`);
  latestData = await response.json();
  document.getElementById("generatedAt").textContent = fmtTime(latestData.generated_at);
  updateDataFreshness(latestData.generated_at);
  renderSummary(latestData);
  renderCards(latestData);

  const errors = document.getElementById("errors");
  errors.hidden = true;
  errors.textContent = "";
  if (latestData.errors?.length) {
    errors.hidden = false;
    errors.dataset.level = "warn";
    errors.textContent = `有 ${latestData.errors.length} 个标的因数据源暂时不可用未能生成。已成功扫描 ${latestData.scanned ?? 0} 个，稍后重新运行 workflow 可补全。`;
  } else {
    errors.dataset.level = "";
  }

  if (isManual) {
    const sameVersion = lastLoadedGeneratedAt === latestData.generated_at;
    setRefreshState(
      false,
      sameVersion
        ? "已是最新部署版本；如需重新拉取行情，请在 GitHub Actions 运行 workflow。"
        : `已加载新版本（${fmtTime(latestData.generated_at)}）。`,
    );
  } else {
    setRefreshState(false, "");
  }
  lastLoadedGeneratedAt = latestData.generated_at;
}

function bindControls() {
  document.getElementById("directionFilter").addEventListener("change", (event) => {
    state.direction = event.target.value;
    renderCards(latestData);
  });
  document.getElementById("statusFilter").addEventListener("change", (event) => {
    state.status = event.target.value;
    renderCards(latestData);
  });
  document.getElementById("sortFilter").addEventListener("change", (event) => {
    state.sort = event.target.value;
    renderCards(latestData);
  });
  document.getElementById("searchInput").addEventListener("input", (event) => {
    state.query = event.target.value;
    renderCards(latestData);
  });
  document.getElementById("refreshButton").addEventListener("click", () => {
    load(true).catch(handleLoadError);
  });
}

const { handleLoadError } = initializeAuthGate(() => load().catch(handleLoadError));
markActiveNav();
bindControls();
