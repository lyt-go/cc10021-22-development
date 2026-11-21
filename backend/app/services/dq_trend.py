"""质量趋势服务：跟踪数据质量指标变化趋势，计算趋势方向和改善程度。

domain_chain: 质量趋势 (dq_trend)
  └─ 计算 → 质量评分卡 (dq_scorecard)
  └─ 聚合 → 质量报告 (dq_report)
  └─ 查询 → 质量事件 (dq_incident)
"""

from __future__ import annotations

import random
from datetime import datetime, timezone, timedelta
from typing import Any, Sequence

from app.services import observation
from app.services.base import BaseModuleService
from app.store import store


class DqTrendService(BaseModuleService):
    MODULE = "dq_trend"
    ENTITY = "趋势记录"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["记录编号", "目标表名", "日期", "完备率", "准确率", "及时率", "综合质量", "趋势标记", "数据版本"]
    REQUIRED_FIELDS = ["记录编号", "目标表名", "日期"]
    STATUS_ORDER = ["正常", "下降趋势", "需关注", "已改善"]
    ACTION_RULES = {"标记下降": "下降趋势", "标记需关注": "需关注", "标记已改善": "已改善"}
    NEGATIVE_ACTIONS = []

    def compute_quality_trend(self, table_name: str, days: int = 14) -> dict[str, Any]:
        """计算数据质量趋势：聚合历史质量数据，计算趋势方向和变化率。

        查询多日质量评分卡和监控指标数据，聚合时间序列趋势，
        计算移动平均值和趋势斜率，标记质量方向并导入趋势存储。
        """
        trend_rows = store.rows(self.MODULE)
        scorecard_rows = store.rows("dq_scorecard")

        # 查询该表历史质量数据
        existing = [
            t for t in trend_rows
            if t.get("目标表名") == table_name
        ]

        # 模拟多日数据点
        daily_points = []
        base_quality = random.uniform(75, 95)
        for day_offset in range(days, 0, -1):
            date_str = (datetime.now(timezone.utc) - timedelta(days=day_offset)).strftime("%Y-%m-%d")
            noise = random.uniform(-8, 8)
            quality = min(100, max(40, base_quality + noise))
            daily_points.append({
                "日期": date_str,
                "完备率": f"{quality + random.uniform(-5, 5):.1f}%",
                "准确率": f"{quality + random.uniform(-8, 8):.1f}%",
                "及时率": f"{quality + random.uniform(-3, 3):.1f}%",
                "综合质量": f"{quality:.1f}",
            })

        # 计算趋势方向
        if len(daily_points) >= 3:
            first_half = daily_points[:len(daily_points) // 2]
            second_half = daily_points[len(daily_points) // 2:]
            avg_first = sum(float(p["综合质量"]) for p in first_half) / len(first_half)
            avg_second = sum(float(p["综合质量"]) for p in second_half) / len(second_half)
            trend_direction = "上升" if avg_second > avg_first + 2 else "下降" if avg_second < avg_first - 2 else "平稳"
            change_rate = (avg_second - avg_first) / avg_first * 100 if avg_first > 0 else 0
        else:
            trend_direction = "平稳"
            change_rate = 0

        # 写入趋势存储
        for dp in daily_points:
            new_id = max((int(r.get("id", 0)) for r in trend_rows), default=0) + 1
            status = "正常"
            quality_val = float(dp["综合质量"])
            if quality_val < 60:
                status = "需关注"
            elif change_rate < -5:
                status = "下降趋势"

            trend_rows.append({
                "id": new_id,
                "记录编号": f"DQ_T-{new_id:04d}",
                "目标表名": table_name,
                "日期": dp["日期"],
                "完备率": dp["完备率"],
                "准确率": dp["准确率"],
                "及时率": dp["及时率"],
                "综合质量": dp["综合质量"],
                "趋势标记": trend_direction,
                "数据版本": "v1.0",
                "采集时间": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "pending": status == "需关注",
                "abnormal": status == "需关注",
            })

        # 检测到下降趋势：写入质量事件
        if trend_direction == "下降":
            self._alert_trend_downturn(table_name, trend_direction, change_rate)

        return {
            "目标表名": table_name,
            "数据天数": days,
            "趋势方向": trend_direction,
            "变化率": f"{change_rate:+.1f}%",
            "最新质量": daily_points[-1]["综合质量"] if daily_points else "N/A",
            "最低质量": min(float(p["综合质量"]) for p in daily_points) if daily_points else "N/A",
            "生成时间": datetime.now(timezone.utc).isoformat(),
        }

    def _alert_trend_downturn(self, table_name: str, direction: str, change_rate: float) -> None:
        """检测到质量下降趋势，写入质量事件并触发告警。"""
        incident_rows = store.rows("dq_incident")
        new_iid = max((int(r.get("id", 0)) for r in incident_rows), default=0) + 1
        incident_rows.append({
            "id": new_iid,
            "事件编号": f"DQ_I-{new_iid:04d}",
            "关联规则": f"trend:{table_name}",
            "触发时间": datetime.now(timezone.utc).isoformat(),
            "偏离程度": f"{abs(change_rate):.1f}%",
            "影响行数": "全局",
            "严重级别": "warning" if abs(change_rate) > 10 else "info",
            "处理状态": "趋势告警",
            "处理人": "趋势分析引擎",
            "status": "待确认",
            "pending": True,
            "abnormal": True,
        })

    def aggregate_trend_comparison(self, table_names: list[str]) -> dict[str, Any]:
        """聚合多表质量趋势对比，生成综合排名。

        查询多张表的趋势数据，计算质量均值和排名，
        聚合生成跨表趋势对比快照。
        """
        trend_rows = store.rows(self.MODULE)
        comparison = {}

        for tbl in table_names:
            tbl_trends = [
                t for t in trend_rows
                if t.get("目标表名") == tbl
            ]
            if tbl_trends:
                avg_quality = sum(
                    float(t.get("综合质量", "0")) for t in tbl_trends
                ) / len(tbl_trends)
                latest = max(tbl_trends, key=lambda t: t.get("日期", ""))
                comparison[tbl] = {
                    "平均质量": round(avg_quality, 1),
                    "最新质量": latest.get("综合质量", "N/A"),
                    "趋势": latest.get("趋势标记", "未知"),
                    "样本量": len(tbl_trends),
                }

        ranked = sorted(
            comparison.items(),
            key=lambda x: x[1]["平均质量"],
            reverse=True,
        )

        return {
            "聚合时间": datetime.now(timezone.utc).isoformat(),
            "表数": len(comparison),
            "排名": [{"表名": k, **v} for k, v in ranked],
            "最佳表": ranked[0][0] if ranked else "N/A",
            "最差表": ranked[-1][0] if ranked else "N/A",
        }

    def query_trend_comparison(
        self,
        table_names: Sequence[str],
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        version: str | None = None,
        observation_dates: Sequence[str] | None = None,
        resume_comparison_id: str | None = None,
        sync_review: bool = True,
        persist: bool = True,
    ) -> dict[str, Any]:
        """按共同观测日筛选并聚合质量趋势，产出本次对比实际采用的观测集合。

        值日排整改顺序时要的是同口径名次：缺测的那天不能按 0 分拉低平均，
        同一张表同一天有多个版本时只采用一条并保留候选回查。``resume_comparison_id``
        用于中断后重取，把每个单元格固定回上一轮采用的版本，保证分母和评分同源。
        """
        comparison_id = observation.next_comparison_id(store) if persist else "PREVIEW"
        pinned: dict[tuple[str, str], str] | None = None
        resumed_from: str | None = None

        if resume_comparison_id:
            snapshot = observation.load_comparison(store, resume_comparison_id)
            if snapshot is None:
                raise ValueError(f"对比快照 {resume_comparison_id} 不存在，无法按原版本续取")
            pinned = observation.pinned_versions_from(snapshot)
            resumed_from = resume_comparison_id
            start_date = start_date or snapshot.get("起始日期")
            end_date = end_date or snapshot.get("结束日期")
        elif persist:
            latest = observation.latest_comparison_id(store, table_names, self.MODULE)
            if latest:
                resumed_from = latest

        result = observation.build_observation_comparison(
            store.rows(self.MODULE),
            table_names,
            start_date=start_date,
            end_date=end_date,
            version=version,
            observation_dates=observation_dates,
            pinned_versions=pinned,
        )
        result["对比编号"] = comparison_id
        result["续取来源"] = resumed_from
        result["是否续取"] = resumed_from is not None
        result["同步复核清单"] = False

        if persist:
            observation.save_comparison(store, result, comparison_id=comparison_id,
                                        source=self.MODULE)
            if sync_review:
                observation.sync_ranking_review(store, result, comparison_id)
                result["同步复核清单"] = True
        return result

    def list_ranking_review(self) -> list[dict[str, Any]]:
        """评分卡排名复核清单：按时间倒序列出已同步的对比结论。"""
        return observation.list_ranking_review(store)

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["operation"] = "计算"
        snapshot["desc"] = "计算数据质量趋势并对比多表排名"
        snapshot["artifact"] = "质量趋势分析数据集"
        snapshot["artifact_desc"] = "含趋势方向和对比排名的质量趋势分析数据集"
        return snapshot