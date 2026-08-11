<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  getEvolutionItems,
  getEvolutionOperations,
  getEvolutionOverview,
  getSourceSnippet,
  retryEvolutionJob,
  rollbackEvolutionRelease,
  updateExperienceStatus,
  type ExperienceAction,
  type SourceSnippet,
} from '../api/evolution'

interface SourceLocation {
  source_id: string
  label: string
  path: string
  symbol: string
  line: number
}

type Item = Record<string, unknown>

const overview = ref<Record<string, unknown>>({})
const evaluations = ref<Item[]>([])
const failures = ref<Item[]>([])
const experiences = ref<Item[]>([])
const jobs = ref<Item[]>([])
const releases = ref<Item[]>([])
const loading = ref(true)
const actionError = ref('')
const sourceSnippet = ref<SourceSnippet | null>(null)
const sourceLoading = ref(false)
const sourceError = ref('')

const overviewLabels: Record<string, string> = {
  evaluation_count: '评审总数',
  average_score: '平均得分',
  failure_count: '失败案例',
  candidate_count: '候选经验',
  active_count: '已发布经验',
  observing_count: '观察中经验',
}
const statusLabels: Record<string, string> = {
  candidate: '待审核',
  active: '已发布',
  observing: '观察发布',
  rejected: '已驳回',
  retired: '已停用',
}
const scopeLabels: Record<string, string> = {
  private: '个人经验',
  global: '全局经验',
}
const verdictLabels: Record<string, string> = {
  high: '优质',
  medium: '合格',
  low: '需改进',
}
const typeLabels: Record<string, string> = {
  response_strategy: '回答策略',
  prompt_guidance: '提示词指导',
  routing_rule: '路由规则',
  retrieval_hint: '检索策略',
  context_strategy: '上下文策略',
  medical_knowledge: '医学知识',
}
const riskLabels: Record<string, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
}
const triggerLabels: Record<string, string> = {
  user_feedback: '用户反馈触发',
  sampling: '自动抽样',
  manual: '人工评审',
}
const feedbackLabels: Record<string, string> = {
  like: '有帮助',
  dislike: '需改进',
}
const attributionLabels: Record<string, string> = {
  prompt: '提示词问题',
  retrieval: '知识检索问题',
  tool_call: '工具调用问题',
  routing: '工作流路由问题',
  memory_profile: '用户画像或记忆问题',
  synthesis: '结果综合问题',
  other: '其他问题',
}

const overviewCards = computed(() =>
  Object.entries(overviewLabels).map(([key, label]) => ({
    key,
    label,
    value: overview.value[key] ?? 0,
  })),
)

const jobCounts = computed(() => (overview.value.job_counts || {}) as Record<string, number>)

function observationMetric(item: Item, bucket: 'treatment' | 'control', key: string) {
  const metrics = item.observation_metrics as Record<string, Record<string, unknown>> | undefined
  return metrics?.[bucket]?.[key] ?? 0
}

function formatEvidence(value: unknown): string {
  if (!Array.isArray(value) || !value.length) return '无'
  return value
    .map((item) => {
      if (typeof item === 'string') return item
      const evidence = item as Record<string, unknown>
      return String(
        evidence.source || evidence.filename || evidence.url || evidence.doc_id || '未知来源',
      )
    })
    .join('、')
}

function translate(value: unknown, labels: Record<string, string>): string {
  const key = String(value || '')
  return labels[key] || '未知'
}

function formatAttributions(value: unknown): string {
  let causes: string[] = []
  if (Array.isArray(value)) {
    causes = value.map(String)
  } else if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      causes = Array.isArray(parsed) ? parsed.map(String) : [value]
    } catch {
      causes = [value]
    }
  }
  return causes.map((cause) => attributionLabels[cause] || '其他问题').join('、')
}

function formatReviewAttributions(value: unknown): string {
  if (!Array.isArray(value)) return ''
  return value
    .map(String)
    .filter((cause) => cause !== 'other')
    .map((cause) => attributionLabels[cause] || '其他问题')
    .join('、')
}

function formatList(value: unknown): string {
  return Array.isArray(value) && value.length ? value.map(String).join('、') : '无'
}

function formatDate(value: unknown): string {
  if (!value) return '时间未知'
  const date = new Date(String(value))
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN')
}

