#!/usr/bin/env python3
"""
Mini 测试运行器：跑通 10 章生成闭环。
验证：
1. 状态机基本流程
2. pending/commit 事务
3. recovery_policy 分流逻辑
4. checkpoint 完整性
支持 --real 参数使用真实 API（SiliconFlow / Qwen），否则使用 MockLLMClient。
"""
import json
import logging
import sys
import time
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from novel_engine.core.llm_client import LLMClient, MockLLMClient
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
from novel_engine.pipeline.reset_state import (
    reset_runtime_state,
    verify_output_files,
    verify_recovery_policy,
)
from novel_engine.core.state_machine import ChapterPhase

PROJECT_ROOT = Path(__file__).parent.parent
logs_dir = PROJECT_ROOT / "runtime/logs"
logs_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(PROJECT_ROOT / "runtime/logs/mini_test.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("mini_test")


def run_mini_test(num_chapters: int = 10, use_real_api: bool = False):
    """运行 Mini 测试并返回结果。"""
    logger.info(f"===== MINI TEST START: {num_chapters} chapters =====")
    logger.info(f"API mode: {'REAL' if use_real_api else 'MOCK'}")
    start_time = time.time()

    project_root = Path(__file__).parent.parent
    # 确保审计目录存在
    (project_root / "audit").mkdir(parents=True, exist_ok=True)
    reset_runtime_state(project_root)

    # 选择 LLM 客户端
    if use_real_api:
        orchestrator = PipelineOrchestrator(str(project_root))
        logger.info("Using real API (SiliconFlow / Qwen)")
    else:
        orchestrator = PipelineOrchestrator(str(project_root), llm_client=MockLLMClient())
        logger.info("Using Mock LLM Client")

    # 1. 验证状态机初始化
    sm = orchestrator.state_machine
    assert sm.current_chapter == 0, f"Expected chapter 0, got {sm.current_chapter}"
    assert sm.current_phase == ChapterPhase.INIT, f"Expected INIT, got {sm.current_phase}"
    logger.info("OK State machine initialized correctly")

    # 2. 验证 checkpoint 管理器
    cm = orchestrator.checkpoint_mgr
    latest = cm.get_latest_checkpoint()
    assert latest is None, f"Expected no checkpoints initially, got {latest}"
    logger.info("OK Checkpoint manager clean")

    # 3. 运行章节生成
    results = []
    for i in range(1, num_chapters + 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"GENERATING CHAPTER {i}/{num_chapters}")
        logger.info(f"{'='*60}")

        try:
            result = orchestrator._run_with_retry(i)
        except Exception as e:
            logger.error(f"Chapter {i} hard-failed after retries: {e}")
            result = {
                "chapter": i,
                "success": False,
                "score": None,
                "errors": [str(e)],
            }

        results.append(result)

        status = "PASS" if result["success"] else "FAIL"
        score = result.get("score", "N/A")
        logger.info(f"Chapter {i}: {status} (score={score})")

        if result["errors"]:
            transient = any(
                any(k in err for k in ("502", "503", "Gateway", "timeout", "Timeout"))
                for err in result["errors"]
            )
            for err in result["errors"]:
                logger.warning(f"  Error: {err}")
            if transient and not result["success"]:
                logger.warning(f"  Transient API error; pausing 60s before next chapter")
                time.sleep(60)

    elapsed = time.time() - start_time

    # 4. 验证结果
    passed = sum(1 for r in results if r.get("success"))
    failed = len(results) - passed

    logger.info(f"\n{'='*60}")
    logger.info("MINI TEST RESULTS")
    logger.info(f"{'='*60}")
    logger.info(f"Chapters attempted: {len(results)}")
    logger.info(f"Passed: {passed}")
    logger.info(f"Failed: {failed}")
    logger.info(f"Time elapsed: {elapsed:.2f}s")

    if results:
        scores = [r.get("score") for r in results if r.get("score") is not None]
        if scores:
            logger.info(f"Average score: {sum(scores)/len(scores):.1f}")
            logger.info(f"Min score: {min(scores)}")
            logger.info(f"Max score: {max(scores)}")

    # 5. 验证 checkpoint
    cp_data = cm.load()
    checkpoint_count = len(cp_data.get("checkpoints", []))
    logger.info(f"Checkpoints created: {checkpoint_count}")

    # 6. 验证文件是否写入
    verify_output_files(project_root, results, num_chapters)

    # 7. 验证 recovery_policy
    verify_recovery_policy(project_root)

    # 8. 汇总质量指标：每章字数/文件大小 + 各评分维度均分
    quality_summary = {"avg_score": None, "min_score": None, "max_score": None,
                       "dimension_averages": {}, "word_counts": []}
    for r in results:
        novel_path = project_root / "chapters" / "novel" / f"chapter_{r['chapter']}.txt"
        if novel_path.exists():
            text = novel_path.read_text(encoding="utf-8")
            r["word_count"] = len(text)
            r["novel_size"] = novel_path.stat().st_size
            quality_summary["word_counts"].append(len(text))

    scores = [r.get("score") for r in results if r.get("score") is not None]
    if scores:
        quality_summary["avg_score"] = round(sum(scores) / len(scores), 1)
        quality_summary["min_score"] = min(scores)
        quality_summary["max_score"] = max(scores)

    reviews_path = project_root / "audit" / "per_chapter_reviews.json"
    if reviews_path.exists():
        try:
            revs = json.loads(reviews_path.read_text(encoding="utf-8")).get("reviews", [])
            dims = ["plot_consistency", "character_consistency", "foreshadow_execution",
                    "style_match", "pacing", "innovation"]
            for d in dims:
                vals = [rev.get("scores", {}).get(d, 0) for rev in revs]
                if vals:
                    quality_summary["dimension_averages"][d] = round(sum(vals) / len(vals), 1)
        except Exception as e:
            logger.warning(f"Could not read per_chapter_reviews.json: {e}")

    # 字数达标度：以章节任务卡的场景字数目标为基准，警告严重不足的章节
    under_target = 0
    for r in results:
        outline_path = project_root / "chapters" / "outline" / f"chapter_{r['chapter']}.json"
        target = 0
        if outline_path.exists():
            try:
                card = json.loads(outline_path.read_text(encoding="utf-8"))
                target = sum(bp.get("word_count_target", 0) for bp in card.get("scene_blueprints", []))
            except Exception:
                pass
        if target > 0:
            pct = r.get("word_count", 0) / target * 100
            r["word_target"] = target
            r["word_pct"] = round(pct, 1)
            if pct < 50:
                under_target += 1
    if under_target:
        logger.warning(f"{under_target}/{num_chapters} chapters below 50% of word-count target")

    # SessionTree 链完整性：root 应恰好延伸到最后一章
    st_path = project_root / "runtime" / "session_tree.json"
    if st_path.exists():
        try:
            st = json.loads(st_path.read_text(encoding="utf-8"))
            nodes = st.get("nodes", {})
            cur = st.get("root_node_id")
            chain_len = 0
            while cur and chain_len <= num_chapters + 1:
                kids = nodes.get(cur, {}).get("children_ids", [])
                if not kids:
                    break
                cur = kids[0]
                chain_len += 1
            if chain_len == num_chapters:
                logger.info(f"OK SessionTree chain extends root->chapter {num_chapters} ({chain_len} links)")
            else:
                logger.warning(f"SessionTree chain length {chain_len}, expected {num_chapters}")
        except Exception as e:
            logger.warning(f"Could not verify SessionTree chain: {e}")

    # 9. 输出最终报告
    report = {
        "test_type": "mini",
        "num_chapters": num_chapters,
        "use_real_api": use_real_api,
        "passed": passed,
        "failed": failed,
        "results": results,
        "checkpoint_count": checkpoint_count,
        "elapsed_seconds": round(elapsed, 2),
        "quality": quality_summary,
        "under_target_chapters": under_target,
        "success": failed == 0,
    }

    report_path = project_root / "audit" / "mini_test_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Report saved to {report_path}")

    return report


if __name__ == "__main__":
    # 解析命令行参数
    use_real_api = "--real" in sys.argv
    num_chapters = 10
    for arg in sys.argv[1:]:
        if arg != "--real":
            try:
                num_chapters = int(arg)
            except ValueError:
                logger.error(f"Unknown argument: {arg}")
                sys.exit(1)

    report = run_mini_test(num_chapters, use_real_api=use_real_api)

    if report["success"]:
        logger.info("\nMINI TEST PASSED")
        sys.exit(0)
    else:
        logger.info("\nMINI TEST FAILED")
        sys.exit(1)
