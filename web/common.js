export const $ = (s) => document.querySelector(s);
export const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const paths = {
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  chat: "M21 11a8 8 0 0 1-8 8H7l-5 3 1-7a8 8 0 0 1 10-12 8 8 0 0 1 8 8Z",
  file: "M14 2H5v20h14V7z M14 2v6h5 M8 12h8 M8 16h6",
  task: "M9 4h12 M9 12h12 M9 20h12 M2 4l2 2 3-4 M2 12l2 2 3-4 M2 20l2 2 3-4",
  users:
    "M16 21v-3a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v3 M9 10a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M18 8a3 3 0 0 1 0 6 M20 21v-3",
  shield: "M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6z M8 12l3 3 5-6",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9 M10 21h4",
  arrow: "M5 12h14 M13 6l6 6-6 6",
  logout: "M9 3H3v18h6 M12 12h9 M17 8l4 4-4 4",
};
export const icon = (name) =>
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="' +
  (paths[name] || paths.grid) +
  '"/></svg>';
export const date = (v) =>
  v ? new Date(v).toLocaleString("zh-CN", { hour12: false }) : "—";
const labels = {
  queued: "排队中",
  running: "处理中",
  pending: "待处理",
  ready: "可检索",
  succeeded: "已完成",
  failed: "处理失败",
  awaiting_approval: "待审核",
  publishing: "发布中",
  notification_queued: "待发布",
  notification_running: "发布中",
  notification_failed: "发布失败",
  completed: "已发布",
  rejected: "已驳回",
  superseded: "已替代",
};
export const badge = (v) =>
  '<span class="badge ' +
  (["ready", "succeeded", "completed"].includes(v)
    ? "good"
    : ["failed", "notification_failed", "rejected"].includes(v)
      ? "bad"
      : "warn") +
  '">' +
  esc(labels[v] || v) +
  "</span>";
export const empty = (text) =>
  '<div class="empty">' + icon("file") + esc(text) + "</div>";
export const stat = (label, value, note) =>
  '<div class="stat"><div class="stat-label">' +
  esc(label) +
  icon("grid") +
  '</div><div class="stat-number">' +
  esc(value) +
  '</div><div class="stat-note">' +
  esc(note) +
  "</div></div>";
export const table = (headers, rows) =>
  rows.length
    ? '<div class="tablewrap"><table><thead><tr>' +
      headers.map((h) => "<th>" + esc(h) + "</th>").join("") +
      "</tr></thead><tbody>" +
      rows
        .map(
          (row) =>
            "<tr>" +
            row.map((cell) => "<td>" + cell + "</td>").join("") +
            "</tr>",
        )
        .join("") +
      "</tbody></table></div>"
    : empty("暂无记录，数据接入后将在此显示");
let token = "",
  user = null,
  base = "",
  config,
  localTestLogin = null,
  publicTrialLogin = null;
export const currentUser = () => user;
export async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (token) headers.Authorization = "Bearer " + token;
  if (options.body && !(options.body instanceof FormData))
    headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(base + "/api" + path, {
      ...options,
      headers,
      signal: AbortSignal.timeout(path === "/ask" ? 100000 : 30000),
    });
  } catch (error) {
    throw new Error(
      error.name === "TimeoutError"
        ? "请求超时；任务可能已受理，请刷新任务列表确认。"
        : "服务暂时无法连接，请稍后重试。",
    );
  }
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error("服务响应异常，请稍后重试。");
  }
  if (!response.ok) {
    if (response.status === 401 && token) {
      token = "";
      user = null;
      setTimeout(login, 0);
    }
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : response.status === 422
          ? "提交内容不符合要求，请检查必填字段和日期范围。"
          : "请求处理失败";
    throw new Error(
      detail + (body.request_id ? "（请求 " + body.request_id + "）" : ""),
    );
  }
  return body;
}
export const post = (path, data) =>
  api(path, { method: "POST", body: JSON.stringify(data) });
