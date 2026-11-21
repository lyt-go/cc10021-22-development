"""观测集合口径：按表、日期和版本整理每日可比较的质量评分。

质量趋势与质量评分卡共用这套口径：

* 同一张表在同一天可能有多条评分记录（补采、重算、多个版本），对比时只采用一条，
  其余记录保留在候选里供回查。
* 没有评分记录的日期按“缺测”处理，不按 0 分参与平均。
* 只有所有参与比较的表都有评分的日期才进入共同观测集，分母就是这些日期的数量。

这样既不会因为某天缺数把表压低，也不会把同一天的多个版本重复计入分母。
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

TABLE_KEYS = ("目标表名", "表名")
DATE_KEYS = ("日期", "观测日期", "评分时间")
VERSION_KEYS = ("数据版本", "评分版本", "版本")
TIME_KEYS = ("采集时间", "更新时间", "评分时间", "录入时间")
SCORE_KEYS = ("综合质量", "综合得分", "评分", "得分")

REVIEW_STATE_KEY = "scorecard_ranking_review"
COMPARISON_STATE_KEY = "quality_trend_comparisons"
LATEST_STATE_KEY = "quality_trend_comparison_latest"

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def first_value(row: dict[str, Any], keys: Sequence[str], default: Any = None) -> Any:
    """按候选字段名取值，兼容趋势表和评分卡表的字段差异。"""
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return default


def parse_date(value: Any) -> str | None:
    """从日期或日期时间里取出 YYYY-MM-DD，取不到时返回 None。"""
    if value is None:
        return None
    match = _DATE_RE.search(str(value))
    return match.group(1) if match else None


def parse_score(value: Any) -> float | None:
    """解析评分值；非数值（例如样例占位文本）视为缺测，而不是 0 分。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def canonical_version(value: Any) -> str:
    """版本归一：v1 / V1.0 / 1 视为同一版本，便于固定版本取数。"""
    text = str(value or "").strip().lower()
    if text in ("", "未标注", "none", "null", "n/a"):
        return ""
    if text.startswith("v"):
        text = text[1:]
    parts = [part for part in text.split(".") if part != ""]
    while len(parts) > 1 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


def version_sort_key(value: Any) -> tuple[tuple[int, Any], ...]:
    """版本自然排序：v2 高于 v1，v10 高于 v9；未标注版本排在最低。"""
    text = canonical_version(value)
    if not text:
        return ((0, ""),)
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part)
        for part in re.split(r"(\d+)", text)
        if part
    )


def _record_summary(row: dict[str, Any], score: float | None,
                    score_label: str) -> dict[str, Any]:
    return {
        "记录id": row.get("id"),
        "记录编号": str(row.get("记录编号") or f"id:{row.get('id')}"),
        "数据版本": str(first_value(row, VERSION_KEYS, "未标注") or "未标注"),
        "版本原值": first_value(row, VERSION_KEYS, ""),
        "采集时间": first_value(row, TIME_KEYS, ""),
        score_label: score,
        "评分值": score,
        "有效评分": score is not None,
    }


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        version_sort_key(candidate.get("版本原值")),
        str(candidate.get("采集时间") or ""),
        int(candidate.get("记录id") or 0),
    )


