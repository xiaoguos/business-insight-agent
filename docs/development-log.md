# 开发日志

所有时间为实际记录（Asia/Shanghai），不回填虚构的开发历史。演示业务日期与开发日期独立。

## 2026-09-16 15:28 +08:00 — 立项与仓库创建

- 目标：完成可运行后端、GitHub Pages 交互演示、回归测试、评估脚本、部署说明与面试材料。
- 仓库：https://github.com/xiaoguos/business-insight-agent
- 已验证：GitHub API 返回仓库创建成功。
- 决策：Pages 仅托管静态页面；Python 后端用 Docker/本地 Python 启动。页面提供显式演示模式，不能把浏览器计算宣传成云端大模型推理。
- 决策：无 API Key 也应能复现核心流程；可选模型适配器单独标识测试覆盖与未验证范围。
- 环境难点：Windows 的 Git credential fill 调用 shell 失败（signal pipe）。直接调用已安装的 Git Credential Manager 获取现有账号凭据解决，凭据不落盘、不写入日志。
- 下一步：实现纵向业务流程，再用失败用例推动修正，最后部署并记录真实结果。

