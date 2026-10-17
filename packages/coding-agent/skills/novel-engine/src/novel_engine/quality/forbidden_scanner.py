import json
import re
from pathlib import Path


class ForbiddenScanner:
    def __init__(self, rules_path: str):
        self.rules = json.loads(Path(rules_path).read_text(encoding="utf-8")).get("rules", [])

    def scan(self, text: str) -> list[dict]:
        hits = []
        for rule in self.rules:
            try:
                rx = re.compile(rule["pattern"])
            except re.error:
                continue
            for m in rx.finditer(text or ""):
                hits.append({
                    "name": rule.get("name") or rule.get("id", "rule"),
                    "severity": rule.get("severity", "medium"),
                    "match": m.group(0),
                    "description": rule.get("description", ""),
                })
        return hits

    def scan_review(self, review: dict) -> list[dict]:
        blobs = []
        for issue in (review.get("issues") or []):
            blobs.append(str(issue.get("description", "")))
            blobs.append(str(issue.get("suggested_fix", "")))
        # Q14 误报修复：评审查报告是技术文本，含 "Scene 1/Scene 2" 等结构词，
        # latin_script_fragment 规则原本针对正文控制层泄漏检测，误拦技术文本。
        # 此处仅跳过 latin_script_fragment 规则，其余规则仍然扫描。
        hits = []
        for rule in self.rules:
            if rule.get("name") == "latin_script_fragment":
                continue  # 跳过 latin_script_fragment，避免误报技术文本中的 "Scene"/"scene_id"
            try:
                rx = re.compile(rule["pattern"])
            except re.error:
                continue
            for m in rx.finditer(" \n ".join(blobs) or ""):
                hits.append({
                    "name": rule.get("name") or rule.get("id", "rule"),
                    "severity": rule.get("severity", "medium"),
                    "match": m.group(0),
                    "description": rule.get("description", ""),
                })
        return hits
