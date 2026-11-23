<#
  check-shape.ps1 -- does a clip have the stream shape that breaks
                    NVEncC / QSVEncC hardware decode (--avhw)?

  The defect's predictor, measured over the whole corpus:

      avhw_frames = container_samples - pictures_presented_before_first_packet

  This script computes both numbers from the container alone. It needs no
  decoder and no GPU, so it can be run anywhere, including on a machine with
  hardware decode disabled.

  Usage:
      .\check-shape.ps1 .\source\20260903_C1170.MP4
      .\check-shape.ps1 .\synthetic\*.mp4 -Ffprobe "C:\path\to\ffprobe.exe"

  A clip "reproduces the shape" when LeadingPictures > 0.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string[]]$Path,

    # ffprobe executable. Default assumes it is on PATH.
    [string]$Ffprobe = "ffprobe"
)

$rows = foreach ($item in $Path) {
    $files = Get-Item -Path $item -ErrorAction SilentlyContinue
    if (-not $files) { Write-Warning "not found: $item"; continue }
    foreach ($f in @($files)) {
        $json = & $Ffprobe -v error -select_streams v:0 `
            -show_entries packet=pts,flags -of json $f.FullName 2>$null
        if ($LASTEXITCODE -ne 0) { Write-Warning "ffprobe failed: $($f.Name)"; continue }

        # @() guards against PowerShell unwrapping a single-element array.
        $pts = @(($json | ConvertFrom-Json).packets |
                 ForEach-Object { [int64]$_.pts })
        if ($pts.Count -eq 0) { Write-Warning "no video packets: $($f.Name)"; continue }

        $anchor = $pts[0]   # decode order: the first packet is the first IRAP
        $lead = @($pts | Where-Object { $_ -lt $anchor }).Count

        [pscustomobject]@{
            File                = $f.Name
            Samples             = $pts.Count
            FirstPacketPts      = $anchor
            LeadingPictures     = $lead
            PredictedAvhwFrames = $pts.Count - $lead
            Reproduces          = if ($lead -gt 0) { "yes" } else { "no" }
        }
    }
}

$rows | Format-Table -AutoSize
