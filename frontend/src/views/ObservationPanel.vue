<template>
  <section class="observation-panel">
    <header class="page-head">
      <div>
        <h3>{{ isScorecard ? '评分卡观测过滤与排名复核' : '质量趋势对比（共同观测日）' }}</h3>
        <p class="page-desc">
          {{ isScorecard
            ? '按同一套观测集合口径过滤缺测样本，整理实际采用的时间和范围；复核清单来自趋势查询结论。'
            : '缺测日期不计 0 分，同一张表同一天有多个版本时只采用一条并保留候选回查。' }}
        </p>
      </div>
    </header>

    <form class="analysis-form" @submit.prevent="runQuery">
      <label>
        <span>对比组（逗号分隔）</span>
        <input v-model="filters.tables" data-testid="obs-tables" placeholder="dwd_trade_detail,ods_order_info" />
      </label>
      <label>
        <span>开始日期</span>
        <input v-model="filters.startDate" data-testid="obs-start" type="date" />
      </label>
      <label>
        <span>结束日期</span>
        <input v-model="filters.endDate" data-testid="obs-end" type="date" />
      </label>
      <label>
        <span>固定版本</span>
        <input v-model="filters.version" data-testid="obs-version" placeholder="留空按逐日最高版本" />
      </label>
      <label>
        <span>指定观测日</span>
        <input v-model="filters.dates" data-testid="obs-dates" placeholder="2026-11-10,2026-11-12" />
      </label>
      <label>
        <span>按对比编号续取</span>
        <input v-model="filters.resumeComparisonId" data-testid="obs-resume" placeholder="CMP-0001" />
      </label>
      <label class="inline-check" v-if="isScorecard">
        <span>观测来源</span>
        <select v-model="filters.useScorecard" data-testid="obs-source">
          <option :value="true">质量评分卡</option>
          <option :value="false">质量趋势</option>
        </select>
      </label>
      <button class="btn primary" data-testid="obs-query" type="submit" :disabled="loading">
        {{ loading ? '查询中…' : '查询观测集合' }}
      </button>
      <button class="btn ghost" type="button" data-testid="obs-reset" @click="resetFilters">重置条件</button>
    </form>

    <p v-if="errorMessage" class="error-text" data-testid="obs-error">{{ errorMessage }}</p>

    <template v-if="result">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">进入比较的日期</span>
          <strong class="stat-value">{{ result.进入比较的日期.length }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">分母（共同观测日）</span>
          <strong class="stat-value" data-testid="obs-denominator">{{ result.分母 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">可参与排序表</span>
          <strong class="stat-value">{{ result.排名.length }} / {{ result.对比组.length }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">复核状态</span>
          <strong class="stat-value" :class="{ warn: !result.可用于复核 }" data-testid="obs-review-state">
            {{ result.复核状态 }}
          </strong>
        </article>
      </div>

      <p class="observation-hint" data-testid="obs-hint">{{ result.复核提示 }}</p>
      <p class="observation-meta">
        <span>对比编号：{{ result.对比编号 }}</span>
        <span>观测范围：{{ result.起始日期 || '—' }} ~ {{ result.结束日期 || '—' }}</span>
        <span>取数版本：{{ result.取数版本 }}</span>
        <span v-if="result.是否续取">按 {{ result.续取来源 }} 续取</span>
      </p>

      <h4>实际采用的观测集合</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>表名</th><th>日期</th><th>记录编号</th><th>版本</th><th>评分</th>
            <th>候选数</th><th>是否进入比较</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in result.观测集合" :key="`${item.表名}-${item.日期}-${item.记录编号}`">
            <td>{{ item.表名 }}</td>
            <td>{{ item.日期 }}</td>
            <td>{{ item.记录编号 }}</td>
            <td>{{ item.数据版本 }}</td>
            <td>{{ item.评分值 }}</td>
            <td>{{ item.候选数 }}</td>
            <td>{{ item.进入比较 ? '是' : '否（不在共同观测日）' }}</td>
          </tr>
          <tr v-if="!result.观测集合.length">
            <td colspan="7" class="empty-state">选定范围内没有有效评分记录</td>
          </tr>
        </tbody>
      </table>

      <div class="observation-grid">
        <div>
          <h4>缺数的表</h4>
          <ul class="observation-list" data-testid="obs-missing">
            <li v-for="item in result.缺数表" :key="item.表名">
              <strong>{{ item.表名 }}</strong>：缺 {{ item.缺失日期.join('、') || '—' }}
            </li>
            <li v-if="!result.缺数表.length" class="muted">没有缺测样本</li>
          </ul>
          <h4>同一天多版本回查</h4>
          <ul class="observation-list" data-testid="obs-version-trace">
            <li v-for="item in result.版本回查" :key="`${item.表名}-${item.日期}`">
              <strong>{{ item.表名 }} {{ item.日期 }}</strong>：采用 {{ item.采用记录编号 }}（{{ item.采用版本 }}），候选
              <span v-for="candidate in item.候选记录" :key="candidate.记录编号">
                {{ candidate.记录编号 }}/{{ candidate.数据版本 }}={{ candidate.评分值 }}
              </span>
            </li>
            <li v-if="!result.版本回查.length" class="muted">没有同日多版本记录</li>
          </ul>
        </div>
        <div>
          <h4>排名（仅同口径样本）</h4>
          <table class="data-table">
            <thead><tr><th>名次</th><th>表名</th><th>平均质量</th><th>样本量</th><th>采用版本</th></tr></thead>
            <tbody>
              <tr v-for="item in result.排名" :key="item.表名" :data-testid="`obs-rank-${item.表名}`">
                <td>{{ item.名次 }}</td>
                <td>{{ item.表名 }}</td>
                <td>{{ item.平均质量 }}</td>
                <td>{{ item.样本量 }}</td>
                <td>{{ item.采用版本.join('、') }}</td>
              </tr>
              <tr v-if="!result.排名.length">
                <td colspan="5" class="empty-state">还没有可同口径排序的表</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <template v-if="isScorecard">
      <h4>排名复核清单</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>对比编号</th><th>对比组</th><th>进入比较的日期</th><th>分母</th>
            <th>缺数表</th><th>可用于复核</th><th>更新时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in reviews" :key="item.对比编号" :data-testid="`review-${item.对比编号}`">
            <td>{{ item.对比编号 }}</td>
            <td>{{ item.对比组.join('、') }}</td>
            <td>{{ item.进入比较的日期.join('、') || '—' }}</td>
            <td>{{ item.分母 }}</td>
            <td>
              <span v-for="missing in item.缺数表" :key="missing.表名">
                {{ missing.表名 }}（{{ missing.缺失日期.join('、') || '—' }}）
              </span>
              <span v-if="!item.缺数表.length">—</span>
            </td>
            <td>{{ item.可用于复核 ? '是' : item.复核状态 }}</td>
            <td>{{ item.更新时间 }}</td>
          </tr>
          <tr v-if="!reviews.length">
            <td colspan="7" class="empty-state">尚无同步的排名复核结论，可先在质量趋势看板发起对比</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'

import {
  fetchRankingReview,
  queryScorecardObservations,
  queryTrendComparison,
  type ObservationQuery,
  type ObservationResult,
  type RankingReview,
} from '@/api/backend-service'

const props = defineProps<{ moduleKey: string }>()
const isScorecard = computed(() => props.moduleKey === 'dq_scorecard')

const filters = reactive({
  tables: 'dwd_trade_detail,ods_order_info',
  startDate: '2026-11-10',
  endDate: '2026-11-12',
  version: '',
  dates: '',
  resumeComparisonId: '',
  useScorecard: true,
})

const result = ref<ObservationResult | null>(null)
const reviews = ref<RankingReview[]>([])
const errorMessage = ref('')
const loading = ref(false)

function buildQuery(): ObservationQuery {
  return {
    tables: filters.tables,
    startDate: filters.startDate,
    endDate: filters.endDate,
    version: filters.version,
    dates: filters.dates,
    resumeComparisonId: filters.resumeComparisonId,
    useScorecard: isScorecard.value ? filters.useScorecard : false,
    preview: true,
  }
}

async function loadReviews() {
  if (!isScorecard.value) return
  try {
    reviews.value = await fetchRankingReview()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '排名复核清单读取失败'
  }
}

async function runQuery() {
  errorMessage.value = ''
  loading.value = true
  try {
    if (isScorecard.value) {
      result.value = await queryScorecardObservations(buildQuery())
      await loadReviews()
    } else {
      result.value = await queryTrendComparison(buildQuery())
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '观测集合查询失败'
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.tables = 'dwd_trade_detail,ods_order_info'
  filters.startDate = '2026-11-10'
  filters.endDate = '2026-11-12'
  filters.version = ''
  filters.dates = ''
  filters.resumeComparisonId = ''
  filters.useScorecard = true
  result.value = null
  errorMessage.value = ''
}

watch(() => props.moduleKey, () => {
  result.value = null
  errorMessage.value = ''
  void loadReviews()
})

onMounted(() => {
  void loadReviews()
  void runQuery()
})
</script>

<style scoped>
.observation-panel { margin-top: 20px; padding-top: 16px; border-top: 2px solid var(--border); }
.observation-panel h3 { margin: 0; }
.observation-panel h4 { margin: 16px 0 8px; font-size: 14px; }
.observation-hint { font-size: 13px; background: #fff7e6; border: 1px solid #f5d9a8; padding: 8px 12px; border-radius: 6px; }
.observation-meta { display: flex; flex-wrap: wrap; gap: 16px; font-size: 12px; color: var(--muted); }
.observation-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.observation-list { margin: 0; padding-left: 18px; font-size: 13px; }
.observation-list li { margin-bottom: 4px; }
.muted { color: var(--muted); }
.stat-value.warn { color: #b42318; }
.inline-check select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
</style>
