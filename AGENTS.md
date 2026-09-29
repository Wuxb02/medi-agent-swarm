# Repository Guidelines

## 项目结构与模块

`mediZJ/` 是 Python 后端：`api/` 提供 FastAPI 路由与服务，`swarm/` 和 `lgraph/` 组织多智能体流程，`knowledge/` 管理医学知识，`validation/` 校验回答，`prompt/` 保存提示词模板。`tests/` 按后端模块分组；Vue 3 前端位于 `frontend/src/`，组件、页面和 API 分别放在 `components/`、`views/`、`api/`，测试放在相邻的 `__tests__/`。静态资源位于 `mediZJ/assets/` 和 `frontend/src/assets/`；开发脚本位于 `scripts/`。

## 构建、测试与本地运行

- `uv sync --extra dev`：安装后端及开发依赖。
- `uv run python mediZJ/api_main.py`：启动本地 API。
- `uv run pytest tests/ -m "not integration"`：运行无需外部服务的后端测试。
- `uv run pytest tests/ --cov=mediZJ --cov-report=term-missing`：检查后端覆盖率；需要外部服务的用例按 `tests/conftest.py` 的选项配置。
- `cd frontend && npm install && npm run dev`：安装前端依赖并启动 Vite。
- `cd frontend && npm run build`：类型检查并构建；`npm test` 运行 Vitest。

## 编码风格与命名

Python 遵循 PEP 8、四空格缩进；模块、函数和变量用 `snake_case`，类用 `PascalCase`。公开接口添加类型标注，注释简洁且默认使用中文。后端使用 Ruff 和 mypy 检查；前端使用 ESLint、Prettier，Vue 组件命名为 `PascalCase.vue`。避免无必要的兜底或兼容层，直接定位并修复错误。

## 测试规范

Pytest 文件、函数分别命名为 `test_*.py`、`test_*`；外部服务测试标记 `integration`，真实 LLM 慢测试标记 `slow`。单元测试应模拟 LLM、数据库和网络边界，覆盖成功、失败及关键边界情况；新改代码以至少 80% 覆盖率为目标。前端测试使用 `*.spec.ts`，运行 `cd frontend && npm run test:coverage` 查看覆盖率。

## 提交与拉取请求

近期提交主要采用 Angular 格式，如 `feat(knowledge): 支持文档有效期时间范围与过期标记`。提交信息写为 `<type>(<scope>): <subject>`，保持改动聚焦；由维护者自行执行提交。拉取请求需说明行为变化、关联问题、验证命令和配置或迁移影响；界面改动附截图。

## 安全与配置

以 `.env.example` 为模板配置本地 `.env`，不要提交密钥、患者数据或运行日志。修改知识检索与回答链路时，保留有效期过滤、可核验引用及医学回答校验。
