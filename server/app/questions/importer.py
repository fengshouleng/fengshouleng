"""题库批量导入解析（CSV / XLSX）。

文件格式（首行为表头，大小写与首尾空白不敏感）：

    type,stem,option_a,option_b,option_c,answer,explanation,difficulty,tags

- `type`：single / multiple / judge（也接受“单选/多选/判断”）
- 选项列：`option_a..option_f`（也接受 `a..f` / `选项a..选项f`）
- `answer`：`A`、`A,C`、`A C`、`AC` 均可；判断题接受 `对/错`
- `judge` 行无需填选项列，系统固定生成 `A=对` / `B=错`
- `explanation` 必填（契约 v1.0.1 C-10）；`difficulty` 缺省 3；`tags` 用
  `|` / `;` / `,` 分隔

解析阶段只做“行级”校验：格式/缺列属于文件级错误（400），单行校验失败
进入 `failed[]`，不影响同文件其它行（AC-06）。
"""

from __future__ import annotations

import csv
import importlib
import io
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app import errors
from app.errors import AppError
from app.models.question import QuestionType
from app.questions.schemas import Option, QuestionCreate

MAX_IMPORT_BYTES = 10 * 1024 * 1024

_HEADER_ALIASES: dict[str, frozenset[str]] = {
    "type": frozenset({"type", "题型", "类型"}),
    "stem": frozenset({"stem", "题干", "题目"}),
    "answer": frozenset({"answer", "答案", "正确答案"}),
    "explanation": frozenset({"explanation", "解析", "答案解析"}),
    "difficulty": frozenset({"difficulty", "难度"}),
    "tags": frozenset({"tags", "标签"}),
    # 可选：把选项写成 JSON 数组或 "A:文本|B:文本" 的单列格式
    "options": frozenset({"options", "选项", "选项列表"}),
}
_OPTION_ALIASES: dict[str, frozenset[str]] = {
    key: frozenset({f"option_{key.lower()}", key.lower(), f"选项{key.lower()}"})
    for key in "ABCDEF"
}
_REQUIRED_COLUMNS: tuple[str, ...] = ("type", "stem", "answer", "explanation")

_TYPE_ALIASES: dict[str, QuestionType] = {
    "single": QuestionType.SINGLE,
    "单选": QuestionType.SINGLE,
    "单选题": QuestionType.SINGLE,
    "multiple": QuestionType.MULTIPLE,
    "多选": QuestionType.MULTIPLE,
    "多选题": QuestionType.MULTIPLE,
    "judge": QuestionType.JUDGE,
    "判断": QuestionType.JUDGE,
    "判断题": QuestionType.JUDGE,
    "true_false": QuestionType.JUDGE,
}

_ANSWER_SEPARATORS: tuple[str, ...] = (",", "，", ";", "；", "、", "/", "|", " ")
_TAG_SEPARATORS: tuple[str, ...] = ("|", ";", "；")


@dataclass(frozen=True)
class ParsedRow:
    """一行解析结果：`payload` 与 `reason` 二选一。"""

    row: int
    payload: QuestionCreate | None = None
    reason: str | None = None


def _normalize_header(value: Any) -> str:
    return "" if value is None else str(value).strip().lower()


def _resolve_columns(header: list[Any]) -> dict[str, int]:
    """把表头映射为规范列名 -> 下标；缺必需列时抛 400。"""
    columns: dict[str, int] = {}
    for index, raw in enumerate(header):
        name = _normalize_header(raw)
        if not name:
            continue
        for field, aliases in _HEADER_ALIASES.items():
            if name in aliases and field not in columns:
                columns[field] = index
        for key, aliases in _OPTION_ALIASES.items():
            option_field = f"option_{key}"
            if name in aliases and option_field not in columns:
                columns[option_field] = index

    missing = [field for field in _REQUIRED_COLUMNS if field not in columns]
    if missing:
        raise AppError(
            errors.VALIDATION_ERROR,
            f"导入文件缺少必需列：{', '.join(missing)}",
            400,
        )
    return columns


def _cell(row: list[Any], columns: dict[str, int], field: str) -> str:
    index = columns.get(field)
    if index is None or index >= len(row) or row[index] is None:
        return ""
    return str(row[index]).strip()


def _is_blank(row: list[Any]) -> bool:
    return all(value is None or str(value).strip() == "" for value in row)


def _parse_type(raw: str) -> QuestionType | None:
    return _TYPE_ALIASES.get(raw.strip().lower())


def _parse_difficulty(raw: str) -> tuple[int, str | None]:
    if not raw:
        return 3, None
    try:
        number = float(raw)
    except ValueError:
        return 0, "难度必须为 1-5 的整数"
    if not number.is_integer() or not 1 <= int(number) <= 5:
        return 0, "难度必须为 1-5 的整数"
    return int(number), None


def _parse_options_cell(raw: str) -> list[Option]:
    """解析单列 options：JSON 数组或 `A:文本|B:文本`。"""
    text = raw.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        options: list[Option] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip().upper()
            option_text = str(item.get("text", "")).strip()
            if len(key) == 1 and key.isalpha() and option_text:
                options.append(Option(key=key, text=option_text))
        return options

    options = []
    for chunk in text.replace("；", "|").replace(";", "|").split("|"):
        part = chunk.strip()
        if not part:
            continue
        for separator in (":", "：", "、", "."):
            if separator in part:
                key, option_text = part.split(separator, 1)
                normalized_key = key.strip().upper()
                if len(normalized_key) == 1 and normalized_key.isalpha():
                    value = option_text.strip()
                    if value:
                        options.append(Option(key=normalized_key, text=value))
                break
    return options


