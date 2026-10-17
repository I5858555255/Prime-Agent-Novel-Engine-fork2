@echo off
chcp 65001 >nul
rem Unattended supervisor: keep generating until the target chapter count is
rem reached. Mid-batch HALTs (e.g. a chapter scoring below the publication
rem line) are retried with backoff; a chapter that keeps failing trips the
rem circuit breaker. Pass target chapter count as arg1 (default: total_chapters).
cd /d "%~dp0..\..\src"
"D:\Program Files\Python312\python.exe" -m novel_engine.pipeline.supervisor %1
