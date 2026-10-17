#!/bin/bash
# ============================================================
# 小说生成流水线 — 一键启动脚本
# ============================================================
# 用法:
#   ./start.sh                    # 启动 Web 控制台
#   ./start.sh test 10            # 运行 10 章测试
#   ./start.sh test 10 --real     # 真实 API 测试 10 章
#   ./start.sh production 3000    # 全量生产 3000 章
#   ./start.sh production 3000 --real  # 真实 API 全量生产
#   ./start.sh dashboard          # 仅启动 Web 控制台
#   ./start.sh status             # 查看当前状态
#   ./start.sh resume 70          # 从第 70 章继续
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# 数据根目录 = src/novel_engine（包根即数据根）
PROJECT_DIR="$SCRIPT_DIR/../src/novel_engine"
# 代码包根目录（novel_engine 包所在目录）
PY_ROOT="$SCRIPT_DIR/../src"
CDN_PORT=8080
CDN_HOST="0.0.0.0"

# ─── 颜色输出 ───────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ─── 环境检查 ───────────────────────────────────────────
check_env() {
    if ! command -v python3 &>/dev/null; then
        error "未找到 python3，请先安装 Python 3.8+"
        exit 1
    fi

    # 检查必要的目录
    for dir in bible planning simulation foreshadow config memory chapters; do
        if [ ! -d "$PROJECT_DIR/$dir" ]; then
            warn "目录不存在: $PROJECT_DIR/$dir"
        fi
    done

    # 检查关键文件
    local missing=0
    for f in bible/world_bible.md bible/character_bible.md bible/style_bible.md \
             bible/author_intent.md bible/ending_bible.md \
             planning/volumes.json planning/plot_graph.json \
             simulation/constraints.json simulation/rules.json \
             foreshadow/registry.json; do
        if [ ! -f "$PROJECT_DIR/$f" ]; then
            warn "文件缺失: $f"
            missing=$((missing + 1))
        fi
    done

    if [ $missing -gt 5 ]; then
        error "缺少 $missing 个必要文件，请先完成编写"
        exit 1
    fi

    ok "环境检查通过 ($missing 个警告)"
}

# ─── 启动 Web 控制台 ─────────────────────────────────────
start_dashboard() {
    info "正在启动 Web 控制台..."
    cd "$PY_ROOT"
    python3 -m novel_engine.pipeline.web_dashboard --host "$CDN_HOST" --port "$CDN_PORT" &
    DASHBOARD_PID=$!
    echo $DASHBOARD_PID > "$PROJECT_DIR/runtime/dashboard.pid" 2>/dev/null || true
    ok "Web 控制台已启动 (PID: $DASHBOARD_PID)"
    info "访问地址: http://localhost:$CDN_PORT"
    sleep 2
    # 验证是否成功启动
    if curl -s "http://localhost:$CDN_PORT/health" >/dev/null 2>&1; then
        ok "Web 控制台健康检查通过"
    else
        warn "Web 控制台可能启动失败，请检查日志"
    fi
}

# ─── 停止 Web 控制台 ─────────────────────────────────────
stop_dashboard() {
    local pid_file="$PROJECT_DIR/runtime/dashboard.pid"
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            ok "Web 控制台已停止 (PID: $pid)"
        else
            warn "进程 $pid 不存在"
        fi
        rm -f "$pid_file"
    else
        # 尝试通过端口查找
        local pid=$(lsof -ti:"$CDN_PORT" 2>/dev/null || true)
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
            ok "Web 控制台已停止 (PID: $pid)"
        else
            warn "未找到运行的 Web 控制台"
        fi
    fi
}

# ─── 运行测试 ────────────────────────────────────────────
run_test() {
    local num_chapters=${1:-10}
    local use_real=${2:-false}
    local real_flag=""
    [ "$use_real" = "true" ] && real_flag="--real"

    info "开始 ${num_chapters} 章测试..."
    [ "$use_real" = "true" ] && info "使用真实 API (SiliconFlow / Qwen)" || info "使用 Mock LLM"

    cd "$PY_ROOT"
    python3 -m novel_engine.tests.medium_test_runner $num_chapters $real_flag
}

# ─── 运行生产 ────────────────────────────────────────────
run_production() {
    local num_chapters=${1:-3000}
    local start_from=${2:-1}
    local use_real=${3:-false}
    local real_flag=""
    [ "$use_real" = "true" ] && real_flag="--real"

    info "开始全量生产：${num_chapters} 章（从第 ${start_from} 章开始）..."
    [ "$use_real" = "true" ] && info "使用真实 API (SiliconFlow / Qwen)" || info "使用 Mock LLM"

    cd "$PY_ROOT"
    python3 -m novel_engine.pipeline.production_runner $real_flag $num_chapters $start_from
}