function sourceLocations(value: unknown): SourceLocation[] {
  return Array.isArray(value) ? (value as SourceLocation[]) : []
}

async function openSource(sourceId: string) {
  sourceLoading.value = true
  sourceError.value = ''
  try {
    sourceSnippet.value = await getSourceSnippet(sourceId)
  } catch {
    sourceError.value = '源码片段加载失败'
  } finally {
    sourceLoading.value = false
  }
}

function closeSource() {
  sourceSnippet.value = null
  sourceError.value = ''
}

async function load() {
  loading.value = true
  try {
    const operations = getEvolutionOperations()
    ;[overview.value, evaluations.value, failures.value, experiences.value] = await Promise.all([
      getEvolutionOverview(),
      getEvolutionItems('evaluations'),
      getEvolutionItems('failures'),
      getEvolutionItems('experiences'),
    ])
    const operationData = await operations
    jobs.value = operationData.jobs
    releases.value = operationData.releases
  } finally {
    loading.value = false
  }
}

async function setStatus(id: string, action: ExperienceAction) {
  actionError.value = ''
  try {
    await updateExperienceStatus(id, action)
    await load()
  } catch (error: unknown) {
    const response = (error as { response?: { data?: { detail?: string } } }).response
    actionError.value = response?.data?.detail || '操作失败，请稍后重试'
  }
}

async function deleteExperience(id: string) {
  if (!window.confirm('确定永久删除这条已驳回经验吗？')) return
  await setStatus(id, 'delete')
}

async function retryJob(id: string) {
  actionError.value = ''
  try {
    await retryEvolutionJob(id)
    await load()
  } catch {
    actionError.value = '任务重新入队失败'
  }
}

async function rollback(version: number) {
  actionError.value = ''
  try {
    await rollbackEvolutionRelease(version)
    await load()
  } catch (error: unknown) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
    actionError.value = typeof detail === 'string' ? detail : '版本包含不安全经验，无法回滚'
  }
}

onMounted(load)
</script>

