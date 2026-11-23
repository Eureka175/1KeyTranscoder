<#
  make-synthetic-clip.ps1 -- build a small, FULLY SYNTHETIC clip that has the
  same stream shape as Sony XAVC HS, i.e. the shape that NVEncC / QSVEncC
  mishandle under --avhw:

      the first packet in decode order is a keyframe (CRA) whose presentation
      timestamp is LATER than the pictures that follow it in decode order
      (the leading / RASL pictures).

  Why this clip matters
  ---------------------
  The real XAVC clips that show the defect are camera footage (tens of MB, and
  4K at ~150 Mbps). This clip carries the same *shape* at a few hundred KB and
  with no camera footage in it, so it can be attached to a bug report freely.
  The defect is driven by the stream shape, not by the codec, the resolution or
  the container -- that was established by measurement across the corpus.

  Method
  ------
  1. Encode with open-GOP, so GOP boundaries are CRA + leading pictures.
  2. Cut at a CRA boundary with `-c copy`. The leading pictures that follow the
     CRA in decode order are kept, and they are presented before it -- exactly
     the shape under test.
  3. Shift every timestamp so presentation times are non-negative. XAVC reaches
     the same shape with an edit list; this is the same ordering, expressed
     directly.
  4. Assert the shape with check-shape.ps1.

  Requires ffmpeg and ffprobe (on PATH, or pass -Ffmpeg / -Ffprobe).
#>
[CmdletBinding()]
param(
    [string]$Ffmpeg = "ffmpeg",
    [string]$Ffprobe = "ffprobe",
    [string]$OutDir = (Join-Path $PSScriptRoot "synthetic"),
    [string]$Name = "xavc-shape-synthetic.mp4",

    # Encoding parameters. Open-GOP plus B-frames is what creates leading
    # pictures; the rest only affects size and how it looks.
    [int]$Seconds = 5,
    [int]$Gop = 30,
    [int]$Rate = 30,
    # 640x480 is deliberate: at this size x265 places the CRA so that exactly
    # 3 leading pictures survive the cut, matching Sony XAVC HS, and the result
    # is only ~300 KB. The defect is driven by stream shape, not resolution, so
    # a larger frame size buys nothing but megabytes.
    [int]$Width = 640,
    [int]$Height = 480,
    [string]$X265Params = "log-level=none:keyint=30:min-keyint=30:open-gop=1:bframes=4:b-adapt=2",

    # Which keyframe to cut at (1 = the first, which never has leading
    # pictures). The script tries this one first and then walks forward until it
    # reaches -TargetLeading.
    [int]$CutAtKeyframe = 3,

    # How many leading pictures to aim for. Sony XAVC HS has 3, so the
    # synthetic clip is built to match that, making the two directly
    # comparable. Any value > 0 reproduces the shape.
    [int]$TargetLeading = 3
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$base = Join-Path $OutDir "_base_opengop.mp4"
$out = Join-Path $OutDir $Name

function Invoke-Tool {
    param([string]$Exe, [string[]]$Arguments, [string]$What)
    $output = & $Exe @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host ($output | Out-String)
        throw "$What failed (rc=$LASTEXITCODE): $Exe $($Arguments -join ' ')"
    }
    return $output
}

Write-Host "[1/4] encoding open-GOP base clip ($Width x $Height, $Seconds s) ..."
Invoke-Tool $Ffmpeg @(
    "-y", "-v", "error", "-f", "lavfi",
    "-i", "testsrc2=size=${Width}x${Height}:rate=${Rate}:duration=${Seconds}",
    "-c:v", "libx265", "-preset", "medium", "-crf", "28",
    "-x265-params", $X265Params, "-pix_fmt", "yuv420p10le", $base
) "ffmpeg encode" | Out-Null

