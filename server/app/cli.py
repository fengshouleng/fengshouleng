"""运维 CLI：创建管理员、写入演示数据、环境自检。

运行方式（工作目录 `server/`）::

    python -m app.cli create-admin --username admin --display-name 管理员
    python -m app.cli seed-demo
    python -m app.cli check

密码输入约定（避免明文进入 shell 历史 / 进程参数列表）：

- `create-admin`：密码只从 stdin（TTY 下用 `getpass` 不回显）或
  `--password-env VAR` 读取；**没有** `--password` 参数。
- `seed-demo`：演示账号密码来自 `--password-env VAR`；未提供时使用
  `DEFAULT_DEV_PASSWORD`（**仅限本地开发**，staging/prod 会拒绝执行）。

退出码：0 成功（含 seed-demo 幂等跳过）；1 运行 / 数据库 / 自检失败；
2 用法或前置条件不满足（用户名冲突、非空库、密码缺失或过短等）。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.auth.service import hash_password
from app.db import get_sessionmaker
from app.models.paper import Paper, PaperStatus
from app.models.paper_question import PaperQuestion, PartialRule
from app.models.question import Question, QuestionType
from app.models.user import Role, User
from app.questions.service import compute_stem_hash
from app.settings import get_settings

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

# 仅用于本地开发的演示密码；staging/prod 必须用 --password-env 显式提供
DEFAULT_DEV_PASSWORD = "dev-demo-password-123"

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 64
DISPLAY_NAME_MAX_LENGTH = 64

DEMO_TEACHER_USERNAME = "demo-teacher"
DEMO_STUDENT_USERNAMES: tuple[str, ...] = ("demo-student-1", "demo-student-2")
DEMO_USERNAMES: tuple[str, ...] = (DEMO_TEACHER_USERNAME, *DEMO_STUDENT_USERNAMES)
DEMO_PAPER_TITLE = "演示试卷（100 分）"
DEMO_TOTAL_POINTS = Decimal("100.00")
DEMO_QUESTION_POINTS: tuple[Decimal, ...] = (
    Decimal("40.00"),
    Decimal("30.00"),
    Decimal("30.00"),
)
_REMOTE_ENVS = frozenset({"staging", "prod", "production"})


class CliError(Exception):
    """可预期的 CLI 错误：携带退出码，消息中不含敏感信息。"""

    def __init__(self, message: str, exit_code: int = EXIT_USAGE) -> None:
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SeedResult:
    """`seed-demo` 成功写入后的摘要（不含密码）。"""

    teacher_username: str
    student_usernames: tuple[str, ...]
    question_count: int
    paper_title: str
    total_points: Decimal


@dataclass(frozen=True, slots=True)
class CheckReport:
    """`check` 的三项自检结果。"""

    db_ok: bool
    alembic_revision: str | None
    admin_count: int | None
    errors: tuple[str, ...]


# --------------------------------------------------------------------------- #
# 输入校验与密码读取
# --------------------------------------------------------------------------- #
def _validate_username(raw: str) -> str:
    username = raw.strip()
    if not USERNAME_MIN_LENGTH <= len(username) <= USERNAME_MAX_LENGTH:
        raise CliError(
            f"用户名长度必须为 {USERNAME_MIN_LENGTH}-{USERNAME_MAX_LENGTH} 个字符"
        )
    return username


def _validate_display_name(raw: str) -> str:
    display_name = raw.strip()
    if not 1 <= len(display_name) <= DISPLAY_NAME_MAX_LENGTH:
        raise CliError(f"显示名长度必须为 1-{DISPLAY_NAME_MAX_LENGTH} 个字符")
    return display_name


def _validate_password(password: str) -> str:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise CliError(f"密码长度不能少于 {PASSWORD_MIN_LENGTH} 个字符")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise CliError(f"密码长度不能超过 {PASSWORD_MAX_LENGTH} 个字符")
    return password


def _password_from_env(variable: str) -> str:
    value = os.environ.get(variable)
    if value is None or not value:
        raise CliError(f"环境变量 {variable} 未设置或为空")
    return _validate_password(value)


def _password_from_stdin() -> str:
    if sys.stdin.isatty():
        try:
            password = getpass.getpass("密码（输入不回显）: ")
        except (EOFError, KeyboardInterrupt) as exc:
            raise CliError("未读取到密码，已取消") from exc
    else:
        line = sys.stdin.readline()
        if not line:
            raise CliError("未从 stdin 读到密码；可用 --password-env 指定环境变量")
        password = line.rstrip("\r\n")
    if not password:
        raise CliError("密码不能为空")
    return _validate_password(password)


def _resolve_password(password_env: str | None) -> str:
    if password_env:
        return _password_from_env(password_env)
    return _password_from_stdin()


def _resolve_demo_password(password_env: str | None) -> str:
    if password_env:
        return _password_from_env(password_env)
    app_env = get_settings().app_env.strip().lower()
    if app_env in _REMOTE_ENVS:
        raise CliError(
            f"{app_env} 环境禁止使用开发默认密码，请用 --password-env 指定环境变量"
        )
    return DEFAULT_DEV_PASSWORD


# --------------------------------------------------------------------------- #
# create-admin
# --------------------------------------------------------------------------- #
def create_admin(
    db: Session,
    *,
    username: str,
    display_name: str,
    password: str,
) -> User:
    """创建管理员并写审计；审计 actor_id=None（CLI 视为系统操作）。"""
    if db.scalar(select(User.id).where(User.username == username)) is not None:
        raise CliError(f"用户名已存在：{username}")
    user = User(
        username=username,
        display_name=display_name,
        role=Role.ADMIN.value,
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()
        audit.record(
            db,
            action=audit.USER_CREATE,
            actor_id=None,
            target_type="user",
            target_id=user.id,
            detail={"username": user.username, "role": user.role, "source": "cli"},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise CliError(f"用户名已存在：{username}") from exc
    return user


# --------------------------------------------------------------------------- #
# seed-demo
# --------------------------------------------------------------------------- #
def _build_question(
    *,
    owner_id: int,
    question_type: QuestionType,
    stem: str,
    options: list[dict[str, str]],
    answer: list[str],
    explanation: str,
    difficulty: int,
    tags: list[str],
) -> Question:
    return Question(
        owner_id=owner_id,
        type=question_type.value,
        stem=stem,
        stem_hash=compute_stem_hash(stem),
        options=options,
        answer=answer,
        explanation=explanation,
        difficulty=difficulty,
        tags=tags,
        is_deleted=False,
    )


def _demo_questions(owner_id: int) -> list[Question]:
    """演示题库：单选 / 多选 / 判断各一道。"""
    return [
        _build_question(
            owner_id=owner_id,
            question_type=QuestionType.SINGLE,
            stem="演示单选题：HTTP 状态码 200 表示什么？",
            options=[
                {"key": "A", "text": "服务器内部错误"},
                {"key": "B", "text": "请求成功"},
                {"key": "C", "text": "资源未找到"},
                {"key": "D", "text": "永久重定向"},
            ],
            answer=["B"],
            explanation="200 OK 表示请求已成功处理。",
            difficulty=1,
            tags=["演示", "HTTP"],
        ),
        _build_question(
            owner_id=owner_id,
            question_type=QuestionType.MULTIPLE,
            stem="演示多选题：以下哪些是 HTTP 安全方法（不改变服务端状态）？",
            options=[
                {"key": "A", "text": "GET"},
                {"key": "B", "text": "POST"},
                {"key": "C", "text": "HEAD"},
                {"key": "D", "text": "DELETE"},
            ],
            answer=["A", "C"],
            explanation="GET 与 HEAD 只读取资源，属于安全方法。",
            difficulty=3,
            tags=["演示", "HTTP"],
        ),
        _build_question(
            owner_id=owner_id,
            question_type=QuestionType.JUDGE,
            stem="演示判断题：HTTP 404 表示请求的资源未找到。",
            options=[
                {"key": "A", "text": "对"},
                {"key": "B", "text": "错"},
            ],
            answer=["A"],
            explanation="404 Not Found 表示资源不存在。",
            difficulty=2,
            tags=["演示", "HTTP"],
        ),
    ]


def seed_demo(db: Session, *, password: str) -> SeedResult | None:
    """写入演示数据；演示用户名已存在时返回 None（幂等跳过）。

    只在空库执行：若 users 表已有其他账号则拒绝，避免污染真实环境。
    """
    existing = db.scalar(
        select(User.id).where(User.username.in_(DEMO_USERNAMES)).limit(1)
    )
    if existing is not None:
        return None

    user_count = int(db.scalar(select(func.count()).select_from(User)) or 0)
    if user_count:
        raise CliError("seed-demo 仅支持空库；当前 users 表非空")

    try:
        teacher = User(
            username=DEMO_TEACHER_USERNAME,
            display_name="演示教师",
            role=Role.TEACHER.value,
            password_hash=hash_password(password),
            is_active=True,
        )
        students = [
            User(
                username=username,
                display_name=f"演示学生 {index}",
                role=Role.STUDENT.value,
                password_hash=hash_password(password),
                is_active=True,
            )
            for index, username in enumerate(DEMO_STUDENT_USERNAMES, start=1)
        ]
        db.add(teacher)
        db.add_all(students)
        db.flush()

        questions = _demo_questions(teacher.id)
        db.add_all(questions)
        db.flush()

        paper = Paper(
            owner_id=teacher.id,
            title=DEMO_PAPER_TITLE,
            total_points=DEMO_TOTAL_POINTS,
            status=PaperStatus.DRAFT.value,
        )
        db.add(paper)
        db.flush()

        for order_no, (question, points) in enumerate(
            zip(questions, DEMO_QUESTION_POINTS, strict=True),
            start=1,
        ):
            db.add(
                PaperQuestion(
                    paper_id=paper.id,
                    question_id=question.id,
                    order_no=order_no,
                    points=points,
                    partial_rule=PartialRule.ALL_OR_NOTHING.value,
                )
            )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    return SeedResult(
        teacher_username=DEMO_TEACHER_USERNAME,
        student_usernames=DEMO_STUDENT_USERNAMES,
        question_count=len(questions),
        paper_title=DEMO_PAPER_TITLE,
        total_points=DEMO_TOTAL_POINTS,
    )


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #
def run_check(db: Session) -> CheckReport:
    """执行三项自检：DB 连通（SELECT 1）、alembic 版本、管理员数量。"""
    errors: list[str] = []

    db_ok = True
    try:
        db.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError as exc:
        db_ok = False
        errors.append(f"数据库连通性检查失败：{type(exc).__name__}")
        db.rollback()

    revision: str | None = None
    try:
        revisions = [
            str(item)
            for item in db.execute(
                text("SELECT version_num FROM alembic_version")
            )
            .scalars()
            .all()
        ]
        if revisions:
            revision = ",".join(revisions)
        else:
            errors.append("alembic_version 为空：尚未执行迁移")
    except SQLAlchemyError as exc:
        errors.append(f"读取 alembic 版本失败：{type(exc).__name__}")
        db.rollback()

    admin_count: int | None = None
    try:
        admin_count = int(
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == Role.ADMIN.value)
            )
            or 0
        )
    except SQLAlchemyError as exc:
        errors.append(f"管理员数量统计失败：{type(exc).__name__}")
        db.rollback()

    return CheckReport(
        db_ok=db_ok,
        alembic_revision=revision,
        admin_count=admin_count,
        errors=tuple(errors),
    )


# --------------------------------------------------------------------------- #
# 命令入口
# --------------------------------------------------------------------------- #
def _print_seed_result(result: SeedResult) -> None:
    print(
        "已生成演示数据：教师 1 名、学生 "
        f"{len(result.student_usernames)} 名、题目 {result.question_count} 道、"
        f"试卷《{result.paper_title}》（总分 {result.total_points:.2f}）"
    )
    print(
        "演示账号："
        f"{result.teacher_username}、"
        f"{'、'.join(result.student_usernames)}"
    )
    print("演示密码来自 --password-env 或本地开发默认值（默认值仅限开发环境）。")


def _print_check_report(report: CheckReport) -> None:
    print(f"alembic 当前版本: {report.alembic_revision or '未知'}")
    print(f"数据库连通性: {'ok' if report.db_ok else '失败'}")
    print(f"管理员数量: {'未知' if report.admin_count is None else report.admin_count}")


def _command_create_admin(
    args: argparse.Namespace,
    factory: Callable[[], Session],
) -> int:
    username = _validate_username(str(args.username))
    display_name = _validate_display_name(str(args.display_name))
    password = _resolve_password(args.password_env)
    with factory() as db:
        user = create_admin(
            db,
            username=username,
            display_name=display_name,
            password=password,
        )
        message = f"已创建管理员：{user.username}（角色：{user.role}）"
    print(message)
    return EXIT_OK


def _command_seed_demo(
    args: argparse.Namespace,
    factory: Callable[[], Session],
) -> int:
    password = _resolve_demo_password(args.password_env)
    with factory() as db:
        result = seed_demo(db, password=password)
    if result is None:
        print("检测到演示数据已存在，跳过 seed-demo（幂等）。")
    else:
        _print_seed_result(result)
    return EXIT_OK


def _command_check(factory: Callable[[], Session]) -> int:
    with factory() as db:
        report = run_check(db)
    _print_check_report(report)
    for error in report.errors:
        print(f"错误: {error}", file=sys.stderr)
    return EXIT_OK if not report.errors else EXIT_FAILURE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="选择题答题系统运维 CLI",
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
    )

    admin = subparsers.add_parser(
        "create-admin",
        help="创建管理员账号（密码从 stdin 或环境变量读取）",
        allow_abbrev=False,
    )
    admin.add_argument("--username", required=True, help="用户名（3-64 字符）")
    admin.add_argument("--display-name", required=True, help="显示名（1-64 字符）")
    admin.add_argument(
        "--password-env",
        metavar="VAR",
        default=None,
        help="从环境变量 VAR 读取密码；未提供时从 stdin 读取",
    )

    seed = subparsers.add_parser(
        "seed-demo",
        help="写入演示数据（仅空库，重复执行幂等）",
        allow_abbrev=False,
    )
    seed.add_argument(
        "--password-env",
        metavar="VAR",
        default=None,
        help="从环境变量 VAR 读取演示账号密码；未提供时使用开发默认密码",
    )

    subparsers.add_parser(
        "check",
        help="自检 alembic 版本、数据库连通性与管理员数量",
        allow_abbrev=False,
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
) -> int:
    """CLI 入口；`session_factory` 仅用于测试注入。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        factory: Callable[[], Session] = session_factory or get_sessionmaker()
        command: str = args.command
        if command == "create-admin":
            return _command_create_admin(args, factory)
        if command == "seed-demo":
            return _command_seed_demo(args, factory)
        if command == "check":
            return _command_check(factory)
        raise CliError(f"未知命令：{command}")
    except CliError as exc:
        print(f"错误: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except SQLAlchemyError as exc:
        # 只打印异常类型，避免把连接串等内部信息写进终端/日志
        print(f"错误: 数据库操作失败（{type(exc).__name__}）", file=sys.stderr)
        return EXIT_FAILURE


__all__ = [
    "DEFAULT_DEV_PASSWORD",
    "CheckReport",
    "CliError",
    "SeedResult",
    "build_parser",
    "create_admin",
    "main",
    "run_check",
    "seed_demo",
]


if __name__ == "__main__":
    raise SystemExit(main())
