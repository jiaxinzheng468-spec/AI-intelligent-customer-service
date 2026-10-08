const state = {
  session: null,
  csrf: "",
  socket: null,
  socketRetry: 1000,
  currentPath: location.pathname,
  shops: [],
  knowledgeTab: "products",
  selectedShop: null,
  jobs: new Map(),
  refreshTimer: null,
};

const routes = {
  "/auto-reply": ["自动回复", "连接监控与账号控制"],
  "/accounts": ["账号管理", "拼多多店铺与客服账号"],
  "/keywords": ["关键词管理", "命中后转接人工客服"],
  "/knowledge": ["知识库", "商品知识与客服政策"],
  "/logs": ["运行日志", "脱敏后的服务运行记录"],
  "/settings": ["系统设置", "模型、营业时间与 Agent 指令"],
};

const app = document.querySelector("#app");
const modal = document.querySelector("#modal");
const toastRoot = document.querySelector("#toast-root");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function toast(message, type = "info") {
  const item = document.createElement("div");
  item.className = `toast ${type === "error" ? "error" : ""}`;
  item.textContent = message;
  toastRoot.appendChild(item);
  setTimeout(() => item.remove(), 4200);
}

function errorMessage(error) {
  return error?.message || "操作失败，请稍后重试";
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  const isForm = options.body instanceof FormData;
  if (options.body && !isForm && typeof options.body !== "string") {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && state.csrf) {
    headers.set("X-CSRF-Token", state.csrf);
  }
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    method,
    headers,
    credentials: "same-origin",
  });
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login") {
      state.session = null;
      state.csrf = "";
      history.replaceState({}, "", "/login");
      renderLogin();
    }
    const error = new Error(data?.error?.message || `请求失败 (${response.status})`);
    error.code = data?.error?.code;
    error.details = data?.error?.details;
    throw error;
  }
  return data;
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString("zh-CN", { hour12: false });
}

function platformBadge(status) {
  if (status === 1) return '<span class="badge badge-green">在线</span>';
  if (status === 3) return '<span class="badge badge-gray">离线</span>';
  return '<span class="badge badge-amber">未确认</span>';
}

function runtimeBadge(runtimeState) {
  const map = {
    connected: ["已连接", "badge-green"],
    connecting: ["连接中", "badge-blue"],
    reconnecting: ["重连中", "badge-amber"],
    stopping: ["停止中", "badge-amber"],
    error: ["异常", "badge-red"],
    disconnected: ["未运行", "badge-gray"],
  };
  const [label, cls] = map[runtimeState] || [runtimeState || "未知", "badge-gray"];
  return `<span class="badge ${cls}">${escapeHtml(label)}</span>`;
}

function showModal({ title, body, submitText = "保存", danger = false, onSubmit, wide = false }) {
  modal.style.width = wide ? "min(820px, calc(100vw - 28px))" : "";
  modal.innerHTML = `
    <form id="modal-form">
      <div class="modal-header">
        <h3>${escapeHtml(title)}</h3>
        <button type="button" class="modal-close" aria-label="关闭">×</button>
      </div>
      <div class="modal-body">${body}</div>
      <div class="modal-footer">
        <button type="button" class="btn modal-cancel">取消</button>
        <button type="submit" class="btn ${danger ? "btn-danger" : "btn-primary"}">${escapeHtml(submitText)}</button>
      </div>
    </form>`;
  const form = modal.querySelector("#modal-form");
  const close = () => modal.close();
  modal.querySelector(".modal-close").onclick = close;
  modal.querySelector(".modal-cancel").onclick = close;
  form.onsubmit = async (event) => {
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const shouldClose = await onSubmit(new FormData(form), form);
      if (shouldClose !== false) close();
    } catch (error) {
      toast(errorMessage(error), "error");
    } finally {
      submit.disabled = false;
    }
  };
  modal.showModal();
}

function confirmAction(title, message, action, submitText = "确认") {
  showModal({
    title,
    danger: true,
    submitText,
    body: `<p>${escapeHtml(message)}</p>`,
    onSubmit: async () => {
      await action();
      return true;
    },
  });
}

function renderLogin() {
  disconnectEvents();
  app.innerHTML = `
    <main class="login-page">
      <section class="login-panel">
        <div class="brand-mark">AC</div>
        <h2>登录管理后台</h2>
        <p class="muted">使用部署时配置的管理员凭据</p>
        <form id="login-form" class="field">
          <div class="field">
            <label for="login-username">用户名</label>
            <input id="login-username" name="username" class="input" autocomplete="username" required autofocus>
          </div>
          <div class="field">
            <label for="login-password">密码</label>
            <input id="login-password" name="password" type="password" class="input" autocomplete="current-password" required>
          </div>
          <button class="btn btn-primary" type="submit">登录</button>
          <p id="login-error" class="error-text" role="alert"></p>
        </form>
      </section>
      <section class="login-context">
        <div class="login-context-content">
          <h1>Agent-Customer</h1>
          <p>集中管理店铺账号、AI 自动回复、商品知识和客服政策。后台连接独立运行，不依赖浏览器保持打开。</p>
        </div>
      </section>
    </main>`;
  const form = document.querySelector("#login-form");
  form.onsubmit = async (event) => {
    event.preventDefault();
    const submit = form.querySelector("button");
    const errorNode = document.querySelector("#login-error");
    submit.disabled = true;
    errorNode.textContent = "";
    const values = new FormData(form);
    try {
      const result = await api("/auth/login", {
        method: "POST",
        body: { username: values.get("username"), password: values.get("password") },
      });
      state.session = { username: result.username };
      state.csrf = result.csrf_token;
      navigate("/auto-reply", true);
    } catch (error) {
      errorNode.textContent = errorMessage(error);
    } finally {
      submit.disabled = false;
    }
  };
}

