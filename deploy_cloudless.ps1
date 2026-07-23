# ---------------------------------------------------------------------------
# Deploy the 100% cloud-free Ambientika stack to ambientika-local-app
# as a Pull-Request branch. YOU run this (it uses YOUR git credentials);
# nothing is pushed on your behalf by anyone else.
#
# Requirements:
#   - git installed and authenticated over HTTPS to github.com
#     (Git Credential Manager will prompt once if needed).
#   - You are a collaborator on ambientika-eu/ambientika-local-app (you are).
#
# Run in PowerShell:
#   powershell -ExecutionPolicy Bypass -File "$HOME\OneDrive - tincx srl\Desktop\CLAUDE\cloudless-stack\deploy_cloudless.ps1"
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Stop"

$src    = Join-Path $HOME "OneDrive - tincx srl\Desktop\CLAUDE\cloudless-stack"
$work   = Join-Path $env:TEMP "ambientika-local-app-deploy"
$repo   = "https://github.com/ambientika-eu/ambientika-local-app.git"
$branch = "feature/local-mode"

Write-Host "Source folder : $src"
Write-Host "Working clone : $work"
if (-not (Test-Path $src)) { throw "Source folder not found: $src" }

if (Test-Path $work) { Remove-Item -Recurse -Force $work }
git clone $repo $work
Set-Location $work
git checkout -b $branch

# Files that go to the repo root
Copy-Item (Join-Path $src "ambientika_local_bridge.py")   . -Force
Copy-Item (Join-Path $src "docker-compose.local.yml")     . -Force
Copy-Item (Join-Path $src "Dockerfile.bridge")            . -Force
Copy-Item (Join-Path $src "README_LOCAL_CLOUDLESS.md")    . -Force
# env template -> proper .env.local.example name
Copy-Item (Join-Path $src "env.local.example.txt")        ".env.local.example" -Force
# mosquitto config (preserve path)
New-Item -ItemType Directory -Force -Path "mosquitto\config" | Out-Null
Copy-Item (Join-Path $src "mosquitto\config\mosquitto.conf") "mosquitto\config\mosquitto.conf" -Force

git add -A
git commit -m "Add 100% cloud-free local stack (device TCP:11000 bridge, schedule + NeuraCell-X)"
git push -u origin $branch

$compare = "https://github.com/ambientika-eu/ambientika-local-app/compare/main...$branch?expand=1"
Write-Host ""
Write-Host "==================================================================="
Write-Host " Pushed branch '$branch'. Open the Pull Request here:"
Write-Host "   $compare"
Write-Host "==================================================================="
Start-Process $compare  # opens the PR page in your browser
