#!/usr/bin/env python3
"""
小说生成流水线 — Web 可视化控制台

提供实时进度、质量趋势、成本追踪的 Web 仪表盘。
通过轮询运行时状态文件实现实时更新（无需 WebSocket）。

用法：
    python web_dashboard.py              # 默认端口 8080
    python web_dashboard.py --port 9090  # 自定义端口
    python web_dashboard.py --host 0.0.0.0 --port 8080  # 局域网访问
"""
import json
import os
import sys
import time
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime

# ─── 配置 ────────────────────────────────────────────────
DEFAULT_PORT = 8080
POLL_INTERVAL = 5  # 秒，轮询间隔
PROJECT_ROOT = Path(__file__).parent.parent  # 脚本所在目录即为项目根目录

# ─── 状态读取器 ──────────────────────────────────────────
class StateReader:
    """从文件系统读取流水线运行状态。"""

    def __init__(self, root: Path):
        self.root = root

    def _load_json(self, path: Path) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def get_overview(self) -> dict:
        """获取总览数据。"""
        result = {
            "total_chapters": 0,
            "passed": 0,
            "failed": 0,
            "avg_score": 0,
            "elapsed_seconds": 0,
            "status": "idle",
            "current_chapter": 0,
        }

        # 从 production_report.json 读取最终结果
        report_path = self.root / "audit" / "production_report.json"
        if report_path.exists():
            report = self._load_json(report_path)
            result["total_chapters"] = report.get("total_chapters", 0)
            result["passed"] = report.get("passed", 0)
            result["failed"] = report.get("failed", 0)
            result["avg_score"] = report.get("average_score", 0)
            result["elapsed_seconds"] = report.get("elapsed_seconds", 0)
            result["status"] = "completed" if report.get("success") else "error"
            cost = report.get("cost_report", {})
            result["cost_usd"] = cost.get("cost_usd", {}).get("estimated_full_production", 0)
            result["total_api_calls"] = cost.get("total_api_calls", 0)
            result["total_tokens"] = cost.get("token_usage", {}).get("total_tokens", 0)

        # 从 state_machine.json 读取当前状态
        sm_path = self.root / "runtime" / "state_machine.json"
        if sm_path.exists():
            sm = self._load_json(sm_path)
            result["current_chapter"] = sm.get("current_chapter", 0)
            result["status"] = "running"
            result["current_phase"] = sm.get("current_phase", "")

        # 从 per_chapter_reviews.json 计算实时分数
        reviews_path = self.root / "audit" / "per_chapter_reviews.json"
        if reviews_path.exists():
            reviews = self._load_json(reviews_path)
            review_list = reviews.get("reviews", [])
            if review_list:
                scores = [r.get("total_score", 0) for r in review_list]
                result["avg_score"] = sum(scores) / len(scores)
                result["min_score"] = min(scores)
                result["max_score"] = max(scores)
                result["score_history"] = [
                    {"chapter": rv.get("chapter_num", i), "score": s, "verdict": rv.get("verdict", "?")}
                    for i, (rv, s) in enumerate(zip(review_list, scores))
                ]
                result["passed"] = sum(1 for r in review_list if r.get("verdict") == "pass")
                result["failed"] = sum(1 for r in review_list if r.get("verdict") == "fail")

        return result

    def get_score_trend(self, window: int = 50) -> list[dict]:
        """获取最近 N 章的分数趋势。"""
        reviews_path = self.root / "audit" / "per_chapter_reviews.json"
        if not reviews_path.exists():
            return []
        reviews = self._load_json(reviews_path)
        review_list = reviews.get("reviews", [])[-window:]
        return [
            {
                "chapter": r.get("chapter_num", i),
                "score": r.get("total_score", 0),
                "verdict": r.get("verdict", "?"),
            }
            for i, r in enumerate(review_list)
        ]

    def get_quality_dimensions(self) -> list[dict]:
        """获取各维度平均分。"""
        reviews_path = self.root / "audit" / "per_chapter_reviews.json"
        if not reviews_path.exists():
            return []
        reviews = self._load_json(reviews_path)
        review_list = reviews.get("reviews", [])
        if not review_list:
            return []

        dims = ["plot_consistency", "character_consistency", "foreshadow_execution",
                "style_match", "pacing", "innovation"]
        weights = [25, 20, 20, 15, 10, 10]
        result = []
        for dim, weight in zip(dims, weights):
            total = sum(r.get("scores", {}).get(dim, 0) for r in review_list)
            avg = total / len(review_list) * 100 / weight if review_list else 0  # 百分比
            result.append({
                "name": dim.replace("_", " "),
                "weight": weight,
                "avg_raw": round(total / len(review_list), 1) if review_list else 0,
                "avg_pct": round(avg, 1),
            })
        return result

    def get_recent_chapters(self, limit: int = 10) -> list[dict]:
        """获取最近生成的章节列表。"""
        novel_dir = self.root / "chapters" / "novel"
        if not novel_dir.exists():
            return []
        files = sorted(novel_dir.glob("chapter_*.txt"), key=lambda f: int(f.stem.split("_")[1]))
        result = []
        for f in files[-limit:]:
            size = f.stat().st_size
            result.append({
                "chapter": int(f.stem.split("_")[1]),
                "size": size,
                "path": str(f.relative_to(self.root)),
            })
        return result

    def get_cost_data(self) -> dict:
        """获取成本数据。"""
        report_path = self.root / "audit" / "production_report.json"
        if report_path.exists():
            report = self._load_json(report_path)
            cost = report.get("cost_report", {})
            return {
                "total_api_calls": cost.get("total_api_calls", 0),
                "total_tokens": cost.get("token_usage", {}).get("total_tokens", 0),
                "cost_per_chapter": cost.get("cost_usd", {}).get("per_chapter_avg", 0),
                "estimated_full": cost.get("cost_usd", {}).get("estimated_full_production", 0),
                "within_budget": cost.get("budget", {}).get("within_budget", True),
                "budget_max": cost.get("budget", {}).get("full_production_max", 400),
            }
        return {}


