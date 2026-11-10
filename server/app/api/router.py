"""API 路由聚合。

后续任务卡的领域路由（questions / papers / exams / sessions / reports / audit）
在这里 `include_router`，对外统一位于 `/api` 前缀下。

TASK-BE-001 起：聚合路由器挂全局 CSRF 依赖（非 GET 请求校验 X-CSRF-Token，
登录接口与安全方法由 `enforce_csrf` 内部豁免），并注册 auth / users 路由；
TASK-BE-002 起注册 questions 路由；
TASK-BE-003 起注册 papers 路由；
TASK-BE-004 起注册 exams / sessions / me 路由（`/me/exams` 由 sessions 模块提供）；
TASK-BE-005 起注册 reports 路由（`/sessions/{id}/result`、`/me/results`、
`/me/wrong-questions`、`/exams/{id}/results`）；
TASK-BE-006 起注册 audit 路由（`/audit-logs`，仅 admin）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.health import router as health_router
from app.audit.router import router as audit_router
from app.auth.csrf import enforce_csrf
from app.auth.router import router as auth_router
from app.exams.router import router as exams_router
from app.papers.router import router as papers_router
from app.questions.router import router as questions_router
from app.reports.router import (
    exams_results_router,
    me_reports_router,
    sessions_result_router,
)
from app.sessions.router import exam_sessions_router, me_router
from app.sessions.router import router as sessions_router
from app.users.router import router as users_router

api_router = APIRouter(dependencies=[Depends(enforce_csrf)])
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(questions_router)
api_router.include_router(papers_router)
api_router.include_router(exams_router)
api_router.include_router(exam_sessions_router)
api_router.include_router(sessions_router)
api_router.include_router(me_router)
api_router.include_router(sessions_result_router)
api_router.include_router(me_reports_router)
api_router.include_router(exams_results_router)
api_router.include_router(audit_router)

__all__ = ["api_router"]
