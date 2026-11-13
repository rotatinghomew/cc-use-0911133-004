"""问卷量尺衔接分析包（仅分析用途，不改变原有授权与分流规则）。

模块划分：
- scoring: 原始分与共同题锚点分，严格区分未展示、未答、拒绝授权三种状态；
- linking: 桥接样本诊断、共同题漂移检验、适用范围（cell）判定与映射拟合；
- trends:  队列需求量趋势、as-of 确认可见性、原始下降的形式/人群分解；
- pipeline: fit/apply 编排与落盘工件（含内容指纹，保证历史映射不被新批次改写）；
- cli:     ``python -m intake_hub.questionnaire fit|apply`` 子命令入口。
"""

from intake_hub.questionnaire.pipeline import apply, fit, load_artifact, write_artifact

__all__ = ["fit", "apply", "load_artifact", "write_artifact"]
