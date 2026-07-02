# QuantGPT

QuantGPT 是一个在 Mac 本地运行的 A 股量化因子学习平台。它适合小白从“什么是因子”开始，逐步练习写表达式、跑回测、看指标、保存因子、对比因子，并在需要时接入 AI Agent 做自动化因子挖掘。

本fork项目，仅供本人自用学习。
## 你可以用它做什么

- 打开本地 Dashboard，集中查看因子研究任务、运行状态、评分分布和历史结果。
- 在网页里输入一个因子表达式，跑出分组收益、IC、换手率、评分和 HTML 报告。
- 从最小股票池开始练习，再扩展到沪深 300、中证 500、中证 1000、中证 2000。
- 保存表现较好的因子，做多因子组合和因子对比。
- 用 MCP 接入 Claude Code 或 Claude Desktop，让 AI Agent 调用本项目的工具自动做因子实验。
- 可选接入 DeepSeek，让自然语言描述自动转换为因子表达式。
- 可选接入 WorldQuant BRAIN 或 QuantGPT Cloud，做更进一步的外部验证。

## 适合谁

- 想学量化因子，但还不熟悉 Python 回测框架的人。
- 想从 A 股日频数据开始，理解动量、反转、波动率、成交量、基本面等因子的人。
- 想把 AI Agent 当研究助理，而不是只让它聊天的人。
- 想在 Mac 上本地跑完整项目，不想先折腾服务器、Docker 或云部署的人。

## 不适合谁

- 想要实盘自动交易、分钟级高频、盘口撮合或券商下单的人。
- 想要保证收益的人。
- 想要开箱即用的投资建议的人。

## 项目结构

```text
.
├── quantgpt/                  # Python 后端、回测引擎、MCP 工具和 API
│   ├── api_server.py           # FastAPI 服务入口
│   ├── mcp_server.py           # MCP 工具入口
│   ├── expression_parser.py    # 因子表达式解析器
│   ├── backtest.py             # 分组回测引擎
│   ├── market_data.py          # A 股行情下载和 Parquet 缓存
│   ├── anti_overfit.py         # 反过拟合检测
│   ├── rolling_validator.py    # 滚动样本外验证
│   ├── iteration.py            # 因子评分和迭代逻辑
│   └── routes/                 # API 路由
├── frontend/                  # React + TypeScript 网页界面
├── scripts/                   # 批量挖掘、预热和辅助脚本
├── docs/                      # 详细文档
├── example_factor/            # 示例因子截图和静态页面
├── tests/                     # 现有测试
├── pyproject.toml             # Python 项目配置
├── Makefile                   # 常用本地命令
└── .env.example               # 本地环境变量模板
```

## 核心概念

### 因子表达式

因子表达式是一段用字段和算子写出的规则。比如：

```text
rank(close / ts_mean(close, 20))
```

它的含义是：把当前收盘价除以过去 20 日均价，再在同一天的股票横截面里排序。排名越高，说明短期涨幅相对越强。

### 分组回测

项目会把股票按因子值从低到高分组，例如分成 5 组，然后观察高分组、低分组和多空组合的收益表现。小白先看三个指标就够：

| 指标 | 你应该怎么理解 |
|------|----------------|
| `IC` | 因子值和未来收益的相关性，越稳定越好 |
| `Sharpe` | 收益相对波动是否划算，越高越好 |
| `Turnover` | 换手率，过高会被交易成本吃掉 |

### 股票池

| 名称 | 用途 |
|------|------|
| `small_scale` | 5 只蓝筹股，适合第一次快速试跑 |
| `hs300` | 沪深 300，适合新手正式练习 |
| `csi500` | 中证 500，适合测试中盘股 |
| `csi1000` | 中证 1000，数据量更大，速度更慢 |
| `csi2000` | 中证 2000，适合进阶实验 |

## Mac 环境准备

先安装 Homebrew。如果你已经装过，可以跳过。

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

安装 Python、Node.js 和 Git：

```bash
brew install python@3.12 node git
```

确认版本：

```bash
python3 --version
node --version
npm --version
git --version
```

建议使用 Python 3.10 到 3.12。Python 太新时，部分数据或科学计算依赖可能还没有完全适配。

## 第一次启动

### 1. 下载项目

```bash
git clone https://github.com/Miasyster/QuantGPT.git
cd QuantGPT
```

如果你已经在本地有这个目录，直接进入项目根目录即可。

### 2. 安装 Python 依赖

```bash
make setup
```

如果你的 `python3 --version` 不是 3.10 到 3.12，可以明确指定 Homebrew 的 Python 3.12：

```bash
make PYTHON="$(brew --prefix python@3.12)/bin/python3.12" setup
```

这一步会创建 `.venv/`，安装后端依赖，并从 `.env.example` 生成 `.env`。

默认 `.env` 里 `AUTH_DISABLED=true`，本地学习时不需要注册和邮箱验证码。

