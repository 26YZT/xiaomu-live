/* Installation and operations UI. Secrets are write-only and never persisted by the browser. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const active = new Set(["creating", "connecting", "waiting_stage", "live", "ending"]);
  let runtime = null, state = null, dirty = false;
  const labels = { vidu: "Vidu 数字人", evolink: "Evolink 素材", obs: "OBS 控制", youtube: "YouTube 推流" };
  document.addEventListener("wheel", (event) => {
    const focused = document.activeElement;
    if (focused instanceof HTMLInputElement && focused.type === "number" && event.target === focused) {
      focused.blur(); event.preventDefault();
    }
  }, {passive: false});
  async function request(path, body) {
    const response = await fetch(path, body === undefined ? {cache: "no-store"} : {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
    return data;
  }
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function message(id, text, error = false) {
    $(id).hidden = false;
    $(id).textContent = text;
    $(id).classList.toggle("platform-error", error);
  }
  function row(label, text, good) {
    const node = element("li");
    node.append(element("span", label), element("span", text, good ? "platform-ok" : "platform-pending"));
    return node;
  }
  async function busy(button, callback) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try { return await callback(); }
    finally { button.disabled = false; button.removeAttribute("aria-busy"); updateControls(); }
  }
  function updateControls() {
    const on = state && active.has(state.status);
    if (!("true" === $("demoTour").getAttribute("aria-busy"))) $("demoTour").disabled = !state || on;
    $("welcomeTitle").textContent = on ? (state.mode === "dry_run" ? "演练进行中" : "数字人运行中") : "从一场演练开始";
    $("welcomeText").textContent = on && state.mode === "dry_run"
      ? "正在演练弹幕与静态换装。当前不生成语音或动态视频，费用为 ¥0。"
      : on ? "可在下方发弹幕、观察队列和画面；结束后记得停止会话。"
      : "体验弹幕排队、过滤和静态换装，无需 API Key。";
    for (const id of ["connectionProvider", "connectionSecret", "saveConnection", "removeConnection"]) {
      if ($(id).getAttribute("aria-busy") !== "true") $(id).disabled = !!on;
    }
    if ($("verifyVidu").getAttribute("aria-busy") !== "true") $("verifyVidu").disabled = !runtime?.services.vidu || !!on;
    if (!on && runtime) $("removeConnection").disabled = !runtime.services[$("connectionProvider").value];
  }
  function budgetHint() {
    const amount = Number($("dailyBudget").value || 0) * (state?.credit_price_rmb || 0.03125);
    $("dailyBudgetRmb").textContent = `约 ¥${amount.toFixed(2)}。达到上限将停止会话并阻止自动恢复。`;
  }
  function render() {
    if (!runtime) return;
    $("connectionStatus").replaceChildren(...Object.entries(labels).map(([key, label]) => row(label, runtime.services[key] ? "已保存" : "未配置", runtime.services[key])));
    const c = runtime.checks;
    const inbound = runtime.inbound;
    $("readinessList").replaceChildren(
      row("本机服务", `运行中 · Python ${c.python}`, true),
      row("视频编码", c.ffmpeg ? "ffmpeg 已安装" : "待安装 ffmpeg", c.ffmpeg),
      row("RTC 网页组件", c.rtc_sdk ? "已就绪" : "待下载", c.rtc_sdk),
      row("OBS 应用", c.obs_installed ? "已安装" : "可选，尚未安装", c.obs_installed),
      row("抖音弹幕", inbound ? `最近接收：${inbound.source}` : "待接入外部弹幕桥接", !!inbound),
    );
    $("dailyCost").textContent = `¥${runtime.usage.today_rmb.toFixed(2)}`;
    $("dailyCostHint").textContent = `${runtime.usage.date} · 北京时间 · ${runtime.usage.basis}`;
    if (!dirty) {
      $("dailyBudget").value = runtime.daily_budget_credits;
      $("autoRecover").checked = runtime.auto_recover;
      budgetHint();
    }
    const rows = runtime.usage.sessions.map(item => {
      const tr = element("tr");
      const time = new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}).format(new Date(item.started * 1000));
      for (const value of [time, `${item.profile} · ${item.mode === "dry_run" ? "演练" : "Vidu"}`, `${Math.floor(item.elapsed / 60)}分${item.elapsed % 60}秒`, `¥${item.rmb.toFixed(2)}`, {active:"进行中",ended:"已结束",unconfirmed:"待核对"}[item.status] || item.status]) tr.append(element("td", value));
      tr.title = item.reason || "";
      return tr;
    });
    if (!rows.length) {
      const tr = element("tr"), cell = element("td", "还没有会话记录，完成一场演练后会保存在这里。", "ledger-empty");
      cell.colSpan = 5; tr.append(cell); rows.push(tr);
    }
    $("sessionLedger").replaceChildren(...rows);
    updateControls();
  }
  async function refresh() { runtime = await request("/api/platform"); render(); }
  window.addEventListener("avatar:state", (event) => {
    const before = state?.status;
    state = event.detail; updateControls();
    if (before !== state.status) refresh().catch(() => {});
  });
  $("connectionProvider").addEventListener("change", () => {
    const p = $("connectionProvider").value;
    $("secretLabel").textContent = {vidu:"Vidu API Key",evolink:"Evolink API Key",obs:"OBS WebSocket 密码",youtube:"YouTube 推流码"}[p];
    $("connectionSecret").placeholder = p === "vidu" ? "粘贴 vda_ 开头的 API Key…" : "粘贴配置值…";
    $("connectionSecret").value = "";
    updateControls();
  });
  $("connectionForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const secret = $("connectionSecret");
    try {
      await busy($("saveConnection"), () => request("/api/platform/credentials", {provider:$("connectionProvider").value,value:secret.value}));
      secret.value = "";
      message("connectionMessage", "已保存到本机。可验证 Vidu 连接，保存本身不会开启数字人会话。");
      await refresh();
    } catch (error) { message("connectionMessage", error.message, true); secret.focus(); }
  });
  $("removeConnection").addEventListener("click", async () => {
    const provider = $("connectionProvider").value;
    if (!confirm(`移除 ${labels[provider]} 的本机配置？`)) return;
    try {
      await busy($("removeConnection"), () => request("/api/platform/credentials", {provider,remove:true}));
      $("connectionSecret").value = "";
      message("connectionMessage", "已移除该服务的本机配置。"); await refresh();
    } catch (error) { message("connectionMessage", error.message, true); }
  });
  $("verifyVidu").addEventListener("click", async () => {
    try {
      message("connectionMessage", "正在查询 Vidu 音色接口…");
      const data = await busy($("verifyVidu"), () => request("/api/platform/verify-vidu", {}));
      message("connectionMessage", data.message);
    } catch (error) { message("connectionMessage", error.message, true); }
  });
  $("demoTour").addEventListener("click", async () => {
    try {
      const data = await busy($("demoTour"), () => request("/api/platform/demo", {}));
      message("demoTourMessage", `${data.accepted} 条弹幕进入队列，${data.filtered} 条广告被过滤。${data.archived ? `旧队列 ${data.archived} 条已归档。` : ""}演练每 5 秒执行一条，可继续点击下方的换装弹幕。`);
      await refresh();
    } catch (error) { message("demoTourMessage", error.message, true); }
  });
  $("platformPreferences").addEventListener("input", () => { dirty = true; budgetHint(); });
  $("platformPreferences").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await busy(event.submitter, () => request("/api/platform/preferences", {daily_budget_credits:Number($("dailyBudget").value),auto_recover:$("autoRecover").checked}));
      dirty = false; await refresh(); message("preferencesMessage", "已保存，重启平台后仍然生效。");
    } catch (error) { message("preferencesMessage", error.message, true); }
  });
  $("platformRefresh").addEventListener("click", () => busy($("platformRefresh"), refresh).catch(error => message("preferencesMessage", error.message, true)));
  Promise.all([request("/api/state"), refresh()]).then(([s]) => {state=s;updateControls();}).catch(error => message("connectionMessage", error.message, true));
  setInterval(() => { if (!document.hidden) refresh().catch(() => {}); }, 10000);
})();
