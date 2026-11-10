"""从 CI/容器的 api 日志里还原**服务端侧**延迟分布，用于把「应用容量」与「压测机 CPU 争用」分开。

背景：G4 性能门槛用 k6 的 `http_req_duration` 判 P95<300ms，而 k6 与被测 API 同机
（GitHub 4 vCPU runner 上 k6 + postgres + api + docker 共享 CPU），k6 侧的数值混入了 VU
调度与 JSON 解析排队，无法区分「应用慢」和「压测机饿」。`app/middleware/access_log.py`
对每个请求在响应结束时用 `time.perf_counter()` 记了 `latency_ms`（纯服务端视角，不含网络
与客户端排队），容器日志里就有——归档的 `compose-logs.txt` 足以做这项对照。

用法：
  python reports/perf/analyze_server_latency.py <compose-logs.txt> [--json out.json]

输出：按路由族（save / read / start / login / other）统计 count / p50 / p90 / p95 / p99 / max，
以及按 5s 时间桶的 save、read 侧 p95，便于区分「开局 burst / 每 30s 保存 burst」与「稳态读」。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# 容器日志行形如：`api-1  | {json}`；其余（uvicorn 启动、postgres 日志）跳过
LINE_PREFIX = re.compile(r"^[a-z0-9_-]+\s+\|\s+")
ANSWER_SAVE = re.compile(r"^/api/sessions/\d+/answers$")
SESSION_READ = re.compile(r"^/api/sessions/\d+$")
SESSION_START = re.compile(r"^/api/exams/\d+/sessions$")
BUCKET_SECONDS = 5


def percentile(values: list[float], q: float) -> float:
    """最近秩法分位（与 k6 口径接近，小样本不插值出偏小值）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = int(round(q / 100.0 * len(ordered) + 0.5)) - 1
    return ordered[max(0, min(len(ordered) - 1, rank))]


def family_of(path: str) -> str:
    if ANSWER_SAVE.match(path):
        return "save"
    if SESSION_READ.match(path):
        return "read"
    if SESSION_START.match(path):
        return "start"
    if path == "/api/auth/login":
        return "login"
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("logs", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    samples: dict[str, list[float]] = defaultdict(list)
    save_buckets: dict[int, list[float]] = defaultdict(list)
    read_buckets: dict[int, list[float]] = defaultdict(list)
    moments: list[float] = []
    parsed = malformed = 0
    status_counts: dict[str, int] = defaultdict(int)
    records: list[tuple[float, str, float]] = []

    for raw in args.logs.read_text(encoding="utf-8", errors="replace").splitlines():
        body = LINE_PREFIX.sub("", raw)
        if not body.startswith("{"):
            continue
        try:
            record = json.loads(body)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if record.get("event") != "access":
            continue
        parsed += 1
        status = int(record.get("status") or 0)
        status_counts[str(status)] += 1
        latency = float(record.get("latency_ms") or 0.0)
        family = family_of(str(record.get("path") or ""))
        samples[family].append(latency)
        try:
            moment = datetime.fromisoformat(str(record.get("ts")).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            continue
        moments.append(moment)
        records.append((moment, family, latency))

    base = min(moments) if moments else 0.0
    for moment, family, latency in records:
        bucket = int((moment - base) // BUCKET_SECONDS)
        if family == "save":
            save_buckets[bucket].append(latency)
        elif family == "read":
            read_buckets[bucket].append(latency)

    report: dict[str, object] = {
        "source": str(args.logs),
        "access_lines": parsed,
        "malformed_lines": malformed,
        "status": dict(sorted(status_counts.items())),
        "families": {},
    }
    print("=" * 82)
    print(f"来源: {args.logs}")
    print(f"access 行数: {parsed}  无法解析: {malformed}")
    print(f"状态码分布: {dict(sorted(status_counts.items()))}")
    print("=" * 82)
    print(f"{'族':<8}{'count':>8}{'p50':>10}{'p90':>10}{'p95':>10}{'p99':>10}{'max':>10}")
    families = {}
    for family in ("save", "read", "start", "login", "other"):
        values = samples.get(family) or []
        if not values:
            continue
        row = {
            "count": len(values),
            "p50": round(percentile(values, 50), 2),
            "p90": round(percentile(values, 90), 2),
            "p95": round(percentile(values, 95), 2),
            "p99": round(percentile(values, 99), 2),
            "max": round(max(values), 2),
        }
        families[family] = row
        print(
            f"{family:<8}{row['count']:>8}{row['p50']:>10}{row['p90']:>10}"
            f"{row['p95']:>10}{row['p99']:>10}{row['max']:>10}"
        )
    report["families"] = families

    for name, buckets in (("save", save_buckets), ("read", read_buckets)):
        print()
        print(f"按 {BUCKET_SECONDS}s 桶的 {name} 侧服务端延迟（看 burst 与稳态）：")
        rows = []
        for bucket in sorted(buckets):
            values = buckets[bucket]
            row = {
                "bucket_s": bucket * BUCKET_SECONDS,
                "count": len(values),
                "p50": round(percentile(values, 50), 2),
                "p95": round(percentile(values, 95), 2),
                "max": round(max(values), 2),
            }
            rows.append(row)
            print(
                f"  t={row['bucket_s']:>4}s count={row['count']:>4} "
                f"p50={row['p50']:>8} p95={row['p95']:>8} max={row['max']:>9}"
            )
        report[name + "_buckets"] = rows

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