### 3. 构建网页界面

```bash
make frontend
```

这一步会进入 `frontend/`，安装前端依赖并生成 `frontend/dist/`。

### 4. 启动服务

```bash
make run
```

看到 uvicorn 启动后，打开：

[http://localhost:8003](http://localhost:8003)

网页默认进入“研究总览”Dashboard。这里可以看任务总数、成功率、运行中的任务、失败任务、因子评分分布，以及每一次研究任务的表达式、状态、耗时和关键指标。

`make run` 会占用当前终端窗口。想停止服务时，在这个终端里按 `Control + C`。

健康检查：

```bash
curl http://localhost:8003/api/v1/health
```

正常会返回类似：

```json
{"status":"ok","active_tasks":0,"total_tasks":0}
```

## 第一次跑回测

打开网页后进入“单因子回测”，输入：

```text
rank(close / ts_mean(close, 20))
```

建议第一次在高级设置里选择：

| 设置 | 建议值 |
|------|--------|
| 股票池 | `small_scale` |
| 开始日期 | `2023-01-01` |
| 结束日期 | `2025-12-31` |
| 分组数量 | `5` |
| 持仓周期 | `5` |
| 基准 | `hs300` |

第一次运行会下载行情数据，速度取决于网络和数据源响应。后续会优先读取本地缓存，通常会快很多。

回测完成后，页面会显示单次因子的结果 Dashboard，包括收益指标、Sharpe、最大回撤、IC、Rank IC、IC 胜率、换手率、分组收益、反过拟合检测、完整 HTML 报告入口，以及“收藏因子”按钮。

收藏后的因子会进入右侧“因子库”，后续可以用于多因子组合和因子对比。

## 推荐学习顺序

1. 跑通 `small_scale`，确认服务、网页和数据下载都正常。
2. 改成 `hs300`，观察同一个表达式在更大股票池上的表现。
3. 只改一个变量，例如把 `20` 改成 `10` 或 `60`，比较结果变化。
4. 尝试不同类型的因子：动量、反转、成交量、波动率、基本面。
5. 保存表现较好的因子，在“多因子组合”和“因子对比”里比较。
6. 接入 MCP，让 AI Agent 帮你批量实验，但结论仍然要自己复核。

## 常用入门表达式

| 类型 | 表达式 | 直觉 |
|------|--------|------|
| 动量 | `rank(close / ts_mean(close, 20))` | 当前价格相对 20 日均价越高，排名越靠前 |
| 反转 | `rank(-1 * ts_delta(close, 5) / ts_shift(close, 5))` | 过去 5 日跌得越多，反转排名越靠前 |
| 成交量 | `rank(volume / ts_mean(volume, 10))` | 当前成交量相对 10 日均量越大，排名越靠前 |
| 波动率 | `rank(-1 * ts_std(close / ts_shift(close, 1) - 1, 20))` | 波动越低，排名越靠前 |
| 量价相关 | `rank(ts_corr(close, volume, 10))` | 价格和成交量相关性越高，排名越靠前 |

注意：一个表达式回测表现好，不代表它未来一定有效。你需要继续做不同时间段、不同股票池、不同参数下的验证。

## 量化因子 Dashboard

这个项目有两层 Dashboard：

### 研究总览 Dashboard

打开 [http://localhost:8003](http://localhost:8003) 后默认看到的是“研究总览”。它用来管理和复盘所有因子研究任务：

| 内容 | 用途 |
|------|------|
| 任务统计 | 查看总任务数、成功数、失败数、运行中任务和成功率 |
| 评分分布 | 查看 A/B/C/D 因子数量，快速筛选高质量因子 |
| 任务列表 | 查看每次研究的 Prompt、表达式、标签、评分、状态、耗时和时间 |
| 任务详情 | 点开任务后查看关键指标、WQ 模拟指标、稳健性检测和完整报告入口 |
| 状态筛选 | 按成功、失败、运行中任务筛选 |
| 评分筛选 | 按 A/B/C/D 评分筛选 |

### 回测结果 Dashboard

在“单因子回测”里跑完一个表达式后，会看到当前因子的结果 Dashboard：

| 内容 | 用途 |
|------|------|
| 因子表达式 | 确认本次真正用于回测的表达式 |
| 收益指标 | 查看总收益、年化收益、Sharpe、Sortino、最大回撤、波动率、胜率 |
| 因子指标 | 查看多空年化、单调性、分组价差、IC、Rank IC、IC IR、IC 胜率 |
| 交易指标 | 查看换手率和 WQ Fitness |
| 稳健性检测 | 查看反过拟合检测、对抗验证和综合评分 |
| 分组收益 | 比较不同分组的收益是否有清晰排序 |
| 股票因子值 | 查看部分股票在该因子下的打分结果 |
| 完整报告 | 打开 QuantStats HTML 报告进一步检查收益曲线和风险指标 |
| 收藏因子 | 把当前因子保存到因子库，后续用于组合和对比 |

### 页面入口

| 页面 | 用途 |
|------|------|
| 研究总览 | 查看所有因子任务、评分分布、状态筛选和任务详情 |
| 单因子回测 | 输入表达式并运行回测 |
| 多因子组合 | 把多个已保存因子组合起来测试 |
| 因子对比 | 对比不同因子的指标和相关性 |
| 侧边栏 | 管理研究会话、历史任务和收藏因子 |

## MCP 给 AI Agent 使用

如果你使用 Claude Code 或 Claude Desktop，可以把 QuantGPT 注册成 MCP 工具。推荐使用项目虚拟环境里的 Python。

先查看当前项目路径：

```bash
pwd
```

再查看虚拟环境 Python 路径：

```bash
./.venv/bin/python -c "import sys; print(sys.executable)"
```

Claude Code 可在项目根目录创建 `.mcp.json`：

```json
{
  "mcpServers": {
    "quantgpt": {
      "type": "stdio",
      "command": "/你的项目绝对路径/.venv/bin/python",
      "args": ["-m", "quantgpt"],
      "cwd": "/你的项目绝对路径"
    }
  }
}
```

Claude Desktop 可编辑：

```text
~/Library/Application Support/Claude/claude_desktop_config.json
```

配置内容：

```json
{
  "mcpServers": {
    "quantgpt": {
      "command": "/你的项目绝对路径/.venv/bin/python",
      "args": ["-m", "quantgpt"],
      "cwd": "/你的项目绝对路径"
    }
  }
}
```

重启客户端后，让 Agent 先调用：

```text
list_operators
list_universes
```

再让它执行类似任务：

```text
请在 small_scale 上测试 3 个简单动量因子，并解释每个因子的 IC、换手率和分组收益。
```

## 可选：启用 DeepSeek

不配置 DeepSeek 时，只能直接输入因子表达式。配置后，可以输入自然语言，例如“帮我测试一个 20 日动量因子”。

编辑 `.env`：

```text
DEEPSEEK_API_KEY=你的 DeepSeek Key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

重启服务：

先在运行服务的终端里按 `Control + C` 停止旧服务，然后执行：

```bash
make run
```

## 可选：预下载数据

如果你准备频繁测试沪深 300 或中证 500，可以先预热缓存：

```bash
./.venv/bin/python -m quantgpt --prefetch hs300 csi500
```

数据会缓存在 `data/` 下。这个目录是运行产物，不需要提交到 Git。

## 可选：WorldQuant BRAIN 和 QuantGPT Cloud

项目里保留了 WQ BRAIN 和 QuantGPT Cloud 的集成，但它们不是小白入门的必要步骤。

如果你只是学习本地因子挖掘，先不要配置这些账号。等你能稳定解释本地回测结果后，再阅读：

- [MCP 配置指南](docs/MCP_GUIDE.md)
- [因子挖掘指南](docs/FACTOR_MINING.md)
- [API 文档](docs/API_DOC.md)

## 常用命令

| 命令 | 作用 |
|------|------|
| `make setup` | 创建虚拟环境并安装后端依赖 |
| `make frontend` | 安装前端依赖并构建网页 |
| `make run` | 启动本地服务 |
| `make dev` | 用 8003 端口启动本地服务 |
| `make test` | 运行现有测试 |
| `make lint` | 运行 Ruff 和 Pyright |
| `./.venv/bin/python -m quantgpt --prefetch hs300` | 预下载沪深 300 数据 |

## 常见问题

### 网页打不开

先确认服务还在运行：

```bash
curl http://localhost:8003/api/v1/health
```

如果 API 正常但网页不显示，通常是还没有构建前端：

```bash
make frontend
make run
```

### 端口 8003 被占用

查看占用进程：

```bash
lsof -i :8003
```

结束进程：

```bash
kill <PID>
```

如果进程不退出，再使用：

```bash
kill -9 <PID>
```

### 首次回测很慢

首次回测会下载并缓存行情数据。先用 `small_scale` 试跑，确认流程正常后再换到 `hs300`。

### 自然语言输入失败

如果没有配置 `DEEPSEEK_API_KEY`，请直接输入表达式，不要输入自然语言。

### 数据下载失败

免费数据源偶尔会慢或失败。可以稍后重试，或先缩短日期范围、改用 `small_scale`。

## 学习建议

- 每次只改一个变量，不要同时改表达式、股票池、日期和持仓周期。
- 先理解结果，再追求更高分数。
- 记录失败实验。失败的路径能帮你避免重复试错。
- 不要只看收益曲线，也要看 IC、换手率、分组单调性和样本外表现。
- 不要把回测结果当成投资承诺。

## 许可证

[MIT](LICENSE)

本项目仅用于学习、研究和工程实验，不构成任何投资建议。历史回测表现不代表未来收益。
