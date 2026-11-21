import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export type ObservationQuery = {
  tables: string
  startDate: string
  endDate: string
  version: string
  dates: string
  resumeComparisonId: string
  useScorecard: boolean
  preview: boolean
}

function observationQuery(query: ObservationQuery): string {
  const params = new URLSearchParams()
  if (query.tables) params.set('tables', query.tables)
  if (query.startDate) params.set('start_date', query.startDate)
  if (query.endDate) params.set('end_date', query.endDate)
  if (query.version) params.set('version', query.version)
  if (query.dates) params.set('dates', query.dates)
  if (query.resumeComparisonId) params.set('resume_comparison_id', query.resumeComparisonId)
  params.set('use_scorecard', String(query.useScorecard))
  params.set('preview', String(query.preview))
  return params.toString()
}

// 质量趋势对比：返回共同观测日、分母、缺数表和实际采用的记录/版本。
export async function queryTrendComparison(query: ObservationQuery): Promise<ObservationResult> {
  const response = await request(`/api/dq_trend/comparison?${observationQuery(query)}`)
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail ?? '质量趋势对比失败')
  return payload as ObservationResult
}

export async function fetchTrendComparison(comparisonId: string): Promise<Record<string, unknown>> {
  const response = await request(`/api/dq_trend/comparison/${comparisonId}`)
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail ?? '对比快照读取失败')
  return payload as Record<string, unknown>
}

// 评分卡观测过滤：按同一套观测集合口径过滤缺测样本。
export async function queryScorecardObservations(query: ObservationQuery): Promise<ObservationResult> {
  const response = await request(`/api/dq_scorecard/observations?${observationQuery(query)}`)
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail ?? '评分卡观测过滤失败')
  return payload as ObservationResult
}

export type RankingReview = {
  对比编号: string
  对比组: string[]
  观测日期范围: string[]
  进入比较的日期: string[]
  分母: number
  取数版本: string
  缺数表: { 表名: string; 缺失日期: string[] }[]
  待补录: { 表名: string; 日期: string; 原因: string }[]
  排名: { 名次: number; 表名: string; 平均质量: number; 样本量: number }[]
  可用于复核: boolean
  复核状态: string
  复核提示: string
  更新时间: string
}

export async function fetchRankingReview(): Promise<RankingReview[]> {
  const response = await request('/api/dq_scorecard/ranking-review')
  const payload = await response.json()
  if (!response.ok) throw new Error(payload.detail ?? '排名复核清单读取失败')
  return payload.items as RankingReview[]
}

export type ObservationRecord = {
  表名: string
  日期: string
  记录编号: string
  数据版本: string
  评分值: number
  候选数: number
}

export type ObservationResult = {
  对比编号: string
  对比组: string[]
  起始日期: string
  结束日期: string
  取数版本: string
  观测日期范围: string[]
  进入比较的日期: string[]
  共同观测日: string[]
  分母: number
  实际采用观测集合: ObservationRecord[]
  观测集合: (ObservationRecord & { 进入比较: boolean })[]
  版本回查: {
    表名: string
    日期: string
    采用记录编号: string
    采用版本: string
    候选记录: { 记录编号: string; 数据版本: string; 评分值: number }[]
  }[]
  缺数表: { 表名: string; 缺失日期: string[]; 观测天数: number }[]
  待补录: { 表名: string; 日期: string; 原因: string }[]
  不可参与排序表: string[]
  排名: {
    名次: number
    表名: string
    平均质量: number
    样本量: number
    采用记录编号: string[]
    采用版本: string[]
  }[]
  可用于复核: boolean
  复核状态: string
  复核提示: string
  续取来源: string | null
  是否续取: boolean
  同步复核清单: boolean
  观测来源名称?: string
}

