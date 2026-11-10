"""IDOR 与防枚举负例（TASK-QA-002；docs/12 v1.3 §2/§2.0；AC-18 / AC-19）。

核心口径（docs/12 §2 判定顺序，v1.0.3 / CHG-036 定稿）：
- 第 3 步：角色不允许 → 403 ``FORBIDDEN``（例：teacher 读单会话结果，CHG-030）；
- 第 4 步：角色允许、资源可见但非本人 → 403（学生 B 读学生 A 的会话/成绩；
  **教师同角色他人的题目/试卷/考试，含 `draft`，也是 403，不得 404**）；
- 第 5 步：资源不可见/不存在 → 404 ``NOT_FOUND``（**学生视角**的 draft 考试、任意不存在 id）。

docs/13 §8.1.2 第 3 条对应的运行期用例在本文件：
- 学生视角：``test_draft_exam_session_returns_404_for_student`` +
  ``test_my_exams_excludes_draft_exam``；
- 教师同角色：``test_teacher_b_cannot_read_teacher_a_draft_paper`` /
  ``...cannot_update...draft_paper`` / ``...cannot_delete...draft_paper`` /
  ``...cannot_read_teacher_a_draft_exam`` / ``...cannot_patch_teacher_a_draft_exam``。
这些用例同时关闭了 ``tests/security/README.md`` §6.11 记录的“他人 draft 资源 403/404”张力。

所有 403/404 都必须是统一 ``Error{code,message,request_id}``，不得夹带
``items/score/selected/correct_answer`` 等业务字段（契约 Forbidden/NotFound description）。

未设置 ``EXAM_API_URL`` 时整模块 skip；缺少 seed 账号时相关用例 skip。
"""

from __future__ import annotations

import os

import pytest

from conftest import assert_error, assert_json

pytestmark = pytest.mark.skipif(
    not os.environ.get("EXAM_API_URL", "").strip(),
    reason="EXAM_API_URL 未设置：安全负例需要运行中的 API（见 tests/security/README.md）",
)


# --------------------------------------------------------------------------- #
# 学生 B 读/写学生 A 的会话（AC-18；matrix §2.1 #1）
# --------------------------------------------------------------------------- #
STUDENT_IDOR_READ_CASES: list[tuple[str, str, str]] = [
    ("GET /sessions/{id}", "GET", "/sessions/{session_id}"),
    ("GET /sessions/{id}/result", "GET", "/sessions/{session_id}/result"),
    ("POST /sessions/{id}/submit", "POST", "/sessions/{session_id}/submit"),
]


@pytest.mark.parametrize(
    "case_id,method,path_template",
    STUDENT_IDOR_READ_CASES,
    ids=[case[0] for case in STUDENT_IDOR_READ_CASES],
)
def test_student_b_cannot_access_student_a_session(
    student_b, victim_scenario, case_id: str, method: str, path_template: str
) -> None:
    path = path_template.format(session_id=victim_scenario.session_id)
    response = student_b.request(method, path)
    assert_error(response, 403, "FORBIDDEN", label=case_id)
    for marker in ("selected", "correct_answer", "score", "items", "questions"):
        assert marker not in response.text, (
            f"{case_id}: 403 响应不得夹带业务字段 {marker!r}：{response.text[:400]}"
        )


def test_student_b_cannot_save_answers_to_student_a_session(
    student_b, victim_scenario
) -> None:
    response = student_b.put(
        f"/sessions/{victim_scenario.session_id}/answers",
        json_body={
            "client_seq": 2,
            "answers": [
                {"question_id": int(victim_scenario.question["id"]), "selected": ["A"]},
            ],
        },
    )
    assert_error(
        response,
        403,
        "FORBIDDEN",
        label="PUT /sessions/{id}/answers（学生 B 写学生 A 会话）",
    )


def test_student_b_cannot_read_student_a_result_without_business_data(
    student_b, victim_scenario
) -> None:
    """AC-18 硬性要求：IDOR 返回 403，且响应体只有 Error 三字段。"""
    response = student_b.get(f"/sessions/{victim_scenario.session_id}/result")
    body = assert_error(response, 403, "FORBIDDEN", label="AC-18 result IDOR")
    assert "message" in body and "request_id" in body


