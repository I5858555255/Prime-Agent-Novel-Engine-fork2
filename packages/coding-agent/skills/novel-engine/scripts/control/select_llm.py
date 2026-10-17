#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
novel-engine LLM 选择器
=======================
切换全引擎使用的 LLM（作用于 src/novel_engine/config/llm_providers.json 的
active_profile），并可把对应 API Key 写入 src/novel_engine/.env。真实密钥只写
.env，绝不写进 llm_providers.json，脚本也不会回显完整密钥。

用法（在 scripts/control 目录或通过 select_llm.bat）：
  select_llm.py current                 查看当前 profile 与各阶段模型、Key 是否就位
  select_llm.py list                    列出所有可选 profile
  select_llm.py switch <profile>       仅切换 active_profile（不改动密钥）
  select_llm.py set-key <profile> <key> 为该 profile 的 api_key_env 写入/更新 .env
  select_llm.py use <profile> [key]     switch + set-key 一步到位

新增供应商：直接编辑 config/llm_providers.json 的 profiles，按现有 agnes 结构
复制一个 profile（base_url / api_key_env / phases），再用本脚本 switch。
"""
import sys
import json
import os
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
NE = SKILL_DIR / "src" / "novel_engine"
CFG = NE / "config" / "llm_providers.json"
ENV_FILE = NE / ".env"

REQUIRED = ("base_url", "api_key_env", "phases")


def _load():
    if not CFG.exists():
        raise SystemExit(f"找不到配置文件: {CFG}")
    try:
        return json.loads(CFG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"llm_providers.json 不是合法 JSON: {e}")


def _save(cfg):
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _mask(v: str) -> str:
    if not v:
        return "(空)"
    return v[:6] + "..." + v[-4:] if len(v) > 12 else "****"


def _phase_models(prof):
    out = {}
    for ph, pc in (prof.get("phases") or {}).items():
        ms = (pc or {}).get("models") or []
        if ms:
            out[ph] = ",".join(ms)
    return out


def _check_env(name):
    return bool((os.environ.get(name) or "").strip())


def cmd_list(_args):
    cfg = _load()
    active = cfg.get("active_profile")
    profs = cfg.get("profiles") or {}
    if not profs:
        print("profiles 为空。")
        return
    print(f"{'(当前)' if False else '      '} {'profile':<22} {'base_url':<34} key_env / 状态")
    for name, prof in profs.items():
        flag = "  => " if name == active else "     "
        env = prof.get("api_key_env", "")
        mark = "key已在环境" if _check_env(env) else "key未在当前环境(可set-key写.env)"
        print(f"{flag}{name:<22} {prof.get('base_url',''):<34} {env}  {mark}")


def cmd_current(_args):
    cfg = _load()
    name = cfg.get("active_profile")
    prof = (cfg.get("profiles") or {}).get(name)
    if not prof:
        raise SystemExit(f"active_profile='{name}' 在 profiles 中不存在，请用 list 查看。")
    print(f"当前 LLM profile : {name}")
    print(f"base_url         : {prof.get('base_url')}")
    env = prof.get("api_key_env", "")
    in_env = _check_env(env)
    in_dotenv = _env_has_key(env)
    print(f"api_key_env      : {env}  (当前进程环境: {'有' if in_env else '无'}; .env: {'有' if in_dotenv else '无'})")
    if in_dotenv:
        print(f".env 中密钥       : {_mask(_env_get(env))}")
    print("各阶段模型:")
    for ph, ms in _phase_models(prof).items():
        print(f"  - {ph:<9}: {ms}")
    missing = [k for k in REQUIRED if not prof.get(k)]
    if missing:
        print(f"警告: 该 profile 缺少必填字段 {missing}，启动会报 ConfigFatalError。")


def _env_lines():
    if not ENV_FILE.exists():
        return []
    return ENV_FILE.read_text(encoding="utf-8").splitlines()


def _env_has_key(name):
    return any(l.strip().startswith(name + "=") for l in _env_lines())


def _env_get(name):
    for l in _env_lines():
        if l.strip().startswith(name + "="):
            return l.split("=", 1)[1].strip()
    return ""


def cmd_set_key(args):
    if len(args) < 2:
        raise SystemExit("用法: set-key <profile> <api_key>")
    name, key = args[0], args[1].strip()
    cfg = _load()
    prof = (cfg.get("profiles") or {}).get(name)
    if not prof:
        raise SystemExit(f"profile '{name}' 不存在，可用: {list((cfg.get('profiles') or {}).keys())}")
    if not key:
        raise SystemExit("api_key 为空，已取消。")
    env_name = prof["api_key_env"]
    lines = _env_lines()
    updated = False
    for i, l in enumerate(lines):
        if l.strip().startswith(env_name + "="):
            lines[i] = f"{env_name}={key}"
            updated = True
            break
    if not updated:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{env_name}={key}")
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已向 {ENV_FILE} 写入 {env_name}={_mask(key)}（profile={name}）。")
    print("注意：需在 start/resume 启动的环境里可读到该变量；若用控制脚本启动会自动加载 .env。")


def cmd_switch(args):
    if not args:
        raise SystemExit("用法: switch <profile>")
    name = args[0]
    cfg = _load()
    profs = cfg.get("profiles") or {}
    if name not in profs:
        raise SystemExit(f"profile '{name}' 不存在，可选: {list(profs.keys())}")
    prof = profs[name]
    missing = [k for k in REQUIRED if not prof.get(k)]
    if missing:
        raise SystemExit(f"profile '{name}' 缺少必填字段 {missing}，拒绝切换。")
    cfg["active_profile"] = name
    _save(cfg)
    env = prof.get("api_key_env", "")
    print(f"已切换 active_profile -> {name}（base_url={prof.get('base_url')}, key_env={env}）。")
    if not _env_has_key(env):
        print(f"提醒: .env 尚未配置 {env}，请执行: select_llm set-key {name} <你的key>")
    print("下次 start/resume 生效。")


def cmd_use(args):
    if not args:
        raise SystemExit("用法: use <profile> [api_key]")
    cmd_switch(args[:1])
    if len(args) >= 2 and args[1].strip():
        cmd_set_key(args[:2])


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]
    table = {"list": cmd_list, "current": cmd_current, "switch": cmd_switch,
             "set-key": cmd_set_key, "use": cmd_use}
    fn = table.get(cmd)
    if not fn:
        raise SystemExit(f"未知子命令 '{cmd}'。可用: {list(table.keys())}")
    fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
