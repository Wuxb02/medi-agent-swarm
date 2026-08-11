import api from './client'

export type FeedbackRating = 'like' | 'dislike'

export interface Feedback {
  feedback_id: string
  assistant_message_id: string
  rating: FeedbackRating
  reason_codes: string[]
  comment: string
  version: number
}

export async function submitFeedback(
  assistantMessageId: string,
  rating: FeedbackRating,
  reasonCodes: string[] = [],
  comment = '',
): Promise<Feedback> {
  const { data } = await api.post('/evolution/feedback', {
    assistant_message_id: Number(assistantMessageId),
    rating,
    reason_codes: reasonCodes,
    comment,
  })
  return data
}

export async function getFeedback(assistantMessageId: string): Promise<Feedback | null> {
  const { data } = await api.get(`/evolution/feedback/${assistantMessageId}`)
  return data.feedback
}

export async function getEvolutionOverview() {
  const { data } = await api.get('/evolution/overview')
  return data
}

export async function getEvolutionItems(kind: 'evaluations' | 'failures' | 'experiences') {
  const { data } = await api.get(`/evolution/${kind}`)
  return data.items
}

export type ExperienceAction =
  | 'observe'
  | 'activate'
  | 'reject'
  | 'retire'
  | 'reapply'
  | 'delete'

export async function updateExperienceStatus(experienceId: string, action: ExperienceAction) {
  await api.post(`/evolution/experiences/${experienceId}/status`, { action })
}

export async function getEvolutionOperations() {
  const [jobs, releases] = await Promise.all([
    api.get('/evolution/jobs'),
    api.get('/evolution/releases'),
  ])
  return { jobs: jobs.data.items, releases: releases.data.items }
}

export async function retryEvolutionJob(jobId: string) {
  await api.post(`/evolution/jobs/${jobId}/retry`)
}

export async function rollbackEvolutionRelease(version: number) {
  await api.post(`/evolution/releases/${version}/rollback`)
}

export interface SourceSnippet {
  source_id: string
  label: string
  path: string
  symbol: string
  line: number
  start_line: number
  end_line: number
  content: string
}

export async function getSourceSnippet(sourceId: string): Promise<SourceSnippet> {
  const { data } = await api.get(`/evolution/sources/${encodeURIComponent(sourceId)}`)
  return data
}
