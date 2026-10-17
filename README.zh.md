# AuditPilot 🔍 — 订阅账单取证审计器

**把忘记退的订阅、悄悄涨的价、重复扣的款，从一份银行/信用卡账单里全部找出来——100% 离线。**

AuditPilot 读取账单（CSV 或粘贴文本），跑一个 6 阶段智能体闭环：

```
PERCEIVE ──► PARSE ──► DETECT ──► PLAN ──► EXPLAIN ──► RECEIPT
```

| 阶段 | 做什么 |
|---|---|
| PERCEIVE | 接收账单文本（上传或粘贴） |
| PARSE | 表头/BOM/列推断 → 交易列表；无法解析的行只计数，绝不猜测 |
| DETECT | 6 个检测器全量运行（见下） |
| PLAN | 发现 → 有序、带证据的省钱动作 |
| EXPLAIN | 确定性叙述（可选 LLM 润色） |
| RECEIPT | JSON 回执 + 自包含 HTML 报告 |

**每个发现都带证据**——触发它的源行号和原始行内容。账单里没有的东西，绝不推断。

---

## 能找出什么

| 检测器 | 示例发现 |
|---|---|
| `detect_recurring` | `Planet Fitness` 每月 $39.99（$39.99/月，出现 9 次） |
| `detect_price_changes` | `Netflix` $15.99 → $17.99（2026-04-03，+12.5%，年多扣 $24.00） |
| `detect_duplicates` | `Doordash` 同日 $24.50 ×2——可追回 $24.50 |
| `detect_trials` | `Spotify` $1.00 → $11.99（2026-01-28）——试用转正式 |
| `detect_fees` | `Overdraft Fee` 3 次 = $105.00（按此频率年约 $430，可避免） |
| `detect_creep` | `Cloud Sync` $12.00 → $13.99，分 4 次每次不到 5%（累计 +16.6%）——任何单次涨幅警报都不会响 |

每次审计输出：

- **每月 / 每年固定支出**（周账单折算月等效）
- **可立即追回**（重复扣款）
- **年潜在节省**（涨价部分）
- **泄漏分** `min(100, 35×重复 + 12×涨价 + 8×试用转化 + 6×可避免手续费类目)`，
  分级 LOW / MODERATE / HIGH / CRITICAL——公式公开，可复现
- 有序的**省钱计划**（争议 → 复核 → 核查 → 审计）
- **可分享的 HTML 报告**（单文件、无外部资源、无 JS）

## 快速开始

```bash
# 审计一份账单（CLI，零依赖——Python 3.8+）
python -m auditpilot audit examples/statement_messy.csv
python -m auditpilot audit statement.csv --json --html report.html

# 审计两个内置示例
python -m auditpilot demo

# Web 应用（Gradio 6）——读 PORT，绑 0.0.0.0
pip install -r requirements.txt
python app.py
```

Web 应用有「加载演示账单并审计」按钮——一键查看完整报告，不需要你的任何数据。

## 自验证

```bash
python eval.py             # 100 条断言
python test_agentguard.py  # 44 条断言（AgentGuard 安全层，含真实 HTTP 测试）
```

覆盖：解析（CSV / 无表头 / 借贷列 / 粘贴文本 / BOM / 空文件）、每个检测器的
正例**与负例**（单次账单尖峰不是涨价、2% 抖动不是涨价、波动水电费不是涨价、
序列中价格回落不是 creep、单笔手续费不做年化外推）、智能体闭环、回执结构、
HTML 报告卫生（无 script 标签、无外部资源）、经**真实子进程**的 stdio MCP
服务器（initialize → tools/list → tools/call → 未知工具 → 畸形 JSON）、以及 CLI。

## AgentGuard —— 智能体安全层（第七届中国人工智能大赛）

本仓还有第二套独立引擎：`auditpilot/agentguard.py` + `china_ai_service.py`
组成一个**确定性安全网关**，为智能体安全防护挑战赛（https://ai.xm.gov.cn/ ）
构建——该赛评分四维：提示词注入防护 / 权限访问控制 / 隐私信息保护 / 误报率。

```bash
python test_agentguard.py                                  # 44/44 离线通过，含服务层
AGENTGUARD_TOKEN=demo python china_ai_service.py           # OpenAI 兼容网关，读 $PORT
```

- 27 条中英规则（指令覆盖、越狱/DAN、伪造系统标签、工具劫持、数据外发、
  提示词回显、凭据读取）——每个发现带规则 id 与命中原文区间。
- PII 检测带真实校验位：中国手机号、18 位身份证（GB11643 校验位）、
  银行卡（Luhn）、邮箱、固话——响应与日志中一律掩码。
- 误报校准编码为测试：教育/防御语境中引用的攻击模式记录但降级；否定语境、
  营销文案、二维码防骗提问、非拉丁语种全部放行。硬红线：外发/工具劫持/
  凭据/伪装系统标签四类发现**绝不**因语境降级。
- HTTP 合同：`POST /v1/chat/completions`（OpenAI 结构 + `guard` 字段）、
  `POST /v1/guard`（判定 JSON）、`POST /v1/moderations`（OpenAI 结构）、
  `GET /v1/models`、`GET /healthz`。访问控制用 `AGENTGUARD_TOKEN` Bearer 门禁。
- 实测：44/44 断言通过（`LC_ALL=C` 无 `PYTHONUTF8` 亦通过）；单条判定
  0.05–0.25ms，对比赛制 60s/次上限有十万倍余量。

部署与合同适配 runbook：**`CHINA_AI_DEPLOY.md`**；Render blueprint 已把网关
作为独立服务发布（`auditpilot-guard`）。

## 项目结构

```
auditpilot/
  statement.py     解析 + 商户名归一化
  detectors.py     订阅 / 涨价 / 重复 / 试用 / 手续费 / creep
  brain.py         离线叙述器 + 可选 OpenAI 兼容润色
  agent.py         6 阶段闭环、省钱计划、风险分
  receipts.py      JSON 回执 + 自包含 HTML 报告
  mcp_server.py    零依赖 stdio MCP 服务器（spec 2025-11-25）
  cli.py           audit / demo / mcp 子命令
  agentguard.py    智能体安全检测层（27 条规则 + PII 校验 + 决策引擎）
app.py             Gradio 6 Web 应用（经 launch(mcp_server=True) 同时是 MCP 服务器）
china_ai_service.py  AgentGuard HTTP 网关（OpenAI 兼容，读 $PORT）
eval.py            100 条断言测试
test_agentguard.py 44 条 AgentGuard 断言（库 + 真实 HTTP）
examples/          凌乱示例（预埋问题）+ 干净示例（阴性对照）
```

## 许可证

MIT——见 `LICENSE`。
