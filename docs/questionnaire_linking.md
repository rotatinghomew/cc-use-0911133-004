# 问卷量尺衔接（fit / apply）方法与运行说明

社区把到院前服务需求问卷从统一长表（`long`，4 题）改成分支短表（`short`，3 题起，按分支再裁剪）。
本模块只做**分析用途的量尺衔接与趋势校验**：不输出疾病风险，不读取也不修改 intake_hub 既有的
授权、问答采集与人工分流规则。入口为 `python -m intake_hub.questionnaire`，含 `fit`、`apply` 两个子命令。

## 1. 三类缺失与授权的区别对待

原始数据里三种情况语义不同，代码（`intake_hub/questionnaire/scoring.py`）严格区分，**绝不用零分填满**：

| 情况 | 数据特征 | 处理 |
| --- | --- | --- |
| 未展示 | 题目不在 `displayed`（如 `skip-travel` 分支没有 travel） | 记 `not_displayed`，不计分、不插补 |
| 展示未答 | 题目在 `displayed` 但 `answers` 为 `null` | 记 `displayed_unanswered`，不计分、不当 0 |
| 已答 | `answers` 为取值范围内整数 | 正常计入 |

- 原始分 `raw_score` 只对**已答题**求和，并返回答题数与缺失清单，因此缺失不会被悄悄当作 0。
- 共同题锚点分采用 complete-case：任一共同题未展示或未答，该记录**不产出可比较分**并给出结构化原因。
- 拒绝分析授权（`analysis_consent=false`）的记录：不参与拟合，apply 时不产出任何派生分数
  （原始分也置空），但**保留在队列量（volume）计数里**，业务不会因为不换算而漏掉这些人。

## 2. 衔接方案的选择

可选方案包括全样本回归换算、观察分配等值、共同题锚点等。本资料的桥接样本只有 36 对、
非随机招募（全部来自 `service-desk`），且存在群体无共同题覆盖、共同题改版漂移，因此：

- **不**在 33 人小样本上回归一张全人群换算表（样本量与招募方式都不支持外推）；
- 采用**共同题锚点恒等映射**：可比较分 = 共同题（contact/travel/digital）已答分之和，
  两版使用同一定义；桥接样本不用于"拟合系数"，而用于**检验恒等假设在哪个群体×分支单元成立**；
- 检验不通过或无桥接覆盖的单元，**显式保留 long/short 两个独立量尺**，只报原始分与拒绝原因，
  不强行换算。

### 支持比较的群体（cell = group × branch）

对每个单元依次检查：是否有桥接覆盖、共同题 complete-case 对子数是否达到最小样本量（5）、
共同题是否通过漂移检验、共同题轮廓是否逐题恒等。本批数据结论：

| group | branch | 结论 | 依据 |
| --- | --- | --- | --- |
| urban | full | **支持单一锚点量尺** | 24 个 complete-case 对子共同题逐题相等率 100%，各题配对均差 0 |
| remote | full | 保留双量尺 | contact 题漂移：7 对中 5 对不一致，配对均差 +1.14，95% CI 不含 0 |
| urban | skip-travel | 保留双量尺 | 桥接全部是 full→full，该分支无任何桥接（分支缺失） |
| remote | skip-travel | 保留双量尺 | 分支缺失 + contact 漂移 + 样本不足 |
| new-region | other（`third` 版本） | 不支持 | 版本间无共同题、无桥接，记录为 `unsupported_version` |

### 假设

1. 锚点恒等假设：通过检验的单元内，共同题在两版测量同一构念、取值含义不变；
2. 局部可推广假设：service-desk 招募的桥接者可代表同 group×branch 单元内的常规应答者；
3. complete-case 假设：共同题不完整的记录退出换算而非插补；
4. 稳定性假设：题目措辞与分支逻辑在拟合后不变，任何题目改版都必须重新拟合新版本映射。

### 不确定性

- 桥接样本小且非随机，结论**仅限覆盖单元内**，不覆盖招募选择偏差；
- bootstrap 95% 区间只反映桥接样本内变异；
- 漂移单元无法区分跨版差异是真实变化还是测量漂移，因此保留双量尺。

## 3. 四项必做检验（fit 工件的 `checks` 区）

1. **桥接断开（bridge_disconnect）**：36 对中 3 对（bridge-0/17/34）因任一侧拒绝授权被排除，
   33 对进入分析；并记录招募渠道，提示禁止外推。
2. **分支缺失（branch_coverage）**：桥接只有 `full->full`，30 条 `skip-travel` 记录所在分支
   没有桥接覆盖，对应单元判 `bridge_branch_missing`。
3. **共同题漂移（common_item_drift）**：按 group×共同题做配对差值（short−long）检验，
   闸门为不一致率 ≥ 0.25 或 |均差| ≥ 0.5；remote 的 contact 被判漂移。
4. **未授权样本（consent）**：列出全部拒绝授权记录并声明其只进队列量、不进任何分析分。

`confirmed_demand`（服务意愿人工确认）**完全不进入 fit**（工件 `calibration_inputs_excluded`
显式声明），避免用未来确认结果校准同一批人的当期分数。

## 4. 映射工件的版本、适用范围与不可变性

`fit` 输出（即 `apply --mapping` 读取的工件）包含：

