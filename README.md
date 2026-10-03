# 桥梁应变班交台

测量员上报跨段编号与微应变读数，**微应变判定前先做温度补偿**：

```
补偿后读数 = 原始读数 + 补偿系数 × (现场气温 − 基准气温)
```

测量员在「温度补偿」专页为每跨段设置补偿系数（允许范围 **0～0.5**）与基准气温。报送时必须带现场气温，服务端取该跨段系数算出补偿后读数，再按 **80～220 με** 判定 **合格** 或 **越界**。后台工人用 `FOR UPDATE SKIP LOCKED` 认领待处理队列，回填账本中冻结的结论。

## 温度补偿与账本规则

- 报送必须带现场气温；气温缺失/非数字、系数越出 0～0.5、跨段未设补偿，**网页投递与绕过网页直连接口一律 400 退回，说法来自同一服务端文案**。
- 入队（`strain_readings`）与账本落笔（`compensation_ledger`）在**同一事务**，少一边即整体回滚失败。
- 补偿账本为**追加式**：原文、系数、基准/现场气温、补偿后读数、结论逐笔冻结；`BEFORE UPDATE OR DELETE` 触发器拒绝任何改/删。
- 事后有人改在线单据上的数字（读数台账「改单」），账本旧值不动；`/api/ledger` 用账本与在线单对拍，漂移逐字段标红，恢复原值后重新对拍一致。
- 复核员可查看补偿系数表与补偿账本，但不能改系数、不能报送、不能改单（403）。

系数设为 **0.1**、现场气温比基准高 **10℃** 时，补偿后读数比原文多 **1**（如原文 150 → 151 με，合格；原文 219.9 → 220.9 με，越界）。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python Sanic + psycopg（异步连接池） |
| 工人 | `worker.py`（psycopg 同步，`FOR UPDATE SKIP LOCKED`） |
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
| surveyor | surv123456 | 测量员，可提交读数 |
| reviewer | rev123456 | 复核员，只读列表 |

## 启动

```bash
cd projects/19-bridge-strain-shift
docker compose up --build
```

健康检查：`GET http://localhost:8198/api/health` → `{"status":"ok","service":"bridge-strain-shift"}`

## 种子数据

| 跨段 | 微应变 | 结论 |
|------|--------|------|
| 跨中S1 | 150 με | 合格 |
| 支座S2 | 40 με | 越界 |

## 本地开发（可选）

```bash
cd backend && pip install -r requirements.txt
python -m sanic api.app --host=0.0.0.0 --port=8000 --single-process
python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8198**。
