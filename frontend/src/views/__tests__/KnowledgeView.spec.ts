import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import KnowledgeView from '../KnowledgeView.vue'
import { getDocuments } from '../../api/knowledge'

vi.mock('../../api/knowledge')

async function clickButton(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find((item) => item.text() === text)
  if (!button) throw new Error(`找不到按钮：${text}`)
  await button.trigger('click')
  await flushPromises()
}

describe('文档列表加载', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getDocuments).mockResolvedValue({ documents: [], total: 0 })
  })

  it('切换标签复用已加载的列表，刷新按钮仍重新请求', async () => {
    const wrapper = mount(KnowledgeView)
    await clickButton(wrapper, '文档管理')
    expect(getDocuments).toHaveBeenCalledTimes(1)
    await clickButton(wrapper, '搜索')
    await clickButton(wrapper, '文档管理')
    expect(getDocuments).toHaveBeenCalledTimes(1)
    await clickButton(wrapper, '刷新')
    expect(getDocuments).toHaveBeenCalledTimes(2)
  })

  it('首次请求失败后再次进入会重试', async () => {
    vi.mocked(getDocuments).mockRejectedValueOnce(new Error('网络失败'))
    const wrapper = mount(KnowledgeView)
    await clickButton(wrapper, '文档管理')
    await clickButton(wrapper, '搜索')
    await clickButton(wrapper, '文档管理')
    expect(getDocuments).toHaveBeenCalledTimes(2)
  })
})