export function error(message) {
  const target = $("#feedback");
  if (target) {
    target.innerHTML =
      '<div class="notice error" role="alert">' + esc(message) + "</div>";
    target.scrollIntoView({ block: "nearest" });
  }
}
export function bindForm(selector, handler) {
  const form = $(selector);
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("[type=submit]");
    if (button) button.disabled = true;
    try {
      await handler(new FormData(form));
    } catch (e) {
      error(e.message);
    } finally {
      if (button) button.disabled = false;
    }
  });
}
export function on(selector, handler) {
  document.querySelectorAll(selector).forEach((el) =>
    el.addEventListener("click", async () => {
      el.disabled = true;
      try {
        await handler(el);
      } catch (e) {
        error(e.message);
      } finally {
        el.disabled = false;
      }
    }),
  );
}
function login() {
  document.title = config.title;
  $("#app").innerHTML =
    '<div class="login-layout"><aside class="login-intro"><div class="brand"><span class="project-seal">' +
    esc(config.seal) +
    "</span>" +
    esc(config.brand) +
    '</div><div class="login-story"><div class="eyebrow">' +
    esc(config.loginLabel) +
    "</div><h1>" +
    config.tagline +
    "</h1><p>" +
    esc(config.description) +
    '</p><ol class="login-steps">' +
    config.steps.map((step) => "<li>" + esc(step) + "</li>").join("") +
    '</ol></div><p class="login-footnote">' +
    esc(config.loginFootnote) +
    '</p></aside><section class="login-form"><div class="login-box"><div class="brand"><span class="project-seal">' +
    esc(config.seal) +
    "</span>" +
    esc(config.brand) +
    "</div><h1>" +
    esc(config.loginHeading) +
    '</h1><p class="muted">使用企业账号或访客账号继续。</p><div id="feedback"></div><form id="login"><label for="email">登录邮箱</label><input id="email" name="email" type="email" autocomplete="username" required placeholder="请输入账号邮箱"><label for="password">登录密码</label><input id="password" name="password" type="password" autocomplete="current-password" required placeholder="输入账号密码"><button class="btn primary" type="submit">进入平台 ' +
    icon("arrow") +
    "</button></form></div></section></div>";
  if (localTestLogin) {
    $("#email").placeholder = "本地测试邮箱：" + localTestLogin.email;
    $("#password").placeholder = "本地测试密码：" + localTestLogin.password;
    const hint = document.createElement("p");
    hint.className = "muted";
    hint.textContent =
      "输入框中的提示为本机专用测试凭据，填写后即可登录；请勿公开分享。";
    $("#login").append(hint);
  } else if (publicTrialLogin) {
    $("#email").value = publicTrialLogin.email;
    $("#password").value = publicTrialLogin.password;
    const hint = document.createElement("p");
    hint.className = "muted";
    hint.textContent = "访客账号已填写，可直接登录。试用数据与管理权限分开。";
    $("#login").append(hint);
  }
  bindForm("#login", async (data) => {
    if (!base) throw new Error("试用服务暂未开放，请稍后访问。");
    const result = await post("/auth/login", {
      email: data.get("email"),
      password: data.get("password"),
    });
    token = result.access_token;
    user = result.user;
    await navigate();
  });
}
export async function start(options) {
  config = options;
  try {
    const response = await fetch("./config.json", { cache: "no-store" });
    const runtime = await response.json();
    if (typeof runtime.api_base_url === "string" && runtime.api_base_url) {
      const endpoint = new URL(runtime.api_base_url);
      if (endpoint.protocol !== "https:" || endpoint.username || endpoint.password || endpoint.search || endpoint.hash)
        throw new Error("Invalid deployment endpoint");
      base = endpoint.href.replace(/\/$/, "");
    }
    if (typeof runtime.public_trial_login?.email === "string" &&
        typeof runtime.public_trial_login?.password === "string")
      publicTrialLogin = runtime.public_trial_login;
    if (
      ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname) &&
      typeof runtime.local_test_login?.email === "string" &&
      typeof runtime.local_test_login?.password === "string"
    )
      localTestLogin = runtime.local_test_login;
  } catch {}
  if (!location.hostname.endsWith("github.io") && !base) base = location.origin;
  window.addEventListener("hashchange", () => {
    if (user) navigate();
  });
  login();
}
export async function navigate() {
  if (!user) return login();
  const page = location.hash.slice(1) || config.defaultPage;
  const nav = config.navigation.filter(
    (n) => !n.admin || user.role === "admin",
  );
  const selected = nav.find((n) => n.id === page) || nav[0];
  $("#app").innerHTML =
    '<div class="shell"><aside class="sidebar"><div class="brand"><span class="project-seal">' +
    esc(config.seal) +
    "</span><div>" +
    esc(config.brand) +
    "<small>" +
    esc(config.workspaceLabel) +
    '</small></div></div><div class="navlabel">工作区</div><nav>' +
    nav
      .map(
        (n) =>
          '<a class="navlink ' +
          (n.id === selected.id ? "active" : "") +
          '" href="#' +
          n.id +
          '">' +
          icon(n.icon) +
          esc(n.label) +
          "</a>",
      )
      .join("") +
    '</nav><div class="sidebar-footer">' +
    icon("shield") +
    " 已连接企业服务<br>" +
    esc(user.tenant) +
    '<br>数据访问受账号权限约束</div></aside><main class="main"><header class="topbar"><span class="breadcrumb">企业工作空间 /<strong>' +
    esc(selected.label) +
    '</strong></span><div class="account"><span class="badge">' +
    esc(
      {
        admin: "管理员",
        member: "成员",
        analyst: "分析师",
        reviewer: "审核人",
      }[user.role],
    ) +
    '</span><span class="avatar">' +
    esc(user.name.slice(0, 1)) +
    "</span>" +
    esc(user.name) +
    '<button class="btn small" id="logout" aria-label="退出登录">' +
    icon("logout") +
    '</button></div></header><section class="content"><div id="feedback"></div><div id="view"><div class="loading"><div class="spinner"></div>正在读取工作空间</div></div><div class="footer"><span>' +
    esc(config.brand) +
    "</span><span>" +
    esc(config.footer) +
    "</span></div></section></main></div>";
  on("#logout", async () => {
    try {
      await post("/auth/logout", {});
    } finally {
      token = "";
      user = null;
      login();
    }
  });
  try {
    if (selected.id === "users") await users();
    else if (selected.id === "audit") await audit();
    else await config.render(selected.id);
  } catch (e) {
    $("#view").innerHTML = empty("暂时无法加载，请检查连接后刷新");
    error(e.message);
  }
}
export const head = (eyebrow, title, description, action = "") =>
  '<div class="pagehead"><div><div class="eyebrow">' +
  esc(config.workspaceLabel) +
  "</div><h1>" +
  esc(title) +
  '</h1><p class="muted">' +
  esc(description) +
  "</p></div>" +
  action +
  "</div>";
