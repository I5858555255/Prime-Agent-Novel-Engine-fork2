#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel-engine 生成控制脚本
========================
子命令:
  clean            清理生成记录(章节/运行时状态/审计/日志)，不会删除源码与配置
  start [章节数]   开始全量生成(默认读取 config 的 pipeline.total_chapters，0=读配置)
  stop             优雅停止生成(已完成章节不会丢失)
  resume           从最后一个已完成章节续写(自动探测)
  status           查看运行状态与最近日志
  setkey <key>     修改当前 active_profile 对应 api_key_env 的 .env 密钥（切换供应商用 select_llm）

说明:
  - 生成进程以后台方式启动，pid 保存在本脚本同级的 runtime_gen.pid。
  - start 默认会重置运行时(等同 clean 后再生成)；resume 不会重置，可断点续写。
  - 所有路径均相对于本文件位置自动推导，移动整个 skill 目录也可正常工作。
"""
import sys
import os
import time
import json
import shutil
import subprocess
from pathlib import Path

# 强制 stdout/stderr 为 UTF-8，避免中文在控制台乱码
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 路径推导: control.py 位于 <skill>/scripts/control/control.py
SKILL_DIR = Path(__file__).resolve().parent.parent.parent   # -> novel-engine
SRC_DIR = SKILL_DIR / "src"
NE = SRC_DIR / "novel_engine"
PYTHON = r"D:\Program Files\Python312\python.exe"
PID_FILE = SKILL_DIR / "runtime_gen.pid"          # 放在 skill 根，clean 不会清掉
LOG_FILE = NE / "runtime" / "logs" / "production.log"


def _is_running() -> bool:
    if not PID_FILE.exists():
        return False
    pid = PID_FILE.read_text(encoding="utf-8").strip()
    if not pid.isdigit():
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/NH", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        return pid in out
    except Exception:
        return False


def _last_chapter() -> int:
    novel_dir = NE / "chapters" / "novel"
    last = 0
    if novel_dir.exists():
        for f in novel_dir.glob("chapter_*.txt"):
            try:
                n = int(f.stem.split("_", 1)[1])
                last = max(last, n)
            except Exception:
                pass
    return last


def clean():
    if _is_running():
        print("生成进程正在运行，请先执行 stop 再 clean。")
        return
    root = NE
    files = [
        "runtime/checkpoint.json", "runtime/state_machine.json",
        "runtime/recovery_policy.json", "runtime/session_tree.json",
        "audit/per_chapter_reviews.json", "audit/production_report.json",
        "audit/mini_test_report.json", "memory/quality_memory.json",
    ]
    for rel in files:
        p = root / rel
        if p.exists():
            try:
                p.unlink()
                print("删除 %s" % p)
            except Exception as e:
                print("跳过 %s: %s" % (p, e))
    for d in ["chapters/novel", "chapters/synopsis", "chapters/outline",
              "memory/world_state", "memory/short_term", "memory/long_term",
              "memory/long_term/embeddings"]:
        p = root / d
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
        p.mkdir(parents=True, exist_ok=True)
    for d in ["runtime/backups"]:
        p = root / d
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    sdb = root / "runtime" / "state.db"
    if sdb.exists():
        try:
            sdb.unlink()
        except Exception:
            pass
    logdir = root / "runtime" / "logs"
    if logdir.exists():
        for f in logdir.glob("*.log"):
            try:
                f.unlink()
            except Exception:
                pass
    print("已清理生成记录(章节/运行时状态/审计/日志)。源码与配置未动。")


def start(num_chapters: int = 0, resume: int = 0):
    if _is_running():
        print("生成进程已在运行 (pid 见 %s)。请先 stop。" % PID_FILE)
        return
    cmd = [PYTHON, "-m", "novel_engine.pipeline.production_runner",
           str(num_chapters), "1", str(resume), "--real"]
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logf = open(str(LOG_FILE), "a", encoding="utf-8")
    # API key 注入：从 runtime_config.json 各 api_key_env + .env 读取，注入子进程环境
    env = os.environ.copy()
    _rc = NE / "config" / "runtime_config.json"
    _key_envs = []
    try:
        if _rc.exists():
            _cfg = json.loads(_rc.read_text(encoding="utf-8"))
            for _seg in _cfg.values():
                if isinstance(_seg, dict) and _seg.get("api_key_env"):
                    _key_envs.append(_seg["api_key_env"])
        # ModelRouter 以 llm_providers.json 的 active_profile 为权威：补入其 api_key_env，
        # 保证用 select_llm 切到不同供应商时 start/resume 也能注入对应密钥。
        _pf = NE / "config" / "llm_providers.json"
        try:
            if _pf.exists():
                _pfc = json.loads(_pf.read_text(encoding="utf-8"))
                _ap = _pfc.get("active_profile")
                _prof = (_pfc.get("profiles") or {}).get(_ap) or {}
                if _prof.get("api_key_env"):
                    _key_envs.append(_prof["api_key_env"])
        except Exception:
            pass
        _envfile = NE / ".env"
        _envmap = {}
        if _envfile.exists():
            for line in _envfile.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    _envmap[k.strip()] = v.strip()
        for _ke in _key_envs:
            if not env.get(_ke) and _envmap.get(_ke):
                env[_ke] = _envmap[_ke]
        _have_key = any(env.get(k) for k in _key_envs)
        if _have_key:
            # 启动自检：key 无效立即报错退出，不静默 401 全 draft。
            # CC round-25：端点/模型不再写死 SiliconFlow，改为读 llm_providers.json 的
            # active_profile（与引擎运行时权威一致），缺省回退 Agnes flash。
            import urllib.request
            try:
                _pfc0 = locals().get("_pfc") or {}
                _prof0 = (_pfc0.get("profiles") or {}).get(_pfc0.get("active_profile")) or {}
                _base = (_prof0.get("base_url") or "https://apihub.agnes-ai.com").rstrip("/")
                _model = "agnes-2.5-flash"
                for _pn in ("outline", "director", "scenes"):
                    _ms = ((_prof0.get("phases") or {}).get(_pn) or {}).get("models") or []
                    if _ms:
                        _model = _ms[0]
                        break
                _url = _base if _base.endswith("/chat/completions") else _base + "/v1/chat/completions"
                _body = json.dumps(
                    {"model": _model,
                     "messages": [{"role": "user", "content": "ping"}],
                     "max_tokens": 1}).encode("utf-8")
                req = urllib.request.Request(
                    _url, data=_body,
                    headers={"Authorization": "Bearer " + env.get(_key_envs[0], ""),
                             "Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=20).read()
                print("API key 有效（%s @ %s）" % (_model, _base))
            except Exception as e:
                print("API key 无效或不可用: %s。请用 setkey 更新后重试。" % e)
                return
    except Exception as e:
        print("API key 检查异常: %s（继续启动，可能 401）" % e)
    # DETACHED_PROCESS: 子进程不依附于启动它的控制台，
    # 关闭窗口(CTRL_CLOSE)不会杀掉它，可后台长期运行。
    creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)
    proc = subprocess.Popen(cmd, cwd=str(SRC_DIR), env=env,
                            stdout=logf, stderr=subprocess.STDOUT,
                            creationflags=creationflags)
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    print("已启动生成 (pid=%s, resume=%s)。日志: %s" % (proc.pid, resume, LOG_FILE))
    print("查看进度: status.bat   |   停止: stop.bat   |   续写: resume.bat")


def stop():
    if not PID_FILE.exists():
        print("没有 pid 文件，无需停止。")
        return
    pid = PID_FILE.read_text(encoding="utf-8").strip()
    subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                   capture_output=True, text=True)
    time.sleep(2)
    if PID_FILE.exists():
        PID_FILE.unlink()
    print("已发送停止信号。已完成章节不会丢失，可用 resume 续写。")


def resume():
    last = _last_chapter()
    print("探测到最后章节: %d" % last)
    start(num_chapters=0, resume=last)


def _current_chapter() -> int:
    """从日志末尾反推当前正在生成的章节号。"""
    if not LOG_FILE.exists():
        return 0
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return 0
    cur = 0
    for l in reversed(lines):
        idx = l.find("GENERATING CHAPTER")
        if idx != -1:
            try:
                cur = int(l[idx:].split()[2].split("/")[0])
                break
            except Exception:
                pass
    return cur


def _last_score() -> str:
    """从日志末尾反推最近一次评审分数/结论。"""
    if not LOG_FILE.exists():
        return "-"
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return "-"
    for l in reversed(lines):
        if "review:" in l or "Review completed" in l:
            return l.split("INFO]")[-1].strip()
    return "-"


def status():
    running = _is_running()
    pid = PID_FILE.read_text(encoding="utf-8").strip() if (PID_FILE.exists() and running) else "-"
    last = _last_chapter()
    cur = _current_chapter()
    print("=== novel-engine 状态 ===")
    print("运行: %s%s" % ("是 (pid=%s)" % pid if running else "否", ""))
    print("已完成章节: %d" % last)
    print("正在生成: 第 %d 章" % cur if cur else "正在生成: -")
    print("最近评审: %s" % _last_score())
    print("日志: %s" % LOG_FILE)
    if LOG_FILE.exists():
        try:
            lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            lines = []
        print("----- 最近 15 行日志 -----")
        for l in lines[-15:]:
            print(l)
    print("提示: 实时滚动请运行 tail.bat (Ctrl+C 退出)")


def follow():
    """实时滚动日志 (Ctrl+C 退出)。"""
    if _is_running():
        try:
            pid = PID_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pid = "?"
        print("状态: 运行中 (pid=%s)" % pid)
    else:
        print("状态: 未运行")
    print("已完成章节: %d" % _last_chapter())
    print("日志: %s" % LOG_FILE)
    print("=== 实时滚动 (Ctrl+C 退出) ===")
    try:
        import os
        with open(str(LOG_FILE), "r", encoding="utf-8", errors="ignore") as f:
            size = os.path.getsize(str(LOG_FILE)) if LOG_FILE.exists() else 0
            f.seek(max(0, size - 4000))   # 从末尾附近开始
            f.readline()                  # 丢弃可能半截的首行
            while True:
                line = f.readline()
                if line:
                    print(line.rstrip("\n"))
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n已退出实时日志。")


def _active_key_env() -> str:
    """取 llm_providers.json active_profile 的 api_key_env；失败回退 AGNES_API_KEY。"""
    pf = NE / "config" / "llm_providers.json"
    try:
        pfc = json.loads(pf.read_text(encoding="utf-8"))
        prof = (pfc.get("profiles") or {}).get(pfc.get("active_profile")) or {}
        if prof.get("api_key_env"):
            return prof["api_key_env"]
    except Exception:
        pass
    return "AGNES_API_KEY"


def setkey(key: str):
    env_name = _active_key_env()
    envf = NE / ".env"
    lines = envf.read_text(encoding="utf-8").splitlines() if envf.exists() else []
    out, found = [], False
    for l in lines:
        if l.startswith(env_name + "="):
            out.append("%s=%s" % (env_name, key))
            found = True
        else:
            out.append(l)
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append("%s=%s" % (env_name, key))
    envf.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    print("已更新 %s -> %s" % (env_name, envf))
    print("如需切换供应商/模型，请改用 select_llm.bat（详见 LLM选择说明.md）。")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "clean":
        clean()
    elif cmd == "start":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        start(num_chapters=n)
    elif cmd == "stop":
        stop()
    elif cmd == "resume":
        resume()
    elif cmd == "status":
        status()
    elif cmd == "follow":
        follow()
    elif cmd == "setkey":
        if len(sys.argv) < 3:
            print("用法: setkey <新的API_KEY>")
            return
        setkey(sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
