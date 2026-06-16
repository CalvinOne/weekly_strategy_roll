export const AUTH_HASH = "7f154516b0d91c14e9aa0a3efa0f2183482fbcaf7ae7fe5a46afbdba00898b3a";
export const AUTH_STORAGE_KEY = "weeklyStrategyRollAuth";

export const directionLabels = {
  long: "只做多",
  short: "只做空",
  none: "不做",
};

export const statusLabels = {
  no_trade: "本周不做",
  waiting_data: "等待数据",
  waiting_entry: "等待 4H 确认",
  missed_entry: "错过入场",
  active: "持仓中",
  stopped: "已止损",
  first_target_hit: "首目标已到",
  protected_exit: "保本退出",
  active_to_week_close: "周末仍持仓",
};

export async function sha256(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function fmtNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  if (Math.abs(number) >= 1000) return number.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (Math.abs(number) >= 10) return number.toLocaleString(undefined, { maximumFractionDigits: 3 });
  return number.toLocaleString(undefined, { maximumFractionDigits: 5 });
}

export function fmtTime(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", {
    hour12: false,
    timeZone: "Asia/Shanghai",
  });
}

export function isBeijingWeekday(date = new Date()) {
  const day = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    weekday: "short",
  }).format(date);
  return !["Sat", "Sun"].includes(day);
}

export function formatAge(ms) {
  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  if (hours >= 24) return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时前`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分钟前`;
  return `${minutes} 分钟前`;
}

export function updateDataFreshness(generatedAt) {
  const element = document.getElementById("dataFreshness");
  if (!element) return;
  if (!generatedAt) {
    element.textContent = "-";
    element.dataset.level = "unknown";
    return;
  }

  const ageMs = Date.now() - new Date(generatedAt).getTime();
  const ageHours = ageMs / 3_600_000;
  let message;
  let level = "ok";

  if (!isBeijingWeekday() && ageHours < 48) {
    message = "周末无定时更新，下周一 08:05 自动刷新。";
  } else if (isBeijingWeekday() && ageHours > 5) {
    level = "warn";
    message = `数据已 ${formatAge(ageMs)}，可能不是最新行情。请在 GitHub Actions 运行 workflow 重新拉取。`;
  } else {
    message = `数据 ${formatAge(ageMs)}更新；工作日每 4 小时自动刷新。`;
  }

  element.textContent = message;
  element.dataset.level = level;
}

export function setRefreshState(isLoading, message) {
  const button = document.getElementById("refreshButton");
  const status = document.getElementById("refreshStatus");
  if (!button || !status) return;
  button.disabled = isLoading;
  button.textContent = isLoading ? "加载中..." : "重新加载部署数据";
  status.textContent = message;
}

export function initializeAuthGate(onUnlock) {
  function unlock() {
    document.getElementById("authGate").hidden = true;
    document.getElementById("dashboard").hidden = false;
    onUnlock();
  }

  function handleLoadError(error) {
    const generatedAt = document.getElementById("generatedAt");
    if (generatedAt) generatedAt.textContent = "加载失败";
    setRefreshState(false, `加载失败：${error.message}`);
    const errors = document.getElementById("errors");
    if (errors) {
      errors.hidden = false;
      errors.textContent = error.message;
    }
  }

  try {
    if (localStorage.getItem(AUTH_STORAGE_KEY) === AUTH_HASH) {
      unlock();
      return { handleLoadError };
    }
  } catch {
    // Private browsing may disable localStorage.
  }

  document.getElementById("authForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("authError");
    const password = document.getElementById("authPassword").value;

    if (!crypto?.subtle) {
      error.textContent = "当前浏览器不支持本地密码校验，请换用现代浏览器。";
      return;
    }

    if ((await sha256(password)) !== AUTH_HASH) {
      error.textContent = "密码不正确。";
      return;
    }

    try {
      localStorage.setItem(AUTH_STORAGE_KEY, AUTH_HASH);
    } catch {
      // Continue without remembering auth if storage is unavailable.
    }
    error.textContent = "";
    unlock();
  });

  return { handleLoadError };
}

export function markActiveNav() {
  const current = document.body.dataset.page;
  document.querySelectorAll(".site-nav a").forEach((link) => {
    link.classList.toggle("active", link.dataset.page === current);
  });
}
