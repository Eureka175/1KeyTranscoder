<#
  make-control-fixtures.ps1 -- rebuild the control clips used to isolate the
  defect, from a real Sony XAVC HS source clip.

  These are the four generated fixtures. Together with the source clip they
  separate "the trigger" from "everything that merely correlates with it":

    A  video-only stream copy in MP4
       Same bitstream as the source, edit list rebuilt by the muxer.
       Expect: leading pictures PRESERVED -> the defect still reproduces.

    B  the same bitstream in MKV
       No edit list at all, millisecond timebase.
       Expect: leading pictures PRESERVED -> proves the edit list is NOT the
       trigger.

    C  x265 re-encode, closed GOP
       Expect: leading pictures GONE -> the defect does NOT reproduce.
       (This is why "just re-encode it" is not a workaround for the *test*; it
       removes the very shape under test.)

    D  synthetic testsrc2, in-order, no B-frames, closed GOP
       Expect: leading pictures GONE -> a negative control with no Sony
       material in it at all.

  A and B are large (they keep the full 4K bitstream). Skip them with
  -SkipLarge if you only need C and D.

  Requires ffmpeg and ffprobe (on PATH, or pass -Ffmpeg / -Ffprobe).
#>
[CmdletBinding()]
param(
    [string]$Ffmpeg = "ffmpeg",
    [string]$Ffprobe = "ffprobe",

    # Real Sony XAVC HS clip. Defaults to the source clip shipped alongside
    # this script.
    [string]$Source = (Join-Path $PSScriptRoot "source\20260903_C1170.MP4"),

    [string]$OutDir = (Join-Path $PSScriptRoot "generated"),

    # A and B keep the whole 4K bitstream; use this to build only C and D.
    [switch]$SkipLarge
)

$ErrorActionPreference = "Stop"

function Invoke-Tool {
    param([string]$Exe, [string[]]$Arguments, [string]$What)
    $output = & $Exe @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host ($output | Out-String)
        throw "$What failed (rc=$LASTEXITCODE): $Exe $($Arguments -join ' ')"
    }
    return $output
}

if (-not (Test-Path $Source)) { throw "source clip not found: $Source" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$src = (Get-Item $Source).FullName
Write-Host "source: $src"
Write-Host "output: $OutDir"
Write-Host ""

$built = @()

if (-not $SkipLarge) {
    Write-Host "[A] video-only stream copy -> MP4 ..."
    Invoke-Tool $Ffmpeg @(
        "-y", "-v", "error", "-i", $src, "-c", "copy", "-map", "0:v:0",
        (Join-Path $OutDir "A_sony_copy.mp4")
    ) "fixture A" | Out-Null
    $built += "A_sony_copy.mp4"

    Write-Host "[B] same bitstream -> MKV ..."
    Invoke-Tool $Ffmpeg @(
        "-y", "-v", "error", "-i", $src, "-c", "copy", "-map", "0:v:0",
        (Join-Path $OutDir "B_sony_copy.mkv")
    ) "fixture B" | Out-Null
    $built += "B_sony_copy.mkv"
}

Write-Host "[C] x265 re-encode, closed GOP ..."
Invoke-Tool $Ffmpeg @(
    "-y", "-v", "error", "-i", $src, "-map", "0:v:0",
    "-c:v", "libx265", "-preset", "medium", "-crf", "20",
    "-x265-params", "log-level=none:keyint=60:min-keyint=60:open-gop=0",
    "-pix_fmt", "yuv420p10le",
    (Join-Path $OutDir "C_x265_reencode.mp4")
) "fixture C" | Out-Null
$built += "C_x265_reencode.mp4"

Write-Host "[D] synthetic testsrc2, in-order, closed GOP ..."
Invoke-Tool $Ffmpeg @(
    "-y", "-v", "error", "-f", "lavfi",
    "-i", "testsrc2=size=1920x1080:rate=30:duration=2",
    "-c:v", "libx265", "-preset", "ultrafast", "-crf", "28",
    "-x265-params",
    "log-level=none:keyint=30:min-keyint=30:open-gop=0:b-adapt=0:bframes=0",
    "-pix_fmt", "yuv420p10le",
    (Join-Path $OutDir "D_synthetic_testsrc2.mp4")
) "fixture D" | Out-Null
$built += "D_synthetic_testsrc2.mp4"

Write-Host ""
Write-Host "shape of every generated fixture:"
Write-Host ""
& (Join-Path $PSScriptRoot "check-shape.ps1") `
    -Ffprobe $Ffprobe -Path ($built | ForEach-Object { Join-Path $OutDir $_ })