# --------------------------------------------------------------------------- #
# 教师读单会话结果：角色判定先于归属（CHG-030 / matrix §2.1 #9）
# --------------------------------------------------------------------------- #
def test_teacher_cannot_read_single_session_result_even_own_exam(
    teacher, victim_scenario
) -> None:
    response = teacher.get(f"/sessions/{victim_scenario.session_id}/result")
    assert_error(
        response,
        403,
        "FORBIDDEN",
        label="GET /sessions/{id}/result（teacher，CHG-030；不得 404/200）",
    )


# --------------------------------------------------------------------------- #
# 教师 B 读/改教师 A 的题目、试卷、考试（matrix §2.1 #2）
# --------------------------------------------------------------------------- #
TEACHER_B_READ_CASES: list[tuple[str, str]] = [
    ("GET /questions/{id}", "/questions/{question_id}"),
    ("GET /papers/{id}", "/papers/{paper_id}"),
    ("GET /exams/{id}", "/exams/{exam_id}"),
    ("GET /exams/{id}/results", "/exams/{exam_id}/results"),
]


@pytest.mark.parametrize(
    "case_id,path_template",
    TEACHER_B_READ_CASES,
    ids=[case[0] for case in TEACHER_B_READ_CASES],
)
def test_teacher_b_cannot_read_teacher_a_resources(
    teacher_b, victim_scenario, case_id: str, path_template: str
) -> None:
    path = path_template.format(
        question_id=victim_scenario.question["id"],
        paper_id=victim_scenario.paper["id"],
        exam_id=victim_scenario.exam["id"],
    )
    response = teacher_b.api.get(path)
    assert_error(response, 403, "FORBIDDEN", label=case_id)


def test_teacher_b_cannot_update_teacher_a_question(teacher_b, victim_scenario) -> None:
    question = victim_scenario.question
    # 使用原值作为 payload：即使归属校验失效，写入的也是同内容，不污染证据数据。
    payload = {
        key: question[key]
        for key in ("type", "stem", "options", "answer", "explanation", "difficulty", "tags")
    }
    response = teacher_b.api.put(
        f"/questions/{question['id']}", json_body=payload
    )
    assert_error(
        response,
        403,
        "FORBIDDEN",
        label="PUT /questions/{id}（教师 B 改教师 A 的题）",
    )


def test_teacher_b_cannot_update_teacher_a_paper(teacher_b, victim_scenario) -> None:
    paper = victim_scenario.paper
    payload = {
        "title": paper["title"],
        "questions": [
            {
                "question_id": item["question_id"],
                "order_no": item["order_no"],
                "points": item["points"],
                "partial_rule": item.get("partial_rule", "all_or_nothing"),
            }
            for item in paper["questions"]
        ],
    }
    response = teacher_b.api.put(f"/papers/{paper['id']}", json_body=payload)
    assert_error(
        response,
        403,
        "FORBIDDEN",
        label="PUT /papers/{id}（教师 B 改教师 A 的卷）",
    )


def test_teacher_b_cannot_delete_teacher_a_question_and_paper(
    teacher_b, victim_scenario
) -> None:
    question_response = teacher_b.api.delete(
        f"/questions/{victim_scenario.question['id']}"
    )
    assert_error(
        question_response,
        403,
        "FORBIDDEN",
        label="DELETE /questions/{id}（教师 B 删教师 A 的题）",
    )

    paper_response = teacher_b.api.delete(f"/papers/{victim_scenario.paper['id']}")
    assert_error(
        paper_response,
        403,
        "FORBIDDEN",
        label="DELETE /papers/{id}（教师 B 删教师 A 的卷）",
    )


def test_teacher_b_cannot_patch_teacher_a_exam(teacher_b, victim_scenario) -> None:
    response = teacher_b.api.patch(
        f"/exams/{victim_scenario.exam['id']}",
        json_body={"title": victim_scenario.exam["title"]},
    )
    assert_error(
        response,
        403,
        "FORBIDDEN",
        label="PATCH /exams/{id}（教师 B 改教师 A 的考试）",
    )


