# AGENTS.md

本文件约束整个仓库。后续智能体或协作者在本项目内工作时，必须先阅读本文件，再阅读相关源码和文档。

## 项目定位

- QuantGPT 是一个面向 Mac 本地使用的小白量化因子学习平台。
- README、示例和用户可见文案应优先服务“学习 A 股量化因子挖掘”的场景。
- 默认叙事应是本地学习、表达式回测、因子理解和研究复盘。
- DeepSeek、WorldQuant BRAIN、QuantGPT Cloud、PostgreSQL、Celery、Docker 都是可选能力，不应写成入门必需项。
- 不要把项目描述成荐股工具、收益承诺工具或实盘交易系统。

## 技术结构

- Python 后端位于 `quantgpt/`，入口是 `python -m quantgpt`。
- FastAPI 服务入口是 `quantgpt/api_server.py`。
- MCP 工具入口是 `quantgpt/mcp_server.py`。
- 因子表达式解析在 `quantgpt/expression_parser.py`。
- 分组回测逻辑在 `quantgpt/backtest.py`。
- 行情数据和本地缓存逻辑在 `quantgpt/market_data.py`。
- React 前端位于 `frontend/`。
- 运行产物通常位于 `data/`、`reports/`、`logs/`、`frontend/dist/`，不要提交。

## 本地命令

```bash
make setup
make frontend
make run
make test
make lint
```

常用直接命令：

```bash
./.venv/bin/python -m quantgpt --transport http --port 8003
./.venv/bin/python -m quantgpt --prefetch hs300 csi500
cd frontend && npm run build
```

## 代码风格

- 修改前必须先读相关文件，优先复用已有逻辑。
- 保持改动最小，只处理当前请求涉及的范围。
- 默认使用函数式写法，只有外部系统连接器、接口封装或框架要求时再使用类。
- 新增逻辑应尽量写成单一职责的纯函数，不修改输入参数或全局状态。
- 保持严格类型标注，避免无类型变量、宽泛类型和难以追踪的数据结构。
- 复杂数据结构要定义明确类型。
- 所有 imports 放在文件顶部；如果现有代码因性能或依赖循环使用局部 import，先理解原因再改。
- 不要新增多模式函数，也不要用布尔 flag 在同一函数里切换多套行为。
- 新增代码注释只使用简体中文，并且只在能降低理解成本时添加。
- 遵循现有框架约定。FastAPI、Pydantic、MCP 工具等已有默认参数模式时，不要为了风格偏好做大范围重构。

## 错误处理

- 失败要显式报错，不要静默忽略。
- 错误信息要包含可定位上下文，例如表达式、股票池、日期范围、状态码或响应体。
- 不要使用吞掉根因的 catch-all。
- 外部 API、数据源或服务调用可以重试，但最终失败必须抛出或返回清晰错误。
- 日志尽量使用结构化字段，避免把大量动态上下文拼成不可检索字符串。

## 文档和用户文案

- 面向用户的文档优先使用简体中文。
- README 保持 Mac-only 入门路径，不增加 Windows、Linux、Docker 或云部署作为主路径。
- 文案要从学习者视角出发，少用营销词、夸张词和空泛愿景。
- UI 可见文案要自然、直接、可操作，避免暴露内部模块名、数据结构或实现细节。
- 投资相关表述必须保守，明确说明不构成投资建议。
- 不要把历史回测、WQ 截图或外部验证写成未来收益保证。

## 测试和验证

- 修改代码后，优先运行项目已有验证命令。
- 后端改动通常运行 `make test`，必要时再运行 `make lint`。
- 前端改动通常运行 `cd frontend && npm run build`。
- 文档改动不需要新增测试，但要检查命令、路径和链接是否与仓库真实结构一致。
- 不要为了覆盖率新增脆弱单元测试；确实需要测试时，优先选择能验证真实行为的集成、端到端或 smoke 测试。

## 因子挖掘记录

- 后续进行因子挖掘、批量候选验证或收益曲线生成时，最终结果必须写入因子看板数据库，不能只保存在 `tmp/`、`reports/` 或终端输出里。
- 看板数据至少要落到 `factor_mining_runs`、`factor_mining_candidates` 和 `factor_mining_backtest_series` 对应数据结构，优先复用 `quantgpt/factor_mining_store.py` 里的持久化函数。
- 如果因 MCP 超时或外部数据慢而改用本地脚本探索，`tmp/` 只能作为中间产物目录；完成后必须补写数据库，并通过 `/api/v1/factor-mining/runs` 和 `/api/v1/factor-mining/runs/{run_id}/backtest-series` 验证前端可见。
- 写入记录时要保存股票池、基准、验证窗口、候选表达式、评分指标和收益曲线，方便后续在因子看板中比较和复盘。

## 临时文件

- 临时代码、截图、探索数据只允许放在项目根目录的 `tmp/` 下。
- `tmp/` 不允许加入 Git。
- 不要把本地路径、账号、Token、缓存数据、报告产物提交到仓库。

## Git 规则

- 不要主动创建 commit，除非用户明确要求。
- 不要 revert 用户或其他工具产生的无关改动。
- 查看差异使用非交互命令，例如 `git --no-pager diff`。
- 提交前保持 diff 小而清晰，让用户能直接审阅。
