"""
IncrementalPatcher: 实现增量式高阶自修复与自改进。
不再简单全量重写，而是定位特定"病灶"段落，执行精确热插拔拼接（Hot-Swap Patching）。
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class IncrementalPatcher:
    """增量修复器：根据 Reviewer 提供的局部修复病灶（fix_scope）和修改后内容，进行高精度合并。"""

    @staticmethod
    def extract_scene(full_text: str, scene_num: int) -> Optional[str]:
        """从章节文本中精准抠出特定场景的文本，兼容多种格式。"""
        # Format 1: 【场景N：地点】
        pattern1 = r"(【场景" + str(scene_num) + r"：[^】]*】.*?)(?=【场景" + str(scene_num + 1) + r"：|---|\Z)"
        match = re.search(pattern1, full_text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Format 2: ### 其一/其二/其三...
        section_map = {1: "其一", 2: "其二", 3: "其三", 4: "其四", 5: "其五",
                       6: "其六", 7: "其七", 8: "其八", 9: "其九", 10: "其十"}
        chinese_num = section_map.get(scene_num, str(scene_num))
        pattern2 = r"(###\s*" + re.escape(chinese_num) + r"\s*\n.*?)(?=###\s*|---|\Z)"
        match2 = re.search(pattern2, full_text, re.DOTALL)
        if match2:
            return match2.group(1).strip()
        # Format 3: 按 ※ 分隔符分割（容忍两侧任意换行/空白，兼容最后场景）
        parts = re.split(r'\n?\s*※\s*\n?', full_text)
        if 1 <= scene_num <= len(parts):
            return parts[scene_num - 1].strip()
        return None

    @staticmethod
    def apply_scene_patch(full_text: str, scene_num: int, patched_scene_content: str) -> str:
        """
        对特定场景执行增量热插拔：将修改后的场景文本精准缝合回原有大章节中，
        避免 LLM 在全量生成中误删、吃字、或产生格式遗失，大幅提升千万字流水线下的事实一致性。
        """
        # 尝试多种格式提取场景头部
        # Format 1: 【场景N：地点】
        header_pattern1 = r"【场景" + str(scene_num) + r"：[^】]*】"
        header_match1 = re.search(header_pattern1, full_text)
        
        if header_match1:
            header_text = header_match1.group()
            if not patched_scene_content.startswith("【场景"):
                patched_scene_content = f"{header_text}\n\n{patched_scene_content.strip()}"
            pattern = r"(【场景" + str(scene_num) + r"：[^】]*】.*?)(?=【场景" + str(scene_num + 1) + r"：|---|\Z)"
            new_text, count = re.subn(pattern, patched_scene_content, full_text, flags=re.DOTALL)
            if count > 0:
                logger.info(f"Successfully hot-swapped Scene {scene_num} patch in chapter text.")
                return new_text
        
        # Format 2: ### 其一/其二...
        section_map = {1: "其一", 2: "其二", 3: "其三", 4: "其四", 5: "其五",
                       6: "其六", 7: "其七", 8: "其八", 9: "其九", 10: "其十"}
        chinese_num = section_map.get(scene_num, str(scene_num))
        header_pattern2 = r"###\s*" + re.escape(chinese_num)
        header_match2 = re.search(header_pattern2, full_text)
        if header_match2:
            header_text = header_match2.group()
            if not patched_scene_content.startswith("###"):
                patched_scene_content = f"{header_text}\n\n{patched_scene_content.strip()}"
            pattern = r"(###\s*" + re.escape(chinese_num) + r"\s*\n.*?)(?=###\s*|---|\Z)"
            new_text, count = re.subn(pattern, patched_scene_content, full_text, flags=re.DOTALL)
            if count > 0:
                logger.info(f"Successfully hot-swapped Scene {scene_num} patch (format 2) in chapter text.")
                return new_text
        
        # Format 3: 按 ※ 分隔符分割并替换（容忍任意空白包裹，兼容最后场景；post-enforce 截断后标记可能丢失，故放宽 len 检查）
        parts = re.split(r'\n?\s*※\s*\n?', full_text)
        # 过滤空块但保留索引映射
        non_empty_idx = [i for i, p in enumerate(parts) if p.strip()]
        if 1 <= scene_num <= len(parts):
            # 即使 len==1 也尝试按索引替换（适用于净化后无 ※ 的文本）
            if len(parts) == 1 and scene_num == 1:
                logger.info(f"Single-part text, replacing whole chapter as scene {scene_num} patch")
                return patched_scene_content.strip()
            parts[scene_num - 1] = patched_scene_content.strip()
            logger.info(f"Successfully hot-swapped Scene {scene_num} patch (format 3) in chapter text.")
            return "\n\n※\n\n".join(parts)
        # 降级：按场景序号直接按 ※ 索引替换，忽略标记存在性
        if non_empty_idx and 1 <= scene_num <= len(non_empty_idx):
            idx = non_empty_idx[scene_num - 1]
            parts[idx] = patched_scene_content.strip()
            logger.info(f"Fallback hot-swapped Scene {scene_num} via index {idx} (marker missing but index hit)")
            return "\n\n※\n\n".join(parts)

        logger.warning(f"Scene {scene_num} marker not found. Skipping patch to avoid content duplication.")
        return full_text

    @staticmethod
    def apply_regex_patch(full_text: str, target_keyword: str, patched_text: str) -> str:
        """根据关键字范围（如角色名、特定武功）进行近距离语义缝合。"""
        lines = full_text.splitlines()
        for idx, line in enumerate(lines):
            if target_keyword in line:
                # 局部替换第 idx 行为新生成的 patched_text
                lines[idx] = patched_text
                logger.info(f"Applied semantic keyword patch on line {idx + 1}")
                return "\n".join(lines)
        return full_text
