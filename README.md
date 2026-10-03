# 桥梁应变班交台

测量员上报跨段编号、微应变读数与现场气温。**判定前先吃温度补偿**：服务端按该跨段的补偿系数与基准气温算出补偿后读数，再按 **80～220 με** 判定 **合格** 或 **越界**。

## 温度补偿规则

```
补偿后读数 = 原文读数 + 补偿系数 × (现场气温 − 基准气温)
```

- 补偿系数允许范围 **0～1（含端点）**，基准/现场气温允许 **-50～100℃**。
- 测量员在「温度补偿」专页按跨段设置系数与基准气温；复核员**只读**。
- 报送时必须带现场气温，且跨段必须已设置补偿，否则网页与直连接口一律退回（同一错误说法）。
- 改系数只影响之后的报送，已落账本的记录不重算。

## 补偿账本（只追加）

- 每次报送，**原文读数与补偿后读数**连同系数、气温、结论各存一份快照进 `compensation_ledger`。
- 入队（`strain_readings`）与账本落笔在**同一个数据库事务**里，任一失败整体回滚，缺一边即失败。
- 账本表由触发器禁止 `UPDATE / DELETE / TRUNCATE`；事后改单据上的数字，账本旧值不动。
- 「温度补偿」专页账本区把账本与在线单逐字段**对拍**，单据被改即显示「不一致」并展示在线现值。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python Sanic + psycopg（异步连接池） |
| 工人 | `worker.py`（psycopg 同步，`FOR UPDATE SKIP LOCKED`；只翻转状态，不重算结论） |
| 页面 | Mithril.js + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3198 |
| 接口 | http://localhost:8198 |
| PostgreSQL | localhost:54398（库名 `bridgestrain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| surveyor | surv123456 | 测量员，可设系数、报送读数 |
| reviewer | rev123456 | 复核员，只读列表/补偿表/账本 |

## 启动

```bash
docker compose up --build
```

健康检查：`GET http://localhost:8198/api/health` → `{"status":"ok","service":"bridge-strain-shift"}`

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录取 token |
| GET | `/api/readings` | 在线单列表（含原文/补偿后/是否在账本） |
| GET | `/api/compensation` | 各跨段补偿系数表（登录可读） |
| POST | `/api/compensation` | 设置/更新跨段系数（仅测量员） |
| POST | `/api/readings` | 报送：`span_code`、`microstrain`、`field_temperature`（仅测量员） |
| GET | `/api/ledger` | 补偿账本及与在线单的对拍结果 |

### 报送示例

```bash
curl -X POST http://localhost:8198/api/readings \
  -H "Authorization: Bearer <token>" -H 'Content-Type: application/json' \
  -d '{"span_code":"跨中S1","microstrain":150,"field_temperature":30}'
# 系数 0.1、基准 20℃：150 + 0.1×(30−20) = 151 → 合格
```

错误统一为 `{"detail": "…"}`，例如 `现场气温不能为空`、`补偿系数必须在 0～1 之间（含端点）`，网页与绕过网页的请求拿到的说法一致。

## 种子数据

| 跨段 | 系数 | 基准气温 | 原文 | 现场气温 | 补偿后 | 结论 |
|------|------|----------|------|----------|--------|------|
| 跨中S1 | 0.1 | 20℃ | 150 με | 20℃ | 150 με | 合格 |
| 支座S2 | 0.1 | 20℃ | 40 με | 20℃ | 40 με | 越界 |

## 本地开发（可选）

```bash
cd backend && pip install -r requirements.txt
python -m sanic api.app --host=0.0.0.0 --port=8000 --single-process
python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8198**。