# ─── 查看状态 ────────────────────────────────────────────
show_status() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  小说生成流水线 · 当前状态${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    # 检查 Web 控制台
    local dash_pid=$(lsof -ti:"$CDN_PORT" 2>/dev/null || true)
    if [ -n "$dash_pid" ]; then
        ok "Web 控制台: 运行中 (http://localhost:$CDN_PORT)"
    else
        warn "Web 控制台: 未运行 (运行 ./start.sh dashboard 启动)"
    fi

    # 检查运行中的 Python 进程
    local prod_pid=$(pgrep -f "production_runner" 2>/dev/null || true)
    local med_pid=$(pgrep -f "medium_test_runner" 2>/dev/null || true)
    if [ -n "$prod_pid" ]; then
        ok "生产任务: 运行中 (PID: $prod_pid)"
    elif [ -n "$med_pid" ]; then
        ok "测试任务: 运行中 (PID: $med_pid)"
    else
        info "无运行中的任务"
    fi

    echo ""

    # 读取报告数据
    local report="$PROJECT_DIR/audit/production_report.json"
    if [ -f "$report" ]; then
        python3 -c "
import json
from pathlib import Path
r = json.loads(open('$report').read())
print('--- 上次运行结果 ---')
print(f'  总章节:   {r.get(\"total_chapters\", \"?\")}')
print(f'  通过:     {r.get(\"passed\", \"?\")}')
print(f'  失败:     {r.get(\"failed\", \"?\")}')
print(f'  平均分数: {r.get(\"average_score\", \"?\")}')
print(f'  耗时:     {r.get(\"elapsed_seconds\", 0):.0f}s ({r.get(\"elapsed_seconds\", 0)/3600:.1f}h)')
cost = r.get('cost_report', {})
tokens = cost.get('token_usage', {})
print(f'  API调用:  {cost.get(\"total_api_calls\", \"?\")} 次')
print(f'  Token数:  {tokens.get(\"total_tokens\", 0):,}')
budget = cost.get('budget', {})
print(f'  预算内:   {\"是 ✓\" if budget.get(\"within_budget\") else \"否 ✗\"}')
print(f'  预估成本: \${cost.get(\"cost_usd\", {}).get(\"estimated_full_production\", 0):.2f}')
print(f'  成功率:   {\"成功 ✓\" if r.get(\"success\") else \"异常 ✗\"}')
" 2>/dev/null || warn "无法解析报告"
    else
        warn "暂无运行报告（运行过测试或生产后自动生成）"
    fi

    echo ""

    # 检查文件完整性
    local novel_count=$(ls "$PROJECT_DIR/chapters/novel/chapter_"*.txt 2>/dev/null | wc -l)
    local synopsis_count=$(ls "$PROJECT_DIR/chapters/synopsis/chapter_"*.txt 2>/dev/null | wc -l)
    echo "--- 文件统计 ---"
    echo -e "  章节正文: ${novel_count} 篇"
    echo -e "  章节缩写: ${synopsis_count} 篇"

    local total_size=0
    if [ $novel_count -gt 0 ]; then
        total_size=$(du -sh "$PROJECT_DIR/chapters/novel/" 2>/dev/null | cut -f1)
        echo -e "  总大小:   ${total_size}"
    fi

    echo ""
    echo -e "${CYAN}========================================${NC}"
}

# ─── 恢复进度 ────────────────────────────────────────────
resume_from() {
    local chapter=${1:-1}
    info "从第 ${chapter} 章恢复..."

    # 检查该章之前的章节是否存在
    local last_done=0
    for i in $(seq 1 $chapter); do
        if [ -f "$PROJECT_DIR/chapters/novel/chapter_${i}.txt" ]; then
            last_done=$i
        fi
    done

    if [ $last_done -ge $((chapter - 1)) ]; then
        info "第 ${chapter} 章之前的文件已存在，开始生成第 ${chapter} 章"
        run_production 3000 "$chapter" true
    else
        warn "第 ${chapter} 章之前的文件不完整"
        info "将跳过已有章节，从第 ${chapter} 章开始"
        run_production 3000 "$chapter" true
    fi
}

# ─── 帮助信息 ────────────────────────────────────────────
show_help() {
    echo ""
    echo -e "${CYAN}================================================${NC}"
    echo -e "${CYAN}  小说生成流水线 · 一键启动脚本${NC}"
    echo -e "${CYAN}================================================${NC}"
    echo ""
    echo "用法:"
    echo "  ./start.sh                          # 显示此帮助"
    echo "  ./start.sh dashboard                # 启动 Web 控制台"
    echo "  ./start.sh stop                     # 停止 Web 控制台"
    echo "  ./start.sh status                   # 查看当前状态"
    echo "  ./start.sh test <章节数> [--real]    # 运行测试（默认10章）"
    echo "  ./start.sh production <章节数> [--real]  # 运行生产"
    echo "  ./start.sh resume <章节号>           # 从指定章节恢复"
    echo ""
    echo "示例:"
    echo "  ./start.sh test 10 --real           # 真实API 10章测试"
    echo "  ./start.sh production 3000 --real   # 真实API 全量生产"
    echo "  ./start.sh resume 70                # 从第70章恢复生产"
    echo "  ./start.sh dashboard                # 启动Web监控面板"
    echo ""
    echo "Web 控制台:"
    echo "  启动后访问 http://localhost:8080"
    echo "  实时显示进度、分数趋势、质量维度、成本追踪"
    echo ""
    echo -e "${CYAN}================================================${NC}"
}

# ─── 主入口 ──────────────────────────────────────────────
case "${1}" in
    dashboard)
        check_env
        start_dashboard
        ;;
    stop)
        stop_dashboard
        ;;
    status)
        show_status
        ;;
    test)
        check_env
        NUM=${2:-10}
        REAL=false
        [[ "$3" == "--real" ]] && REAL=true
        run_test "$NUM" "$REAL"
        ;;
    production|prod)
        check_env
        NUM=${2:-3000}
        START=${3:-1}
        REAL=false
        [[ "$4" == "--real" ]] && REAL=true
        run_production "$NUM" "$START" "$REAL"
        ;;
    resume)
        check_env
        CHAPTER=${2:-1}
        resume_from "$CHAPTER"
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        show_help
        ;;
esac