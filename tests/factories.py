"""tests/factories.py — 独立测试数据工厂（TASK-QA-000 §3.3）

设计约束
--------
1. **不导入生产代码**：只用标准库，`server/` 与 `web/` 重构不影响本文件；
2. **只产出契约 payload（dict）**：落库方式由调用方决定（API / 直连 DB / fixtures）；
3. **确定性**：同一 ``seed`` → 同一数据；跨 run 由 ``run_id`` 前缀区分，不靠删库保证唯一；
4. **覆盖契约允许的题型**：``single`` / ``multiple`` / ``judge``，多选两档给分规则；
5. 时间统一 UTC + ISO 8601（契约 info.description L12）；
6. 不依赖 pytest，可在 pytest / Playwright / 一次性脚本中复用。

示例
----
>>> from factories import build_exam_scenario
>>> s = build_exam_scenario(seed=42)
>>> s.teacher.username
'qa-...-teacher-01'
>>> sum(q["points"] for q in s.paper["questions"])
100.0
>>> s.exam_live["duration_min"]
30
"""

from __future__ import annotations

import csv
import io
import random
import string
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "PAPER_TOTAL_POINTS",
    "DEFAULT_PASSWORD",
    "QUESTION_TYPES",
    "Account",
    "AccountFactory",
    "QuestionFactory",
    "PaperFactory",
    "ExamFactory",
    "ImportFileFactory",
    "AnswerScript",
    "ExamScenario",
    "build_exam_scenario",
    "utc_now",
    "iso",
    "new_run_id",
    "split_points",
]

PAPER_TOTAL_POINTS = 100.0
DEFAULT_PASSWORD = "Str0ng-Passw0rd!2026"
QUESTION_TYPES = ("single", "multiple", "judge")
_OPTION_KEYS = ("A", "B", "C", "D")
JUDGE_OPTIONS = ({"key": "A", "text": "对"}, {"key": "B", "text": "错"})


