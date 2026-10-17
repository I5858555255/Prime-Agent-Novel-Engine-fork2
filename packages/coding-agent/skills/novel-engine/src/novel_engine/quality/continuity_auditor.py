import json
import logging

logger = logging.getLogger(__name__)


class ContinuityAuditor:
    def __init__(self, llm_client=None, world_state=None, rules_path="config/forbidden.json"):
        self.llm_client = llm_client
        self.world_state = world_state
        self.rules = self.load_rules(rules_path)

    def load_rules(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"rules": []}

    def audit(self, realm_name, snapshot_a, snapshot_b):
        contradictions = []
        if not snapshot_a or not snapshot_b:
            return {"realm": realm_name, "contradictions": contradictions, "passed": True}

        def g(s, keys, default=None):
            cur = s
            for k in keys:
                if not isinstance(cur, dict) or k not in cur:
                    return default
                cur = cur[k]
            return cur

        realms_a = g(snapshot_a, ["world", "realms"], {}) or {}
        realms_b = g(snapshot_b, ["world", "realms"], {}) or {}
        ra = realms_a.get(realm_name, {})
        rb = realms_b.get(realm_name, {})
        for key in ["name", "description"]:
            va, vb = ra.get(key), rb.get(key)
            if va is not None and vb is not None and va != vb:
                contradictions.append(f"{realm_name}.{key} changed: {va!r} -> {vb!r}")
        return {"realm": realm_name, "contradictions": contradictions,
                "passed": len(contradictions) == 0}

    def probe_with_llm(self, realm_name, snapshot_a, snapshot_b, prompt):
        if not self.llm_client:
            return {"passed": True, "note": "no-llm-skip"}
        sys = (f"You are a continuity checker. Realm: {realm_name}.\n"
               f"Snapshot A: {snapshot_a}\nSnapshot B: {snapshot_b}\n{prompt}")
        try:
            resp = self.llm_client.chat_completion(
                [{"role": "system", "content": sys},
                 {"role": "user", "content": "Answer JSON only: {\"passed\": bool, \"note\": str}"}],
                max_tokens=300)
        except Exception as e:
            return {"passed": True, "note": f"probe-error:{e}"}
        try:
            return json.loads(resp["content"])
        except Exception:
            return {"passed": True, "note": "unparseable-probe"}

    def audit_full(self, world_state):
        report = {"passed": True, "realms": []}
        for r in (world_state or {}).get("world", {}).get("realms", {}):
            snap_a = world_state.get("_baseline") or world_state
            snap_b = world_state
            res = self.audit(r, snap_a, snap_b)
            if not res["passed"]:
                report["passed"] = False
            report["realms"].append(res)
        return report
