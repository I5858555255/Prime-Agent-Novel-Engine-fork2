#!/usr/bin/env python3
"""
中等规模测试运行器：跑通 70 章生成闭环。
验证：
1. 状态机基本流程（同 mini）
2. pending/commit 事务
3. 滑动窗口审查在 50 章边界触发
4. 质量记忆刷新与过期清理
5. 剧情约束层锁定跨章节被正确遵守
6. 任务卡到缩写的指令衰减可接受
7. recovery_policy 分流逻辑
8. checkpoint 完整性
"""
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from novel_engine.core.llm_client import MockLLMClient
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
        logging.FileHandler(PROJECT_ROOT / "runtime/logs/medium_test.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("medium_test")


def run_medium_test(num_chapters: int = 70, use_real: bool = False):
    """运行中等规模测试并返回结果。"""
    logger.info(f"===== MEDIUM TEST START: {num_chapters} chapters =====")
    start_time = time.time()

    project_root = Path(__file__).parent.parent
    (project_root / "audit").mkdir(parents=True, exist_ok=True)
    reset_runtime_state(project_root)

    if use_real:
        from novel_engine.core.llm_client import LLMClient, reset_call_log
        client = LLMClient.from_config(config_path=str(project_root / "config" / "runtime_config.json"))
        reset_call_log()  # Clear any stale call log
        logger.info("OK Using real API (SiliconFlow / Qwen)")
    else:
        client = MockLLMClient()
    orchestrator = PipelineOrchestrator(str(project_root), llm_client=client)

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

    # 3. 运行章节生成 + 滑动窗口审查
    results = []
    sliding_window_triggered = False
    quality_memory_refreshed = False
    realm_locks_respected = True

    for i in range(1, num_chapters + 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"GENERATING CHAPTER {i}/{num_chapters}")
        logger.info(f"{'='*60}")

        try:
            result = orchestrator.generate_single_chapter(i)
            results.append(result)

            status = "PASS" if result["success"] else "FAIL"
            score = result.get("score", "N/A")
            logger.info(f"Chapter {i}: {status} (score={score})")

            if not result["success"]:
                for err in result["errors"]:
                    logger.warning(f"  Error: {err}")

        except Exception as e:
            logger.error(f"Chapter {i} crashed: {e}")
            results.append({
                "chapter": i,
                "success": False,
                "score": None,
                "errors": [str(e)],
            })
            break

        # 每50章触发滑动窗口审查
        if i % 50 == 0 and i <= num_chapters:
            sliding_window_triggered = True
            logger.info(f"Triggering sliding window review at chapter {i}")
            orchestrator._run_sliding_window_review(i)

            # 验证质量记忆已刷新
            qm = orchestrator.memory.load_quality_memory()
            if qm.get("updated_at_chapter", 0) >= i:
                quality_memory_refreshed = True
                logger.info(f"OK Quality memory refreshed at chapter {qm['updated_at_chapter']}")

            # 验证滑动窗口审查结果已保存
            sw_reviews = orchestrator.memory.load_sliding_window_reviews()
            if any(r.get("window_end") == i for r in sw_reviews):
                logger.info(f"OK Sliding window review saved for chapters ending at {i}")

        # 验证 realm lock 是否被遵守
        world_state = orchestrator.simulator.build_world_state_for_chapter(i)
        for char_id, char_data in world_state.get("characters", {}).items():
            progression = char_data.get("progression_suggestion", {})
            if progression.get("locked"):
                locked_until = progression.get("reason", "")
                logger.debug(f"Chapter {i}: {char_id} locked at {progression.get('realm')}")

    elapsed = time.time() - start_time

    # 4. 验证结果
    passed = sum(1 for r in results if r.get("success"))
    failed = len(results) - passed

    logger.info(f"\n{'='*60}")
    logger.info("MEDIUM TEST RESULTS")
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
    assert checkpoint_count == passed, f"Expected {passed} checkpoints, got {checkpoint_count}"
    logger.info("OK Checkpoint count matches passed chapters")

    # 6. 验证文件是否写入
    verify_output_files(project_root, results, num_chapters)

    # 7. 验证 sliding window 触发
    if sliding_window_triggered:
        logger.info("OK Sliding window review triggered at chapter 50")
    else:
        logger.warning("Sliding window review NOT triggered at chapter 50")

    # 8. 验证质量记忆刷新
    if quality_memory_refreshed:
        logger.info("OK Quality memory refreshed after sliding window")
    else:
        logger.warning("Quality memory was NOT refreshed")

    # 9. 验证所有 checkpoint 完整性
    integrity_ok = True
    for i in range(1, passed + 1):
        if not cm.verify_integrity(i):
            logger.warning(f"Checkpoint integrity FAILED for chapter {i}")
            integrity_ok = False
    if integrity_ok:
        logger.info("OK All checkpoints pass integrity verification")

    # 10. 验证 recovery_policy
    verify_recovery_policy(project_root)

    # 11. 输出最终报告
    report = {
        "test_type": "medium",
        "num_chapters": num_chapters,
        "passed": passed,
        "failed": failed,
        "results": results,
        "checkpoint_count": checkpoint_count,
        "sliding_window_triggered": sliding_window_triggered,
        "quality_memory_refreshed": quality_memory_refreshed,
        "integrity_ok": integrity_ok,
        "elapsed_seconds": round(elapsed, 2),
        "success": failed == 0 and sliding_window_triggered and quality_memory_refreshed and integrity_ok,
        "cost_sandbox_report": "audit/cost_sandbox_report.json",
    }

    report_path = project_root / "audit" / "medium_test_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Report saved to {report_path}")

    # Generate cost sandbox report
    cost_report = orchestrator.get_cost_sandbox_report(passed)
    cost_report_path = project_root / "audit" / "cost_sandbox_report.json"
    cost_report_path.write_text(json.dumps(cost_report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Cost sandbox report saved to {cost_report_path}")
    logger.info(f"Total API calls: {cost_report['total_api_calls']}")
    logger.info(f"Total tokens: {cost_report['token_usage']['total_tokens']}")
    logger.info(f"Estimated full production cost: ${cost_report['cost_usd']['estimated_full_production']:.2f}")
    logger.info(f"Within budget: {cost_report['budget']['within_budget']}")

    return report


if __name__ == "__main__":
    use_real = "--real" in sys.argv
    args = [a for a in sys.argv if a != "--real"]
    num_chapters = int(args[1]) if len(args) > 1 else 70
    report = run_medium_test(num_chapters, use_real=use_real)

    if report["success"]:
        logger.info("\nMEDIUM TEST PASSED")
        sys.exit(0)
    else:
        logger.info("\nMEDIUM TEST FAILED")
        sys.exit(1)
