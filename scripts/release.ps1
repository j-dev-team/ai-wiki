[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,

    [switch]$Publish,
    [switch]$Yes,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$releaseVenv = Join-Path $repoRoot '.release-venv'
$distDir = Join-Path $repoRoot 'dist'
$releaseDistDir = Join-Path $repoRoot 'release-dist'
$wheelName = "ai_wiki-$Version-py3-none-any.whl"
$sdistName = "ai_wiki-$Version.tar.gz"
$wheelPath = Join-Path $distDir $wheelName
$sdistPath = Join-Path $distDir $sdistName

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )

    Write-Host "`n==> $Label" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Invoke-ProcessChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    Write-Host "`n==> $Label" -ForegroundColor Cyan
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -NoNewWindow `
        -Wait `
        -PassThru
    if ($process.ExitCode -ne 0) {
        throw "$Label failed with exit code $($process.ExitCode)"
    }
}

function Remove-WorkspaceItem {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $prefix = $repoRoot + [System.IO.Path]::DirectorySeparatorChar
    if (-not $fullPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside repository: $fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
}

function Assert-Contains {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Pattern,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $text = Get-Content -LiteralPath $Path -Raw -Encoding utf8
    if ($text -notmatch $Pattern) {
        throw "$Description is not set to $Version in $Path"
    }
}

function Get-PypiRelease {
    param([Parameter(Mandatory = $true)][string]$ReleaseVersion)

    try {
        return Invoke-RestMethod -Uri "https://pypi.org/pypi/ai-wiki/$ReleaseVersion/json"
    }
    catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 404) {
            return $null
        }
        throw
    }
}

