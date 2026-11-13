# intake_hub

到院前服务资料采集：用途授权、问答整理、风险提示与人工联络队列。当前入口是 `intake_hub.gateway.command`，它使用本地 JSON 资料和 SQLite 状态；目录 `intake_hub` 存放业务模块，`tests/consent_intake` 验证既有业务行为。

## 环境

使用 Python 3.11，全部依赖来自标准库。`requirements.txt` 为空，不需要下载数据、模型或第三方包。数据库在运行时创建，可以删除临时数据库重新开始。

## 检查

```bash
python3 -m unittest discover -s tests/consent_intake -v
python3 -m unittest discover -s tests/analysis_context -v
python3 -m unittest discover -s tests/questionnaire_linking -v
python3 -m compileall -q intake_hub tests scripts
```

## 问卷量尺衔接（分析用途，不改变授权与分流）

`intake_hub.questionnaire` 提供 long/short 问卷的量尺衔接与趋势校验，含 `fit`、`apply` 两个子命令，
均支持 `--input` 与 `--output`，fit 的输出工件可被 apply 直接读取。方法、适用范围、原因码与完整
运行例子见 `docs/questionnaire_linking.md`。

```bash
python3 -m intake_hub.questionnaire fit \
  --input fixtures/questionnaire_link.json --output /tmp/mapping-v1.json
python3 -m intake_hub.questionnaire apply \
  --input fixtures/questionnaire_batch2.json --mapping /tmp/mapping-v1.json \
  --output /tmp/batch2-report.json
```

要点：未展示/展示未答/拒绝授权三态区分且不零填；桥接非随机，仅在通过共同题漂移检验的
group×branch 单元内做锚点恒等衔接，不支持的单元保留两个量尺；映射工件带版本、适用范围与
内容指纹，apply 不重新拟合，历史映射不会被新批次改写；`confirmed_demand` 仅用于 as-of 趋势校验，
不参与任何分数拟合。

## 运行

```bash
python3 -m intake_hub.gateway.command --fixture fixtures/demo.json --database /tmp/intake_hub-demo.sqlite3
python3 scripts/check_project.py
```

`check_project.py` 串行执行测试、编译和真实业务入口，将本次业务输出完整打印到终端；状态文件使用独立临时目录，重复运行不沿用上次状态。

批量分析资料位于 `fixtures/questionnaire_link.json`，字段含义见 `docs/data.md`。它与当前单笔流程的输入格式不同，不能直接交给原来的入口处理。
