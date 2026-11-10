"""选择题答题系统后端应用包。

TASK-BE-000 只提供脚手架：应用工厂、配置、错误模型、数据库会话与健康检查。
业务模块（auth / users / questions / papers / exams / sessions / scoring /
reports / audit）由 TASK-BE-001 起的任务卡按 docs/10-arch.md 的模块边界新增。
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__"]
