# 农产品残留限量版本与复检裁决

面向农产品检测站复核员，处理采信、判定、核算、复核与归档。

## 启动

```bash
docker compose up --build -d
```

访问 http://localhost:18254 。初次启动自动运行脏样例，可切换 clean.csv 与 legacy.csv。顶部“运行批次”会重新计算，同源输入复用批次。

## 业务模块

- 报告采信：`/m1_ingest`、`/api/m1_ingest`
- 项目映射：`/m2_mapping`、`/api/m2_mapping`
- 限量选版：`/m3_versions`、`/api/m3_versions`
- 检出量换算：`/m4_segments`、`/api/m4_segments`
- 复检裁决：`/m5_calculate`、`/api/m5_calculate`
- 批次判级：`/m6_reconcile`、`/api/m6_reconcile`
- 报告归档：`/m7_archive`、`/api/m7_archive`
- 补正裁决：`/m8_amend`、`/api/m8_amend`（GET 台账 / POST 裁决 / POST `/recheck` 重新核对）

## 核心规则

按采样日期选择有效限量，低于检出限保留区间；复检不得越过样本关联与方法适用范围。完整数值、边界和异常定义见 `docs/DATA-DICTIONARY.md`。

## 验证

```bash
docker compose exec -T app pytest -q
docker compose config --quiet
```

原始样例在 `sample_data/`。测试在 `tests/`。封存文件是单份CSV，含运行清单、计算证据、隔离原因与对账。复核需提交当前版本，陈旧复核返回409。

## 数据与限制

SQLite保存在records命名卷，普通重启与构建不会清除。确认可丢弃本项目数据后，运行 `docker compose down -v` 再启动可重置。单文件上限2MB；不接入外部业务系统；历史规则由本项目样例定义，不代表实际行业标准。

## 补正裁决（M8）

同一报告收到多份补正时，按到达先后逐份作用于**当前有效版本**：每份补正整版快照叠加（追加自增版本行，不改写历史行），再重跑 M1–M7 全链路。触发限量选版、区间换算或复检关联规则的补正整份驳回并记录原因码，旧当前版本继续有效，批次可凭既有合格证据出证；通过的补正形成新当前版本，项目结论、批次等级与封存资格随之重算。

补正台账（`amendments` 表）只追加：原始报告与每份补正（含驳回件）均留痕，封存证据内嵌台账。同一补正编号重跑只回放原裁决、不新增版本；编号相同而载荷不同返回 409。裁决在单事务内提交，写入中断整体回滚、旧版完好。工作台“重新核对”按台账重放补丁并比对运行指纹。演示补正见 `sample_data/amendments_demo.json`。