# --------------------------------------------------------------------------- #
# 教师 B 对教师 A 的 draft 资源：403，**不得 404**（CHG-036 / matrix §2.1 #2/#11）
# --------------------------------------------------------------------------- #
# 口径来源：docs/12 v1.3 §2.0（教师同角色可见域含 draft → 第 4 步 403）与
# docs/13 §8.1.2 第 3 条。断言 403 即同时证明“不是 404”，失败信息会显式指出这一点。
def test_teacher_b_cannot_read_teacher_a_draft_paper(teacher_b, victim_scenario) -> None:
    """GET /papers/{id} 指向教师 A 的 draft 试卷 → 403（教师管理视图可见，不得因未发布 404）。"""
    paper = victim_scenario.draft_paper
    assert paper.get("status") == "draft", (
        f"前置试卷应为 draft，实际 {paper.get('status')!r}"
    )
    response = teacher_b.api.get(f"/papers/{paper['id']}")
    assert_error(
        response,
        403,
        "FORBIDDEN",
        label="GET /papers/{id}（教师 B 读教师 A 的 draft 试卷；CHG-036：不得 404）",
    )


def test_teacher_b_cannot_update_teacher_a_draft_paper(teacher_b, victim_scenario) -> None:
    """PUT /papers/{id} 指向教师 A 的 draft 试卷 → 403（归属判定先于业务校验，不是 409/404）。"""
    paper = victim_scenario.draft_paper
    payload = {
        "title": paper["title"],
        "questions": [
            {
                "question_id": item["question_id"],
                "order_no": item["order_no"],
                "points": item["points"],
                "partial_rule": item.get("partial_rule", "all_or_nothing"),
            }
            for item in paper["questions"]
        ],
    }
    response = teacher_b.api.put(f"/papers/{paper['id']}", json_body=payload)
    assert_error(
        response,
        403,
        "FORBIDDEN",
        label="PUT /papers/{id}（教师 B 改教师 A 的 draft 试卷；CHG-036：不得 404/409）",
    )


def test_teacher_b_cannot_delete_teacher_a_draft_paper(teacher_b, victim_scenario) -> None:
    """DELETE /papers/{id} 指向教师 A 的 draft 试卷 → 403。"""
    paper = victim_scenario.draft_paper
    response = teacher_b.api.delete(f"/papers/{paper['id']}")
    assert_error(
        response,
        403,
        "FORBIDDEN",
        label="DELETE /papers/{id}（教师 B 删教师 A 的 draft 试卷；CHG-036：不得 404）",
    )


def test_teacher_b_cannot_read_teacher_a_draft_exam(teacher_b, victim_scenario) -> None:
    """GET /exams/{id} 指向教师 A 的 draft 考试 → 403（不得 404）。"""
    exam = victim_scenario.draft_exam
    assert exam.get("status") == "draft", f"前置考试应为 draft，实际 {exam.get('status')!r}"
    response = teacher_b.api.get(f"/exams/{exam['id']}")
    assert_error(
        response,
        403,
        "FORBIDDEN",
        label="GET /exams/{id}（教师 B 读教师 A 的 draft 考试；CHG-036：不得 404）",
    )


def test_teacher_b_cannot_patch_teacher_a_draft_exam(teacher_b, victim_scenario) -> None:
    """PATCH /exams/{id} 指向教师 A 的 draft 考试 → 403（不得 404/409）。"""
    exam = victim_scenario.draft_exam
    response = teacher_b.api.patch(
        f"/exams/{exam['id']}",
        json_body={"title": exam["title"]},
    )
    assert_error(
        response,
        403,
        "FORBIDDEN",
        label="PATCH /exams/{id}（教师 B 改教师 A 的 draft 考试；CHG-036：不得 404/409）",
    )


# --------------------------------------------------------------------------- #
# 防枚举：不可见/不存在 → 404（matrix §2.1 #4/#7；docs/12 §2 第 5 步）
# --------------------------------------------------------------------------- #
def test_draft_exam_session_returns_404_for_student(student_a, victim_scenario) -> None:
    """matrix §2.1 #4/#13：学生创建 draft 考试的会话 → 404，不是 409/403。"""
    response = student_a.post(
        f"/exams/{victim_scenario.draft_exam['id']}/sessions"
    )
    assert_error(
        response,
        404,
        "NOT_FOUND",
        label="POST /exams/{draft}/sessions（学生；防枚举）",
    )