# --------------------------------------------------------------------------- #
# 时间与通用工具
# --------------------------------------------------------------------------- #
def utc_now() -> datetime:
    """当前 UTC 时间（秒级），测试断言允许 ±5s 漂移。"""
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    """契约要求 ISO 8601 UTC（``2026-11-08T12:00:00+00:00``）。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def new_run_id() -> str:
    """本次运行的隔离前缀，避免并行 CI job 互相踩数据。"""
    return f"{int(time.time())}-{uuid.uuid4().hex[:6]}"


def _rand_suffix(rng: random.Random, n: int = 6) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(rng.choice(alphabet) for _ in range(n))


def split_points(total: float, count: int) -> List[float]:
    """把 ``total`` 均分为 ``count`` 份，保证 **合计严格等于 total** 且最多 2 位小数。

    余数用“分”为单位补到前几项，避免浮点误差让 AC-09（合计 100）偶发失败。
    """
    if count <= 0:
        raise ValueError("count must be > 0")
    cents = int(round(total * 100))
    base, rem = divmod(cents, count)
    parts = [base + (1 if i < rem else 0) for i in range(count)]
    return [round(c / 100.0, 2) for c in parts]


# --------------------------------------------------------------------------- #
# 账号
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Account:
    """一个可登录账号（契约 ``UserCreate`` 字段 + 明文密码，仅测试用）。"""

    username: str
    display_name: str
    role: str
    password: str = DEFAULT_PASSWORD

    def payload(self) -> Dict[str, Any]:
        """``POST /users`` 请求体（仅 admin 可用）。"""
        return {
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "password": self.password,
        }

    def login_payload(self, password: Optional[str] = None) -> Dict[str, str]:
        """``POST /auth/login`` 请求体。"""
        return {"username": self.username, "password": password or self.password}


class AccountFactory:
    """生成三角色账号 + 停用账号 + IDOR 双账号（AC-01/03/04/18）。"""

    def __init__(self, seed: int = 42, run_id: Optional[str] = None, prefix: str = "qa") -> None:
        self.seed = seed
        self.run_id = run_id or new_run_id()
        self.prefix = prefix
        self._rng = random.Random(seed)
        self._counter = 0

    def _username(self, role: str) -> str:
        self._counter += 1
        return f"{self.prefix}-{self.run_id}-{role}-{self._counter:02d}"

    def _account(self, role: str, display: str) -> Account:
        return Account(
            username=self._username(role),
            display_name=f"{display}-{_rand_suffix(self._rng, 4)}",
            role=role,
            password=DEFAULT_PASSWORD,
        )

    # --- 常用单账号 ------------------------------------------------------- #
    def teacher(self) -> Account:
        return self._account("teacher", "QA教师")

    def student(self) -> Account:
        return self._account("student", "QA学生")

    def admin(self) -> Account:
        return self._account("admin", "QA管理员")

    def disabled_student(self) -> Account:
        """创建后需 admin 调 ``PATCH /users/{id}`` 置 ``is_active=false``（AC-04）。"""
        return self._account("student", "QA停用学生")

    # --- 组合 ------------------------------------------------------------- #
    def idor_pair(self) -> "tuple[Account, Account]":
        """返回 (受害者 A, 攻击者 B)，用于 AC-18 / matrix §2.1。"""
        return self._account("student", "QA学生A"), self._account("student", "QA学生B")

    def trio(self) -> Dict[str, Account]:
        return {"teacher": self.teacher(), "student": self.student(), "admin": self.admin()}

    def audit_matrix(self) -> Dict[str, Account]:
        """AC-21 用：teacher 查审计应 403，admin 应 200。"""
        return {"teacher": self.teacher(), "admin": self.admin()}

    def all_roles(self) -> List[Account]:
        return [self.teacher(), self.student(), self.admin()]


# --------------------------------------------------------------------------- #
# 题目
# --------------------------------------------------------------------------- #
def _options(count: int = 4) -> List[Dict[str, str]]:
    return [{"key": k, "text": f"选项{k}"} for k in _OPTION_KEYS[:count]]


class QuestionFactory:
    """生成契约 ``QuestionCreate`` payload（三种题型，答案合法）。"""

    def __init__(self, seed: int = 42, run_id: Optional[str] = None) -> None:
        self._rng = random.Random(seed)
        self.run_id = run_id or new_run_id()
        self._counter = 0

    def _stem(self, kind: str) -> str:
        self._counter += 1
        return f"[{self.run_id}] {kind} 题 {self._counter:03d}：{_rand_suffix(self._rng, 8)}"

    def single(self) -> Dict[str, Any]:
        options = _options(4)
        answer = [self._rng.choice([o["key"] for o in options])]
        return {
            "type": "single",
            "stem": self._stem("single"),
            "options": options,
            "answer": answer,
            "explanation": "单选题解析：正确答案见 answer 字段。",
            "difficulty": self._rng.randint(1, 5),
            "tags": ["qa", "l1"],
        }

    def multiple(self, partial_rule: str = "all_or_nothing") -> Dict[str, Any]:
        """多选：默认 2 个正确项；``partial_rule`` ∈ {all_or_nothing, partial}（AC-17）。"""
        if partial_rule not in ("all_or_nothing", "partial"):
            raise ValueError("partial_rule must be 'all_or_nothing' or 'partial'")
        options = _options(4)
        keys = [o["key"] for o in options]
        correct = sorted(self._rng.sample(keys, 2))
        q = {
            "type": "multiple",
            "stem": self._stem("multiple"),
            "options": options,
            "answer": correct,
            "explanation": "多选题解析：少选且无错选时按比例给分。",
            "difficulty": self._rng.randint(1, 5),
            "tags": ["qa", "l2"],
        }
        # partial_rule 属于试卷级配置（PaperQuestion），这里随题带出供工厂使用
        q["_partial_rule"] = partial_rule
        return q

    def judge(self) -> Dict[str, Any]:
        """判断题选项固定 A=对 / B=错（docs/11 §2）。"""
        return {
            "type": "judge",
            "stem": self._stem("judge"),
            "options": [dict(o) for o in JUDGE_OPTIONS],
            "answer": [self._rng.choice(["A", "B"])],
            "explanation": "判断题解析：A=对，B=错。",
            "difficulty": self._rng.randint(1, 5),
            "tags": ["qa", "l1"],
        }

    def by_type(self, kind: str) -> Dict[str, Any]:
        if kind == "single":
            return self.single()
        if kind == "multiple":
            return self.multiple()
        if kind == "judge":
            return self.judge()
        raise ValueError(f"unknown question type: {kind}")

    def batch(self, count: int = 10) -> List[Dict[str, Any]]:
        """默认构成：4 单选 + 3 多选（2 全对 / 1 部分）+ 3 判断，合计 10 题。"""
        plan = (
            ["single"] * 4
            + ["multiple"] * 3
            + ["judge"] * 3
        )
        if count != len(plan):
            plan = [QUESTION_TYPES[i % len(QUESTION_TYPES)] for i in range(count)]
        out: List[Dict[str, Any]] = []
        for i, kind in enumerate(plan):
            if kind == "multiple" and i % 3 == 2:
                out.append(self.multiple(partial_rule="partial"))
            else:
                out.append(self.by_type(kind))
        return out

    def wrong_answer_for(self, question: Dict[str, Any]) -> List[str]:
        """给出一个必定判错的选项组合（用于错题本/AC-16/20）。"""
        keys = [o["key"] for o in question["options"]]
        correct = set(question.get("answer", []))
        wrong = [k for k in keys if k not in correct]
        if question["type"] == "multiple":
            # 必须“有错选”才保证 0 分：真答案 + 一个错误项
            return sorted(correct) + wrong[:1]
        return wrong[:1] or keys[:1]


# --------------------------------------------------------------------------- #
# 试卷 / 考试
# --------------------------------------------------------------------------- #
class PaperFactory:
    """生成契约 ``PaperCreate``，``points`` 合计严格 100（AC-09）。"""

    def __init__(self, seed: int = 42, run_id: Optional[str] = None) -> None:
        self.run_id = run_id or new_run_id()
        self.questions = QuestionFactory(seed=seed, run_id=self.run_id)

    def build(
        self,
        question_count: int = 10,
        total_points: float = PAPER_TOTAL_POINTS,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        items = self.questions.batch(question_count)
        points = split_points(total_points, len(items))
        paper_questions: List[Dict[str, Any]] = []
        for idx, (q, pts) in enumerate(zip(items, points), start=1):
            entry: Dict[str, Any] = {
                "question_id": idx,  # 占位：真实 ID 由调用方在建题后回填
                "order_no": idx,
                "points": pts,
            }
            rule = q.get("_partial_rule")
            if q["type"] == "multiple" and rule:
                entry["partial_rule"] = rule
            paper_questions.append(entry)
        return {
            "title": title or f"[{self.run_id}] QA 试卷 {question_count} 题 / {total_points:g} 分",
            "questions": paper_questions,
            "_question_payloads": items,  # 非契约字段：建题时使用
            "_total_points": round(sum(points), 2),
        }

    @staticmethod
    def with_real_ids(paper: Dict[str, Any], question_ids: Sequence[int]) -> Dict[str, Any]:
        """把占位 question_id 换成真实 ID（建题 → 组卷的衔接点）。"""
        if len(question_ids) != len(paper["questions"]):
            raise ValueError("question_ids 数量与试卷题目数不一致")
        fixed = {k: v for k, v in paper.items() if not k.startswith("_")}
        fixed["questions"] = [
            {**entry, "question_id": qid}
            for entry, qid in zip(paper["questions"], question_ids)
        ]
        return fixed


class ExamFactory:
    """生成契约 ``ExamCreate``，内置四种时间窗预设（AC-10/11/14）。"""

    def __init__(self, run_id: Optional[str] = None, duration_min: int = 30) -> None:
        self.run_id = run_id or new_run_id()
        self.duration_min = duration_min

    def _base(self, paper_id: int, start: datetime, end: datetime, title: str) -> Dict[str, Any]:
        return {
            "paper_id": paper_id,
            "title": f"[{self.run_id}] {title}",
            "start_at": iso(start),
            "end_at": iso(end),
            "duration_min": self.duration_min,
            "shuffle_questions": False,
            "shuffle_options": False,
            "publish": True,
        }

    def live(self, paper_id: int = 1) -> Dict[str, Any]:
        now = utc_now()
        return self._base(paper_id, now - timedelta(minutes=5), now + timedelta(hours=2), "进行中考试")

    def future(self, paper_id: int = 1) -> Dict[str, Any]:
        """AC-11：未到开始时间。"""
        now = utc_now()
        return self._base(paper_id, now + timedelta(hours=1), now + timedelta(hours=2), "未开始考试")

    def expired(self, paper_id: int = 1) -> Dict[str, Any]:
        """AC-14：时长已到（start=now-3h，duration=30min）。"""
        now = utc_now()
        return self._base(paper_id, now - timedelta(hours=3), now - timedelta(hours=1), "已截止考试")

    def draft(self, paper_id: int = 1) -> Dict[str, Any]:
        """matrix §2.4：草稿考试，学生创建会话应 404/403。"""
        now = utc_now()
        payload = self._base(paper_id, now, now + timedelta(hours=2), "草稿考试")
        payload["publish"] = False
        return payload


# --------------------------------------------------------------------------- #
# 导入文件
# --------------------------------------------------------------------------- #
_CSV_HEADER = ["type", "stem", "option_a", "option_b", "option_c", "option_d", "answer", "explanation", "difficulty", "tags"]


class ImportFileFactory:
    """生成 CSV 导入文件（AC-06 部分成功 / AC-08 重复导入幂等）。"""

    def __init__(self, seed: int = 42, run_id: Optional[str] = None) -> None:
        self.run_id = run_id or new_run_id()
        self.questions = QuestionFactory(seed=seed, run_id=self.run_id)

    def rows(self, count: int = 3) -> List[List[str]]:
        out: List[List[str]] = []
        for q in self.questions.batch(count):
            opts = {o["key"]: o["text"] for o in q["options"]}
            out.append(
                [
                    q["type"],
                    q["stem"],
                    opts.get("A", ""),
                    opts.get("B", ""),
                    opts.get("C", ""),
                    opts.get("D", ""),
                    "|".join(q["answer"]),
                    q.get("explanation", ""),
                    str(q.get("difficulty", 3)),
                    ",".join(q.get("tags", [])),
                ]
            )
        return out

    def csv_bytes(
        self,
        count: int = 3,
        bad_row: bool = False,
        duplicate: bool = False,
        newline: str = "",
    ) -> bytes:
        """``bad_row=True`` 制造 1 行缺答案（缺列），``duplicate=True`` 追加重复行。"""
        rows = self.rows(count)
        if bad_row:
            broken = list(rows[0])
            broken[6] = ""  # answer 列为空 → 契约 AC-06 的“缺答案”
            rows = [broken] + rows[1:]
        if duplicate:
            rows = rows + [list(r) for r in rows[:1]]
        buf = io.StringIO(newline="")
        writer = csv.writer(buf)
        writer.writerow(_CSV_HEADER)
        writer.writerows(rows)
        return buf.getvalue().encode("utf-8")

    @staticmethod
    def filename(run_id: str, bad_row: bool = False) -> str:
        return f"qa-{run_id}-{'bad' if bad_row else 'ok'}.csv"


# --------------------------------------------------------------------------- #
# 作答脚本
# --------------------------------------------------------------------------- #
@dataclass
class AnswerScript:
    """生成 ``AnswerSaveRequest`` 序列：单调递增 + 乱序两套（AC-12/15）。"""

    questions: List[Dict[str, Any]] = field(default_factory=list)
    base_seq: int = 1
    seconds_between: int = 2

    def _answers(self, correct: bool = True) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for idx, q in enumerate(self.questions):
            selected = list(q["answer"]) if correct else QuestionFactory().wrong_answer_for(q)
            out.append({"question_id": idx + 1, "selected": selected})
        return out

    def monotonic(self, steps: int = 3) -> List[Dict[str, Any]]:
        """seq 递增：第 1 步答前 1/3 题，逐步补全（模拟作答过程）。"""
        reqs: List[Dict[str, Any]] = []
        total = len(self.questions) or 1
        for step in range(steps):
            seq = self.base_seq + step
            upto = max(1, round(total * (step + 1) / steps))
            reqs.append(
                {
                    "client_seq": seq,
                    "answers": self._answers()[:upto],
                }
            )
        return reqs

    def stale_replay(self, newest_seq: int) -> Dict[str, Any]:
        """旧 seq 重放：服务端必须忽略覆盖（AC-15）。"""
        return {"client_seq": max(1, newest_seq - 1), "answers": self._answers(correct=False)}

    def all_correct(self) -> Dict[str, Any]:
        return {"client_seq": self.base_seq + 99, "answers": self._answers(correct=True)}


# --------------------------------------------------------------------------- #
# 完整场景
# --------------------------------------------------------------------------- #
@dataclass
class ExamScenario:
    """E2E 一键场景：账号 + 题库 + 试卷 + 四类考试 + 作答脚本。"""

    run_id: str
    teacher: Account
    student: Account
    victim: Account
    attacker: Account
    admin: Account
    disabled_student: Account
    questions: List[Dict[str, Any]]
    paper: Dict[str, Any]
    exam_live: Dict[str, Any]
    exam_future: Dict[str, Any]
    exam_expired: Dict[str, Any]
    exam_draft: Dict[str, Any]
    answers: AnswerScript
    import_csv: bytes

    def exam_payloads(self) -> Dict[str, Dict[str, Any]]:
        return {
            "live": self.exam_live,
            "future": self.exam_future,
            "expired": self.exam_expired,
            "draft": self.exam_draft,
        }


def build_exam_scenario(seed: int = 42, run_id: Optional[str] = None) -> ExamScenario:
    """构造一套完整、可重复的测试场景（不触达任何生产代码）。"""
    rid = run_id or new_run_id()
    accounts = AccountFactory(seed=seed, run_id=rid)
    victim, attacker = accounts.idor_pair()
    questions = QuestionFactory(seed=seed, run_id=rid).batch(10)
    paper = PaperFactory(seed=seed, run_id=rid).build(question_count=10)
    exams = ExamFactory(run_id=rid)
    return ExamScenario(
        run_id=rid,
        teacher=accounts.teacher(),
        student=accounts.student(),
        victim=victim,
        attacker=attacker,
        admin=accounts.admin(),
        disabled_student=accounts.disabled_student(),
        questions=questions,
        paper=paper,
        exam_live=exams.live(),
        exam_future=exams.future(),
        exam_expired=exams.expired(),
        exam_draft=exams.draft(),
        answers=AnswerScript(questions=questions),
        import_csv=ImportFileFactory(seed=seed, run_id=rid).csv_bytes(count=3, bad_row=True, duplicate=True),
    )


if __name__ == "__main__":  # 轻量自检：python tests/factories.py
    sc = build_exam_scenario()
    assert abs(sum(q["points"] for q in sc.paper["questions"]) - PAPER_TOTAL_POINTS) < 1e-9
    assert all(q["type"] in QUESTION_TYPES for q in sc.questions)
    assert sc.questions[-1]["options"] == list(JUDGE_OPTIONS)
    assert isinstance(sc.import_csv, bytes) and b"single" in sc.import_csv
    assert sc.answers.monotonic(3)[-1]["client_seq"] == 3
    print("factories self-check OK:", sc.run_id)
