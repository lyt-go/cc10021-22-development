"""观测集合查询接口：质量趋势对比、评分卡观测过滤和排名复核清单。

这些接口属于跨模块查询，路径比通用资源路由更具体，必须在 resource 之前注册，
否则 /api/{module}/{entry_id} 会把 comparison、ranking-review 当成整数 id。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.dq_scorecard import DqScorecardService
from app.services.dq_trend import DqTrendService

router = APIRouter(prefix="/api", tags=["观测集合"])

trend_service = DqTrendService()
scorecard_service = DqScorecardService()


class ObservationComparisonPayload(BaseModel):
    tables: list[str] = Field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None
    version: str | None = None
    observation_dates: list[str] | None = None
    resume_comparison_id: str | None = None
    sync_review: bool = True
    use_scorecard: bool = False


class ReviewSyncPayload(BaseModel):
    comparison_id: str


def _split_tables(tables: str | None) -> list[str]:
    return [item.strip() for item in (tables or "").split(",") if item.strip()]


def _split_dates(dates: str | None) -> list[str] | None:
    if dates is None:
        return None
    return [item.strip() for item in dates.split(",") if item.strip()]


def _run_comparison(payload: ObservationComparisonPayload) -> dict[str, Any]:
    try:
        if payload.use_scorecard:
            return scorecard_service.filter_trend_observations(
                payload.tables,
                start_date=payload.start_date,
                end_date=payload.end_date,
                version=payload.version,
                observation_dates=payload.observation_dates,
                use_scorecard=True,
                resume_comparison_id=payload.resume_comparison_id,
            )
        return trend_service.query_trend_comparison(
            payload.tables,
            start_date=payload.start_date,
            end_date=payload.end_date,
            version=payload.version,
            observation_dates=payload.observation_dates,
            resume_comparison_id=payload.resume_comparison_id,
            sync_review=payload.sync_review,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/dq_trend/comparison", response_model=dict)
def query_trend_comparison(
    tables: str | None = Query(default=None, description="对比组表名，逗号分隔"),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    version: str | None = Query(default=None, description="固定版本；留空按逐日最高版本"),
    dates: str | None = Query(default=None, description="显式观测日，逗号分隔"),
    resume_comparison_id: str | None = Query(default=None, description="中断后按原快照版本续取"),
    sync_review: bool = Query(default=True),
    preview: bool = Query(default=False, description="只看结果，不落快照也不同步复核清单"),
) -> dict[str, Any]:
    """按共同观测日筛选并聚合趋势，返回本次实际采用的观测集合。"""
    payload = ObservationComparisonPayload(
        tables=_split_tables(tables),
        start_date=start_date,
        end_date=end_date,
        version=version,
        observation_dates=_split_dates(dates),
        resume_comparison_id=resume_comparison_id,
        sync_review=sync_review,
    )
    result = _run_comparison(payload)
    if preview:
        result["对比编号"] = "PREVIEW"
        result["同步复核清单"] = False
    return result


@router.post("/dq_trend/comparison", response_model=dict)
def record_trend_comparison(payload: ObservationComparisonPayload) -> dict[str, Any]:
    """执行一次趋势对比查询，落快照并把结论同步到评分卡排名复核清单。"""
    return _run_comparison(payload)


@router.get("/dq_trend/comparison/{comparison_id}", response_model=dict)
def get_trend_comparison(comparison_id: str) -> dict[str, Any]:
    """回查某轮对比实际采用的日期、记录和版本，供中断后重取对齐。"""
    from app.services import observation
    from app.store import store

    snapshot = observation.load_comparison(store, comparison_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"对比快照 {comparison_id} 不存在")
    return snapshot


@router.get("/dq_scorecard/observations", response_model=dict)
def filter_scorecard_observations(
    tables: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    version: str | None = Query(default=None),
    dates: str | None = Query(default=None),
    use_scorecard: bool = Query(default=True),
    resume_comparison_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """按观测集合过滤缺测样本，整理能参与排序的表和实际采用范围。"""
    payload = ObservationComparisonPayload(
        tables=_split_tables(tables),
        start_date=start_date,
        end_date=end_date,
        version=version,
        observation_dates=_split_dates(dates),
        resume_comparison_id=resume_comparison_id,
        use_scorecard=use_scorecard,
    )
    return _run_comparison(payload)


@router.get("/dq_scorecard/ranking-review", response_model=dict)
def list_ranking_review() -> dict[str, Any]:
    """评分卡排名复核清单：趋势查询同步过来的结论。"""
    items = scorecard_service.ranking_review_list()
    return {"items": items, "total": len(items)}


@router.post("/dq_scorecard/ranking-review", response_model=dict)
def sync_ranking_review(payload: ReviewSyncPayload) -> dict[str, Any]:
    """按对比编号重新同步评分卡排名复核清单。"""
    from app.services import observation
    from app.store import store

    snapshot = observation.load_comparison(store, payload.comparison_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"对比快照 {payload.comparison_id} 不存在")
    review = observation.sync_ranking_review(store, snapshot, payload.comparison_id)
    return {"ok": True, "message": "排名复核清单已同步", "review": review}
