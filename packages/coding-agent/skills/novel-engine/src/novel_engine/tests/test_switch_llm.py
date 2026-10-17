"""Tests for switch_llm.py (W8)."""
import json
import os
import sys
import tempfile
from pathlib import Path

# Ensure novel_engine/config is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "config"))


def _make_tmp_config(tmp_path):
    """Copy llm_providers.json to tmp_path for testing."""
    src = Path(__file__).parent.parent / "config" / "llm_providers.json"
    dst = tmp_path / "llm_providers.json"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def test_list_shows_active_and_profiles(tmp_path, monkeypatch):
    """List command shows current active and all profiles (model-agnostic)."""
    import switch_llm
    cfg_path = _make_tmp_config(tmp_path)
    monkeypatch.setattr(switch_llm, "CONFIG_PATH", cfg_path)

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    active = cfg["active_profile"]
    others = [p for p in cfg["profiles"] if p != active]

    import io
    old_stdout = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        sys.argv = ["switch_llm.py"]
        switch_llm.main()
    finally:
        sys.stdout = old_stdout

    output = buf.getvalue()
    assert f"Active profile: {active}" in output
    assert f"[{active}]" in output
    assert any(f"[{p}]" in output for p in others), "no other profile listed"


def test_switch_direct_syntax(tmp_path, monkeypatch):
    """switch_llm.py <profile> works as direct switch."""
    import switch_llm
    cfg_path = _make_tmp_config(tmp_path)
    monkeypatch.setattr(switch_llm, "CONFIG_PATH", cfg_path)

    import io
    old_stdout = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        sys.argv = ["switch_llm.py", "siliconflow"]
        switch_llm.main()
    finally:
        sys.stdout = old_stdout

    output = buf.getvalue()
    assert "Switched to profile 'siliconflow'" in output

    # Verify file was changed
    cfg = switch_llm._load()
    assert cfg["active_profile"] == "siliconflow"


def test_switch_subcommand_syntax(tmp_path, monkeypatch):
    """switch_llm.py switch <profile> works."""
    import switch_llm
    cfg_path = _make_tmp_config(tmp_path)
    monkeypatch.setattr(switch_llm, "CONFIG_PATH", cfg_path)

    import io
    old_stdout = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        sys.argv = ["switch_llm.py", "switch", "siliconflow"]
        switch_llm.main()
    finally:
        sys.stdout = old_stdout

    output = buf.getvalue()
    assert "Switched to profile 'siliconflow'" in output

    cfg = switch_llm._load()
    assert cfg["active_profile"] == "siliconflow"


def test_switch_preserves_other_fields(tmp_path, monkeypatch):
    """Switching profile must preserve _help and other profiles unchanged."""
    import switch_llm
    cfg_path = _make_tmp_config(tmp_path)
    monkeypatch.setattr(switch_llm, "CONFIG_PATH", cfg_path)

    # Read original
    original = json.loads(cfg_path.read_text(encoding="utf-8"))
    original_help = original.get("_help")
    original_siliconflow = json.dumps(original["profiles"]["siliconflow"], sort_keys=True)

    import io
    old_stdout = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        sys.argv = ["switch_llm.py", "siliconflow"]
        switch_llm.main()
    finally:
        sys.stdout = old_stdout

    # Read back
    cfg = switch_llm._load()
    assert cfg.get("_help") == original_help
    siliconflow_back = json.dumps(cfg["profiles"]["siliconflow"], sort_keys=True)
    assert siliconflow_back == original_siliconflow
    assert cfg["active_profile"] == "siliconflow"


def test_invalid_profile_exits_nonzero(tmp_path, monkeypatch):
    """Invalid profile name exits with non-zero code."""
    import switch_llm
    cfg_path = _make_tmp_config(tmp_path)
    monkeypatch.setattr(switch_llm, "CONFIG_PATH", cfg_path)

    sys.argv = ["switch_llm.py", "nonexistent"]
    try:
        switch_llm.main()
        assert False, "should have exited"
    except SystemExit as e:
        assert e.code == 1


def test_no_args_lists_profiles(tmp_path, monkeypatch):
    """No args prints list (exits 0)."""
    import switch_llm
    cfg_path = _make_tmp_config(tmp_path)
    monkeypatch.setattr(switch_llm, "CONFIG_PATH", cfg_path)

    sys.argv = ["switch_llm.py"]
    try:
        switch_llm.main()
    except SystemExit as e:
        assert e.code == 0
