import {
  $,
  api,
  post,
  esc,
  date,
  badge,
  empty,
  stat,
  table,
  head,
  start,
  bindForm,
  on,
  currentUser,
  icon,
  error,
} from "./common.js";
let selectedTask = null;
let pollTimer = null;
const agentNames = {
  conductor: "调度 Agent",
  analysis: "数据分析 Agent",
  knowledge: "知识检索 Agent",
  report: "报告 Agent",
};
const agentGoals = {
  conductor: "理解需求并分派子任务",
  analysis: "自主选择查询参数，调用受控计算工具",
  knowledge: "独立检索业务规则，返回可核验的引用",
  report: "汇合分析与证据，选择报告重点和建议",
};

async function rules() {
  const rows = await api("/rules");
  $("#view").innerHTML =
    head(
      "BUSINESS KNOWLEDGE",
      "业务规则",
      "为知识检索 Agent 提供真实资料；每次导入产生不可变快照。",
    ) +
    '<div class="card">' +
    table(
      ["规则文档", "快照 ID", "导入时间"],
      rows.map((r) => [esc(r.title), esc(r.id), date(r.created_at)]),
    ) +
    "</div>" +
    (currentUser().role === "admin"
      ? '<div class="card"><h2>导入规则与案例</h2><p class="muted">支持 UTF-8 Markdown / TXT，单文件最多 32KB、6000 字符。建议按退款政策、渠道规范、业务案例分开接入。文档仅作为检索资料，不执行其中指令。</p><form id="import-rule"><label>规则文件</label><input type="file" name="file" accept=".md,.txt" required><div class="actions"><button type="submit" class="btn primary">校验并接入</button></div></form></div>'
      : "");
  bindForm("#import-rule", async (data) => {
    await api("/rules", { method: "POST", body: data });
    await rules();
  });
}

