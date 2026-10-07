# Builds Token Pace Mini (tokenpace-mini.exe) with the C# compiler and WPF that ship with
# Windows 10 and 11 (.NET Framework 4.8). Nothing to install.
#   powershell -File windows/build.ps1            # -> windows/build/tokenpace-mini.exe
param([string]$Out = (Join-Path $PSScriptRoot "build\tokenpace-mini.exe"))
$ErrorActionPreference = "Stop"
$fw = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319"
$wpf = Join-Path $fw "WPF"
$csc = Join-Path $fw "csc.exe"
if (-not (Test-Path $csc)) { throw "csc.exe not found in $fw (.NET Framework 4.8 is part of Windows 10 and 11)" }
New-Item -ItemType Directory -Force -Path (Split-Path $Out) | Out-Null
$refs = @("$wpf\PresentationFramework.dll", "$wpf\PresentationCore.dll", "$wpf\WindowsBase.dll", "$fw\System.Xaml.dll",
    "System.Windows.Forms.dll", "System.Drawing.dll", "System.Web.Extensions.dll", "System.Security.dll") | ForEach-Object { "/r:$_" }
& $csc /nologo /target:winexe /optimize+ /warn:4 "/out:$Out" @refs (Join-Path $PSScriptRoot "TokenPaceMini.cs")
if ($LASTEXITCODE -ne 0) { throw "csc failed with exit code $LASTEXITCODE" }
Write-Host "built $Out"