def build_observation_comparison(
    rows: Iterable[dict[str, Any]],
    table_names: Sequence[str],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    version: str | None = None,
    observation_dates: Sequence[str] | None = None,
    pinned_versions: dict[tuple[str, str], str] | None = None,
    table_keys: Sequence[str] = TABLE_KEYS,
    date_keys: Sequence[str] = DATE_KEYS,
    version_keys: Sequence[str] = VERSION_KEYS,
    score_keys: Sequence[str] = SCORE_KEYS,
    time_keys: Sequence[str] = TIME_KEYS,
    score_label: str = "综合质量",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """构建一次对比的观测集合、共同观测日和排名。

    缺测日期不会以 0 分进入平均；同一 (表, 日期) 的有效记录按“版本优先、
    采集时间其次、记录 id 最后”选出一条，并把全部候选放进候选记录里。
    ``pinned_versions`` 用于中断后重取：每个单元格固定到上一轮采用的版本，
    该版本缺失时宁可标为缺测，也不自动换成新版本。
    """
    tables = list(dict.fromkeys(str(name).strip() for name in table_names if str(name).strip()))
    if not tables:
        raise ValueError("对比组至少需要一张表")

    if start_date and end_date and start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")

    date_scope = None
    if observation_dates is not None:
        date_scope = {parse_date(item) for item in observation_dates}
        date_scope.discard(None)

    pinned = dict(pinned_versions or {})
    global_pin = canonical_version(version) if version else None

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    wanted = set(tables)
    for row in rows:
        table = str(first_value(row, table_keys, "") or "").strip()
        if table not in wanted:
            continue
        date = parse_date(first_value(row, date_keys))
        if date is None:
            continue
        if start_date and date < start_date:
            continue
        if end_date and date > end_date:
            continue
        if date_scope is not None and date not in date_scope:
            continue
        grouped[(table, date)].append(row)

    adopted: dict[tuple[str, str], dict[str, Any]] = {}
    candidates_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    pinned_missing: list[dict[str, Any]] = []

    for key, group_rows in grouped.items():
        candidates: list[dict[str, Any]] = []
        for row in group_rows:
            score = parse_score(first_value(row, score_keys))
            candidates.append(_record_summary(row, score, score_label))
        candidates.sort(key=_candidate_sort_key, reverse=True)
        candidates_by_key[key] = candidates

        cell_pin = pinned.get(key)
        pin = canonical_version(cell_pin) if cell_pin else global_pin
        eligible = [
            candidate for candidate in candidates
            if candidate["有效评分"]
            and (pin is None or canonical_version(candidate["版本原值"]) == pin)
        ]
        if not eligible:
            if pin is not None:
                pinned_missing.append({
                    "表名": key[0],
                    "日期": key[1],
                    "需要版本": cell_pin or version,
                    "可用版本": sorted(
                        {candidate["数据版本"] for candidate in candidates}
                    ),
                })
            continue
        chosen = eligible[0]
        adopted[key] = {
            "表名": key[0],
            "日期": key[1],
            "记录id": chosen["记录id"],
            "记录编号": chosen["记录编号"],
            "数据版本": chosen["数据版本"],
            "采集时间": chosen["采集时间"],
            score_label: chosen["评分值"],
            "评分值": chosen["评分值"],
            "候选数": len(candidates),
            "候选记录": candidates,
            "版本回查": len({item["数据版本"] for item in candidates}) > 1,
        }

    dates_by_table: dict[str, set[str]] = {table: set() for table in tables}
    for (table, date) in adopted:
        dates_by_table[table].add(date)

    if date_scope is not None:
        all_dates = sorted(date_scope)
    else:
        all_dates = sorted({date for (_, date) in adopted})

    participating = [table for table in tables if dates_by_table[table]]
    # 共同观测日必须覆盖整个对比组：某张表一天数据都没有时，没有哪一天是“共同”的，
    # 不能拿其余表的交集冒充分母。
    common_dates = sorted(
        set.intersection(*(dates_by_table[table] for table in tables))
    ) if tables else []

    def missing_reason(table: str, date: str) -> str:
        key = (table, date)
        candidates = candidates_by_key.get(key, [])
        cell_pin = pinned.get(key)
        pin = canonical_version(cell_pin) if cell_pin else global_pin
        if pin is not None and candidates:
            available = {canonical_version(c["版本原值"]) for c in candidates}
            if pin not in available:
                return "版本缺失"
        if candidates:
            return "无有效评分"
        return "缺测"

    coverage: list[dict[str, Any]] = []
    missing_tables: list[dict[str, Any]] = []
    for table in tables:
        own_dates = dates_by_table[table]
        missing_details = [
            {"日期": date, "原因": missing_reason(table, date)}
            for date in all_dates if date not in own_dates
        ]
        own_scores = [
            adopted[(table, date)]["评分值"]
            for date in sorted(own_dates)
            if (table, date) in adopted
        ]
        item = {
            "表名": table,
            "观测天数": len(own_dates),
            "缺失日期": [detail["日期"] for detail in missing_details],
            "缺失明细": missing_details,
            "完整": bool(all_dates) and not missing_details and len(own_dates) == len(all_dates),
            "范围内平均质量": round(sum(own_scores) / len(own_scores), 1) if own_scores else None,
            "参与比较天数": len(common_dates),
        }
        coverage.append(item)
        if not item["完整"]:
            missing_tables.append(item)

    ranking: list[dict[str, Any]] = []
    for table in participating:
        scores = [
            adopted[(table, date)]["评分值"]
            for date in common_dates
            if (table, date) in adopted
        ]
        if not common_dates or len(scores) != len(common_dates):
            continue
        records = [adopted[(table, date)] for date in common_dates]
        ranking.append({
            "表名": table,
            "平均质量": round(sum(scores) / len(scores), 1),
            "样本量": len(scores),
            "采用日期": list(common_dates),
            "采用记录编号": [item["记录编号"] for item in records],
            "采用版本": sorted({item["数据版本"] for item in records}),
            "范围内样本量": len(dates_by_table[table]),
            "范围内平均质量": next(
                item["范围内平均质量"] for item in coverage if item["表名"] == table
            ),
        })

    ranking.sort(key=lambda item: (-item["平均质量"], item["表名"]))
    for index, item in enumerate(ranking, start=1):
        item["名次"] = index

    # 只有一张表可排时不存在“名次”这回事：对比组至少要两张表同口径才能排序。
    if len(ranking) < 2:
        ranking = []

    observation_set: list[dict[str, Any]] = []
    for (table, date), item in sorted(adopted.items(), key=lambda pair: (pair[0][1], pair[0][0])):
        entry = dict(item)
        entry["进入比较"] = date in common_dates
        observation_set.append(entry)

    actual_set = [item for item in observation_set if item["进入比较"]]
    excluded = [
        {**item, "排除原因": "不在共同观测日"}
        for item in observation_set if not item["进入比较"]
    ]
    version_trace = [
        {
            "表名": item["表名"],
            "日期": item["日期"],
            "采用记录编号": item["记录编号"],
            "采用版本": item["数据版本"],
            "候选记录": item["候选记录"],
        }
        for item in observation_set
        if item["候选数"] > 1
    ]

    pending_backfill = [
        {"表名": item["表名"], "日期": date, "原因": reason}
        for item in coverage
        for date, reason in (
            (detail["日期"], detail["原因"]) for detail in item["缺失明细"]
        )
    ]

    rankable_tables = [item["表名"] for item in ranking]
    rankable = bool(ranking) and len(ranking) == len(tables)
    review_ready = (
        len(tables) >= 2
        and bool(all_dates)
        and bool(common_dates)
        and not missing_tables
        and rankable
    )

    if len(tables) < 2:
        review_state = "无法比较"
        review_hint = "对比组至少需要两张表才能排名"
    elif not all_dates:
        review_state = "无法比较"
        review_hint = "选定范围内没有有效评分记录"
    elif not common_dates:
        review_state = "无法比较"
        review_hint = "各表没有共同观测日，无法形成同口径排名"
    elif missing_tables:
        review_state = "待补录"
        names = "、".join(item["表名"] for item in missing_tables)
        dates = sorted({detail["日期"] for item in missing_tables for detail in item["缺失明细"]})
        date_text = "、".join(dates) if dates else "选定范围内"
        detail_text = "、".join(
            f"{item['表名']} {detail['日期']}（{detail['原因']}）"
            for item in missing_tables for detail in item["缺失明细"]
        )
        review_hint = f"{names} 在 {date_text} 缺测；补齐（{detail_text}）后名次可用于复核"
    elif not rankable:
        review_state = "待补录"
        review_hint = "仍有表无法在共同观测日上形成完整样本，补齐后再用于复核"
    else:
        review_state = "可复核"
        review_hint = "各表观测日期完整，名次可用于复核"

    version_label = version.strip() if version and version.strip() else "逐日最高版本"
    return {
        "对比组": tables,
        "起始日期": start_date or (all_dates[0] if all_dates else None),
        "结束日期": end_date or (all_dates[-1] if all_dates else None),
        "取数版本": version_label,
        "版本口径": "pinned" if (global_pin or pinned) else "latest_per_date",
        "观测日期范围": list(all_dates),
        "进入比较的日期": list(common_dates),
        "共同观测日": list(common_dates),
        "分母": len(common_dates),
        "分母口径": "按每张表进入比较的共同观测日计数，缺测日期不计 0 分",
        "总样本数": len(common_dates) * len(ranking),
        "表数": len(tables),
        "可参与排序表": rankable_tables,
        "不可参与排序表": [item["表名"] for item in coverage if item["表名"] not in set(rankable_tables)],
        "各表覆盖": coverage,
        "缺数表": [
            {
                "表名": item["表名"],
                "缺失日期": item["缺失日期"],
                "缺失明细": item["缺失明细"],
                "观测天数": item["观测天数"],
                "完整": item["完整"],
            }
            for item in missing_tables
        ],
        "待补录": pending_backfill,
        "版本缺失": pinned_missing,
        "观测集合": observation_set,
        "实际采用观测集合": actual_set,
        "排除观测": excluded,
        "版本回查": version_trace,
        "版本快照": [
            {
                "表名": item["表名"],
                "日期": item["日期"],
                "记录编号": item["记录编号"],
                "数据版本": item["数据版本"],
            }
            for item in actual_set
        ],
        "排名": ranking,
        "最佳表": ranking[0]["表名"] if ranking else "N/A",
        "最差表": ranking[-1]["表名"] if ranking else "N/A",
        "可用于复核": review_ready,
        "复核状态": review_state,
        "复核提示": review_hint,
        "取数时间": fetched_at or datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# 对比快照与评分卡排名复核清单
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def group_key(table_names: Sequence[str], source: str) -> str:
    return f"{source}::{'|'.join(sorted(set(table_names)))}"


def next_comparison_id(store) -> str:
    state = store.state.setdefault(COMPARISON_STATE_KEY, {})
    index = len(state) + 1
    comparison_id = f"CMP-{index:04d}"
    while comparison_id in state:
        index += 1
        comparison_id = f"CMP-{index:04d}"
    return comparison_id


def save_comparison(store, comparison: dict[str, Any], *, comparison_id: str,
                    source: str) -> dict[str, Any]:
    """保存本轮实际采用的日期与版本，供中断后按同一口径重取。"""
    state = store.state.setdefault(COMPARISON_STATE_KEY, {})
    snapshot = {
        **comparison,
        "对比编号": comparison_id,
        "来源模块": source,
        "对比组": list(comparison["对比组"]),
        "进入比较的日期": list(comparison.get("进入比较的日期") or []),
        "实际采用观测集合": [
            {
                "表名": item["表名"],
                "日期": item["日期"],
                "记录编号": item["记录编号"],
                "数据版本": item["数据版本"],
                "评分值": item["评分值"],
                "候选数": item.get("候选数", 1),
            }
            for item in comparison.get("实际采用观测集合") or []
        ],
        "更新时间": _now(),
    }
    state[comparison_id] = snapshot
    store.state.setdefault(LATEST_STATE_KEY, {})[
        group_key(comparison["对比组"], source)
    ] = comparison_id
    return snapshot


def load_comparison(store, comparison_id: str) -> dict[str, Any] | None:
    return store.state.setdefault(COMPARISON_STATE_KEY, {}).get(comparison_id)


def latest_comparison_id(store, table_names: Sequence[str], source: str) -> str | None:
    return store.state.setdefault(LATEST_STATE_KEY, {}).get(
        group_key(table_names, source)
    )


def pinned_versions_from(snapshot: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {
        (item["表名"], item["日期"]): item["数据版本"]
        for item in snapshot.get("实际采用观测集合") or []
    }


def sync_ranking_review(store, comparison: dict[str, Any],
                        comparison_id: str) -> dict[str, Any]:
    """把查询结论写入评分卡排名复核清单，保留可回查的日期、缺数表和名次。"""
    review = {
        "对比编号": comparison_id,
        "对比组": list(comparison["对比组"]),
        "观测日期范围": list(comparison.get("观测日期范围") or []),
        "进入比较的日期": list(comparison.get("进入比较的日期") or []),
        "分母": comparison.get("分母", 0),
        "取数版本": comparison.get("取数版本"),
        "缺数表": [
            {"表名": item["表名"], "缺失日期": list(item.get("缺失日期") or [])}
            for item in comparison.get("缺数表") or []
        ],
        "待补录": list(comparison.get("待补录") or []),
        "排名": [
            {
                "名次": item["名次"],
                "表名": item["表名"],
                "平均质量": item["平均质量"],
                "样本量": item["样本量"],
            }
            for item in comparison.get("排名") or []
        ],
        "可用于复核": bool(comparison.get("可用于复核")),
        "复核状态": comparison.get("复核状态"),
        "复核提示": comparison.get("复核提示"),
        "更新时间": _now(),
    }
    store.state.setdefault(REVIEW_STATE_KEY, {})[comparison_id] = review
    return review


def list_ranking_review(store) -> list[dict[str, Any]]:
    reviews = store.state.setdefault(REVIEW_STATE_KEY, {})
    return sorted(reviews.values(), key=lambda item: item["更新时间"], reverse=True)