async function audit() {
  const events = await api("/audit");
  $("#view").innerHTML =
    head(
      "GOVERNANCE",
      "审计日志",
      "最近 100 条服务端操作记录，仅管理员可见。",
    ) +
    '<div class="card">' +
    table(
      ["操作", "资源 ID", "操作人 ID", "时间"],
      events.map((e) => [
        esc(e.action),
        esc(e.resource_id || "—"),
        esc(e.user_id),
        date(e.at),
      ]),
    ) +
    "</div>";
}
async function users() {
  const rows = await api("/users");
  $("#view").innerHTML =
    head(
      "ACCESS MANAGEMENT",
      "成员与权限",
      "角色决定可执行的操作，部门授权约束知识检索范围。",
    ) +
    '<div class="columns"><div class="card">' +
    table(
      ["成员", "角色", "状态", "操作"],
      rows.map((u) => [
        esc(u.name) + '<p class="muted">' + esc(u.email) + "</p>",
        esc(u.role),
        u.active
          ? '<span class="badge good">正常</span>'
          : '<span class="badge bad">停用</span>',
        u.id === user.id
          ? "当前账号"
          : '<button class="btn small toggle" data-id="' +
            u.id +
            '">' +
            (u.active ? "停用" : "启用") +
            "</button>",
      ]),
    ) +
    '</div><div class="card"><h2>邀请成员</h2><p class="muted">创建账号后，请通过安全渠道交付初始密码。</p><form id="create-user"><label>姓名</label><input name="name" required maxlength="100"><label>企业邮箱</label><input name="email" type="email" required><label>初始密码</label><input name="password" type="password" minlength="14" required autocomplete="new-password"><label>角色</label><select name="role"><option value="member">成员</option><option value="analyst">分析师</option><option value="reviewer">审核人</option><option value="admin">管理员</option></select><label>部门授权（逗号分隔）</label><input name="departments" placeholder="运维, 技术"><div class="actions"><button type="submit" class="btn primary">创建账号</button></div></form></div></div>';
  bindForm("#create-user", async (data) => {
    await post("/users", {
      ...Object.fromEntries(data),
      departments: String(data.get("departments"))
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean),
    });
    await users();
  });
  on(".toggle", async (el) => {
    const target = rows.find((r) => r.id === el.dataset.id);
    if (
      !confirm("确认" + (target.active ? "停用" : "启用") + target.name + "？")
    )
      return;
    await api("/users/" + target.id, {
      method: "PUT",
      body: JSON.stringify({
        active: !target.active,
        role: target.role,
        departments: target.departments,
      }),
    });
    await users();
  });
}