function shell() {
  const path = routes[state.currentPath] ? state.currentPath : "/auto-reply";
  state.currentPath = path;
  const [title, subtitle] = routes[path];
  const links = [
    ["/auto-reply", "AR", "自动回复"],
    ["/accounts", "A", "账号管理"],
    ["/keywords", "K", "关键词管理"],
    ["/knowledge", "KB", "知识库"],
    ["/logs", "L", "日志"],
    ["/settings", "S", "设置"],
  ];
  app.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar" id="sidebar">
        <div class="sidebar-brand"><div class="brand-mark">AC</div><div><strong>Agent-Customer</strong><span>Web Console</span></div></div>
        <nav class="nav">${links.map(([href, glyph, label]) => `
          <a class="nav-link ${path === href ? "active" : ""}" data-nav href="${href}"><span class="nav-glyph">${glyph}</span>${label}</a>`).join("")}</nav>
        <div class="sidebar-footer"><div class="user">${escapeHtml(state.session?.username || "admin")}</div><button id="logout-button">退出登录</button></div>
      </aside>
      <div class="content-shell">
        <header class="topbar">
          <div style="display:flex;align-items:center;gap:10px"><button class="btn icon-btn mobile-menu" id="menu-button" aria-label="打开导航">≡</button><div><h1>${escapeHtml(title)}</h1><span class="muted" style="font-size:11px">${escapeHtml(subtitle)}</span></div></div>
          <div class="connection-indicator"><span id="connection-dot" class="connection-dot"></span><span id="connection-text">正在连接事件流</span></div>
        </header>
        <main id="page" class="page"><div class="loading">正在加载...</div></main>
      </div>
    </div>`;
  document.querySelectorAll("[data-nav]").forEach((link) => {
    link.onclick = (event) => {
      event.preventDefault();
      navigate(link.getAttribute("href"));
    };
  });
  document.querySelector("#logout-button").onclick = async () => {
    try { await api("/auth/logout", { method: "POST" }); } catch (_) {}
    state.session = null;
    state.csrf = "";
    history.replaceState({}, "", "/login");
    renderLogin();
  };
  document.querySelector("#menu-button").onclick = () => document.querySelector("#sidebar").classList.toggle("open");
  connectEvents();
}

async function navigate(path, replace = false) {
  if (!state.session) {
    history.replaceState({}, "", "/login");
    renderLogin();
    return;
  }
  state.currentPath = routes[path] ? path : "/auto-reply";
  if (replace) history.replaceState({}, "", state.currentPath);
  else history.pushState({}, "", state.currentPath);
  shell();
  await renderPage();
}

async function renderPage() {
  try {
    if (state.currentPath === "/auto-reply") await loadAutoReply();
    else if (state.currentPath === "/accounts") await loadAccounts();
    else if (state.currentPath === "/keywords") await loadKeywords();
    else if (state.currentPath === "/knowledge") await loadKnowledge();
    else if (state.currentPath === "/logs") await loadLogs();
    else if (state.currentPath === "/settings") await loadSettings();
  } catch (error) {
    const page = document.querySelector("#page");
    if (page) page.innerHTML = `<div class="empty"><strong>页面加载失败</strong><p>${escapeHtml(errorMessage(error))}</p><button class="btn" id="retry-page">重试</button></div>`;
    document.querySelector("#retry-page")?.addEventListener("click", renderPage);
  }
}

function entityCell(item) {
  const initial = escapeHtml((item.username || item.shop_name || "A").slice(0, 1).toUpperCase());
  const avatar = item.shop_logo
    ? `<img class="avatar" src="${escapeHtml(item.shop_logo)}" alt="">`
    : `<span class="avatar">${initial}</span>`;
  return `<div class="entity">${avatar}<div><strong>${escapeHtml(item.username)}</strong><span>${escapeHtml(item.shop_name)} · ${escapeHtml(item.user_id)}</span></div></div>`;
}

async function loadAutoReply() {
  const data = await api("/runtimes");
  const items = data.items;
  const counts = {
    total: items.length,
    online: items.filter((item) => item.platform_status === 1).length,
    connected: items.filter((item) => item.state === "connected").length,
    pending: items.filter((item) => ["connecting", "reconnecting", "stopping"].includes(item.state)).length,
    error: items.filter((item) => item.state === "error").length,
  };
  const page = document.querySelector("#page");
  page.innerHTML = `
    <div class="page-heading"><h2>连接运行状态</h2><p class="muted">账号连接在服务端持续运行，关闭浏览器不会停止回复。</p></div>
    <div class="metrics">
      <div class="metric"><div class="metric-label">账号总数</div><div class="metric-value">${counts.total}</div></div>
      <div class="metric good"><div class="metric-label">平台在线</div><div class="metric-value">${counts.online}</div></div>
      <div class="metric good"><div class="metric-label">已连接</div><div class="metric-value">${counts.connected}</div></div>
      <div class="metric warn"><div class="metric-label">处理中</div><div class="metric-value">${counts.pending}</div></div>
      <div class="metric bad"><div class="metric-label">异常</div><div class="metric-value">${counts.error}</div></div>
    </div>
    <div class="toolbar">
      <input id="runtime-search" class="input search" placeholder="搜索账号、店铺或用户 ID">
      <select id="runtime-filter" class="select" style="width:150px"><option value="">全部状态</option><option value="connected">已连接</option><option value="disconnected">未运行</option><option value="error">异常</option></select>
      <div class="toolbar-spacer"></div>
      <button class="btn icon-btn" id="refresh-runtime" title="刷新">↻</button>
      <button class="btn btn-primary" id="start-all">开始所有</button>
      <button class="btn btn-danger" id="stop-all">停止所有</button>
    </div>
    <div class="table-wrap"><table class="table"><thead><tr><th>账号</th><th>平台状态</th><th>自动回复</th><th>连接时间</th><th>重连</th><th>最近错误</th><th>操作</th></tr></thead><tbody id="runtime-body"></tbody></table></div>`;

  const renderRows = () => {
    const search = document.querySelector("#runtime-search").value.toLowerCase();
    const filter = document.querySelector("#runtime-filter").value;
    const filtered = items.filter((item) => {
      const haystack = `${item.username} ${item.shop_name} ${item.user_id}`.toLowerCase();
      return (!search || haystack.includes(search)) && (!filter || item.state === filter);
    });
    document.querySelector("#runtime-body").innerHTML = filtered.length ? filtered.map((item) => `
      <tr data-id="${item.account_id}">
        <td>${entityCell(item)}</td>
        <td>${platformBadge(item.platform_status)}</td>
        <td>${runtimeBadge(item.state)}</td>
        <td>${escapeHtml(formatDate(item.connect_time || item.started_at))}</td>
        <td>${item.reconnect_count || 0}</td>
        <td>${item.last_error ? `<span class="error-text">${escapeHtml(item.last_error)}</span>` : '<span class="muted">-</span>'}</td>
        <td><div class="actions">
          ${item.platform_status === 1 ? `<button class="btn btn-sm" data-status="3">下线</button>` : `<button class="btn btn-sm" data-status="1">上线</button>`}
          ${["connected", "connecting", "reconnecting", "error"].includes(item.state) ? `<button class="btn btn-sm btn-danger" data-runtime="stop">停止</button>` : `<button class="btn btn-sm btn-primary" data-runtime="start" ${item.platform_status !== 1 ? "disabled" : ""}>启动</button>`}
        </div></td>
      </tr>`).join("") : '<tr><td colspan="7" class="empty">没有符合条件的账号</td></tr>';
    document.querySelectorAll("[data-runtime]").forEach((button) => button.onclick = async () => {
      const id = Number(button.closest("tr").dataset.id);
      button.disabled = true;
      try {
        await api(`/runtimes/${id}/${button.dataset.runtime}`, { method: "POST" });
        toast(button.dataset.runtime === "start" ? "启动请求已受理" : "账号已停止");
        await loadAutoReply();
      } catch (error) { toast(errorMessage(error), "error"); button.disabled = false; }
    });
    document.querySelectorAll("[data-status]").forEach((button) => button.onclick = async () => {
      const id = Number(button.closest("tr").dataset.id);
      const status = Number(button.dataset.status);
      button.disabled = true;
      try {
        await api(`/accounts/${id}/platform-status`, { method: "PUT", body: { status } });
        toast(status === 1 ? "账号已上线" : "账号已下线");
        await loadAutoReply();
      } catch (error) { toast(errorMessage(error), "error"); button.disabled = false; }
    });
  };
  renderRows();
  document.querySelector("#runtime-search").oninput = renderRows;
  document.querySelector("#runtime-filter").onchange = renderRows;
  document.querySelector("#refresh-runtime").onclick = loadAutoReply;
  document.querySelector("#start-all").onclick = () => confirmAction("开始所有自动回复", `将启动所有平台在线且未运行的账号。`, async () => {
    const result = await api("/runtimes/actions/start-all", { method: "POST" });
    toast(`启动 ${result.succeeded.length} 个，跳过 ${result.skipped.length} 个`);
    await loadAutoReply();
  }, "开始");
  document.querySelector("#stop-all").onclick = () => confirmAction("停止所有自动回复", `确定停止 ${counts.connected + counts.pending + counts.error} 个运行中的账号？`, async () => {
    const result = await api("/runtimes/actions/stop-all", { method: "POST" });
    toast(`停止 ${result.succeeded.length} 个`);
    await loadAutoReply();
  }, "停止所有");
}

async function loadAccounts() {
  const data = await api("/accounts?page_size=100");
  const items = data.items;
  const page = document.querySelector("#page");
  page.innerHTML = `
    <div class="page-heading"><h2>客服账号</h2><p class="muted">凭据只保存在服务端，页面不会回显密码或 Cookie。</p></div>
    <div id="job-banner" class="job-banner"></div>
    <div class="toolbar"><input id="account-search" class="input search" placeholder="搜索账号或店铺"><div class="toolbar-spacer"></div><button class="btn icon-btn" id="refresh-accounts" title="刷新">↻</button><button class="btn btn-primary" id="add-account">添加账号</button></div>
    <div class="table-wrap"><table class="table"><thead><tr><th>账号</th><th>平台</th><th>凭据</th><th>状态</th><th>操作</th></tr></thead><tbody id="accounts-body"></tbody></table></div>`;
  const renderRows = () => {
    const search = document.querySelector("#account-search").value.toLowerCase();
    const filtered = items.filter((item) => `${item.username} ${item.shop_name} ${item.user_id}`.toLowerCase().includes(search));
    document.querySelector("#accounts-body").innerHTML = filtered.length ? filtered.map((item) => `
      <tr data-id="${item.id}">
        <td>${entityCell(item)}</td><td>${escapeHtml(item.channel_name)}</td>
        <td><span class="badge ${item.has_cookies ? "badge-green" : "badge-red"}">${item.has_cookies ? "Cookie 已保存" : "需要验证"}</span></td>
        <td>${platformBadge(item.status)}</td>
        <td><div class="actions"><button class="btn btn-sm" data-account="verify">验证</button><button class="btn btn-sm" data-account="edit">编辑</button><button class="btn btn-sm btn-danger" data-account="delete">删除</button></div></td>
      </tr>`).join("") : '<tr><td colspan="5" class="empty">暂无账号</td></tr>';
    document.querySelectorAll("[data-account]").forEach((button) => button.onclick = () => accountAction(button.dataset.account, items.find((item) => item.id === Number(button.closest("tr").dataset.id))));
  };
  renderRows();
  document.querySelector("#account-search").oninput = renderRows;
  document.querySelector("#refresh-accounts").onclick = loadAccounts;
  document.querySelector("#add-account").onclick = openAddAccount;
}

function openAddAccount() {
  showModal({
    title: "添加拼多多账号",
    submitText: "开始登录",
    body: `<div class="form-grid"><div class="field span-2"><label>用户名</label><input class="input" name="username" required maxlength="100" autocomplete="off"></div><div class="field span-2"><label>密码</label><input class="input" type="password" name="password" required maxlength="512" autocomplete="new-password"></div><label class="checkbox span-2"><input type="checkbox" name="headless"> 使用无头浏览器登录</label></div>`,
    onSubmit: async (values) => {
      const job = await api("/account-login-jobs", { method: "POST", body: { username: values.get("username"), password: values.get("password"), headless: values.get("headless") === "on" } });
      trackJob(job.id, "账号登录", loadAccounts);
      toast("登录任务已创建，请留意任务进度");
      return true;
    },
  });
}

function accountAction(action, item) {
  if (action === "edit") {
    showModal({
      title: "编辑账号",
      body: `<div class="form-grid"><div class="field span-2"><label>用户名</label><input class="input" name="username" value="${escapeHtml(item.username)}" required maxlength="100"></div><div class="field span-2"><label>新密码</label><input class="input" name="password" type="password" maxlength="512" placeholder="留空表示不修改"><small>页面不会显示原密码。</small></div></div>`,
      onSubmit: async (values) => {
        await api(`/accounts/${item.id}`, { method: "PATCH", body: { username: values.get("username"), password: values.get("password") } });
        toast("账号信息已更新");
        await loadAccounts();
      },
    });
  } else if (action === "verify") {
    confirmAction("验证账号", `将使用服务器上保存的浏览器会话验证“${item.username}”。`, async () => {
      const job = await api(`/accounts/${item.id}/verify`, { method: "POST" });
      trackJob(job.id, "账号验证", loadAccounts);
      toast("验证任务已创建");
    }, "开始验证");
  } else if (action === "delete") {
    confirmAction("删除账号", `确定删除“${item.username}”？正在运行的账号会拒绝删除。`, async () => {
      await api(`/accounts/${item.id}`, { method: "DELETE" });
      toast("账号已删除");
      await loadAccounts();
    }, "删除");
  }
}

async function loadKeywords() {
  const data = await api("/keywords?page_size=100");
  const page = document.querySelector("#page");
  page.innerHTML = `
    <div class="page-heading"><h2>转人工关键词</h2><p class="muted">消息命中关键词时优先转接人工客服。</p></div>
    <div class="toolbar"><input id="keyword-new" class="input search" maxlength="100" placeholder="输入新关键词"><button class="btn btn-primary" id="keyword-add">添加</button><div class="toolbar-spacer"></div><button class="btn" id="keyword-import">批量导入</button></div>
    <div class="table-wrap"><table class="table"><thead><tr><th>ID</th><th>关键词</th><th>操作</th></tr></thead><tbody>${data.items.length ? data.items.map((item) => `<tr data-id="${item.id}"><td>${item.id}</td><td><strong>${escapeHtml(item.keyword)}</strong></td><td><div class="actions"><button class="btn btn-sm" data-keyword="edit">编辑</button><button class="btn btn-sm btn-danger" data-keyword="delete">删除</button></div></td></tr>`).join("") : '<tr><td colspan="3" class="empty">暂无关键词</td></tr>'}</tbody></table></div>`;
  document.querySelector("#keyword-add").onclick = async () => {
    const input = document.querySelector("#keyword-new");
    if (!input.value.trim()) return;
    try { await api("/keywords", { method: "POST", body: { keyword: input.value } }); toast("关键词已添加"); await loadKeywords(); } catch (error) { toast(errorMessage(error), "error"); }
  };
  document.querySelector("#keyword-import").onclick = () => showModal({
    title: "批量导入关键词", submitText: "导入",
    body: '<div class="field"><label>每行一个关键词</label><textarea class="textarea" name="content" required></textarea></div>',
    onSubmit: async (values) => { const result = await api("/keywords/import", { method: "POST", body: { content: values.get("content") } }); toast(`新增 ${result.added}，跳过 ${result.skipped}，失败 ${result.failed}`); await loadKeywords(); },
  });
  document.querySelectorAll("[data-keyword]").forEach((button) => button.onclick = () => {
    const item = data.items.find((entry) => entry.id === Number(button.closest("tr").dataset.id));
    if (button.dataset.keyword === "edit") showModal({ title: "编辑关键词", body: `<div class="field"><label>关键词</label><input class="input" name="keyword" value="${escapeHtml(item.keyword)}" required maxlength="100"></div>`, onSubmit: async (values) => { await api(`/keywords/${item.id}`, { method: "PATCH", body: { keyword: values.get("keyword") } }); toast("关键词已更新"); await loadKeywords(); } });
    else confirmAction("删除关键词", `确定删除“${item.keyword}”？`, async () => { await api(`/keywords/${item.id}`, { method: "DELETE" }); toast("关键词已删除"); await loadKeywords(); }, "删除");
  });
}

async function loadKnowledge() {
  const shopsData = await api("/shops");
  state.shops = shopsData.items;
  if (!state.selectedShop || !state.shops.some((shop) => shop.id === state.selectedShop)) state.selectedShop = state.shops[0]?.id || null;
  const page = document.querySelector("#page");
  page.innerHTML = `
    <div class="page-heading"><h2>店铺知识库</h2><p class="muted">商品事实和客服政策按店铺隔离。</p></div>
    <div id="job-banner" class="job-banner"></div>
    <div class="toolbar"><select id="knowledge-shop" class="select" style="width:260px">${state.shops.map((shop) => `<option value="${shop.id}" ${shop.id === state.selectedShop ? "selected" : ""}>${escapeHtml(shop.shop_name)} · ${escapeHtml(shop.shop_id)}</option>`).join("")}</select></div>
    ${state.selectedShop ? '<div class="tabs"><button class="tab" data-tab="products">商品知识</button><button class="tab" data-tab="customer">客服知识</button></div><div id="knowledge-content"></div>' : '<div class="empty">请先添加店铺账号</div>'}`;
  document.querySelector("#knowledge-shop")?.addEventListener("change", (event) => { state.selectedShop = Number(event.target.value); loadKnowledge(); });
  document.querySelectorAll("[data-tab]").forEach((button) => { button.classList.toggle("active", button.dataset.tab === state.knowledgeTab); button.onclick = () => { state.knowledgeTab = button.dataset.tab; loadKnowledgeSection(); }; });
  if (state.selectedShop) await loadKnowledgeSection();
}

async function loadKnowledgeSection() {
  document.querySelectorAll("[data-tab]").forEach((button) => button.classList.toggle("active", button.dataset.tab === state.knowledgeTab));
  if (state.knowledgeTab === "products") await loadProducts();
  else await loadCustomerKnowledge();
}

async function loadProducts() {
  const data = await api(`/shops/${state.selectedShop}/product-knowledge?page_size=100`);
  const content = document.querySelector("#knowledge-content");
  content.innerHTML = `
    <div class="toolbar"><input id="product-search" class="input search" placeholder="搜索商品名称或 ID"><div class="toolbar-spacer"></div><button class="btn" id="sync-incremental">增量同步</button><button class="btn btn-primary" id="sync-full">全量同步</button><button class="btn btn-danger" id="clear-products">清空全部</button></div>
    <div class="table-wrap"><table class="table"><thead><tr><th>商品</th><th>价格</th><th>销量</th><th>提取状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody id="products-body"></tbody></table></div>`;
  const renderRows = () => {
    const search = document.querySelector("#product-search").value.toLowerCase();
    const items = data.items.filter((item) => `${item.goods_name} ${item.goods_id}`.toLowerCase().includes(search));
    document.querySelector("#products-body").innerHTML = items.length ? items.map((item) => `<tr data-id="${item.id}"><td><strong>${escapeHtml(item.goods_name)}</strong><div class="muted mono">${escapeHtml(item.goods_id)}</div></td><td>${escapeHtml(item.price || "-")}</td><td>${item.sold_quantity ?? "-"}</td><td>${item.extracted_content ? '<span class="badge badge-green">已提取</span>' : '<span class="badge badge-amber">待提取</span>'}</td><td>${escapeHtml(formatDate(item.updated_at))}</td><td><div class="actions"><button class="btn btn-sm" data-product="edit">编辑</button><button class="btn btn-sm btn-danger" data-product="delete">删除</button></div></td></tr>`).join("") : '<tr><td colspan="6" class="empty">暂无商品知识</td></tr>';
    document.querySelectorAll("[data-product]").forEach((button) => button.onclick = () => {
      const item = data.items.find((entry) => entry.id === Number(button.closest("tr").dataset.id));
      if (button.dataset.product === "edit") showModal({ title: item.goods_name, wide: true, body: `<div class="field"><label>规格信息</label><textarea class="textarea" name="specifications">${escapeHtml(item.specifications || "")}</textarea></div><div class="field" style="margin-top:14px"><label>提取内容</label><textarea class="textarea" style="min-height:220px" name="extracted_content">${escapeHtml(item.extracted_content || "")}</textarea></div>`, onSubmit: async (values) => { await api(`/product-knowledge/${item.id}`, { method: "PATCH", body: { specifications: values.get("specifications"), extracted_content: values.get("extracted_content") } }); toast("商品知识已更新"); await loadProducts(); } });
      else confirmAction("删除商品知识", `确定删除“${item.goods_name}”的本地知识？`, async () => { await api(`/product-knowledge/${item.id}`, { method: "DELETE" }); toast("商品知识已删除"); await loadProducts(); }, "删除");
    });
  };
  renderRows();
  document.querySelector("#product-search").oninput = renderRows;
  document.querySelector("#sync-incremental").onclick = () => startProductSync("incremental");
  document.querySelector("#sync-full").onclick = () => confirmAction("全量同步商品知识", "全量同步会重新提取所有商品，并覆盖已提取的知识。", () => startProductSync("full"), "开始全量同步");
  document.querySelector("#clear-products").onclick = () => confirmAction("清空商品知识", `确定删除当前店铺的 ${data.total} 条商品知识？此操作不可撤销。`, async () => { const result = await api(`/shops/${state.selectedShop}/product-knowledge`, { method: "DELETE" }); toast(`已删除 ${result.deleted} 条商品知识`); await loadProducts(); }, "清空全部");
}

async function startProductSync(mode) {
  const job = await api(`/shops/${state.selectedShop}/product-sync-jobs`, { method: "POST", body: { mode } });
  trackJob(job.id, mode === "full" ? "全量商品同步" : "增量商品同步", loadProducts);
  toast("商品同步任务已启动");
}

async function loadCustomerKnowledge() {
  const data = await api(`/shops/${state.selectedShop}/customer-knowledge?page_size=100`);
  const content = document.querySelector("#knowledge-content");
  content.innerHTML = `
    <div class="toolbar"><input id="customer-search" class="input search" placeholder="搜索标题或内容"><div class="toolbar-spacer"></div><button class="btn" id="customer-import">导入文件</button><button class="btn btn-primary" id="customer-add">添加客服知识</button></div>
    <div class="table-wrap"><table class="table"><thead><tr><th>标题</th><th>标签</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody id="customer-body"></tbody></table></div>`;
  const renderRows = () => {
    const search = document.querySelector("#customer-search").value.toLowerCase();
    const items = data.items.filter((item) => `${item.title} ${item.content}`.toLowerCase().includes(search));
    document.querySelector("#customer-body").innerHTML = items.length ? items.map((item) => `<tr data-id="${item.id}"><td><strong>${escapeHtml(item.title)}</strong><div class="muted" style="max-width:520px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(item.content)}</div></td><td>${escapeHtml(item.tags || "-")}</td><td>${item.enabled ? '<span class="badge badge-green">启用</span>' : '<span class="badge badge-gray">停用</span>'}</td><td>${escapeHtml(formatDate(item.updated_at))}</td><td><div class="actions"><button class="btn btn-sm" data-customer="edit">编辑</button><button class="btn btn-sm btn-danger" data-customer="delete">删除</button></div></td></tr>`).join("") : '<tr><td colspan="5" class="empty">暂无客服知识</td></tr>';
    document.querySelectorAll("[data-customer]").forEach((button) => button.onclick = () => {
      const item = data.items.find((entry) => entry.id === Number(button.closest("tr").dataset.id));
      if (button.dataset.customer === "edit") openCustomerKnowledge(item);
      else confirmAction("删除客服知识", `确定删除“${item.title}”？`, async () => { await api(`/customer-knowledge/${item.id}`, { method: "DELETE" }); toast("客服知识已删除"); await loadCustomerKnowledge(); }, "删除");
    });
  };
  renderRows();
  document.querySelector("#customer-search").oninput = renderRows;
  document.querySelector("#customer-add").onclick = () => openCustomerKnowledge(null);
  document.querySelector("#customer-import").onclick = openCustomerImport;
}