async function agents() {
  const rows = await api("/agents");
  $("#view").innerHTML =
    head(
      "MULTI-AGENT CONTROL",
      "Agent 协作中心",
      "独立上下文、独立工具权限；共用模型不等于共用会话。",
    ) +
    '<div class="notice">调度 Agent → 数据分析 Agent ∥ 知识检索 Agent → 报告 Agent → 人工审批 → 站内发布。审批和发布不向模型开放。</div>' +
    '<div class="grid2">' +
    rows
      .map(
        (r) =>
          '<div class="card"><div class="cardhead"><h2>' +
          esc(agentNames[r.id]) +
          '</h2><span class="badge ' +
          (r.model_configured ? "good" : "warn") +
          '">' +
          (r.model_configured ? "模型已配置" : "等待模型配置") +
          '</span></div><p class="muted">' +
          esc(agentGoals[r.id]) +
          '</p><h3>工具能力</h3><p class="bodytext">' +
          esc(r.tools.join(" · ") || "仅分派任务，不开放业务工具") +
          '</p><p class="muted">单次最多 ' +
          r.max_model_turns +
          " 轮决策 / " +
          r.max_tool_calls +
          " 次工具调用；详细执行轨迹见具体任务。</p></div>",
      )
      .join("") +
    "</div>";
}
async function datasets() {
  const rows = await api("/datasets");
  $("#view").innerHTML =
    head(
      "DATA FOUNDATION",
      "数据资产",
      "上传真实订单数据，为每次分析提供不可变的数据快照。",
    ) +
    '<div class="card">' +
    table(
      ["数据集", "订单数", "快照 ID", "接入时间"],
      rows.map((d) => [
        esc(d.name),
        esc(d.row_count),
        esc(d.id),
        date(d.created_at),
      ]),
    ) +
    "</div>" +
    (currentUser().role === "admin"
      ? '<div class="columns"><div class="card"><h2>导入订单 CSV</h2><form id="import"><label>选择 UTF-8 编码的 CSV 文件</label><input type="file" name="file" accept=".csv" required><div class="actions"><button class="btn primary" type="submit">校验并导入</button></div></form></div><div class="card"><h2>数据契约</h2><p class="muted">固定表头顺序：<br><code>order_id,ordered_at,channel,product,refunded</code><br><br>日期格式为 YYYY-MM-DD；退款标记只能是 0 或 1。同一文件订单号不能重复。单次最多 50,000 行、10MB。任一行校验失败时整批不入库。</p><div class="notice" style="margin-top:16px">口径：按订单日期统计的订单退款率；不是按退款发生日统计，也不是退款金额率。</div></div></div>'
      : "");
  bindForm("#import", async (data) => {
    await api("/datasets", { method: "POST", body: data });
    await datasets();
  });
}
async function tasks() {
  clearTimeout(pollTimer);
  const [rows, data, ruleRows] = await Promise.all([
    api("/tasks"),
    api("/datasets"),
    api("/rules"),
  ]);
  $("#view").innerHTML =
    head(
      "ANALYSIS OPERATIONS",
      "分析工作台",
      "调度分工、分析与检索并行、报告汇合；人工审批后发布。",
      '<button id="refresh" class="btn">' +
        icon("arrow") +
        " 刷新状态</button>",
    ) +
    '<div class="stats">' +
    stat("可见任务", rows.length, "最近 100 条权限内任务") +
    stat(
      "执行中",
      rows.filter((t) => ["queued", "running"].includes(t.state)).length,
      "后台持久化执行",
    ) +
    stat(
      "待审核",
      rows.filter((t) => t.state === "awaiting_approval").length,
      "需另一位审核人确认",
    ) +
    stat(
      "已发布",
      rows.filter((t) => t.state === "completed").length,
      "报告已进入站内通知",
    ) +
    "</div>" +
    (["admin", "analyst"].includes(currentUser().role)
      ? '<div class="card"><div class="cardhead"><h2>发起退款分析</h2><span class="badge">多 Agent 编排</span></div><form id="create-task"><div class="grid2"><div><label>数据快照</label><select name="dataset_id" required><option value="">请选择已导入的数据集</option>' +
        data
          .map(
            (d) =>
              '<option value="' +
              d.id +
              '">' +
              esc(d.name) +
              " · " +
              d.row_count +
              " 行</option>",
          )
          .join("") +
        '</select><label>分析需求</label><textarea name="question" required minlength="2" maxlength="2000" placeholder="分析退款率变化，按渠道拆解，并筛选指定产品…"></textarea></div><div><div class="grid2"><div><label>基准期开始</label><input name="baseline_start" type="date" required></div><div><label>基准期结束</label><input name="baseline_end" type="date" required></div><div><label>当前期开始</label><input name="current_start" type="date" required></div><div><label>当前期结束</label><input name="current_end" type="date" required></div></div><p class="muted" style="margin-top:16px">分析以所选日期为准。基准期必须早于当前期，两个期间不可重叠。数据缺失或模型调用失败时，任务明确失败，不生成替代报告。</p></div></div><div class="actions"><button class="btn primary" type="submit" ' +
        (!data.length ? "disabled" : "") +
        ">创建分析任务 " +
        icon("arrow") +
        "</button>" +
        (!data.length
          ? '<span class="muted">请先由管理员导入订单数据。</span>'
          : "") +
        "</div></form></div>"
      : "") +
    '<div class="card"><h2>任务列表</h2>' +
    table(
      ["分析问题", "运行状态", "数据快照", "创建时间", "操作"],
      rows.map((t) => [
        esc(t.payload.question),
        badge(t.state),
        esc(t.payload.dataset_id.slice(0, 12)) + "…",
        date(t.created_at),
        '<button class="btn small task-detail" data-id="' +
          t.id +
          '">查看详情</button>',
      ]),
    ) +
    '</div><div id="task-detail"></div>';
  let key = crypto.randomUUID();
  const taskForm = $("#create-task");
  if (taskForm) {
    const choices = document.createElement("div");
    choices.innerHTML =
      '<label>知识检索范围（可多选）</label><select name="rule_ids" multiple size="3">' +
      ruleRows
        .map(
          (r) => '<option value="' + r.id + '">' + esc(r.title) + "</option>",
        )
        .join("") +
      '</select><p class="muted">仅检索所选规则快照，未选则没有规则证据。任务创建后固定该范围，不被后续上传改变。</p><label><input type="checkbox" name="require_rules" style="width:auto"> 必须具备业务规则证据；知识检索失败时停止任务，不允许降级</label>';
    taskForm.insertBefore(choices, taskForm.querySelector(".actions"));
  }
  bindForm("#create-task", async (values) => {
    const result = await post("/tasks", {
      ...Object.fromEntries(values),
      rule_ids: values.getAll("rule_ids"),
      require_rules: values.has("require_rules"),
      request_key: key,
    });
    selectedTask = result.id;
    await tasks();
    await detail(result.id);
  });
  on("#refresh", async () => {
    await tasks();
    if (selectedTask) await detail(selectedTask);
  });
  on(".task-detail", async (el) => {
    selectedTask = el.dataset.id;
    await detail(selectedTask);
    $("#task-detail").scrollIntoView({ behavior: "smooth", block: "start" });
  });
}
async function detail(id) {
  clearTimeout(pollTimer);
  const [t, runs] = await Promise.all([
    api("/tasks/" + id),
    api("/tasks/" + id + "/agents"),
  ]);
  const result = t.result || {};
  const canReview =
    ["admin", "reviewer"].includes(currentUser().role) &&
    currentUser().id !== t.owner_id &&
    t.state === "awaiting_approval";
  $("#task-detail").innerHTML =
    '<div class="card"><div class="cardhead"><h2>任务详情 · ' +
    esc(id.slice(0, 12)) +
    "</h2>" +
    badge(t.state) +
    "</div>" +
    (t.error ? '<div class="notice error">' + esc(t.error) + "</div>" : "") +
    (result.plan
      ? '<div class="notice">已执行计划：按 ' +
        esc(result.plan.dimension === "channel" ? "渠道" : "产品") +
        " 分组 · 渠道过滤 " +
        esc(result.plan.channel || "全部") +
        " · 产品过滤 " +
        esc(result.plan.product || "全部") +
        "</div>"
      : '<p class="muted">后台执行后将在这里展示计划、分析结果和报告。</p>') +
    (result.report
      ? '<pre class="bodytext">' +
        esc(result.report) +
        '</pre><p class="muted">报告校验值：' +
        esc(t.report_hash) +
        '</p><div class="actions"><button class="btn download">下载报告</button></div>'
      : "") +
    (t.review_note
      ? '<div class="notice">审核意见：' + esc(t.review_note) + "</div>"
      : "") +
    (canReview
      ? '<form id="review"><label>审核意见</label><textarea name="note" minlength="2" maxlength="1000" required placeholder="核对数据口径、分组结果和报告边界后填写审核意见"></textarea><label>审核结论</label><select name="approved"><option value="true">批准发布到发起人的站内通知</option><option value="false">驳回报告</option></select><div class="actions"><button class="btn primary" type="submit">提交审核</button></div></form>'
      : "") +
    (["failed", "notification_failed"].includes(t.state) &&
    ["admin", "analyst"].includes(currentUser().role)
      ? '<div class="actions"><button class="btn retry">重试失败步骤</button></div>'
      : "") +
    "</div>";
  const trace = document.createElement("section");
  trace.className = "card";
  trace.innerHTML =
    '<div class="cardhead"><h2>Agent 执行轨迹</h2><span class="badge">服务端持久化</span></div>' +
    (runs.length
      ? '<div class="grid2">' +
        runs
          .filter(
            (r) =>
              r.job_id === t.job_id ||
              t.state === "completed" ||
              t.state.startsWith("notification"),
          )
          .slice(-4)
          .map(
            (r) =>
              '<div class="evidence"><div class="cardhead"><h2>' +
              esc(agentNames[r.agent]) +
              "</h2>" +
              badge(r.state) +
              '</div><p class="muted">' +
              esc(r.goal) +
              '</p><p class="muted">模型调用 ' +
              r.model_calls +
              " 次 · 返回 usage " +
              r.total_tokens +
              " tokens · 尝试 " +
              r.job_attempt +
              "</p><details><summary>工具与状态事件</summary>" +
              r.events
                .map(
                  (e) =>
                    '<p class="muted">' +
                    date(e.at) +
                    " · " +
                    esc(e.kind) +
                    (e.name ? " · " + esc(e.name) : "") +
                    (e.arguments
                      ? " · " + esc(JSON.stringify(e.arguments))
                      : "") +
                    "</p>",
                )
                .join("") +
              "</details>" +
              (r.error
                ? '<p class="notice error">' + esc(r.error) + "</p>"
                : "") +
              "</div>",
          )
          .join("") +
        "</div>"
      : empty("任务尚未开始 Agent 执行")) +
    table(
      ["Agent", "状态", "调用", "任务尝试", "开始时间"],
      runs.map((r) => [
        esc(agentNames[r.agent]),
        badge(r.state),
        esc(r.model_calls),
        esc(r.job_attempt),
        date(r.started_at),
      ]),
    );
  $("#task-detail").appendChild(trace);
  if (
    [
      "queued",
      "running",
      "notification_queued",
      "notification_running",
    ].includes(t.state)
  ) {
    pollTimer = setTimeout(() => {
      if (selectedTask === id && ["", "#tasks"].includes(location.hash))
        detail(id).catch((e) => error(e.message));
    }, 4000);
  }
  bindForm("#review", async (data) => {
    if (!confirm("确认提交审核？将绑定当前报告校验值。")) return;
    await post("/tasks/" + id + "/approval", {
      approved: data.get("approved") === "true",
      note: data.get("note"),
      report_hash: t.report_hash,
    });
    await detail(id);
  });
  on(".retry", async () => {
    await post("/tasks/" + id + "/retry", {});
    await detail(id);
  });
  on(".download", async () => {
    const url = URL.createObjectURL(
      new Blob([result.report], { type: "text/markdown;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "refund-report-" + id + ".md";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
async function notifications() {
  const rows = await api("/notifications");
  $("#view").innerHTML =
    head(
      "INBOX",
      "站内通知",
      "仅发布已通过审核的报告，不向外部邮件或消息平台模拟发送。",
    ) +
    '<div class="card">' +
    table(
      ["通知内容", "报告任务", "发布时间"],
      rows.map((n) => [
        '<span class="badge good">报告已审核发布</span>',
        esc(n.task_id),
        date(n.created_at),
      ]),
    ) +
    "</div>";
}
start({
  id: "insight",
  brand: "Prism Insight",
  title: "Prism Insight · 企业分析工作台",
  symbol: "grid",
  tagline: "从业务问题<br>到可信的分析行动。",
  description:
    "连接真实业务数据与业务规则，通过多 Agent 分工完成分析。每次工具调用可追踪，每份报告经审核后发布。",
  defaultPage: "tasks",
  navigation: [
    { id: "tasks", label: "分析工作台", icon: "grid" },
    { id: "datasets", label: "数据资产", icon: "file" },
    { id: "rules", label: "业务规则", icon: "file" },
    { id: "agents", label: "Agent 协作", icon: "task" },
    { id: "notifications", label: "站内通知", icon: "bell" },
    { id: "users", label: "成员与权限", icon: "users", admin: true },
    { id: "audit", label: "审计日志", icon: "shield", admin: true },
  ],
  render: (page) => ({ tasks, datasets, rules, agents, notifications })[page](),
});