Push-Location $repoRoot
try {
    Invoke-Checked 'Verify Git repository' { git rev-parse --show-toplevel | Out-Null }

    $versionParts = $Version.Split('.')
    $minorFloor = "$($versionParts[0]).$($versionParts[1])"
    $minorCeiling = "$($versionParts[0]).$([int]$versionParts[1] + 1)"
    $escapedVersion = [regex]::Escape($Version)
    $escapedFloor = [regex]::Escape($minorFloor)
    $escapedCeiling = [regex]::Escape($minorCeiling)

    Assert-Contains 'pyproject.toml' "(?m)^version\s*=\s*`"$escapedVersion`"\s*$" 'Project version'
    Assert-Contains 'src/ai_wiki/__init__.py' "(?m)^__version__\s*=\s*`"$escapedVersion`"\s*$" 'Module version'
    Assert-Contains 'src/ai_wiki/variant.py' "__version__ = [^\r\n]*$escapedVersion" 'Generated package version'
    Assert-Contains 'src/ai_wiki/variant.py' "(?m)^version\s*=\s*`"$escapedVersion`"\s*$" 'Generated distribution version'
    Assert-Contains 'src/ai_wiki/variant.py' "ai-wiki>=$escapedFloor,<$escapedCeiling" 'Generated engine dependency range'
    Assert-Contains 'src/ai_wiki/templates/base.html' "v$escapedVersion" 'Web footer version'
    Assert-Contains 'CHANGELOG.md' "(?m)^##\s+$escapedVersion\s+-\s+\d{4}-\d{2}-\d{2}\s*$" 'Changelog release heading'

    Invoke-Checked 'Check patch whitespace' { git diff --check }

    $trackedChanges = @(git status --porcelain --untracked-files=no)
    if ($trackedChanges.Count -gt 0) {
        $details = $trackedChanges -join [Environment]::NewLine
        throw "Tracked working tree changes must be committed before release:`n$details"
    }

    if (Get-PypiRelease -ReleaseVersion $Version) {
        throw "ai-wiki $Version already exists on PyPI. Increment the version before building a new release."
    }

    if (-not $SkipTests) {
        $oldPythonPath = $env:PYTHONPATH
        try {
            $env:PYTHONPATH = Join-Path $repoRoot 'src'
            Invoke-ProcessChecked 'Run canonical test suite' 'python' @('-m', 'pytest', '-q')
        }
        finally {
            $env:PYTHONPATH = $oldPythonPath
        }
    }

    Remove-WorkspaceItem $distDir
    Remove-WorkspaceItem $releaseDistDir
    Remove-WorkspaceItem (Join-Path $repoRoot 'build')
    Remove-WorkspaceItem (Join-Path $repoRoot 'src/ai_wiki.egg-info')
    Remove-WorkspaceItem $releaseVenv

    Invoke-ProcessChecked 'Create release virtual environment' 'python' @('-m', 'venv', $releaseVenv)
    $releasePython = Join-Path $releaseVenv 'Scripts/python.exe'
    Invoke-ProcessChecked 'Install release tools' $releasePython @('-m', 'pip', 'install', '--quiet', '--upgrade', 'pip', 'build', 'twine')
    Invoke-ProcessChecked 'Build wheel and source distribution' $releasePython @('-m', 'build')
    Invoke-ProcessChecked 'Validate distributions with twine' $releasePython @('-m', 'twine', 'check', $wheelPath, $sdistPath)

    if (-not (Test-Path -LiteralPath $wheelPath) -or -not (Test-Path -LiteralPath $sdistPath)) {
        throw "Expected release artifacts were not created: $wheelName, $sdistName"
    }

    Write-Host "`n==> Inspect wheel resources" -ForegroundColor Cyan
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($wheelPath)
    try {
        $wheelEntries = @($archive.Entries | ForEach-Object { $_.FullName })
    }
    finally {
        $archive.Dispose()
    }
    $requiredWheelEntries = @(
        'ai_wiki/lifecycle.py',
        'ai_wiki/runtime.py',
        'ai_wiki/skill_routing.py',
        'ai_wiki/skill_templates/SKILL.md',
        'ai_wiki/static/style.css',
        'ai_wiki/templates/base.html',
        'ai_wiki/variant_presets/law.yaml'
    )
    $missingWheelEntries = @($requiredWheelEntries | Where-Object { $_ -notin $wheelEntries })
    if ($missingWheelEntries.Count -gt 0) {
        throw "Wheel is missing required files: $($missingWheelEntries -join ', ')"
    }

    New-Item -ItemType Directory -Force -Path $releaseDistDir | Out-Null
    Copy-Item -LiteralPath $wheelPath -Destination (Join-Path $releaseDistDir $wheelName) -Force
    Copy-Item -LiteralPath $sdistPath -Destination (Join-Path $releaseDistDir $sdistName) -Force

    $wheelHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $wheelPath).Hash.ToLowerInvariant()
    $sdistHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sdistPath).Hash.ToLowerInvariant()
    Write-Host "`nRelease artifacts ready:" -ForegroundColor Green
    Write-Host "  $wheelName  $wheelHash"
    Write-Host "  $sdistName  $sdistHash"

    if (-not $Publish) {
        Write-Host "`nPreflight complete. Re-run with -Publish to push and upload." -ForegroundColor Yellow
        return
    }

    $branch = (git branch --show-current).Trim()
    if ($branch -ne 'master') {
        throw "Publishing is allowed only from master; current branch is '$branch'"
    }

    $pypirc = Join-Path $HOME '.pypirc'
    if (-not (Test-Path -LiteralPath $pypirc)) {
        throw "PyPI credentials not found at $pypirc"
    }
    $pypircText = Get-Content -LiteralPath $pypirc -Raw
    if ($pypircText -notmatch '(?m)^\s*username\s*=\s*__token__\s*$' -or
        $pypircText -notmatch '(?m)^\s*password\s*=\s*pypi-\S+\s*$') {
        throw 'The .pypirc file does not contain a PyPI API token configuration.'
    }

    if (-not $Yes) {
        $confirmation = Read-Host "Type v$Version to publish GitHub and PyPI"
        if ($confirmation -ne "v$Version") {
            throw 'Release cancelled.'
        }
    }

    $head = (git rev-parse HEAD).Trim()
    $tagName = "v$Version"
    $existingTag = git tag --list $tagName
    if ($existingTag) {
        $tagCommit = (git rev-list -n 1 $tagName).Trim()
        if ($tagCommit -ne $head) {
            throw "$tagName already points to $tagCommit instead of HEAD $head"
        }
    }
    else {
        Invoke-Checked "Create $tagName" { git tag -a $tagName -m "AI Wiki $Version" }
    }

    Invoke-Checked 'Push master to GitHub' { git push origin master }
    Invoke-Checked "Push $tagName to GitHub" { git push origin $tagName }

    $env:PYTHONIOENCODING = 'utf-8'
    Invoke-ProcessChecked 'Upload exact artifacts to PyPI' $releasePython @(
        '-m', 'twine', 'upload', $wheelPath, $sdistPath, '--disable-progress-bar'
    )

    $published = $null
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        $published = Get-PypiRelease -ReleaseVersion $Version
        if ($published) { break }
        Start-Sleep -Seconds 3
    }
    if (-not $published) {
        throw "PyPI did not expose ai-wiki $Version after upload. Check the project page."
    }

    $publishedWheel = $published.urls | Where-Object { $_.filename -eq $wheelName }
    $publishedSdist = $published.urls | Where-Object { $_.filename -eq $sdistName }
    if (-not $publishedWheel -or -not $publishedSdist) {
        throw 'PyPI release is missing the expected wheel or source distribution.'
    }
    if ($publishedWheel.digests.sha256 -ne $wheelHash -or $publishedSdist.digests.sha256 -ne $sdistHash) {
        throw 'Published PyPI hashes do not match the locally verified artifacts.'
    }

    Write-Host "`nPublished ai-wiki $Version successfully." -ForegroundColor Green
    Write-Host "https://pypi.org/project/ai-wiki/$Version/"
}
finally {
    Pop-Location
}