function openCustomerKnowledge(item) {
  showModal({
    title: item ? "编辑客服知识" : "添加客服知识", wide: true,
    body: `<div class="form-grid"><div class="field span-2"><label>标题</label><input class="input" name="title" value="${escapeHtml(item?.title || "")}" required maxlength="255"></div><div class="field span-2"><label>内容</label><textarea class="textarea" style="min-height:220px" name="content" required>${escapeHtml(item?.content || "")}</textarea></div><div class="field"><label>标签</label><input class="input" name="tags" value="${escapeHtml(item?.tags || "")}" maxlength="255" placeholder="逗号分隔"></div><label class="checkbox"><input type="checkbox" name="enabled" ${item?.enabled !== false ? "checked" : ""}> 启用</label></div>`,
    onSubmit: async (values) => {
      const body = { title: values.get("title"), content: values.get("content"), tags: values.get("tags") || null, enabled: values.get("enabled") === "on" };
      if (item) await api(`/customer-knowledge/${item.id}`, { method: "PATCH", body });
      else await api(`/shops/${state.selectedShop}/customer-knowledge`, { method: "POST", body });
      toast(item ? "客服知识已更新" : "客服知识已添加");
      await loadCustomerKnowledge();
    },
  });
}

function openCustomerImport() {
  showModal({
    title: "导入客服知识", submitText: "导入",
    body: '<div class="field"><label>CSV 或 XLSX 文件</label><input class="input" type="file" name="file" accept=".csv,.xlsx" required><small>列名支持 title/content/tags 或 标题/内容/标签，最大 5 MB、5000 行。</small></div>',
    onSubmit: async (values) => {
      const body = new FormData(); body.append("file", values.get("file"));
      const result = await api(`/shops/${state.selectedShop}/customer-knowledge/import`, { method: "POST", body });
      toast(`新增 ${result.added}，跳过 ${result.skipped}`);
      await loadCustomerKnowledge();
    },
  });
}