def test_my_exams_excludes_draft_exam(student_a, victim_scenario) -> None:
    """CHG-036 / matrix §2.1 #13：`GET /me/exams` 不得包含 draft 考试。

    学生可见域 = 已发布考试 + 自己已考的 closed；draft 属“不可见”（第 5 步），
    因此既不能出现在列表里，也不能作为 403 泄露其存在（建会话是 404）。
    """
    draft_exam_id = int(victim_scenario.draft_exam["id"])
    page_size = 100
    visible_ids: set[int] = set()
    seen = 0
    for page in range(1, 6):  # 最多 5 页（500 条）：draft 若泄漏会出现在某一页
        body = assert_json(
            student_a.get(
                "/me/exams", params={"page": page, "page_size": page_size}
            ),
            200,
            label=f"GET /me/exams?page={page}（CHG-036）",
        )
        items = list(body.get("items") or [])
        visible_ids.update(int(item["exam_id"]) for item in items)
        seen += len(items)
        if not items or seen >= int(body.get("total") or 0):
            break

    assert visible_ids, "学生可见考试列表为空：无法证明 draft 被过滤（先确认 seed/发布数据）"
    assert draft_exam_id not in visible_ids, (
        f"GET /me/exams 返回了 draft 考试 {draft_exam_id}（学生视角不可见，CHG-036）；"
        f"可见 {sorted(visible_ids)}"
    )


@pytest.mark.parametrize(
    "path",
    ["/sessions/999999999", "/sessions/999999999/result"],
    ids=["missing-session", "missing-result"],
)
def test_nonexistent_session_returns_404(student_a, path: str) -> None:
    """matrix §2.1 #7：不存在 id 的会话/成绩 → 404，且仅 Error 三字段。"""
    response = student_a.get(path)
    assert_error(response, 404, "NOT_FOUND", label=path)


def test_403_and_404_use_same_error_shape(student_b, student_a, victim_scenario) -> None:
    """403 和 404 的响应体结构必须完全相同（docs/12 §2 要点 3）。"""
    forbidden = student_b.get(f"/sessions/{victim_scenario.session_id}/result")
    not_found = student_a.get("/sessions/999999999/result")
    forbidden_body = assert_error(
        forbidden, 403, "FORBIDDEN", label="403 形状"
    )
    not_found_body = assert_error(
        not_found, 404, "NOT_FOUND", label="404 形状"
    )
    assert set(forbidden_body) == set(not_found_body) == {
        "code",
        "message",
        "request_id",
    }


# --------------------------------------------------------------------------- #
# 正例：确保“本人/教师汇总/admin”没有被误伤（与上面的负例互补）
# --------------------------------------------------------------------------- #
def test_owner_can_read_own_session_and_result(student_a, victim_scenario) -> None:
    my_exams = assert_json(
        student_a.get("/me/exams", params={"page": 1, "page_size": 100}),
        200,
        label="GET /me/exams（学生本人）",
    )
    assert "server_time" in my_exams, "MyExamPage.server_time 必填（CHG-024）"

    session = assert_json(
        student_a.get(f"/sessions/{victim_scenario.session_id}"),
        200,
        label="GET /sessions/{id}（本人）",
    )
    assert int(session["id"]) == victim_scenario.session_id

    result = assert_json(
        student_a.get(f"/sessions/{victim_scenario.session_id}/result"),
        200,
        label="GET /sessions/{id}/result（本人）",
    )
    assert int(result["session_id"]) == victim_scenario.session_id
    assert result["items"], "已交卷会话的逐题结果不应为空"


def test_teacher_owner_and_admin_can_read_exam_results(
    teacher, admin, victim_scenario
) -> None:
    exam_id = int(victim_scenario.exam["id"])

    teacher_summary = assert_json(
        teacher.get(f"/exams/{exam_id}/results"),
        200,
        label="GET /exams/{id}/results（创建者）",
    )
    assert int(teacher_summary["exam_id"]) == exam_id
    assert teacher_summary["total_sessions"] >= 1
    assert teacher_summary["submitted"] >= 1

    admin_summary = assert_json(
        admin.get(f"/exams/{exam_id}/results"),
        200,
        label="GET /exams/{id}/results（admin）",
    )
    assert int(admin_summary["exam_id"]) == exam_id

    admin_result = assert_json(
        admin.get(f"/sessions/{victim_scenario.session_id}/result"),
        200,
        label="GET /sessions/{id}/result（admin）",
    )
    assert int(admin_result["session_id"]) == victim_scenario.session_id
