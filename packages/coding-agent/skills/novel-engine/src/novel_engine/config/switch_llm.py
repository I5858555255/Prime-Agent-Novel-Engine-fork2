#!/usr/bin/env python3
"""LLM Profile 切换器（零依赖，标准库实现）。

用法：
  python switch_llm.py              # 列出所有 profiles 与当前 active
  python switch_llm.py <profile名>  # 原子切换 active_profile
  python switch_llm.py switch <profile名>  # 同上（带子命令）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "llm_providers.json"

REQUIRED_FIELDS = ["base_url", "api_key_env", "phases"]
PHASE_REQUIRED = ["models"]


def _load() -> dict:
    if not CONFIG_PATH.exists():
        print(f"ERROR: {CONFIG_PATH} not found", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in {CONFIG_PATH}: {e}", file=sys.stderr)
        sys.exit(1)


def _save(cfg: dict) -> None:
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=CONFIG_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _validate_profile(name: str, profile: dict) -> list[str]:
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in profile:
            errors.append(f"profile '{name}' missing required field '{field}'")
    if errors:
        return errors
    phases = profile.get("phases") or {}
    for phase_name, phase_cfg in phases.items():
        for pf in PHASE_REQUIRED:
            if pf not in phase_cfg:
                errors.append(f"profile '{name}' phase '{phase_name}' missing '{pf}'")
        models = phase_cfg.get("models") or []
        if not models:
            errors.append(f"profile '{name}' phase '{phase_name}' has empty models list")
    return errors


def cmd_list() -> None:
    cfg = _load()
    active = cfg.get("active_profile", "")
    profiles = cfg.get("profiles") or {}
    print(f"Active profile: {active}\n")
    for name, profile in profiles.items():
        marker = " <-- ACTIVE" if name == active else ""
        base = profile.get("base_url", "?")
        key_env = profile.get("api_key_env", "?")
        print(f"[{name}]{marker}")
        print(f"  base_url: {base}")
        print(f"  api_key_env: {key_env}")
        phases = profile.get("phases") or {}
        for pname, pcfg in sorted(phases.items()):
            models = pcfg.get("models") or []
            rf = pcfg.get("response_format") or "none"
            conc = pcfg.get("concurrency", "")
            conc_s = f" concurrency={conc}" if conc else ""
            print(f"  phase[{pname}]: models={models}, response_format={rf}{conc_s}")
        print()


def _runtime_config_path() -> Path:
    """runtime_config.json sits next to llm_providers.json; follows CONFIG_PATH."""
    return CONFIG_PATH.parent / "runtime_config.json"


def sync_runtime_config(name: str, profile: dict) -> str:
    """把 runtime_config.json 的生成端点与所选 provider profile 保持一致。

    同步 llm / fallback_llm / provider（若该 profile 提供 review 模型也同步 review）。
    当同级不存在 runtime_config.json 时静默跳过（隔离测试目录）。返回说明字符串。
    """
    rc_path = _runtime_config_path()
    if not rc_path.exists():
        return ""
    rc = json.loads(rc_path.read_text(encoding="utf-8"))
    phases = profile.get("phases") or {}
    gen_phase = phases.get("scenes") or phases.get("director") or {}
    gen_models = gen_phase.get("models") or []
    model = gen_models[0] if gen_models else ""
    base_url = profile.get("base_url", "")
    key_env = profile.get("api_key_env", "")
    common = {"model": model, "api_base": base_url, "api_key_env": key_env}

    llm = dict(rc.get("llm", {}))
    llm.update({"provider": "openai", **common})
    rc["llm"] = llm
    fb = dict(rc.get("fallback_llm", {}))
    # fallback 只随 agnes 系 profile 同步；切到其它供应商时保持既有回退
    # （AGNES_API_KEY 常驻 .env，siliconflow 故障时回退到 agnes，测试断言此不变量）。
    if "agnes" in name:
        fb.update(common)
    elif not fb.get("api_base"):
        fb.update(common)
    rc["fallback_llm"] = fb

    # family 优先取 profile 显式声明；缺省再按名字推导（修正早期 "silicon"->"qwen" 的误映射）
    family = profile.get("family") or (
        "agnes" if "agnes" in name else
        "siliconflow" if "silicon" in name else
        "qwen" if "qwen" in name else
        rc.get("provider", {}).get("family", "openai")
    )
    prov = dict(rc.get("provider", {}))
    prov.update({"family": family, "api_base": base_url, "model": model,
                 "reasoning_fallback": family != "agnes"})
    rc["provider"] = prov

    # 同步遗留 model_router 相位模型，避免任何读者继续用到旧供应商模型名
    if phases:
        def _phase_models(_ph):
            _ms = (phases.get(_ph) or {}).get("models") or []
            return list(_ms) if _ms else ([model] if model else [])
        mr = dict(rc.get("model_router") or {})
        mrp = dict(mr.get("phases") or {})
        for _dst, _src in (("scenes", "scenes"), ("polish", "polish"),
                           ("synopsis", "synopsis"), ("director", "director"),
                           ("review", "review"), ("planning", "outline")):
            _v = _phase_models(_src)
            if _v:
                mrp[_dst] = _v
        mr["phases"] = mrp
        lim = dict(mr.get("limits") or {})
        if model and model not in lim:
            lim[model] = {"rpm": 20, "tpm": 200000}
        mr["limits"] = lim
        rc["model_router"] = mr

    rev_models = (phases.get("review") or {}).get("models") or []
    if rev_models:
        rl = dict(rc.get("review_llm", {}))
        rl.update({"model": rev_models[0], "api_base": base_url, "api_key_env": key_env})
        rc["review_llm"] = rl
        rp = dict(rc.get("review_provider", {}))
        rp["family"] = family
        rc["review_provider"] = rp

    fd, tmp = tempfile.mkstemp(suffix=".json", dir=rc_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, rc_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return f"  runtime_config.json synced -> {family} {model} @ {base_url}"


def cmd_switch(name: str) -> None:
    cfg = _load()
    profiles = cfg.get("profiles") or {}
    if name not in profiles:
        print(f"ERROR: profile '{name}' not found. Available: {list(profiles.keys())}", file=sys.stderr)
        sys.exit(1)
    profile = profiles[name]
    errors = _validate_profile(name, profile)
    if errors:
        print(f"ERROR: profile '{name}' validation failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    cfg["active_profile"] = name
    _save(cfg)
    print(f"Switched to profile '{name}'")
    _sync_note = sync_runtime_config(name, profile)
    if _sync_note:
        print(_sync_note)
    key_env = profile.get("api_key_env", "")
    # Key availability check: look in ENG/.env file directly
    _eng_dir = CONFIG_PATH.parent.parent
    _env_path = _eng_dir / ".env"
    key_set = False
    if _env_path.exists():
        for _line in _env_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line.startswith(key_env + "="):
                key_set = True
                break
    print(f"  base_url: {profile.get('base_url')}")
    print(f"  api_key_env: {key_env} {'[SET]' if key_set else '[NOT SET - will fail at runtime]'}")


def main() -> None:
    if len(sys.argv) < 2:
        cmd_list()
        return
    action = sys.argv[1]
    if action == "list":
        cmd_list()
    elif action == "switch":
        if len(sys.argv) < 3:
            print("Usage: switch_llm.py switch <profile-name>", file=sys.stderr)
            sys.exit(1)
        cmd_switch(sys.argv[2])
    else:
        # Direct profile name: treat as switch
        cmd_switch(action)


if __name__ == "__main__":
    main()
