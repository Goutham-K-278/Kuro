
<#
  generate_project_code.ps1
  Rebuilds project_code_only.txt from all current source files.
  Hard limit: 29 MB (stays safely under 30 MB).
#>

$root    = "c:\Users\gouth\OneDrive\Documents\Kuro-LLM"
$outFile = Join-Path $root "project_code_only.txt"
$maxBytes = 29 * 1024 * 1024   # 29 MB safety ceiling

# ── Extensions to include ─────────────────────────────────────────────────────
$goodExts = @(
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".json", ".html", ".css",
    ".md", ".txt", ".ps1", ".bat", ".vbs",
    ".env", ".sh", ".yaml", ".yml",
    ".cfg", ".ini", ".toml"
)

# ── Named files that have no extension but should always be included ──────────
$alwaysInclude = @(".env", ".env.example", ".gitignore")

# ── Directory name fragments to skip (case-insensitive partial match) ─────────
$skipDirNames = @(
    "node_modules", ".venv", "__pycache__", ".git", ".pytest_cache",
    "dist", "build", "electron-cache", "archives", "logs", "tmp",
    ".gemini", "electron_cache"
)

# ── Specific relative paths to skip ──────────────────────────────────────────
$skipRelPaths = @(
    "data", "rag", "creative", "teaching", "docs", "permissions",
    "ui-plan", "models",
    "kuro\wake_word", "kuro\memory", "kuro\security",
    "kuro\dashboard", "kuro\personality",
    "voice\models", "voice\train",
    "kuro-ai\frontend\electron-cache",
    "kuro-ai\frontend\public"
)

# ── Individual files to skip ──────────────────────────────────────────────────
$skipFileNames = @(
    "project_code_only.txt",
    "tree_output.txt",
    "requirements-lock.txt",
    "package-lock.json",
    "eslint.config.js",
    "postcss.config.js",
    "test_cuda.py",
    "test_cuda_with_preload.py",
    "SILENT_LAUNCHER_PLAN.md",
    "Kuro-icon.png"
)

# ── Helpers ───────────────────────────────────────────────────────────────────
function Should-Skip-Dir([string]$absPath) {
    $parts = $absPath.ToLower() -split '\\'
    foreach ($d in $skipDirNames) {
        if ($parts -contains $d.ToLower()) { return $true }
    }
    $rel = $absPath.Substring($root.Length).TrimStart('\').ToLower()
    foreach ($rp in $skipRelPaths) {
        $rpLow = $rp.ToLower().TrimEnd('\')
        if ($rel -eq $rpLow -or $rel.StartsWith($rpLow + '\')) { return $true }
    }
    return $false
}

# ── Collect ───────────────────────────────────────────────────────────────────
Write-Host "Scanning files..." -ForegroundColor Cyan
$allFiles = Get-ChildItem -Path $root -Recurse -File -ErrorAction SilentlyContinue

$candidates = [System.Collections.Generic.List[System.IO.FileInfo]]::new()

foreach ($file in $allFiles) {
    # Skip if inside a bad directory
    if (Should-Skip-Dir $file.DirectoryName) { continue }

    # Skip bad filenames
    if ($skipFileNames -contains $file.Name) { continue }

    $ext = $file.Extension.ToLower()

    # Include if extension is good OR filename is always-include
    if (($goodExts -contains $ext) -or ($alwaysInclude -contains $file.Name)) {
        $candidates.Add($file)
    }
}

# ── Sort: shallow paths first, then alphabetically ───────────────────────────
$sorted = $candidates | Sort-Object {
    $rel = $_.FullName.Substring($root.Length + 1)
    $depth = ($rel -split '\\').Count
    "{0:D3}|{1}" -f $depth, $rel
}

Write-Host "Candidates: $($sorted.Count) files" -ForegroundColor Cyan

# ── Build output ──────────────────────────────────────────────────────────────
$sep = "=" * 80
$output = [System.Text.StringBuilder]::new(10 * 1024 * 1024)
$totalBytes = 0
$included   = [System.Collections.Generic.List[string]]::new()
$skipped    = [System.Collections.Generic.List[string]]::new()

foreach ($file in $sorted) {
    $rel = $file.FullName.Substring($root.Length + 1)

    # Read as UTF-8 text
    try {
        $text = [System.IO.File]::ReadAllText($file.FullName, [System.Text.Encoding]::UTF8)
    } catch {
        $skipped.Add("$rel  [read-error]")
        continue
    }

    # Skip if binary (null bytes in first 1KB)
    $probe = if ($text.Length -gt 1024) { $text.Substring(0, 1024) } else { $text }
    if ($probe.IndexOf([char]0) -ge 0) {
        $skipped.Add("$rel  [binary]")
        continue
    }

    $block = "$sep`r`nFILE: $rel`r`n$sep`r`n$text`r`n`r`n"
    $blockBytes = [System.Text.Encoding]::UTF8.GetByteCount($block)

    if (($totalBytes + $blockBytes) -gt $maxBytes) {
        $skipped.Add("$rel  [size-limit]")
        continue
    }

    [void]$output.Append($block)
    $totalBytes += $blockBytes
    $included.Add($rel)
}

# ── Write ─────────────────────────────────────────────────────────────────────
[System.IO.File]::WriteAllText($outFile, $output.ToString(), [System.Text.Encoding]::UTF8)

$finalMB = [Math]::Round((Get-Item $outFile).Length / 1MB, 3)

Write-Host ""
Write-Host ("=" * 55) -ForegroundColor Cyan
Write-Host "  project_code_only.txt  UPDATED" -ForegroundColor Green
Write-Host ("=" * 55) -ForegroundColor Cyan
Write-Host "  Included : $($included.Count) files" -ForegroundColor White
Write-Host "  Skipped  : $($skipped.Count) files" -ForegroundColor DarkYellow
Write-Host "  Final    : $finalMB MB" -ForegroundColor White
Write-Host ("=" * 55) -ForegroundColor Cyan
Write-Host ""

Write-Host "--- INCLUDED FILES ---" -ForegroundColor Green
$included | ForEach-Object { Write-Host "  $_" }

if ($skipped.Count -gt 0) {
    Write-Host ""
    Write-Host "--- SKIPPED FILES ---" -ForegroundColor DarkYellow
    $skipped | ForEach-Object { Write-Host "  $_" }
}
