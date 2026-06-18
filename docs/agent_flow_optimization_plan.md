# Agent 流程优化方案与验收标准

> 基于 2026-06-12 架构审查，共识别 20 项优化点，按 P0→P3 优先级排序。

---

## P0 — 高收益低成本（建议首轮修复）

### 1. 记忆检索并行化

**现状**：[swarm/swarm_coordinator.py#L157-L167](swarm/swarm_coordinator.py#L157-L167)
短期记忆和长期记忆查询串行执行，不必要地叠加延迟。

**修改方案**：
- 将 `get_recent_messages()` 和 `search_similar_sessions()` 包裹在 `asyncio.gather()` 中并行执行
- 两者无数据依赖，且后端不同（ShortTermMemory 内存在线 / Mem0 云服务）

**涉及文件**：
- `swarm/swarm_coordinator.py` — `process()` 方法

**验收标准**：
- [x] `get_recent_messages` 和 `search_similar_sessions` 由 `asyncio.gather` 同时发起
- [x] 任一失败不影响另一结果（各自 try-except 包裹，返回空列表降级）
- [x] 现有测试全部通过

---

### 2. 消除短期记忆双重检索

**现状**：
[swarm/swarm_coordinator.py#L158-L161](swarm/swarm_coordinator.py#L158-L161) Coordinator 查了 10 条 `recent_history`，
[core/agent_loop.py#L547](core/agent_loop.py#L547) AgentLoop 初始化时又查 5 条 `get_history()`。
Coordinator 查到的数据仅传给 LeadAgent，未被 Worker 复用。

**修改方案**：
- 在 `AgentLoop` 新增属性 `preloaded_history: Optional[List[Dict]]`
- Coordinator 将已检索的 `recent_history` 注入 `worker.loop.preloaded_history`
- `AgentLoop._initialize_messages()` 优先使用 `preloaded_history`，跳过自身检索

**涉及文件**：
- `swarm/swarm_coordinator.py` — 注入 preloaded_history
- `core/agent_loop.py` — `_initialize_messages()` 增加短路逻辑

**验收标准**：
- [x] Coordinator 检索结果被注入到所有 Worker 的 AgentLoop
- [x] `AgentLoop._initialize_messages()` 检测 `preloaded_history` 非 None 时跳过 `get_history()` 调用
- [x] Swarm 模式和单 Agent 模式均适用
- [x] 短期记忆中的消息数一致性不受影响

---

### 3. Skill 发现结果全局缓存

**现状**：
[swarm/swarm_coordinator.py#L1039-L1060](swarm/swarm_coordinator.py#L1039-L1060) `process_with_swarm()` 便捷函数每次创建全新 `SwarmCoordinator`，导致 3 个 Agent 各自调用 `discover_skills()` 扫描 `.claude/skills/` 目录树，每请求额外 50-200ms。

**修改方案**：
- 在 `core/skill_loader.py` 的 `discover_skills()` 上添加模块级 `_cache: Optional[Dict]`，首次调用后缓存结果
- 增加 `invalidate_cache()` 函数供开发期手动刷新
- `SwarmCoordinator` 改为模块级单例或由 `chat_service` 层复用

**涉及文件**：
- `core/skill_loader.py` — 添加缓存
- `swarm/swarm_coordinator.py` — `process_with_swarm()` 改为复用 Coordinator（可选）
- `api/services/chat_service.py` — 确认已正确复用 Coordinator

**验收标准**：
- [x] `discover_skills()` 首次调用后，后续调用直接返回缓存（不再扫描磁盘）
- [x] 提供 `invalidate_cache()` 清除入口
- [x] `process_with_swarm()` 路径下的 Agent 初始化耗时减少 50%+
- [x] 9 个 Skill 的工具注册数量与缓存前一致

---

### 4. 轮询替代：用 asyncio.Event 唤醒 LeadAgent

**现状**：
[swarm/lead_agent.py#L336-L361](swarm/lead_agent.py#L336-L361) `wait_for_completion()` 使用 `while True` + `asyncio.sleep(0.5)` 轮询（虽然该方法实际未被调用），但实际超时等待逻辑仍在 SwarmCoordinator 的 `wait_for` 中。

**修改方案**：
- 在 `SharedContext` 上新增 `all_completed_event: asyncio.Event`
- `complete_subtask()` 时检查是否所有任务完成，若完成则 `event.set()`
- 将 Coordinator 中的 `wait_for` 替换为监听该 Event + 超时组合

**涉及文件**：
- `swarm/shared_context.py` — 新增 Event 变量，`complete_subtask()` 中触发
- `swarm/swarm_coordinator.py` — `_process_with_swarm()` 中使用 Event.wait(timeout=90)
- `swarm/lead_agent.py` — 删除未使用的 `wait_for_completion()` 死代码

**验收标准**：
- [x] `SharedContext.complete_subtask()` 检测 `is_all_subtasks_completed()` 为 True 时触发 Event
- [x] Swarm 等待逻辑使用 `asyncio.wait()` 替代 `asyncio.wait_for(gather)` 避免丢失中间结果
- [x] 超时逻辑保留（90s），超时后仍正确处理部分结果
- [x] 删除 `LeadAgent.wait_for_completion()` 方法

---

### 5. LTM 保存 fire-and-forget 改为等待完成

**现状**：
[swarm/swarm_coordinator.py#L407-L417](swarm/swarm_coordinator.py#L407-L417) 使用 `asyncio.ensure_future()`，在 `main.py` 交互模式下无人等待，进程退出前未完成的 Mem0 写入会丢失。

**修改方案**：
- 在 `SwarmCoordinator` 上新增 `ltm_save_task: Optional[asyncio.Task]`
- `ChatResponse` 或结果 dict 中携带该 task 引用
- `main.py` 交互模式在显示结果后 `await asyncio.wait_for(ltm_task, timeout=30)`
- `chat_service.py` 流式模式已在 done 后等待，需确认等待逻辑覆盖所有分支

**涉及文件**：
- `swarm/swarm_coordinator.py` — 保存 task 引用到 result
- `main.py` — 交互模式增加 await
- `api/services/chat_service.py` — 确认非流式路径也等待 LTM 完成

**验收标准**：
- [x] `main.py` 交互模式每轮咨询后等待 LTM 保存完成（最多 30s）
- [x] 非流式 API 路径等待 LTM 保存完成（最多 30s）
- [x] 流式 API 路径已覆盖（现有逻辑确认）
- [x] LTM 保存超时不阻塞最终结果返回（超时后降级，记录 warning）
- [x] 不做 `time.sleep` 类的同步等待

---

## P1 — 架构改善（建议本迭代解决）

### 6. SwarmCoordinator.process() 职责拆分

**现状**：
[swarm/swarm_coordinator.py#L108-L420](swarm/swarm_coordinator.py#L108-L420) `process()` 方法 320 行，含记忆检索、信息澄清、任务分解、路由、单 Agent、Swarm、fallback、持久化、trace 等 10+ 职责。

**修改方案**：
- 抽取 `_retrieve_memories()` — 并行检索短期+长期记忆，返回 `enhanced_context`
- 抽取 `_route_and_execute()` — 根据子任务数量路由到单/Swarm/fallback
- 抽取 `_finalize()` — 统一记忆保存、SessionSummary、trace flush
- `process()` 仅保留 pipeline 骨架：检索 → 澄清 → 分解 → 路由 → 收尾

**涉及文件**：
- `swarm/swarm_coordinator.py` — 重构 process()，新增 3 个私有方法
- 不改变任何外部接口

**验收标准**：
- [x] `process()` 方法体 ≤ 80 行
- [x] 新增方法有明确的 docstring 和单一职责
- [x] 所有路由模式（单/多/fallback）行为不变
- [x] 现有测试全部通过

---

### 7. 三路分支重复代码统一

**现状**：
[swarm/swarm_coordinator.py#L230-L419](swarm/swarm_coordinator.py#L230-L419) 单 Agent、Swarm、fallback 各自重复了记忆保存、disclaimer 注入、session summary 生成。

**修改方案**：
- 每个分支返回一个标准化的 `_BranchResult` 结构（含 answer、usage、message_count）
- `_finalize()` 方法统一处理三段后置逻辑：short-term merge + session summary + LTM save + disclaimer/suggestions 填充

**涉及文件**：
- `swarm/swarm_coordinator.py`

**验收标准**：
- [x] 三条分支的重复代码量减少 60%+
- [x] `disclaimer`、`suggestions`、`session_id` 字段在所有分支下一致存在
- [x] 不引入 regression

---

### 8. Thinking 回调注入改为上下文管理器

**现状**：
[swarm/swarm_coordinator.py#L958-L1020](swarm/swarm_coordinator.py#L958-L1020) `_inject_thinking_callbacks()` 和 `_cleanup_thinking_callbacks()` 需调用方手动配对，异常路径易遗漏清理。

**修改方案**：
- 在 `SwarmCoordinator` 上实现 `@asynccontextmanager` 或 `@contextmanager`：`callback_scope(worker, publish_fn)`
- 上下文管理器 `__enter__` 注入回调，`__exit__` 清理
- 调用方使用 `async with self.callback_scope(agent, publish_fn):`

**涉及文件**：
- `swarm/swarm_coordinator.py` — 新增 `callback_scope`，替换直接调用

**验收标准**：
- [x] `_inject_thinking_callbacks` 和 `_cleanup_thinking_callbacks` 标记为 deprecated 或删除
- [x] 回调注入/清理在正常流程和异常流程中均被触发
- [x] 异常路径不泄漏回调引用

---

### 9. 客户端断开时取消后台 Task

**现状**：
[api/services/chat_service.py#L145-L151](api/services/chat_service.py#L145-L151) SSE 连接断开后 `process_task` 继续运行，消耗 LLM token。

**修改方案**：
- 在 `chat_stream()` 中监听 `await request.is_disconnected()`
- 使用 `asyncio.wait([process_task, disconnect_task], return_when=FIRST_COMPLETED)`
- 断开时 `process_task.cancel()` 并在 Coordinator 中响应 `CancelledError`

**涉及文件**：
- `api/services/chat_service.py` — 流式路径增加断开检测
- `swarm/swarm_coordinator.py` — process() 中 `except CancelledError` 优雅降级
- `core/agent_loop.py` — run() 中 `except CancelledError` 中止循环

**验收标准**：
- [x] 客户端断开 SSE 后，500ms 内 `process_task` 被取消
- [x] `CancelledError` 被捕获，不产生未处理异常日志
- [x] 已完成的阶段（如已检索的记忆）不清除
- [x] 取消后不调用后续 LLM API

---

### 10. Clarify 阶段超时跳过

**现状**：
[swarm/swarm_coordinator.py#L200](swarm/swarm_coordinator.py#L200) `clarify()` 中包含 300s 用户问卷等待，如用户不回答则阻塞整个流程。

**修改方案**：
- `clarify()` 增加 `timeout` 参数（默认 30s），超时后返回 `{"clarified": False}` 继续执行
- 问卷事件仍发射到前端，用户可后续回答（不影响本轮）
- 区分"LLM 判断无需澄清"和"超时跳过"两种状态

**涉及文件**：
- `swarm/lead_agent.py` — `clarify()` 增加 timeout
- `swarm/swarm_coordinator.py` — 传入合理 timeout

**验收标准**：
- [x] 问卷等待超时后自动继续任务分解，不阻塞主流程
- [x] 超时跳过时记录 warning 日志
- [x] 超时后 LLM 调用仍正常执行
- [x] 正常回答的路径不受影响

---

### 11. Swarm 超时改为部分结果回退

**现状**：
[swarm/swarm_coordinator.py#L499-L505](swarm/swarm_coordinator.py#L499-L505) `asyncio.wait_for(gather, 90s)` 超时后直接丢弃所有 Worker 的进行中结果。

**修改方案**：
- 使用 `asyncio.wait(FIRST_COMPLETED, timeout=90)` 替代 `wait_for`
- 超时时遍历已完成 tasks 收集结果；未完成的 task.cancel() 前请求中间结果
- Worker 在收到取消信号时应尽快返回已有结果

**涉及文件**：
- `swarm/swarm_coordinator.py` — `_process_with_swarm()`
- `core/agent_loop.py` — 增加取消时返回中间结果的逻辑

**验收标准**：
- [x] 90s 超时后，已完成 Worker 的结果正常参与 synthesize
- [x] 超时未完成的 Worker 被 cancel，不在最终结果中出现
- [x] synthesize 仍能生成有效答案（基于部分结果）
- [x] 超时情况下 Token 不浪费在已取消的 LLM 调用上

---

## P2 — 可靠性与可观测性

### 12. JSON 解析加固

**现状**：
[swarm/lead_agent.py#L278](swarm/lead_agent.py#L278) `re.search(r'\{.*\}', content, re.DOTALL)` 对嵌套 JSON 匹配错误范围。同理 [swarm/swarm_coordinator.py#L848-L854](swarm/swarm_coordinator.py#L848-L854) 的 JSON 解析脆弱。

**修改方案**：
- 使用括号计数法提取最外层 JSON（统计 `{` 和 `}` 平衡点）
- 对提取结果用 `json.loads` 验证，失败则用 `json5` 库容忍尾部逗号等常见 LLM 输出瑕疵
- 对 `assess_and_decompose` 的输出做 JSON Schema 校验

**涉及文件**：
- `swarm/lead_agent.py` — 加固 `assess_and_decompose()` 的 JSON 提取
- `swarm/swarm_coordinator.py` — 加固 `_evaluate_and_extract_memory()` 的 JSON 解析

**验收标准**：
- [ ] 嵌套 JSON 能正确提取最外层对象
- [ ] LLM 输出的尾部逗号、注释等常见瑕疵能容错
- [ ] 解析失败的降级路径保持不变
- [ ] 新增 3+ 单元测试覆盖边界情况（嵌套、截断、空响应）

---

### 13. QuestionnaireManager 注册表 TTL 过期清理

**现状**：
[api/services/chat_service.py#L36](api/services/chat_service.py#L36) `_managers: Dict[str, QuestionnaireManager]` 永不清理，异常断开导致内存泄漏。

**修改方案**：
- `get_manager()` 入口处清理超过 TTL（默认 600s）未活动的条目
- `remove_manager()` 保留显式清理路径
- 设置最大条目数上限（如 1000），超出时 LRU 淘汰

**涉及文件**：
- `api/services/chat_service.py`

**验收标准**：
- [ ] 超过 600s 未活动的 manager 自动清理
- [ ] 条目数不超过 1000
- [ ] 不影响正常问卷交互
- [ ] 清理时无异常日志

---

### 14. Prompt 模板加载缓存

**现状**：
`core/prompt_loader.py` 每次 `load()`/`render()` 可能读磁盘 + 编译 Jinja2 模板。

**修改方案**：
- 在 `PromptLoader` 中使用 Jinja2 的 `FileSystemLoader` + `Environment` 缓存（Jinja2 内置缓存）
- 或在类级别增加 `_template_cache: Dict[str, Template]`，首次加载后缓存 Jinja2 Template 对象

**涉及文件**：
- `core/prompt_loader.py`

**验收标准**：
- [ ] 同一模板第二次 `load()` 命中缓存，不访问磁盘
- [ ] `render()` 中模板变量正确替换
- [ ] 18 个 `.j2` 模板全部可用

---

### 15. LLM 调用增加重试与熔断

**现状**：
AgentLoop 中 LLM 异常直接 `continue` 进入下一迭代，无重试机制。

**修改方案**：
- 在 `LLMClient` 层增加指数退避重试（最多 3 次，初始 1s，倍增）
- 在 `SwarmCoordinator` 层增加简易熔断器：连续 5 次 LLM 错误后拒绝新请求 30s
- AgentLoop 在收到重试后仍失败时记录完整错误上下文

**涉及文件**：
- `core/llm_client.py` — 增加 `chat_with_retry()`（含 3 次指数退避）
- `swarm/swarm_coordinator.py` — 增加熔断计数器
- `core/agent_loop.py` — 使用 `chat_with_retry` 替代 `chat_with_tools`

**验收标准**：
- [ ] 单次 5xx/网络错误自动重试最多 3 次
- [ ] 4xx 错误（如 400 Bad Request）不重试
- [ ] 熔断打开后新请求立即返回错误，不调用 LLM
- [ ] 熔断器在 30s 后半开，允许探测请求通过

---

### 16. 流式 Token 路由重构

**现状**：
[core/agent_loop.py#L162-L209](core/agent_loop.py#L162-L209) 使用 `_has_tools[0]`、`_reasoning_active[0]` list 包装实现闭包内可变状态，推理期间 content token 缓存逻辑依赖 tool_calls 检测时序。

**修改方案**：
- 将流式路由逻辑抽取为独立类 `StreamTokenRouter`，管理状态机的状态转换
- 移除 list 包装技巧，改用 `nonlocal` 或显式状态对象

**涉及文件**：
- `core/agent_loop.py` — 新增 `StreamTokenRouter`，重构流式路由逻辑

**验收标准**：
- [ ] 不再使用 `list` 包装实现闭包可变
- [ ] Thinking 和 Content token 路由逻辑可单独测试
- [ ] 推理内容绝不泄露到 Content 通道
- [ ] 所有流式场景行为不变（纯工具调用、纯回答、混合）

---

## P3 — 代码质量

### 17. session_id 生成统一

**现状**：
`main.py:70` / `swarm/swarm_coordinator.py:127` / `api/services/chat_service.py:108` 三处各自重复 `f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"`。

**修改方案**：
- 新增 `core/utils.py::generate_session_id()` 工具函数
- 全局替换所有重复生成逻辑

**涉及文件**：
- `core/utils.py` — 新增
- `main.py`
- `swarm/swarm_coordinator.py`
- `api/services/chat_service.py`

**验收标准**：
- [ ] `generate_session_id()` 输出格式与现有完全一致
- [ ] 所有 3 处调用统一使用该函数
- [ ] 格式变更不破坏子会话 ID 解析逻辑

---

### 18. 删除 LeadAgent.wait_for_completion 死代码

**现状**：
[swarm/lead_agent.py#L336-L361](swarm/lead_agent.py#L336-L361) 该方法从未被调用，且内含轮询逻辑。

**修改方案**：
- 直接删除方法，P0-4 已将此功能迁移至 SharedContext + Event

**涉及文件**：
- `swarm/lead_agent.py`

**验收标准**：
- [ ] `wait_for_completion` 方法已移除
- [ ] 无其他位置引用该方法
- [ ] 现有测试全部通过

---

### 19. 用户档案注入去重

**现状**：
[swarm/swarm_coordinator.py#L86-L94](swarm/swarm_coordinator.py#L86-L94) 初始化时注入一次，[swarm/swarm_coordinator.py#L149-L154](swarm/swarm_coordinator.py#L149-L154) 每次 `process()` 再次覆盖注入。

**修改方案**：
- 移除初始化时的注入，仅保留 `process()` 中的覆盖逻辑
- 或改为只在档案变化时（检测 `PERSONAL.md` 修改时间）才重新注入

**涉及文件**：
- `swarm/swarm_coordinator.py`

**验收标准**：
- [ ] 同一请求中 `worker.loop.user_context` 仅被设置一次
- [ ] 档案内容正确注入到所有 Worker
- [ ] 不影响澄清阶段使用的档案信息

---

### 20. API 增加速率限制

**现状**：
Web API 无速率限制，无 per-session 并发控制。

**修改方案**：
- 集成 `slowapi`（FastAPI 官方推荐）
- 全局限制：60 req/min per IP
- 问答端点额外限制：10 req/min per session_id
- 被拒绝请求返回 HTTP 429 + 提示信息

**涉及文件**：
- `api/main.py` — 集成 slowapi
- `api/routers/chat.py` — 添加速率限制装饰器
- `requirements.txt` — 添加 `slowapi`

**验收标准**：
- [ ] 超过限制的请求返回 HTTP 429
- [ ] 429 响应包含 `Retry-After` header
- [ ] 限制按 IP 和 session_id 分别计数
- [ ] 不影响本地开发环境（可配置关闭）

---

## 附录：Todo List

### Round 1（P0 — 首轮必做）✅ 已完成
- [x] P0-1: 记忆检索并行化
- [x] P0-2: 消除短期记忆双重检索
- [x] P0-3: Skill 发现结果全局缓存
- [x] P0-4: asyncio.Event 替代轮询 + 删除死代码
- [x] P0-5: LTM 保存改为可等待

### Round 2（P1 — 架构改善）
- [x] P1-6: process() 职责拆分（提取 _retrieve_memories / _route_and_execute / _finalize）
- [x] P1-7: 三路分支去重
- [x] P1-8: Thinking 回调改为上下文管理器
- [x] P1-9: 客户端断开取消后台 Task
- [x] P1-10: Clarify 超时跳过
- [x] P1-11: Swarm 超时部分结果回退

### Round 3（P2 — 可靠性与可观测性）✅ 已完成
- [x] P2-12: JSON 解析加固
- [x] P2-13: QuestionnaireManager TTL 过期清理
- [x] P2-14: Prompt 模板加载缓存
- [x] P2-15: LLM 调用重试 + 熔断器
- [x] P2-16: 流式 Token 路由重构

### Round 4（P3 — 代码质量）
- [ ] P3-17: session_id 生成统一
- [ ] P3-18: 删除 wait_for_completion 死代码
- [ ] P3-19: 用户档案注入去重
- [ ] P3-20: API 速率限制
