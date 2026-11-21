"""观测集合测试：缺测不按 0 分、同一天多版本只采用一条、续取版本一致、复核清单同步。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.dq_scorecard import DqScorecardService
from app.services.dq_trend import DqTrendService
from app.services.observation import (
    build_observation_comparison,
    canonical_version,
    parse_date,
    parse_score,
)
from app.store import store

TABLES = ["dwd_trade_detail", "ods_order_info"]


def _row(row_id: int, table: str, date: str, score, version: str = "v1",
         at: str | None = None) -> dict:
    return {
        "id": row_id,
        "记录编号": f"DQ_T-{row_id:04d}",
        "目标表名": table,
        "日期": date,
        "综合质量": score,
        "数据版本": version,
        "采集时间": at or f"{date}T06:00:00+00:00",
    }


@pytest.fixture
def trend_rows():
    rows = store.rows("dq_trend")
    original = [dict(row) for row in rows]
    rows.clear()
    yield rows
    rows.clear()
    rows.extend(original)


class TestObservationScope:
    def test_missing_day_is_not_zero(self, trend_rows):
        trend_rows.extend([
            _row(1, "dwd_trade_detail", "2026-11-10", "92.0"),
            _row(2, "dwd_trade_detail", "2026-11-11", "91.5"),
            _row(3, "dwd_trade_detail", "2026-11-12", "93.0"),
            _row(4, "ods_order_info", "2026-11-10", "85.0"),
            _row(5, "ods_order_info", "2026-11-12", "86.0"),
        ])
        result = build_observation_comparison(trend_rows, TABLES)

        assert result["进入比较的日期"] == ["2026-11-10", "2026-11-12"]
        assert result["分母"] == 2
        ranking = {item["表名"]: item for item in result["排名"]}
        assert ranking["ods_order_info"]["平均质量"] == 85.5
        assert ranking["ods_order_info"]["样本量"] == 2
        assert all(item["平均质量"] > 0 for item in result["排名"])

    def test_missing_table_is_not_rankable(self, trend_rows):
        trend_rows.extend([
            _row(1, "dwd_trade_detail", "2026-11-10", "92.0"),
            _row(2, "dwd_trade_detail", "2026-11-11", "91.5"),
        ])
        result = build_observation_comparison(trend_rows, TABLES)

        assert result["排名"] == []
        assert result["可用于复核"] is False
        assert result["复核状态"] == "无法比较"
        assert "ods_order_info" in result["不可参与排序表"]
        assert {item["表名"] for item in result["缺数表"]} == {"ods_order_info"}

    def test_single_missing_day_blocks_review(self, trend_rows):
        trend_rows.extend([
            _row(1, "dwd_trade_detail", "2026-11-10", "92.0"),
            _row(2, "dwd_trade_detail", "2026-11-11", "91.5"),
            _row(3, "ods_order_info", "2026-11-10", "85.0"),
        ])
        result = build_observation_comparison(trend_rows, TABLES)

        assert result["可用于复核"] is False
        assert result["复核状态"] == "待补录"
        assert result["待补录"] == [{"表名": "ods_order_info", "日期": "2026-11-11", "原因": "缺测"}]
        assert "2026-11-11" in result["复核提示"]

    def test_date_filter_before_and_after_backfill(self, trend_rows):
        trend_rows.extend([
            _row(1, "dwd_trade_detail", "2026-11-10", "92.0"),
            _row(2, "dwd_trade_detail", "2026-11-11", "91.5"),
            _row(3, "dwd_trade_detail", "2026-11-12", "93.0"),
            _row(4, "ods_order_info", "2026-11-10", "85.0"),
            _row(5, "ods_order_info", "2026-11-12", "86.0"),
        ])
        before = build_observation_comparison(
            trend_rows, TABLES, start_date="2026-11-10", end_date="2026-11-11")
        assert before["进入比较的日期"] == ["2026-11-10"]
        assert before["缺数表"][0]["缺失日期"] == ["2026-11-11"]

        trend_rows.append(_row(6, "ods_order_info", "2026-11-11", "84.0", at="2026-11-11T11:00:00+00:00"))
        after = build_observation_comparison(
            trend_rows, TABLES, start_date="2026-11-10", end_date="2026-11-11")
        assert after["进入比较的日期"] == ["2026-11-10", "2026-11-11"]
        assert after["缺数表"] == []
        assert after["可用于复核"] is True
        assert after["复核状态"] == "可复核"
        assert after["排名"][0]["表名"] == "dwd_trade_detail"

    def test_end_date_before_start_date_rejected(self, trend_rows):
        with pytest.raises(ValueError):
            build_observation_comparison(
                trend_rows, TABLES, start_date="2026-11-12", end_date="2026-11-10")

    def test_placeholder_text_counts_as_missing(self):
        rows = [
            _row(1, "A", "2026-11-10", "质量趋势看板样例1"),
            _row(2, "A", "2026-11-11", "90.0"),
        ]
        result = build_observation_comparison(rows, ["A"])
        assert result["进入比较的日期"] == ["2026-11-11"]
        assert result["分母"] == 1


class TestVersionSelection:
    def test_same_day_multiple_versions_adopts_latest_and_keeps_candidates(self, trend_rows):
        trend_rows.extend([
            _row(1, "dwd_trade_detail", "2026-11-10", "92.0"),
            _row(2, "ods_order_info", "2026-11-10", "85.0", version="v1",
                 at="2026-11-10T06:25:00+00:00"),
            _row(3, "ods_order_info", "2026-11-10", "87.5", version="v2",
                 at="2026-11-10T09:40:00+00:00"),
        ])
        result = build_observation_comparison(trend_rows, TABLES)

        adopted = {
            (item["表名"], item["日期"]): item
            for item in result["实际采用观测集合"]
        }
        chosen = adopted[("ods_order_info", "2026-11-10")]
        assert chosen["数据版本"] == "v2"
        assert chosen["评分值"] == 87.5
        assert chosen["候选数"] == 2
        assert [item["数据版本"] for item in chosen["候选记录"]] == ["v2", "v1"]
        assert result["分母"] == 1

        trace = result["版本回查"][0]
        assert trace["日期"] == "2026-11-10"
        assert trace["采用版本"] == "v2"
        assert len(trace["候选记录"]) == 2

    def test_pinned_version_wins_over_newer(self, trend_rows):
        trend_rows.extend([
            _row(1, "dwd_trade_detail", "2026-11-10", "92.0", version="v1"),
            _row(2, "ods_order_info", "2026-11-10", "85.0", version="v1"),
            _row(3, "ods_order_info", "2026-11-10", "87.5", version="v2"),
        ])
        result = build_observation_comparison(trend_rows, TABLES, version="v1")
        chosen = [
            item for item in result["实际采用观测集合"]
            if item["表名"] == "ods_order_info"
        ][0]
        assert chosen["数据版本"] == "v1"
        assert chosen["评分值"] == 85.0

    def test_pinned_version_missing_is_not_silently_replaced(self, trend_rows):
        trend_rows.extend([
            _row(1, "dwd_trade_detail", "2026-11-10", "92.0", version="v2"),
            _row(2, "ods_order_info", "2026-11-10", "87.5", version="v2"),
        ])
        result = build_observation_comparison(trend_rows, TABLES, version="v1")
        assert result["进入比较的日期"] == []
        assert result["版本缺失"][0]["需要版本"] == "v1"
        assert result["排名"] == []

    def test_canonical_version_and_score_parsing(self):
        assert canonical_version("V1.0") == canonical_version("v1") == "1"
        assert canonical_version("未标注") == ""
        assert parse_score("92.5%") == 92.5
        assert parse_score("N/A") is None
        assert parse_date("2026-11-10T06:20:00+00:00") == "2026-11-10"


class TestResumeAndReview:
    def test_resume_pins_same_records_after_interruption(self, trend_rows):
        trend_rows.extend([
            _row(1, "dwd_trade_detail", "2026-11-10", "92.0"),
            _row(2, "dwd_trade_detail", "2026-11-11", "91.5"),
            _row(3, "ods_order_info", "2026-11-10", "85.0"),
            _row(4, "ods_order_info", "2026-11-11", "86.0"),
        ])
        svc = DqTrendService()
        first = svc.query_trend_comparison(TABLES, start_date="2026-11-10",
                                           end_date="2026-11-11")
        assert first["分母"] == 2
        first_snapshot = dict(first)

        # 中断后补采出了 v2：直接重取会换版本，按快照续取则必须固定回 v1。
        trend_rows.extend([
            _row(5, "dwd_trade_detail", "2026-11-10", "99.0", version="v2",
                 at="2026-11-13T06:00:00+00:00"),
            _row(6, "ods_order_info", "2026-11-11", "99.0", version="v2",
                 at="2026-11-13T06:00:00+00:00"),
        ])
        plain = svc.query_trend_comparison(TABLES, start_date="2026-11-10",
                                           end_date="2026-11-11")
        assert {
            (item["表名"], item["日期"]): item["数据版本"]
            for item in plain["实际采用观测集合"]
        }[("dwd_trade_detail", "2026-11-10")] == "v2"

        resumed = svc.query_trend_comparison(
            TABLES, resume_comparison_id=first_snapshot["对比编号"])
        assert resumed["是否续取"] is True
        assert resumed["分母"] == first_snapshot["分母"]
        assert resumed["进入比较的日期"] == first_snapshot["进入比较的日期"]
        assert (
            [(item["表名"], item["日期"], item["记录编号"], item["数据版本"])
             for item in resumed["实际采用观测集合"]]
            ==
            [(item["表名"], item["日期"], item["记录编号"], item["数据版本"])
             for item in first_snapshot["实际采用观测集合"]]
        )

    def test_unknown_resume_id_rejected(self, trend_rows):
        with pytest.raises(ValueError):
            DqTrendService().query_trend_comparison(TABLES, resume_comparison_id="CMP-9999")

    def test_comparison_syncs_scorecard_review_list(self, trend_rows):
        trend_rows.extend([
            _row(1, "dwd_trade_detail", "2026-11-10", "92.0"),
            _row(2, "dwd_trade_detail", "2026-11-11", "91.5"),
            _row(3, "ods_order_info", "2026-11-10", "85.0"),
        ])
        result = DqTrendService().query_trend_comparison(TABLES)

        assert result["同步复核清单"] is True
        reviews = DqScorecardService().ranking_review_list()
        review = next(item for item in reviews if item["对比编号"] == result["对比编号"])
        assert review["缺数表"] == [{"表名": "ods_order_info", "缺失日期": ["2026-11-11"]}]
        assert review["进入比较的日期"] == ["2026-11-10"]
        assert review["可用于复核"] is False
        assert review["复核状态"] == "待补录"


class TestScorecardObservationFilter:
    def test_scorecard_rows_filtered_by_common_dates(self):
        result = DqScorecardService().filter_trend_observations(
            TABLES, start_date="2026-11-10", end_date="2026-11-12", use_scorecard=True)
        assert result["观测来源"] == "dq_scorecard"
        assert result["进入比较的日期"] == ["2026-11-10", "2026-11-12"]
        assert result["分母"] == 2
        assert result["缺数表"][0]["缺失日期"] == ["2026-11-11"]
        adopted = {
            (item["表名"], item["日期"]): item
            for item in result["实际采用观测集合"]
        }
        assert adopted[("ods_order_info", "2026-11-12")]["数据版本"] == "v2"

    def test_scorecard_review_sync_when_resuming(self, trend_rows):
        trend_rows.extend([
            _row(1, "dwd_trade_detail", "2026-11-10", "92.0"),
            _row(2, "ods_order_info", "2026-11-10", "85.0"),
        ])
        first = DqTrendService().query_trend_comparison(TABLES)
        resumed = DqScorecardService().filter_trend_observations(
            TABLES, resume_comparison_id=first["对比编号"])
        assert resumed["同步复核清单"] is True
        assert resumed["续取来源"] == first["对比编号"]
        review = next(
            item for item in DqScorecardService().ranking_review_list()
            if item["对比编号"] == first["对比编号"]
        )
        assert review["可用于复核"] is True
        assert review["排名"][0]["名次"] == 1