<template>
  <main class="h-full overflow-y-auto bg-slate-50 p-6">
    <div class="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 class="text-xl font-semibold text-slate-800">自进化中心</h1>
        <p class="mt-1 text-sm text-slate-500">评审对话质量，审批经验并持续优化策略。</p>
      </div>

      <div class="grid grid-cols-2 gap-4 md:grid-cols-6">
        <div
          v-for="card in overviewCards"
          :key="card.key"
          class="rounded-xl bg-white p-4 shadow-sm"
        >
          <div class="text-xs text-slate-500">{{ card.label }}</div>
          <div class="mt-1 text-2xl font-semibold text-slate-800">{{ card.value }}</div>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div class="rounded-lg bg-white p-3 text-sm shadow-sm">
          待评审：{{ jobCounts.pending || 0 }}
        </div>
        <div class="rounded-lg bg-white p-3 text-sm shadow-sm">
          执行中：{{ jobCounts.running || 0 }}
        </div>
        <div class="rounded-lg bg-white p-3 text-sm shadow-sm">
          失败：{{ jobCounts.failed || 0 }}
        </div>
        <div class="rounded-lg bg-white p-3 text-sm shadow-sm">
          已过期：{{ jobCounts.superseded || 0 }}
        </div>
      </div>

      <section class="rounded-xl bg-white p-5 shadow-sm">
        <h2 class="mb-3 font-medium text-slate-800">经验候选与发布</h2>
        <div v-if="actionError" class="mb-3 rounded-lg bg-red-50 p-3 text-sm text-red-600">
          {{ actionError }}
        </div>
        <div v-if="loading" class="text-sm text-slate-400">加载中…</div>
        <div v-else-if="!experiences.length" class="text-sm text-slate-400">暂无经验候选</div>
        <div
          v-for="item in experiences"
          :key="String(item.experience_id)"
          class="border-t py-3 first:border-0"
        >
          <div class="flex items-start justify-between gap-4">
            <div class="min-w-0 flex-1">
              <div class="mb-1 flex flex-wrap gap-2 text-xs">
                <span class="rounded bg-blue-50 px-2 py-0.5 text-blue-700">
                  {{ translate(item.experience_type, typeLabels) }}
                </span>
                <span class="rounded bg-amber-50 px-2 py-0.5 text-amber-700">
                  {{ translate(item.risk_level, riskLabels) }}
                </span>
              </div>
              <div class="text-sm text-slate-700">{{ item.content }}</div>
              <div class="mt-1 text-xs text-slate-400">
                {{ translate(item.scope, scopeLabels) }} ·
                {{ translate(item.status, statusLabels) }} · 支持案例 {{ item.support_count }} ·
                独立用户 {{ item.distinct_users }} · 负面反馈 {{ item.negative_count }} · 平均得分
                {{ item.average_score }}
              </div>
              <div class="mt-2 space-y-1 text-xs text-slate-500">
                <div>适用条件：{{ formatList(item.applicability) }}</div>
                <div>排除条件：{{ formatList(item.exclusions) }}</div>
                <div>前置确认：{{ formatList(item.prerequisites) }}</div>
                <div>安全警示：{{ item.safety_notes || '无' }}</div>
                <div>证据来源：{{ formatEvidence(item.evidence_refs) }}</div>
                <div v-if="item.status === 'observing'">
                  观察组：{{ observationMetric(item, 'treatment', 'distinct_users') }} 人，均分
                  {{ observationMetric(item, 'treatment', 'average_score') }}；对照组：
                  {{ observationMetric(item, 'control', 'distinct_users') }} 人，均分
                  {{ observationMetric(item, 'control', 'average_score') }}
                </div>
              </div>
              <div
                v-if="!item.publishable && item.status === 'candidate'"
                class="mt-2 text-xs text-amber-600"
              >
                暂不可发布：{{ item.publication_blocker }}
              </div>
            </div>
            <div class="flex shrink-0 gap-2">
              <button
                v-if="item.status === 'candidate'"
                class="rounded bg-emerald-600 px-2 py-1 text-xs text-white disabled:cursor-not-allowed disabled:opacity-40"
                :disabled="
                  item.scope === 'global' ? !item.eligible_for_observation : !item.publishable
                "
                :title="
                  String(
                    item.scope === 'global'
                      ? item.observation_blocker || ''
                      : item.publication_blocker || '',
                  )
                "
                @click="
                  setStatus(
                    String(item.experience_id),
                    item.scope === 'global' ? 'observe' : 'activate',
                  )
                "
              >
                {{ item.scope === 'global' ? '开始灰度' : '激活' }}
              </button>
              <button
                v-if="item.status === 'candidate'"
                class="rounded bg-slate-200 px-2 py-1 text-xs"
                @click="setStatus(String(item.experience_id), 'reject')"
              >
                驳回
              </button>
              <button
                v-if="item.status === 'retired'"
                class="rounded bg-slate-200 px-2 py-1 text-xs"
                @click="setStatus(String(item.experience_id), 'reject')"
              >
                转为驳回
              </button>
              <button
                v-if="item.status === 'rejected'"
                class="rounded bg-blue-100 px-2 py-1 text-xs text-blue-700"
                @click="setStatus(String(item.experience_id), 'reapply')"
              >
                重新应用
              </button>
              <button
                v-if="item.status === 'rejected'"
                class="rounded bg-red-100 px-2 py-1 text-xs text-red-700"
                @click="deleteExperience(String(item.experience_id))"
              >
                删除
              </button>
              <button
                v-if="item.status === 'observing'"
                class="rounded bg-emerald-600 px-2 py-1 text-xs text-white disabled:cursor-not-allowed disabled:opacity-40"
                :disabled="!item.eligible_for_activation"
                :title="String(item.activation_blocker || '')"
                @click="setStatus(String(item.experience_id), 'activate')"
              >
                正式发布
              </button>
              <button
                v-if="item.status === 'active' || item.status === 'observing'"
                class="rounded bg-amber-100 px-2 py-1 text-xs text-amber-700"
                @click="setStatus(String(item.experience_id), 'retire')"
              >
                停用
              </button>
            </div>
          </div>
        </div>
      </section>

      <div class="grid gap-6 lg:grid-cols-2">
        <section class="rounded-xl bg-white p-5 shadow-sm">
          <h2 class="mb-3 font-medium text-slate-800">失败任务</h2>
          <div v-if="!jobs.some((job) => job.status === 'failed')" class="text-sm text-slate-400">
            暂无失败任务
          </div>
          <div
            v-for="job in jobs.filter((entry) => entry.status === 'failed').slice(0, 20)"
            :key="String(job.job_id)"
            class="flex items-center justify-between border-t py-3 text-sm first:border-0"
          >
            <div class="min-w-0">
              <div>回答 {{ job.assistant_message_id }} · 尝试 {{ job.attempts }} 次</div>
              <div class="truncate text-xs text-red-500">{{ job.last_error || '未知错误' }}</div>
            </div>
            <button
              class="rounded bg-blue-50 px-2 py-1 text-xs text-blue-700"
              @click="retryJob(String(job.job_id))"
            >
              重新入队
            </button>
          </div>
        </section>
        <section class="rounded-xl bg-white p-5 shadow-sm">
          <h2 class="mb-3 font-medium text-slate-800">发布版本</h2>
          <div v-if="!releases.length" class="text-sm text-slate-400">暂无发布版本</div>
          <div
            v-for="release in releases.slice(0, 20)"
            :key="String(release.release_id)"
            class="flex items-center justify-between border-t py-3 text-sm first:border-0"
          >
            <div>
              <div>版本 {{ release.version }} · {{ release.action }}</div>
              <div class="text-xs text-slate-400">{{ formatDate(release.created_at) }}</div>
            </div>
            <button
              class="rounded bg-amber-50 px-2 py-1 text-xs text-amber-700"
              @click="rollback(Number(release.version))"
            >
              安全回滚
            </button>
          </div>
        </section>
      </div>

      <div class="grid gap-6 lg:grid-cols-2">
        <section class="rounded-xl bg-white p-5 shadow-sm">
          <h2 class="mb-3 font-medium text-slate-800">最近评审</h2>
          <div v-if="!loading && !evaluations.length" class="text-sm text-slate-400">
            暂无评审记录
          </div>
          <div
            v-for="item in evaluations.slice(0, 20)"
            :key="String(item.evaluation_id)"
            class="border-t py-4 text-sm first:border-0"
          >
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div>
                <span class="font-medium">评分 {{ item.overall_score }}</span>
                <span class="ml-2 text-slate-500">
                  {{ translate(item.verdict, verdictLabels) }} ·
                  {{ translate(item.trigger_type, triggerLabels) }}
                </span>
              </div>
              <span class="text-xs text-slate-400">{{ formatDate(item.created_at) }}</span>
            </div>

            <div class="mt-3 rounded-lg bg-blue-50 p-3">
              <div class="text-xs font-medium text-blue-700">用户问题</div>
              <div class="mt-1 line-clamp-2 text-slate-700">
                {{ item.question || '原问题不可用' }}
              </div>
            </div>
            <div class="mt-2 rounded-lg bg-slate-50 p-3">
              <div class="text-xs font-medium text-slate-500">助手回答</div>
              <div class="mt-1 line-clamp-3 text-slate-600">
                {{ item.answer || '原回答不可用' }}
              </div>
            </div>

            <div class="mt-2 text-xs text-slate-500">
              <span>用户：{{ item.username || '匿名用户' }}</span>
              <span class="mx-2">·</span>
              <span>会话轮次：{{ Number(item.turn_index || 0) + 1 }}</span>
              <template v-if="item.feedback_rating">
                <span class="mx-2">·</span>
                <span>用户反馈：{{ translate(item.feedback_rating, feedbackLabels) }}</span>
              </template>
            </div>
            <div class="mt-2 text-sm text-slate-500">
              评审说明：{{ item.rationale || '暂无评审说明' }}
            </div>
            <div
              v-if="formatReviewAttributions(item.attribution)"
              class="mt-2 text-sm text-amber-700"
            >
              问题归因：{{ formatReviewAttributions(item.attribution) }}
            </div>
            <div class="mt-2 text-sm text-slate-600">
              <div class="font-medium">优化建议</div>
              <ul
                v-if="Array.isArray(item.recommendations) && item.recommendations.length"
                class="mt-1 list-disc space-y-1 pl-5"
              >
                <li v-for="suggestion in item.recommendations" :key="String(suggestion)">
                  {{ suggestion }}
                </li>
              </ul>
              <div v-else class="mt-1 text-slate-400">暂无优化建议</div>
            </div>

            <div class="mt-3 flex flex-wrap gap-3 text-xs">
              <RouterLink
                v-if="item.session_id"
                :to="`/chat/${item.session_id}`"
                class="font-medium text-blue-600 hover:text-blue-700"
              >
                查看完整对话
              </RouterLink>
              <RouterLink
                v-if="item.trace_id"
                :to="`/trace/${item.trace_id}`"
                class="font-medium text-blue-600 hover:text-blue-700"
              >
                查看调用链
              </RouterLink>
              <span class="text-slate-400">回答编号：{{ item.assistant_message_id }}</span>
            </div>
          </div>
        </section>
        <section class="rounded-xl bg-white p-5 shadow-sm">
          <h2 class="mb-3 font-medium text-slate-800">失败归因</h2>
          <div v-if="!loading && !failures.length" class="text-sm text-slate-400">暂无失败案例</div>
          <div
            v-for="item in failures.slice(0, 20)"
            :key="String(item.failure_id)"
            class="border-t py-4 text-sm first:border-0"
          >
            <div class="text-red-600">问题归因：{{ formatAttributions(item.root_causes) }}</div>
            <div class="text-slate-500">改进建议：{{ item.recommended_fix || '待补充' }}</div>
            <div class="mt-2 rounded-lg bg-slate-50 p-3">
              <div class="text-xs font-medium text-slate-500">对应对话</div>
              <div class="mt-1 line-clamp-2 text-slate-700">
                问：{{ item.question || '原问题不可用' }}
              </div>
              <div class="mt-1 line-clamp-2 text-slate-500">
                答：{{ item.answer || '原回答不可用' }}
              </div>
            </div>

            <div class="mt-3">
              <div class="text-xs font-medium text-slate-600">可能涉及的源码位置</div>
              <div class="mt-2 space-y-2">
                <button
                  v-for="location in sourceLocations(item.source_locations)"
                  :key="location.source_id"
                  class="block w-full rounded-lg border border-slate-200 px-3 py-2 text-left hover:border-blue-300 hover:bg-blue-50"
                  @click="openSource(location.source_id)"
                >
                  <div class="text-xs font-medium text-blue-700">{{ location.label }}</div>
                  <div class="mt-0.5 break-all font-mono text-[11px] text-slate-400">
                    {{ location.path }}:{{ location.line }} · {{ location.symbol }}
                  </div>
                </button>
              </div>
            </div>

            <div class="mt-3 flex flex-wrap gap-3 text-xs">
              <RouterLink
                v-if="item.session_id"
                :to="`/chat/${item.session_id}`"
                class="font-medium text-blue-600 hover:text-blue-700"
              >
                查看原对话
              </RouterLink>
              <RouterLink
                v-if="item.trace_id"
                :to="`/trace/${item.trace_id}`"
                class="font-medium text-blue-600 hover:text-blue-700"
              >
                查看调用链
              </RouterLink>
            </div>
          </div>
        </section>
      </div>
    </div>

    <div
      v-if="sourceSnippet || sourceLoading || sourceError"
      class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-6"
      @click.self="closeSource"
    >
      <div class="flex max-h-[85vh] w-full max-w-5xl flex-col rounded-xl bg-white shadow-xl">
        <div class="flex items-start justify-between border-b border-slate-200 p-4">
          <div>
            <div class="font-medium text-slate-800">
              {{ sourceSnippet?.label || '源码追溯' }}
            </div>
            <div v-if="sourceSnippet" class="mt-1 font-mono text-xs text-slate-400">
              {{ sourceSnippet.path }}:{{ sourceSnippet.line }} · {{ sourceSnippet.symbol }}
            </div>
          </div>
          <button
            class="rounded px-2 py-1 text-slate-500 hover:bg-slate-100"
            aria-label="关闭源码片段"
            @click="closeSource"
          >
            关闭
          </button>
        </div>
        <div class="min-h-0 overflow-auto p-4">
          <div v-if="sourceLoading" class="text-sm text-slate-400">源码加载中…</div>
          <div v-else-if="sourceError" class="text-sm text-red-600">{{ sourceError }}</div>
          <pre
            v-else-if="sourceSnippet"
            class="overflow-x-auto rounded-lg bg-slate-900 p-4 text-xs leading-6 text-slate-100"
          ><code>{{ sourceSnippet.content }}</code></pre>
        </div>
      </div>
    </div>
  </main>
</template>