def _parse_options(
    row: list[Any], columns: dict[str, int], question_type: QuestionType
) -> list[Option]:
    if question_type is QuestionType.JUDGE:
        # 判断题选项固定，文件中的选项列忽略
        return [Option(key="A", text="对"), Option(key="B", text="错")]
    options: list[Option] = []
    for key in "ABCDEF":
        text = _cell(row, columns, f"option_{key}")
        if text:
            options.append(Option(key=key, text=text))
    if not options:
        options = _parse_options_cell(_cell(row, columns, "options"))
    return options


def _parse_answer(raw: str, question_type: QuestionType) -> list[str]:
    text = raw.strip()
    if not text:
        return []
    if question_type is QuestionType.JUDGE:
        if text.upper() in {"A", "对", "正确", "TRUE", "T"}:
            return ["A"]
        if text.upper() in {"B", "错", "错误", "FALSE", "F"}:
            return ["B"]

    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return [str(item).strip().upper() for item in data if str(item).strip()]

    normalized = text
    for separator in _ANSWER_SEPARATORS:
        normalized = normalized.replace(separator, ",")
    answers = [part.strip().upper() for part in normalized.split(",") if part.strip()]
    if len(answers) == 1 and len(answers[0]) > 1 and answers[0].isalpha():
        answers = list(answers[0])
    return answers


def _parse_tags(raw: str) -> list[str]:
    if not raw:
        return []
    if raw.strip().startswith("["):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
    normalized = raw
    for separator in _TAG_SEPARATORS:
        normalized = normalized.replace(separator, ",")
    tags: list[str] = []
    for part in normalized.split(","):
        tag = part.strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _validation_reason(exc: ValidationError) -> str:
    error = exc.errors()[0]
    message = str(error.get("msg", "题目校验失败"))
    prefix = "Value error, "
    if message.startswith(prefix):
        message = message[len(prefix) :]
    return message


def _parse_row(row: list[Any], columns: dict[str, int], row_no: int) -> ParsedRow:
    stem = _cell(row, columns, "stem")
    if not stem:
        return ParsedRow(row=row_no, reason="题干不能为空")

    explanation = _cell(row, columns, "explanation")
    if not explanation:
        return ParsedRow(row=row_no, reason="解析不能为空")

    raw_type = _cell(row, columns, "type")
    question_type = _parse_type(raw_type)
    if question_type is None:
        return ParsedRow(
            row=row_no,
            reason="题型必须为 single / multiple / judge",
        )

    raw_answer = _cell(row, columns, "answer")
    if not raw_answer:
        return ParsedRow(row=row_no, reason="答案不能为空")

    difficulty, difficulty_error = _parse_difficulty(_cell(row, columns, "difficulty"))
    if difficulty_error is not None:
        return ParsedRow(row=row_no, reason=difficulty_error)

    try:
        payload = QuestionCreate(
            type=question_type,
            stem=stem,
            options=_parse_options(row, columns, question_type),
            answer=_parse_answer(raw_answer, question_type),
            explanation=explanation,
            difficulty=difficulty,
            tags=_parse_tags(_cell(row, columns, "tags")),
        )
    except ValidationError as exc:
        return ParsedRow(row=row_no, reason=_validation_reason(exc))
    return ParsedRow(row=row_no, payload=payload)


def _read_csv(data: bytes) -> list[list[Any]]:
    text: str | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise AppError(errors.VALIDATION_ERROR, "CSV 编码无法识别，请使用 UTF-8", 400)
    return [list(row) for row in csv.reader(io.StringIO(text))]


def _read_xlsx(data: bytes) -> list[list[Any]]:
    try:
        openpyxl = importlib.import_module("openpyxl")
    except ImportError as exc:  # 依赖未声明在 pyproject，缺依赖时明确报 400
        raise AppError(
            errors.VALIDATION_ERROR,
            "XLSX 导入需要 openpyxl 依赖，请先在 server/pyproject.toml 中声明",
            400,
        ) from exc

    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(data),
            read_only=True,
            data_only=True,
        )
    except Exception as exc:  # openpyxl 对损坏文件抛多种异常
        raise AppError(errors.VALIDATION_ERROR, "XLSX 文件无法解析", 400) from exc

    try:
        worksheet = workbook.active
        return [list(row) for row in worksheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def parse_import_file(data: bytes, filename: str) -> list[ParsedRow]:
    """解析上传文件为逐行结果；文件级错误抛 `AppError(400)`。"""
    if not data:
        raise AppError(errors.VALIDATION_ERROR, "导入文件为空", 400)
    if len(data) > MAX_IMPORT_BYTES:
        raise AppError(errors.VALIDATION_ERROR, "导入文件超过 10MB 限制", 400)

    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "csv":
        table = _read_csv(data)
    elif suffix in {"xlsx", "xlsm"}:
        table = _read_xlsx(data)
    else:
        raise AppError(
            errors.VALIDATION_ERROR,
            "仅支持 .csv 或 .xlsx 文件",
            400,
        )

    if not table:
        raise AppError(errors.VALIDATION_ERROR, "导入文件没有表头", 400)

    columns = _resolve_columns(table[0])
    parsed: list[ParsedRow] = []
    for row_no, row in enumerate(table[1:], start=2):
        if _is_blank(row):
            continue
        parsed.append(_parse_row(row, columns, row_no))
    return parsed


__all__ = ["MAX_IMPORT_BYTES", "ParsedRow", "parse_import_file"]
