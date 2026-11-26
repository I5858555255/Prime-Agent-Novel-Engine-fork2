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