# ─── HTTP 处理器 ─────────────────────────────────────────
state_reader = StateReader(PROJECT_ROOT)

class DashboardHandler(BaseHTTPRequestHandler):
    """处理 Web 请求。"""

    def log_message(self, format, *args):
        """静默日志。"""
        pass

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/overview":
            data = state_reader.get_overview()
            self._json_response(data)
        elif path == "/api/trend":
            window = int(self.path.split("=")[-1]) if "=" in self.path else 50
            data = state_reader.get_score_trend(window)
            self._json_response({"data": data})
        elif path == "/api/dimensions":
            data = state_reader.get_quality_dimensions()
            self._json_response({"dimensions": data})
        elif path == "/api/chapters":
            data = state_reader.get_recent_chapters()
            self._json_response({"chapters": data})
        elif path == "/api/cost":
            data = state_reader.get_cost_data()
            self._json_response(data)
        elif path == "/health":
            self._json_response({"status": "ok"})
        else:
            self._serve_html()

    def _json_response(self, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        html = HTML_TEMPLATE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html)


# ─── HTML/CSS/JS 模板 ───────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>小说生成流水线 — 实时监控</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0a0e17;color:#c9d1d9;min-height:100vh}
.header{background:linear-gradient(135deg,#161b22,#1f2937);padding:20px 30px;border-bottom:1px solid #30363d;display:flex;justify-content:space-between;align-items:center}
.header h1{font-size:22px;color:#58a6ff}
.header .status{padding:4px 12px;border-radius:12px;font-size:13px;font-weight:600}
.status.idle{background:#333;color:#888}.status.running{background:#1f6feb;color:#fff;animation:pulse 2s infinite}
.status.completed{background:#238636;color:#fff}.status.error{background:#da3633;color:#fff}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.7}}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;padding:20px 30px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px 20px}
.card .label{font-size:12px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}
.card .value{font-size:28px;font-weight:700;color:#f0f6fc}
.card .sub{font-size:12px;color:#8b949e;margin-top:4px}
.section{padding:0 30px 20px}
.section-title{font-size:16px;font-weight:600;color:#58a6ff;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #21262d}
.trend-container{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;height:280px;position:relative}
.trend-container canvas{width:100%!important;height:100%!important}
.dim-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.dim-item{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}
.dim-item .dim-name{font-size:13px;color:#8b949e;margin-bottom:8px;display:flex;justify-content:space-between}
.dim-bar{height:8px;background:#21262d;border-radius:4px;overflow:hidden}
.dim-bar-fill{height:100%;border-radius:4px;transition:width .5s ease}
.chapter-list{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden}
.chapter-row{display:flex;align-items:center;padding:10px 16px;border-bottom:1px solid #21262d;font-size:13px}
.chapter-row:last-child{border-bottom:none}
.chapter-row .ch-num{color:#58a6ff;font-weight:600;width:60px}
.chapter-row .ch-score{width:50px;text-align:right}
.chapter-row .ch-verdict{width:60px;text-align:right}
.verdict-pass{color:#3fb950}.verdict-fix{color:#d29921}.verdict-fail{color:#f85149}
.cost-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.chart-placeholder{text-align:center;padding:60px;color:#484f58;font-size:14px}
.loading{color:#58a6ff}
</style>
</head>
<body>
<div class="header">
  <h1>📖 小说生成流水线 · 实时监控</h1>
  <span id="statusBadge" class="status idle">等待启动</span>
</div>

<div class="grid">
  <div class="card"><div class="label">总章节</div><div class="value" id="totalCh">—</div><div class="sub">目标 3800 章</div></div>
  <div class="card"><div class="label">已完成</div><div class="value" id="doneCh" style="color:#3fb950">—</div><div class="sub" id="progressInfo"></div></div>
  <div class="card"><div class="label">失败</div><div class="value" id="failCh" style="color:#f85149">—</div><div class="sub">需人工介入</div></div>
  <div class="card"><div class="label">平均分数</div><div class="value" id="avgScore">—</div><div class="sub">PASS≥85 / FIX 60-84 / FAIL<60</div></div>
  <div class="card"><div class="label">耗时</div><div class="value" id="elapsed">—</div><div class="sub" id="etaInfo"></div></div>
  <div class="card"><div class="label">预估成本</div><div class="value" id="costUsd">—</div><div class="sub" id="budgetInfo"></div></div>
</div>

<div class="section">
  <div class="section-title">📈 分数趋势（近50章）</div>
  <div class="trend-container">
    <canvas id="trendCanvas"></canvas>
  </div>
</div>

<div class="section">
  <div class="section-title">🎯 质量维度分析</div>
  <div class="dim-grid" id="dimGrid"></div>
</div>

<div class="section">
  <div class="section-title">📄 最近章节</div>
  <div class="chapter-list" id="chapterList"></div>
</div>

<script>
const API = '';
let refreshTimer;

function fmtTime(s){
  if(!s||s===0) return '—';
  const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=Math.floor(s%60);
  return h?`${h}h ${m}m ${sec}s`:`${m}m ${sec}s`;
}

async function fetchJSON(url){
  try{const r=await fetch(url);return await r.json();}catch(e){return null;}
}

async function refresh(){
  const ov = await fetchJSON(API+'api/overview');
  if(!ov) return;

  document.getElementById('totalCh').textContent = ov.total_chapters || '—';
  document.getElementById('doneCh').textContent = ov.passed || 0;
  document.getElementById('failCh').textContent = ov.failed || 0;
  document.getElementById('avgScore').textContent = ov.avg_score ? ov.avg_score.toFixed(1) : '—';

  const pct = ov.total_chapters ? ((ov.passed/ov.total_chapters)*100).toFixed(1) : '—';
  document.getElementById('progressInfo').textContent = ov.total_chapters ? `${pct}% 完成` : '';

  document.getElementById('elapsed').textContent = fmtTime(ov.elapsed_seconds);

  // ETA
  if(ov.passed > 0 && ov.elapsed_seconds > 0){
    const avgPerCh = ov.elapsed_seconds / ov.passed;
    const remaining = (ov.total_chapters - ov.passed) * avgPerCh;
    document.getElementById('etaInfo').textContent = `平均每章 ${fmtTime(avgPerCh)} · 预计剩余 ${fmtTime(remaining)}`;
  }

  // Status badge
  const badge = document.getElementById('statusBadge');
  badge.className = 'status ' + (ov.status||'idle');
  badge.textContent = {idle:'等待启动',running:'运行中',completed:'已完成',error:'错误'}[ov.status]||'未知';

  // Cost
  if(ov.cost_usd){
    document.getElementById('costUsd').textContent = '$'+ov.cost_usd.toFixed(2);
    document.getElementById('budgetInfo').textContent = '预算 $400';
  }

  // Trend chart
  const trend = await fetchJSON(API+'api/trend');
  if(trend && trend.data.length > 0) drawTrend(trend.data);

  // Dimensions
  const dims = await fetchJSON(API+'api/dimensions');
  if(dims && dims.dimensions) drawDimensions(dims.dimensions);

  // Chapters
  const chs = await fetchJSON(API+'api/chapters');
  if(chs && chs.chapters) drawChapters(chs.chapters);
}

function drawTrend(data){
  const canvas = document.getElementById('trendCanvas');
  const ctx = canvas.getContext('2d');
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width - 32;
  canvas.height = rect.height - 32;
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0,0,W,H);

  // Grid lines
  ctx.strokeStyle = '#21262d';
  ctx.lineWidth = 1;
  [25,50,75,100].forEach(y=>{
    const py = H - (y/100)*H;
    ctx.beginPath();ctx.moveTo(0,py);ctx.lineTo(W,py);ctx.stroke();
    ctx.fillStyle='#484f58';ctx.font='11px sans-serif';ctx.fillText(y+'分',4,py+4);
  });

  // Pass/Fix line
  ctx.setLineDash([5,5]);
  ctx.strokeStyle='#3fb950';ctx.beginPath();
  const py85=H-(85/100)*H;
  ctx.moveTo(0,py85);ctx.lineTo(W,py85);ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle='#3fb950';ctx.font='11px sans-serif';ctx.fillText('PASS线 85',W-90,py85-4);

  if(data.length < 2) return;

  const step = Math.max(1, Math.floor(data.length / W));
  const points = [];
  for(let i=0;i<data.length;i+=step){
    const x = (i/(data.length-1))*W;
    const y = H - (data[i].score/100)*H;
    points.push({x,y});
  }

  // Line
  ctx.strokeStyle = '#58a6ff';
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p,i)=>i===0?ctx.moveTo(p.x,p.y):ctx.lineTo(p.x,p.y));
  ctx.stroke();

  // Dots
  data.forEach((d,i)=>{
    const x = (i/(data.length-1))*W;
    const y = H - (d.score/100)*H;
    ctx.beginPath();
    ctx.arc(x,y,3,0,Math.PI*2);
    ctx.fillStyle = d.verdict==='pass'?'#3fb950':d.verdict==='fix'?'#d29921':'#f85149';
    ctx.fill();
  });
}

function drawDimensions(dims){
  const grid = document.getElementById('dimGrid');
  grid.innerHTML = '';
  dims.forEach(d=>{
    const colors = ['#58a6ff','#3fb950','#d29921','#f85149','#bc8cff','#f778ba'];
    const c = colors[dims.indexOf(d)%colors.length];
    grid.innerHTML += `<div class="dim-item">
      <div class="dim-name"><span>${d.name}</span><span>${d.avg_raw}/${d.weight}</span></div>
      <div class="dim-bar"><div class="dim-bar-fill" style="width:${d.avg_pct}%;background:${c}"></div></div>
    </div>`;
  });
}

function drawChapters(chs){
  const list = document.getElementById('chapterList');
  list.innerHTML = '';
  if(!chs.length){list.innerHTML='<div class="chapter-row"><span style="color:#484f58">暂无章节数据</span></div>';return;}
  chs.reverse().forEach(c=>{
    list.innerHTML += `<div class="chapter-row">
      <span class="ch-num">第${c.chapter}章</span>
      <span style="flex:1">${c.size.toLocaleString()} 字节</span>
    </div>`;
  });
}

// Initial load + auto-refresh
refresh();
refreshTimer = setInterval(refresh, ${POLL_INTERVAL}000);
</script>
</body>
</html>
"""


# ─── 主程序 ──────────────────────────────────────────────
def main():
    port = DEFAULT_PORT
    host = "0.0.0.0"

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--port" and i+1 < len(args):
            port = int(args[i+1])
            i += 2
        elif args[i] == "--host" and i+1 < len(args):
            host = args[i+1]
            i += 2
        elif args[i] == "--root" and i+1 < len(args):
            global PROJECT_ROOT
            PROJECT_ROOT = Path(args[i+1])
            state_reader.root = PROJECT_ROOT
            i += 2
        else:
            i += 1

    server = HTTPServer((host, port), DashboardHandler)
    url = f"http://{'localhost' if host=='0.0.0.0' else host}:{port}"

    print("=" * 50)
    print(f"  小说生成流水线 · Web 控制台")
    print(f"  地址: {url}")
    print(f"  数据目录: {PROJECT_ROOT}")
    print(f"  轮询间隔: {POLL_INTERVAL}s")
    print("=" * 50)

    # 自动打开浏览器
    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()


if __name__ == "__main__":
    main()