- `id` / `mapping_version` / `schema_version` / `fit_known_through` / `source_seed`；
- `anchor_scale`：锚点量尺定义、题目与取值范围（共同题 3 题，0–9）；
- `applicability`：可机读的适用范围清单（本批仅 urban/full 的 long、short）；
- `cells`：每个 group×branch 的支持判定、阻断原因、恒等检验与量尺策略；
- `form_effect`：被移除题目（availability）的人均贡献及 bootstrap 区间；
- `checks` / `assumptions` / `uncertainties`：上述检验、假设与不确定性。

不可变性保证：

- 工件写入 `content_fingerprint`（对其余内容的规范化 SHA-256）；
- `apply` 只读取工件，**不会**用新批次重新拟合或修改任何内容；新批次中超出适用范围的记录
  进入 `out_of_scope` 并逐条给原因；
- 加载时校验指纹，工件被改动直接报错退出（退出码 2）；
- 同一份输入重复 fit 得到相同指纹（确定性 bootstrap，种子来自资料 `seed`）。

## 5. 趋势与原始下降分解

- `weekly_trends`：按 ISO 周汇总队列量（含拒绝授权与未换算记录）、版本/分支分布、
  授权者原始均分、可比较锚点均分。
- `demand_trend`：as-of 口径的人工确认序列。仅当 `known_at <= as_of` 时确认结果才算可见，
  否则计 `pending`；默认 `as_of` 取工件的 `fit_known_through`，可用 `--as-of` 显式覆盖。
  确认结果只影响趋势区，永远不影响个人分数。
- `raw_decline_reconciliation`：在支持单元内把 long→short 原始均分差拆成
  **形式效应**（被移除题目贡献，约 1.38 分/人）与**锚点人群变化**（约 +0.16，接近 0）。
  结论：本批原始分下降主要来自问卷形式（少了 availability 题），而非人群服务需求变化——
  这与咨询量未下降的业务观察一致。

## 6. 原因码词汇

| 原因码 | 含义 |
| --- | --- |
| `analysis_consent_refused` | 拒绝分析授权，不产出派生分数，仅计队列量 |
| `unsupported_version` | 版本不在锚点量尺覆盖范围（如 third） |
| `group_not_bridged` | 该人群无桥接覆盖 |
| `bridge_branch_missing` | 该分支无桥接覆盖（如 skip-travel） |
| `common_item_drift` | 共同题在该人群改版后含义漂移，保留双量尺 |
| `common_item_incomplete` | 共同题展示但未答（null），complete-case 不满足 |
| `insufficient_bridge_sample` | 单元内可用桥接对子低于最小样本量 |

## 7. 完整运行例子

```bash
# 1) 拟合并保存带版本与适用范围的映射（工件带内容指纹）
python -m intake_hub.questionnaire fit \
  --input fixtures/questionnaire_link.json \
  --output /tmp/qrun/mapping-v1.json

# 2) 把同一份已保存映射应用到 fit 之后收集的新批次（不重新拟合）
python -m intake_hub.questionnaire apply \
  --input fixtures/questionnaire_batch2.json \
  --mapping /tmp/qrun/mapping-v1.json \
  --output /tmp/qrun/batch2-report.json

# 3) 需要更晚的确认可见口径时显式传 as-of（仍不改变任何个人分数）
python -m intake_hub.questionnaire apply \
  --input fixtures/questionnaire_batch2.json \
  --mapping /tmp/qrun/mapping-v1.json \
  --as-of 2026-09-30 \
  --output /tmp/qrun/batch2-report-late.json
```

新批次（`fixtures/questionnaire_batch2.json`，5 条）的逐条结果：

| record | 处理 | 结果 |
| --- | --- | --- |
| batch2-001（urban/full，授权，共同题完整） | 换算 | raw=4，comparable=4 |
| batch2-002（urban/skip-travel） | 拒绝换算 | `bridge_branch_missing`（travel 未展示，无桥接） |
| batch2-003（remote/full） | 拒绝换算 | `common_item_drift`（remote contact 漂移，保留双量尺） |
| batch2-004（urban/full，未授权） | 不产出分数 | `analysis_consent_refused`，仍计入周队列量 |
| batch2-005（third/new-region） | 不支持 | `unsupported_version`，并进入报告 `out_of_scope` |

安全性质验证：

```bash
# 篡改保存的映射 -> 指纹校验失败，apply 拒绝执行（退出码 2）
python - <<'PY'
import json
m = json.load(open('/tmp/qrun/mapping-v1.json'))
m['anchor_scale']['range'] = [0, 99]
json.dump(m, open('/tmp/qrun/mapping-tampered.json', 'w'))
PY
python -m intake_hub.questionnaire apply \
  --input fixtures/questionnaire_batch2.json \
  --mapping /tmp/qrun/mapping-tampered.json \
  --output /tmp/qrun/x.json   #  stderr: 映射工件内容指纹不匹配，退出码 2

# 对没有 bridges/common_items 的新批次直接 fit 会被拒绝（输入错误，退出码 1），
# 新批次只能走 apply；apply 前后映射文件字节不变。
```

## 8. 测试

```bash
python -m unittest discover -s tests/questionnaire_linking -v
python scripts/check_project.py   # 含全部单测、编译与 fit/apply 冒烟
```
