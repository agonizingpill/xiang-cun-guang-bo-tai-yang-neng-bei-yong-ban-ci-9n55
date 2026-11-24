# 乡村广播太阳能备用班次

岭声乡村广播的节目组和电源值守共用一张备播卡，在版权离线时段安排本地校时与蓄电余量。

## 现场工作与联系人

模拟客户为岭声乡村广播合作社。广播时刻表、蓄电日志、用量周期预测、备用机优先级；节目版权离线时段只能接本地时钟。

- 节目时段表 `airslots`，字段为 `title`（节目）、`hour`（起播小时）、`license`（节目源许可）、`offline`（离线时段）。模型约束位于 `backend/domain/airslots_model.py`，规则位于 `airslots_service.py`。
- 蓄电班记与测量 `battery_logs`，字段为 `meter`（电表编号）、`hour`（两日内整点）、`load_wh`（用量 Wh）、`clear`（天空晴）、`verified`（抄表审核）。模型约束位于 `backend/domain/battery_logs_model.py`，规则位于 `battery_logs_service.py`。
- 备用分钟核算 `reserves`，字段为 `airslot`（节目时段）、`plan_wh`（计划备用 Wh）、`fallback`（备用源优先级）、`mae`（时间验证MAE Wh）、`token`（计算号）。模型约束位于 `backend/domain/reserves_model.py`，规则位于 `reserves_service.py`。
- 班组备播卡 `switchcards`，字段为 `reserve`（备用评估）、`source`（实际时钟源）、`approved_wh`（锁定备用 Wh）、`state`（状态）。模型约束位于 `backend/domain/switchcards_model.py`，规则位于 `switchcards_service.py`。

团队为模拟的6 人广播合作社值守组，负责两个电源档位的备播。

值守员用节目表和电池抄数安排断电时段。周期负荷难从表格读出，漏掉一小时会扭曲拟合，离线版权段容易误选台外时钟。

节目时刻表和已签电量日志进入估计，接班人拿到有限额与时钟源的备用卡。

### 一天的业务操作

1. 07:00 确认 AIR-17 晚间村讯版权离线。
2. 08:00 班组登记 BAT-11 整点 88.4 Wh。
3. 09:00 核对 BAT-04 两日班记。
4. 11:20 用 24 小时 Fourier 基回测末 6 小时。
5. 15:00 签发 RV-24 备用电量评估。
6. 17:45 台外时钟请求被拦后改用本地时钟。
7. 22:10 持 WR-77 完成闭台交接。

## 启动

```sh
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```

打开 `http://127.0.0.1:18317`。FastAPI OpenAPI 在 `http://127.0.0.1:19317/docs`。
Compose 运行 Nginx、Python FastAPI、独立 Redis 消费进程、SQL 数据库和 Redis，SQL 与 Redis 使用命名卷。
本机开发时，在 Python 3.11 环境安装 `backend/requirements.txt` 后从 backend 目录运行 `uvicorn core.app:app`。须提供 `DATABASE_URL`、`REDIS_URL`、`JWT_SECRET`，`SEED_DEMO` 控制演示初始化。

| 演示账号 | 权限 |
|---|---|
| operator@demo.test | 提交领域记录和计算请求 |
| reviewer@demo.test | 提交记录并执行有业务约束的复核操作 |
| viewer@demo.test | 浏览业务台账 |

三个本地演示账号均使用 `DemoC0de!2026`。角色在 `backend/core/roles.py` 和 `frontend/src/store.js` 中声明。计算状态在 worker、queueing、API 和同一前端 store 中使用。

## 页面与接口

| 业务任务 | FastAPI 路由 | 页面 |
|---|---|---|
| 节目时段表 | `/airslots` /airslots/mark-offline | `frontend/src/pages/airslots.js` |
| 蓄电班记与测量 | `/battery_logs` /battery_logs/log-charge，/battery_logs/approve-meter | `frontend/src/pages/battery_logs.js` |
| 备用分钟核算 | `/reserves` /reserves/forecast-wh，/reserves/save-reserve，/reserves/trial-models，/reserves/enable-model，/reserves/active-model，/reserves/model-activations | `frontend/src/pages/reserves.js` |
| 班组备播卡 | `/switchcards` /switchcards/issue-switch，/switchcards/handover | `frontend/src/pages/switchcards.js` |

SQL 实体与约束在 `backend/domain` 下维护。前端使用原生 ES modules。

### 预测模型与启用治理

注册于 `backend/domain/learning.py` 的两个模型，均输出末 6 小时留出 MAE 与点预测备用量：

- `fourier`（现用默认）：24 小时 Fourier 基（一、二阶谐波）+ 晴天标记的 OLS，备用量为截零点预测加 MAE。
- `persistence`（候选新模型）：拿前一天同一时刻的审签用量做预测（24 小时滞后基准），留出段逐点与实际比 MAE。

模型启用规则（见 `model_control_service.py`）：

1. `/reserves/trial-models` 把两个模型放进同一个计算任务，用同一批连续 36 小时审签读数、同一段末 6 小时各自回测，得到对照/候选两个 MAE。
2. `/reserves/enable-model` 仅复核员可提交；候选 MAE 必须严格小于对照 MAE，且试算固化的对照模型仍是当前现用模型，否则拒绝。两份启用申请并发时，设置单行行锁串行化，只有仍以现用模型为对照的那份能生效。
3. 同一试算号重试启用是幂等的：不重复切换，原样返回既有启用单与原凭据（`MA-xx` 编号）。
4. 预测任务入队时把当前模型快照写进 payload，已排队任务始终沿用排队时的模型；启用后的新模型只影响之后入队的请求。
5. 启用时把试算依据（两种模型、两个 MAE、留出小时、审签行数与小时范围等）固化到 `model_activations` 表；Redis 试算记录（24 小时过期）失效后，备用核算页面仍可通过 `/reserves/model-activations` 查到启用依据。`model_settings` 单行保存现用模型指针。

按模拟广播班记推演。卡片供人工值守查阅，应用只处理纸本条件的数字记录。

## 测试

后端测试不依赖 MySQL/Redis 服务，使用内存 SQLite 与 fakeredis：

```sh
pip install -r backend/requirements-dev.txt
cd backend
python -m pytest tests/test_learning.py tests/test_workflow.py   # 单元与端到端工作流（15 项）
python tests/test_concurrency.py                                # 双线程并发启用语义
```

前端为原生 ES modules，`node --check` 可做语法检查；`tests/` 之外的 DOM 冒烟可用 jsdom 驱动 `frontend/src/pages/reserves.js`。

[MIT](LICENSE)
