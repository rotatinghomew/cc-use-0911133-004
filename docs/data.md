# 业务样例数据

`fixtures/questionnaire_link.json` 使用本地合成数据，不含真实个人资料。

displayed 是实际呈现的题目；answers 中 null 是未回答，不在 displayed 中则是未展示。bridges 是同一人双版本记录；known_at 约束人工确认的可见日期。

`fixtures/demo.json` 是当前单笔业务流程的样例，和批量分析资料分开保存。
