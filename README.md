# 基金策略平台 MVP

这是一个从 0 搭起来的第一版基金平台原型，目标是先把下面这条链路跑通：

- 导入你当前买的基金
- 自动计算净值趋势、收益、波动和最大回撤
- 对每只基金给出规则化策略建议
- 展示持仓分析、观察池和基金详情

这一版为了保证本地可运行，使用了 Python 标准库，不依赖第三方框架。

## 真实数据来源

当前已经接入真实基金净值数据，优先使用天天基金公开页面接口：

- 基金代码与名称列表
  - `https://fund.eastmoney.com/js/fundcode_search.js`
- 历史净值
  - `https://fund.eastmoney.com/f10/F10DataApi.aspx?type=lsjz&code=<基金代码>&page=1&per=49`

项目里对应实现见：

- [app/real_data.py](/Users/gaodanchun/Documents/jijin/app/real_data.py)

系统现在只保留真实基金数据，不再写入任何 mock、seed、示例或占位净值。

## 运行方式

```bash
python3 server.py
```

启动后打开：

[http://127.0.0.1:8000](http://127.0.0.1:8000)

## AI 解读

当前已经支持针对持仓基金生成每日解读报告，报告会分析：

- 近期涨跌原因
- 可能的上涨驱动
- 可能的下跌驱动
- 接下来要看的风险点
- 对当前持仓的操作建议

页面里可以直接点击“生成今日 AI 解读”。

如果要启用大模型版本的解读，现在可以直接在项目根目录放一个 `.env` 文件：

```bash
AI_PROVIDER=openai
AI_MODEL=gpt-5-mini

OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-5-mini

MINIMAX_API_KEY=your_minimax_api_key_here
MINIMAX_MODEL=MiniMax-M2.7
```

项目启动时会自动读取 `.env`。

仓库里也提供了示例文件：

`/Users/gaodanchun/Documents/jijin/.env.example`

如果你更习惯环境变量，也仍然可以继续这样设置：

```bash
export OPENAI_API_KEY=your_key_here
export OPENAI_MODEL=gpt-5-mini
```

现在已经支持多模型提供商：

- `OpenAI`
- `MiniMax`

你可以：

- 在 `.env` 里通过 `AI_PROVIDER` / `AI_MODEL` 设置默认 provider 和模型
- 在页面里直接切换当前 provider 和模型
- 切换后配置会持久化到本地 SQLite，下次打开仍然生效

如果当前 provider 没有可用 API Key，或者模型调用失败，系统会自动回退到规则化解读，保证日报仍然可以生成。

也可以手动运行每日解读脚本：

```bash
python3 -m app.daily_report
```

生成后的 markdown 报告会写到：

`data/reports/YYYY-MM-DD.md`

## 已实现功能

- 持仓导入
  - 支持页面快速导入单只基金
  - 输入基金代码、当前持有金额、当前收益率即可换算持仓
  - 支持 CSV 或 JSON 导入
  - 支持覆盖导入和追加导入
  - 导入时会自动尝试同步真实基金历史净值
  - 拉取失败的基金不会写入数据库
- 数据存储
  - SQLite 存储基金基础信息、净值历史、持仓和预留信号表
- 真实数据刷新
  - 支持刷新持仓真实数据
  - 支持刷新全部基金真实数据
  - 页面会显示最近同步时间
- AI 解读
  - 支持单只基金生成今日解读
  - 支持脚本生成全部持仓的每日解读 markdown
  - 支持 `OpenAI / MiniMax` 多 provider
  - 页面里可以直接切换 provider 和模型
  - 模型失败时会自动回退到规则解读，并在页面里显示回退原因
- 基金分析
  - 近 1/3/6/12 月收益
  - 年化波动率
  - 最大回撤
  - 20/60 日均线趋势
  - 近阶段位置判断
- 策略建议
  - 适合继续定投
  - 可以分批加仓
  - 继续观察
  - 暂缓操作
  - 注意风险
  - 持仓浮盈较高且趋势转弱时会提示考虑止盈
- 可视化页面
  - 持仓总览
  - 策略重点
  - 持仓表格
  - 单基金详情
  - 候选基金观察池

## 导入格式

推荐 CSV 表头：

```csv
基金代码,基金名称,持有份额,持仓成本,买入日期
110011,华夏景气成长混合,1800,1.16,2025-08-16
000961,沪深300联接基金,2500,1.11,2025-11-02
161725,消费主题指数增强,1200,1.29,2025-09-22
```

也支持 JSON：

```json
[
  {
    "fund_code": "110011",
    "fund_name": "华夏景气成长混合",
    "shares": 1800,
    "cost_basis": 1.16,
    "buy_date": "2025-08-16"
  }
]
```

## 项目结构

```text
.
├── app
│   ├── analytics.py
│   ├── db.py
│   └── real_data.py
├── data
├── static
│   ├── app.js
│   ├── index.html
│   └── styles.css
├── server.py
└── README.md
```

## API 概览

- `GET /api/dashboard`
  - 返回持仓汇总、策略信号、观察池
- `GET /api/funds`
  - 返回所有基金及分析结果
- `GET /api/funds/<code>`
  - 返回单只基金详情和净值曲线
- `POST /api/positions/import`
  - 导入持仓
- `POST /api/positions/quick-import`
  - 按单只基金的当前持有金额和当前收益率快速导入
- `POST /api/funds/refresh`
  - 刷新真实基金数据
- `POST /api/reports/generate`
  - 生成单只基金或全部持仓的每日解读
- `GET /api/reports?fund_code=<code>`
  - 返回某只基金最新的解读报告
- `GET /api/ai/config`
  - 返回当前 AI provider、模型和可选 provider
- `POST /api/ai/config`
  - 保存当前 AI provider 和模型
- `POST /api/ai/test`
  - 测试当前 provider 是否能正常调用
- `GET /api/data-source`
  - 返回当前真实数据源说明
- `POST /api/positions/reset`
  - 清空当前持仓

## 当前限制

- 真实净值依赖公开页面接口，接口格式未来可能调整
- 少数基金可能因为页面无数据、网络限制或接口异常而导入失败
- 还没有接入登录系统、账户体系和消息通知
- 策略是规则引擎，不是机器学习或量化回测结果

## 下一步建议

下一阶段最值得做的 4 件事：

1. 增加定时同步和失败重试
2. 增加用户风险偏好和投资期限配置
3. 做定投计划、止盈线和提醒系统
4. 接入大模型，把指标翻译成更自然的投资建议说明
