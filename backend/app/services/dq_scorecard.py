"""质量评分卡服务：计算表级数据质量多维度评分，聚合规则执行结果生成综合评分。

domain_chain: 质量评分卡 (dq_scorecard)
  └─ 计算 → 质量趋势 (dq_trend)
  └─ 聚合 → 质量报告 (dq_report)
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any, Sequence

from app.services import observation
from app.services.base import BaseModuleService
from app.store import store


class DqScorecardService(BaseModuleService):
    MODULE = "dq_scorecard"
    ENTITY = "评分记录"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["记录编号", "目标表名", "完整性得分", "准确性得分", "时效性得分", "综合得分", "评分时间", "评分版本"]
    REQUIRED_FIELDS = ["记录编号", "目标表名", "完整性得分"]
    STATUS_ORDER = ["草稿", "计算中", "已完成", "已归档"]
    ACTION_RULES = {"发起评分": "计算中", "确认完成": "已完成", "归档记录": "已归档"}
    NEGATIVE_ACTIONS = []

    def calculate_quality_score(self, table_name: str, rule_results: list[dict] | None = None) -> dict[str, Any]:
        """计算表级综合质量评分：完整性、准确性、时效性、一致性、唯一性五个维度。

        聚合该表所有质量规则执行结果，分别计算各维度得分，加权汇总为综合得分。
        计算完成后将评分结果关联写入质量趋势记录。
        """
        if rule_results is None:
            # 模拟规则执行结果
            rule_results = [
                {"规则类型": "空值检查", "结果": random.choice(["通过", "通过", "通过", "失败"]), "权重": 0.25},
                {"规则类型": "值域范围", "结果": random.choice(["通过", "通过", "通过", "失败"]), "权重": 0.20},
                {"规则类型": "格式校验", "结果": random.choice(["通过", "通过", "失败"]), "权重": 0.15},
                {"规则类型": "唯一性检查", "结果": random.choice(["通过", "通过", "通过", "通过", "失败"]), "权重": 0.20},
                {"规则类型": "时效性检查", "结果": random.choice(["通过", "通过", "失败"]), "权重": 0.10},
                {"规则类型": "参照完整性", "结果": random.choice(["通过", "通过", "通过", "失败"]), "权重": 0.10},
            ]

        dimension_scores = {}
        passed = 0
        total_weight = 0
        for rule in rule_results:
            dim = rule["规则类型"]
            weight = rule["权重"]
            score = 100 if rule["结果"] == "通过" else random.uniform(20, 60)
            dimension_scores[dim] = round(score, 1)
            if rule["结果"] == "通过":
                passed += 1
            total_weight += weight * score

        completeness = dimension_scores.get("空值检查", 85)
        accuracy = (dimension_scores.get("值域范围", 85) + dimension_scores.get("格式校验", 85)) / 2
        timeliness = dimension_scores.get("时效性检查", 85)
        overall = round(total_weight, 1)

        # 写入评分卡记录
        rows = store.rows(self.MODULE)
        new_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        scored_at = datetime.now(timezone.utc)
        entry = {
            "id": new_id,
            "记录编号": f"DQ_S-{new_id:04d}",
            "目标表名": table_name,
            "完整性得分": str(round(completeness, 1)),
            "准确性得分": str(round(accuracy, 1)),
            "时效性得分": str(round(timeliness, 1)),
            "综合得分": str(round(overall, 1)),
            "评分时间": scored_at.isoformat(),
            "评分版本": "v1.0",
            "数据版本": "v1.0",
            "status": "已完成",
            "pending": False,
            "abnormal": overall < 60,
        }
        rows.append(entry)

        # 聚合评分结果写入趋势记录
        self._write_trend_record(table_name, completeness, accuracy, timeliness, overall,
                                 scored_at=scored_at, version="v1.0")

        return {
            "目标表名": table_name,
            "规则总数": len(rule_results),
            "通过数": passed,
            "通过率": f"{passed / len(rule_results) * 100:.1f}%",
            "完整性得分": round(completeness, 1),
            "准确性得分": round(accuracy, 1),
            "时效性得分": round(timeliness, 1),
            "综合得分": round(overall, 1),
            "等级": "A" if overall >= 90 else "B" if overall >= 75 else "C" if overall >= 60 else "D",
            "记录编号": entry["记录编号"],
        }

    def _write_trend_record(self, table_name: str, completeness: float, accuracy: float,
                            timeliness: float, overall: float,
                            *, scored_at: datetime | None = None, version: str = "v1.0") -> None:
        """聚合评分数据写入质量趋势记录。

        评分卡和趋势记录用同一时间、同一版本落库，后续按 (表, 日期, 版本) 回查时
        两边对得上，不会出现评分卡取到 v1、趋势取到 v2 的分母错位。
        """
        trend_rows = store.rows("dq_trend")
        new_tid = max((int(r.get("id", 0)) for r in trend_rows), default=0) + 1
        moment = scored_at or datetime.now(timezone.utc)
        trend_rows.append({
            "id": new_tid,
            "记录编号": f"DQ_T-{new_tid:04d}",
            "目标表名": table_name,
            "日期": moment.strftime("%Y-%m-%d"),
            "完备率": f"{completeness:.1f}%",
            "准确率": f"{accuracy:.1f}%",
            "及时率": f"{timeliness:.1f}%",
            "综合质量": f"{overall:.1f}",
            "趋势标记": "正常" if overall >= 75 else "下降趋势",
            "评分版本": version,
            "数据版本": version,
            "采集时间": moment.isoformat(),
            "status": "正常" if overall >= 75 else "下降趋势",
            "pending": False,
            "abnormal": overall < 60,
        })

    def filter_trend_observations(
        self,
        table_names: Sequence[str],
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        version: str | None = None,
        observation_dates: Sequence[str] | None = None,
        use_scorecard: bool = False,
        resume_comparison_id: str | None = None,
    ) -> dict[str, Any]:
        """按观测集合过滤缺测样本，整理实际采用的时间和范围，供评分卡排名复核。

        默认以质量趋势记录为观测来源；``use_scorecard=True`` 时直接以评分卡记录为准。
        缺测日期不按 0 分计入，同一 (表, 日期) 只采用一条记录，其余保留候选回查。
        """
        if resume_comparison_id:
            snapshot = observation.load_comparison(store, resume_comparison_id)
            if snapshot is None:
                raise ValueError(f"对比快照 {resume_comparison_id} 不存在，无法按原版本续取")
            pinned = observation.pinned_versions_from(snapshot)
            start_date = start_date or snapshot.get("起始日期")
            end_date = end_date or snapshot.get("结束日期")
        else:
            pinned = None

        source = "dq_scorecard" if use_scorecard else "dq_trend"
        rows = store.rows(source)
        result = observation.build_observation_comparison(
            rows,
            table_names,
            start_date=start_date,
            end_date=end_date,
            version=version,
            observation_dates=observation_dates,
            pinned_versions=pinned,
            version_keys=("数据版本", "评分版本", "版本"),
            score_keys=("综合得分", "综合质量", "评分") if use_scorecard
            else ("综合质量", "综合得分", "评分"),
            time_keys=("评分时间", "采集时间", "更新时间") if use_scorecard
            else ("采集时间", "评分时间", "更新时间"),
            score_label="综合得分" if use_scorecard else "综合质量",
        )
        result["观测来源"] = source
        result["观测来源名称"] = "质量评分卡" if use_scorecard else "质量趋势"
        result["续取来源"] = resume_comparison_id
        result["是否续取"] = resume_comparison_id is not None
        result["同步复核清单"] = False

        if resume_comparison_id:
            observation.sync_ranking_review(store, result, resume_comparison_id)
            result["同步复核清单"] = True
        return result

    def ranking_review_list(self) -> list[dict[str, Any]]:
        """评分卡排名复核清单：趋势查询同步过来的结论，含日期、缺数表和名次。"""
        return observation.list_ranking_review(store)

    def domain_snapshot(self) -> dict[str, Any]:
        snapshot = super().domain_snapshot()
        snapshot["operation"] = "计算"
        snapshot["desc"] = "计算表级多维度质量评分并聚合为评分卡"
        snapshot["artifact"] = "质量评分卡数据集"
        snapshot["artifact_desc"] = "聚合质量规则执行结果的评分卡数据集"
        return snapshot