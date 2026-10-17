# 清理脚本
$base = "D:/projectAGI/Prime-Agent-Novel-Engine-feat-stability-quality"

Write-Host "=== 开始清理 ===" -ForegroundColor Yellow

# 1. 删除空目录
Write-Host "`n1. 清理空目录..." -ForegroundColor Cyan
$emptyDirs = @("memory/long_term/embeddings", "memory/short_term", "runtime/traces")
foreach ($dir in $emptyDirs) {
    $path = Join-Path $base "packages/coding-agent/skills/novel-engine/src/novel_engine/$dir"
    if (Test-Path $path) {
        Remove-Item $path -Force -Recurse -ErrorAction SilentlyContinue
        Write-Host "  已删除: $dir" -ForegroundColor Green
    }
}

# 2. 检查runtime目录内容（应该被gitignore排除）
Write-Host "`n2. 检查运行时输出..." -ForegroundColor Cyan
$runtimeSize = (Get-ChildItem -Path "$base/packages/coding-agent/skills/novel-engine/src/novel_engine/runtime" -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
Write-Host "  runtime/ 目录大小: $([math]::Round($runtimeSize/1KB, 2)) KB"

# 3. 检查cache目录内容
Write-Host "`n3. 检查缓存目录..." -ForegroundColor Cyan
$cacheSize = (Get-ChildItem -Path "$base/packages/coding-agent/skills/novel-engine/src/novel_engine/cache" -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
Write-Host "  cache/ 目录大小: $([math]::Round($cacheSize/1KB, 2)) KB"

# 4. 检查chapters目录内容
Write-Host "`n4. 检查章节目录..." -ForegroundColor Cyan
$chaptersSize = (Get-ChildItem -Path "$base/packages/coding-agent/skills/novel-engine/src/novel_engine/chapters" -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
Write-Host "  chapters/ 目录大小: $([math]::Round($chaptersSize/1KB, 2)) KB"

Write-Host "`n=== 清理完成 ===" -ForegroundColor Yellow