async function loadLogs() {
  const data = await api("/logs?page_size=300");
  const page = document.querySelector("#page");
  page.innerHTML = `
    <div class="toolbar"><select id="log-level" class="select" style="width:150px"><option value="">全部级别</option><option>DEBUG</option><option>INFO</option><option>WARNING</option><option>ERROR</option></select><input id="log-search" class="input search" placeholder="搜索模块或消息"><button class="btn" id="log-filter">筛选</button><div class="toolbar-spacer"></div><select id="log-format" class="select" style="width:100px"><option>json</option><option>csv</option><option>txt</option></select><button class="btn" id="log-export">导出</button><button class="btn btn-danger" id="log-clear">清空视图</button></div>
    <div class="log-view">${data.items.length ? data.items.map((item) => `<div class="log-row"><span>${escapeHtml(formatDate(item.timestamp))}</span><strong class="${escapeHtml(item.level)}">${escapeHtml(item.level)}</strong><span class="log-module">${escapeHtml(item.module)}</span><span>${escapeHtml(item.message)}</span></div>`).join("") : '<div class="empty">当前没有日志</div>'}</div>`;
  document.querySelector("#log-filter").onclick = async () => {
    const level = encodeURIComponent(document.querySelector("#log-level").value);
    const search = encodeURIComponent(document.querySelector("#log-search").value);
    const filtered = await api(`/logs?page_size=300&level=${level}&search=${search}`);
    document.querySelector(".log-view").innerHTML = filtered.items.map((item) => `<div class="log-row"><span>${escapeHtml(formatDate(item.timestamp))}</span><strong class="${escapeHtml(item.level)}">${escapeHtml(item.level)}</strong><span class="log-module">${escapeHtml(item.module)}</span><span>${escapeHtml(item.message)}</span></div>`).join("") || '<div class="empty">没有匹配日志</div>';
  };
  document.querySelector("#log-export").onclick = () => {
    const format = document.querySelector("#log-format").value;
    const level = encodeURIComponent(document.querySelector("#log-level").value);
    const search = encodeURIComponent(document.querySelector("#log-search").value);
    location.href = `/api/v1/logs/export?format=${format}&level=${level}&search=${search}`;
  };
  document.querySelector("#log-clear").onclick = () => confirmAction("清空日志视图", "只清空 Web 内存缓冲区，不会删除服务端日志文件。", async () => { await api("/logs/buffer", { method: "DELETE" }); await loadLogs(); }, "清空");
}

