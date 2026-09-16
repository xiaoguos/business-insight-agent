# 架构与取舍

## 公共工程基础

- FastAPI API 与后台 Worker 分进程；PostgreSQL 同时保存业务数据和持久任务队列。两个项目分别部署，默认没有跨项目共享用户数据库。
- Argon2 密码散列；随机 Bearer 会话仅在服务端存摘要，浏览器令牌只在内存，不写 localStorage；刷新页面需重新登录。退出、停用账号都会阻止后续请求。
- 角色和租户来自服务端用户记录，不相信前端传来的租户字段。操作授权和对象授权同时执行。当前是固定角色权限，不是任意可配置的完整 IAM/SSO 平台。
- 用户账号登录失败采用数据库记录、PostgreSQL advisory lock 串行限流；仍需网关级 IP/WAF 防护。接口并发有 Uvicorn 上限，不能替代容量规划。
- 文档导入/任务创建与入队同一数据库事务，避免双写丢任务。Worker 用 FOR UPDATE SKIP LOCKED 认领，120秒可续租，15秒心跳；最多3次崩溃回收。业务异常标记失败，由用户显式重试。
- 每次认领生成新的 lease_token；写结果先锁任务并核对 token，阻止已被替代的 Worker 提交。租约不是 exactly-once：外部模型请求仍可能重放，最终数据库副作用按事务与唯一约束去重。
- API 结构化状态、请求 ID、审计、Worker 日志分工明确。审计是数据库记录，不是防管理员篡改的合规归档系统。
- 初始迁移固定 SQL，不动态从未来版本模型生成；后续改表必须新增迁移。启动不偷偷 create_all。

## 项目专属链路

真实CSV与规则文档 → 不可变Dataset/Order、BusinessRule → Task+Job同事务创建 → Conductor分工 → Analysis和Knowledge并行 → Report汇合 → 结果+report_hash+awaiting_approval落库 → 另一位审核人审核 → 发布Job → Notification唯一记录+completed同事务。

四个Agent各自运行独立决策循环，而不是只将函数改名。每个循环最多4轮模型决策、3次工具调用；持久化AgentRun和已校验工具事件。模型适配器返回结构化Decision，服务端先检查角色工具能力，再检查参数和结果。Conductor无业务工具；模型输出不能赋予其他Agent更多权限。

Analysis只获得问题、用户指定窗口、授权快照的维度catalog；Knowledge只获得问题与检索任务，不获得订单数据；Report仅获得已完成的聚合指标和已验证引用，不继承其他Agent的完整消息历史。系统不记录/展示隐藏思维链。

业务规则ID由用户选择并固化到Task，检索同时限定tenant与rule_ids。规则记录不可变，后续上传不会悄悄影响已有任务。require_rules属于用户政策，Conductor只能收紧不能放宽；可选分支失败必须在报告中披露，必选分支失败不得发布。

为什么不自动执行模型SQL：当前业务只需要退款率、渠道/产品分组和可选过滤。固定结构化 Plan 与 SQLAlchemy参数化查询足够表达需求，并大幅缩小权限与注入面。模型输出严格Pydantic校验，维度只能来自Literal白名单，过滤值必须存在于所选快照。

恢复边界：当前整个多Agent作业可以重跑，AgentRun用于审计，不是假装存在的checkpoint。重复模型调用仍可能计费。审批与站内发布在图外持久化，通知重试不重跑模型。若模型成本/图长度增大，再加入基于输入hash的节点缓存或checkpoint，并版本化图与状态。

超额退款=当前退款数-当前订单数×该组基准退款率。它是排查线索，不证明因果；没有基准样本的组显示不可比较，不用0伪造对照。站内通知是实际产品能力，不是外部消息推送的模拟器。

## 实现依据

LangGraph多出边并行、显式汇合使用官方[Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)；独立Agent上下文与监督者模式参考官方[子Agent说明](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents-personal-assistant)。这些模式说明不能替代本项目自身的真实模型评估。
