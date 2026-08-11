import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

import api from '../client'
import {
  getEvolutionOperations,
  retryEvolutionJob,
  rollbackEvolutionRelease,
  updateExperienceStatus,
} from '../evolution'

describe('evolution api', () => {
  beforeEach(() => vi.clearAllMocks())

  it('使用明确治理动作更新经验', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: {} })

    await updateExperienceStatus('exp-1', 'observe')
    await updateExperienceStatus('exp-2', 'reapply')
    await updateExperienceStatus('exp-3', 'delete')

    expect(api.post).toHaveBeenNthCalledWith(1, '/evolution/experiences/exp-1/status', {
      action: 'observe',
    })
    expect(api.post).toHaveBeenNthCalledWith(2, '/evolution/experiences/exp-2/status', {
      action: 'reapply',
    })
    expect(api.post).toHaveBeenNthCalledWith(3, '/evolution/experiences/exp-3/status', {
      action: 'delete',
    })
  })

  it('加载任务与发布版本', async () => {
    vi.mocked(api.get)
      .mockResolvedValueOnce({ data: { items: [{ job_id: 'job-1' }] } })
      .mockResolvedValueOnce({ data: { items: [{ version: 2 }] } })

    const result = await getEvolutionOperations()

    expect(result.jobs).toEqual([{ job_id: 'job-1' }])
    expect(result.releases).toEqual([{ version: 2 }])
  })

  it('支持失败任务重试与安全回滚', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: {} })

    await retryEvolutionJob('job-1')
    await rollbackEvolutionRelease(3)

    expect(api.post).toHaveBeenNthCalledWith(1, '/evolution/jobs/job-1/retry')
    expect(api.post).toHaveBeenNthCalledWith(2, '/evolution/releases/3/rollback')
  })
})