async function loadSettings() {
  const data = await api("/settings");
  const llm = data.llm;
  const page = document.querySelector("#page");
  page.innerHTML = `
    <form id="settings-form">
      <div class="settings-section"><h3>LLM 连接</h3><div class="form-grid">
        <div class="field"><label>供应商</label><select class="select" name="provider">${data.providers.map((item) => `<option value="${escapeHtml(item.value)}" ${item.value === llm.provider ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}</select></div>
        <div class="field"><label>模型名称</label><input class="input" name="model_name" value="${escapeHtml(llm.model_name || "")}" required></div>
        <div class="field span-2"><label>Base URL</label><input class="input" name="api_base" value="${escapeHtml(llm.api_base || "")}" placeholder="留空使用供应商默认端点"></div>
        <div class="field span-2"><label>API Key</label><input class="input" type="password" name="api_key" autocomplete="new-password" placeholder="${llm.api_key_configured ? "已配置，留空表示保留" : "请输入 API Key"}"><small>${llm.api_key_configured ? "当前密钥已安全保存，页面不会读取原值。" : "尚未配置 API Key。"}</small></div>
        <div class="field"><label>端点信任</label><select class="select" name="endpoint_trust_mode"><option value="default" ${llm.endpoint_trust_mode === "default" ? "selected" : ""}>默认端点</option><option value="explicit" ${llm.endpoint_trust_mode === "explicit" ? "selected" : ""}>自定义 HTTPS</option><option value="local" ${llm.endpoint_trust_mode === "local" ? "selected" : ""}>本地或私有端点</option></select></div>
        <div class="field"><label>工具调用</label><select class="select" name="tool_policy"><option value="enabled" ${llm.tool_policy === "enabled" ? "selected" : ""}>启用</option><option value="disabled" ${llm.tool_policy === "disabled" ? "selected" : ""}>禁用</option></select></div>
        <label class="checkbox span-2"><input type="checkbox" name="confirm_risk"> 我确认当前模型能力及自定义端点的工具调用风险</label>
      </div></div>
      <div class="settings-section"><h3>营业时间</h3><div class="form-grid"><div class="field"><label>开始时间</label><input class="input" type="time" name="start" value="${escapeHtml(data.business_hours.start)}" required></div><div class="field"><label>结束时间</label><input class="input" type="time" name="end" value="${escapeHtml(data.business_hours.end)}" required></div></div></div>
      <div class="settings-section"><h3>Agent 指令</h3><div class="field"><label>每行一条指令</label><textarea class="textarea" style="min-height:260px" name="instructions">${escapeHtml((data.prompt.instructions || []).join("\n"))}</textarea></div></div>
      <div class="toolbar"><button class="btn btn-primary" type="submit">保存设置</button><span class="muted">运行中的账号需停止并重新启动后使用新配置。</span></div>
    </form>`;
  const form = document.querySelector("#settings-form");
  form.onsubmit = async (event) => {
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]'); submit.disabled = true;
    const values = new FormData(form);
    try {
      await api("/settings", { method: "PUT", body: {
        version: data.version,
        llm: { provider: values.get("provider"), model_name: values.get("model_name"), api_key: values.get("api_key"), api_base: values.get("api_base"), endpoint_trust_mode: values.get("endpoint_trust_mode"), tool_policy: values.get("tool_policy"), confirm_risk: values.get("confirm_risk") === "on" },
        business_hours: { start: values.get("start"), end: values.get("end") },
        prompt: { instructions: String(values.get("instructions") || "").split("\n") },
      }});
      toast("设置已保存");
      await loadSettings();
    } catch (error) { toast(errorMessage(error), "error"); } finally { submit.disabled = false; }
  };
}

function updateJobBanner(job, label) {
  const banner = document.querySelector("#job-banner");
  if (!banner) return;
  const progress = job.progress || {};
  const total = Number(progress.total || 0);
  const current = Number(progress.current || 0);
  const percent = total > 0 ? Math.min(100, Math.round(current / total * 100)) : (job.status === "succeeded" ? 100 : 5);
  banner.classList.add("visible");
  banner.innerHTML = `<div><strong>${escapeHtml(label)}</strong><div>${escapeHtml(progress.message || progress.current_goods_name || job.status)} ${total ? `(${current}/${total})` : ""}</div></div><div class="progress"><span style="width:${percent}%"></span></div>`;
}

function trackJob(jobId, label, onDone) {
  state.jobs.set(jobId, { label, onDone });
  const poll = async () => {
    if (!state.jobs.has(jobId)) return;
    try {
      const job = await api(`/jobs/${jobId}`);
      updateJobBanner(job, label);
      if (["succeeded", "failed", "cancelled"].includes(job.status)) {
        state.jobs.delete(jobId);
        toast(job.status === "succeeded" ? `${label}已完成` : `${label}${job.status === "cancelled" ? "已取消" : "失败"}`, job.status === "failed" ? "error" : "info");
        if (onDone) await onDone();
        return;
      }
      setTimeout(poll, 1200);
    } catch (error) { state.jobs.delete(jobId); toast(errorMessage(error), "error"); }
  };
  poll();
}

function connectEvents() {
  if (!state.session || state.socket?.readyState === WebSocket.OPEN || state.socket?.readyState === WebSocket.CONNECTING) return;
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/api/v1/events`);
  state.socket = socket;
  socket.onopen = () => {
    state.socketRetry = 1000;
    document.querySelector("#connection-dot")?.classList.add("online");
    const text = document.querySelector("#connection-text"); if (text) text.textContent = "实时事件已连接";
  };
  socket.onmessage = (event) => {
    let payload; try { payload = JSON.parse(event.data); } catch (_) { return; }
    if (payload.type === "runtime.status_changed" && state.currentPath === "/auto-reply") scheduleRefresh(loadAutoReply);
    if (payload.type === "log.appended" && state.currentPath === "/logs") scheduleRefresh(loadLogs, 800);
    if (payload.type?.startsWith("job.")) {
      const tracked = state.jobs.get(payload.data.id);
      if (tracked) updateJobBanner(payload.data, tracked.label);
    }
  };
  socket.onclose = () => {
    document.querySelector("#connection-dot")?.classList.remove("online");
    const text = document.querySelector("#connection-text"); if (text) text.textContent = "事件流已断开，正在重连";
    state.socket = null;
    if (state.session) {
      setTimeout(connectEvents, state.socketRetry);
      state.socketRetry = Math.min(15000, state.socketRetry * 2);
    }
  };
}

function disconnectEvents() {
  if (state.socket) { state.socket.onclose = null; state.socket.close(); state.socket = null; }
}

function scheduleRefresh(callback, delay = 250) {
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(() => {
    if (document.querySelector("#page")) callback().catch(() => {});
  }, delay);
}

window.addEventListener("popstate", () => {
  state.currentPath = location.pathname;
  if (state.session) { shell(); renderPage(); } else renderLogin();
});

async function bootstrap() {
  try {
    const result = await api("/auth/me");
    state.session = { username: result.username };
    state.csrf = result.csrf_token;
    if (!routes[location.pathname]) history.replaceState({}, "", "/auto-reply");
    state.currentPath = location.pathname;
    shell();
    await renderPage();
  } catch (_) {
    history.replaceState({}, "", "/login");
    renderLogin();
  }
}

bootstrap();
