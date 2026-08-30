<!-- Source URL: https://github.com/cdgriffith/FastFlix/releases/tag/6.2.1 -->
<!-- Fetched at: 2026-08-29 17:12:12 -->

TAG: 6.2.1  PUBLISHED: 03/21/2026 22:56:20

BODY:
* Fixing #529 window geometry and menu anchoring issues when displays are powered off/on or reconfigured during use (thanks to wiznillyp)
* Fixing #734 startup crash when FFmpeg/FFprobe exits with non-zero code despite producing valid output (e.g. custom builds that crash during cleanup) (thanks to kliffgomel)
* Fixing #727 post-encode FFprobe failure when FFprobe crashes on cleanup but produces valid probe data (thanks to danycat201489-a11y)
* Fixing return from queue bug with FFmpeg nvenc av1