Write-Host "[2/4] locating keyframes ..."
$pk = @((& $Ffprobe -v error -select_streams v:0 `
        -show_entries packet=pts_time,flags -of json $base |
        ConvertFrom-Json).packets)
$kf = @($pk | Where-Object { $_.flags -like "*K*" })
if ($kf.Count -le $CutAtKeyframe) {
    throw "need more than $CutAtKeyframe keyframes, found $($kf.Count)"
}

function Get-Leading {
    param([string]$File)
    $p = @((& $Ffprobe -v error -select_streams v:0 -show_entries packet=pts `
            -of json $File | ConvertFrom-Json).packets |
            ForEach-Object { [int64]$_.pts })
    return @{
        Samples = $p.Count
        Leading = @($p | Where-Object { $_ -lt $p[0] }).Count
        MinPts  = ($p | Measure-Object -Minimum).Minimum
    }
}

Write-Host "[3/4] cutting at a CRA and normalising timestamps ..."
$tmp = Join-Path $OutDir "_cut.mp4"
$chosen = $null
for ($i = $CutAtKeyframe; $i -le $kf.Count; $i++) {
    $cutTime = [double]$kf[$i - 1].pts_time
    Invoke-Tool $Ffmpeg @(
        "-y", "-v", "error", "-ss", "$cutTime", "-i", $base, "-c", "copy", $tmp
    ) "ffmpeg cut" | Out-Null

    $r = Get-Leading $tmp
    $r.CutTime = $cutTime
    Write-Host ("      keyframe {0,2}/{1}: t={2,6} s -> samples={3,3} leading={4}" -f `
        $i, $kf.Count, $cutTime, $r.Samples, $r.Leading)

    if ($r.Leading -eq $TargetLeading) { $chosen = $r; break }
    if ($r.Leading -gt 0 -and -not $chosen) { $chosen = $r }  # fallback
}
if (-not $chosen) {
    throw "no cut point produced leading pictures - cannot build the reproducer"
}

# The loop leaves $tmp holding whichever cut ran last, which is not necessarily
# the chosen one. Re-do the chosen cut so the file matches what is reported.
Invoke-Tool $Ffmpeg @(
    "-y", "-v", "error", "-ss", "$($chosen.CutTime)", "-i", $base,
    "-c", "copy", $tmp
) "ffmpeg cut (final)" | Out-Null
$got = Get-Leading $tmp
if ($got.Leading -ne $chosen.Leading -or $got.Samples -ne $chosen.Samples) {
    throw ("re-cut mismatch: expected samples=$($chosen.Samples) " +
        "leading=$($chosen.Leading), got samples=$($got.Samples) " +
        "leading=$($got.Leading)")
}
if ($got.Leading -ne $TargetLeading) {
    Write-Warning ("target was $TargetLeading leading pictures, best found is " +
        "$($got.Leading) - the shape still reproduces the defect")
}

$shift = -$got.MinPts
Invoke-Tool $Ffmpeg @(
    "-y", "-v", "error", "-i", $tmp, "-c", "copy",
    "-bsf:v", "setts=pts=PTS+${shift}:dts=DTS+${shift}",
    "-avoid_negative_ts", "disabled", "-movflags", "+faststart", $out
) "ffmpeg timestamp shift" | Out-Null
Remove-Item $tmp, $base -Force -ErrorAction SilentlyContinue

Write-Host "[4/4] verifying the shape ..."
$verify = @((& $Ffprobe -v error -select_streams v:0 -show_entries packet=pts `
        -of json $out | ConvertFrom-Json).packets | ForEach-Object { [int64]$_.pts })
$lead = @($verify | Where-Object { $_ -lt $verify[0] }).Count
$size = [math]::Round((Get-Item $out).Length / 1KB, 1)
Write-Host ""
Write-Host "  $out"
Write-Host "  size               : $size KB"
Write-Host "  container samples  : $($verify.Count)"
Write-Host "  leading pictures   : $lead"
Write-Host "  predicted --avhw   : $($verify.Count - $lead)"
if ($lead -gt 0) {
    Write-Host "  => reproduces the shape. Expected: --avhw delivers $($verify.Count - $lead) of $($verify.Count)."
} else {
    throw "the produced clip has no leading pictures - the cut did not land on a CRA with RASL pictures"
}
