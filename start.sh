#!/usr/bin/env bash
set -Eeuo pipefail

# 固定项目路径，支持从任意工作目录调用。
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'EOF'
用法：./start.sh [--no-build] [--help]

默认构建镜像并后台启动前端、API 和全部基础设施，等待服务就绪。
  --no-build  使用已有镜像启动，跳过构建
  --help      显示帮助

启动前请将 .env.example 复制为 .env 并配置密钥和密码。
START_WAIT_TIMEOUT 可设置就绪等待秒数，默认 600（不含构建时间）。
停止服务：docker compose down（保留数据卷）
EOF
}

BUILD_ARGS=(--build)
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build) BUILD_ARGS=() ;;
        --help|-h) usage; exit 0 ;;
        *) printf '错误：未知参数 %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

WAIT_TIMEOUT="${START_WAIT_TIMEOUT:-600}"
if [[ ! "$WAIT_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
    printf '错误：START_WAIT_TIMEOUT 必须为正整数。\n' >&2
    exit 2
fi

cd -- "$PROJECT_DIR"
if [[ ! -f .env ]]; then
    printf '错误：缺少 %s/.env。\n请先执行 cp .env.example .env 并填写配置，再重新启动。\n' "$PROJECT_DIR" >&2
    exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
    printf '错误：未安装 Docker，请先安装并启动 Docker。\n' >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    printf '错误：Docker Compose 插件不可用。\n' >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    printf '错误：Docker 引擎不可用，请先启动 Docker。\n' >&2
    exit 1
fi

COMPOSE=(docker compose --project-directory "$PROJECT_DIR" -f "$PROJECT_DIR/compose.yaml")
# 只校验配置，不输出可能包含密钥的展开结果。
"${COMPOSE[@]}" config --quiet

printf '正在启动服务；首次构建需要下载依赖和模型，请耐心等待……\n'
if "${COMPOSE[@]}" up -d "${BUILD_ARGS[@]}" --wait --wait-timeout "$WAIT_TIMEOUT"; then
    printf '\n服务已就绪。应用端口映射：\n'
    "${COMPOSE[@]}" port app 8000
    printf '请使用上方宿主机端口访问 http://localhost:<端口>，API 文档路径为 /docs。\n'
else
    status=$?
    printf '\n启动失败，以下为服务状态和最近日志：\n' >&2
    "${COMPOSE[@]}" ps -a || true
    "${COMPOSE[@]}" logs --tail 60 app migrate mysql redis milvus etcd minio || true
    exit "$status"
fi
