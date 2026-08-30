<!-- Source URL: https://gitlab.com/AOMediaCodec/SVT-AV1/-/releases -->
<!-- Fetched at: 2026-08-29 17:19:09 -->

===== v4.2.0 (07/14/2026 18:22:54) =====
## [4.2.0] - 2026-07-14

VOD / Random Access

- Added TUNE-VMAF mode targeting ~15% VMAF BD-rate improvement at minimal PSNR loss
- Implemented single-thread processing mode with RA handling
- RA preset tuning and bitrate optimization for M3-M5
- New CLI options: `--cqp`, `--enable-intrabc`, `--hbd-mds`, `--enable-kf-tf`
- Added raw OBU output format as an alternative to IVF
- Signal `initial_display_delay` in sequence header to fix A/V sync on seek

RTC / Low Delay

- Added CBR rate control with Kalman-filter QP estimation, cyclic refresh, and frame re-encode
- Added on-the-fly MG size, preset, bitrate, and frame rate changes
- Added reference frame management API with LTR support and two-layer RPS structure
- Exposed `--max-intra-bitrate-pct` and `--max-inter-bitrate-pct` parameters
- Improved compression efficiency vs. cycle tradeoff across RTC presets
- Optimized memory footprint for RTC mode with small resolutions
- Further speed and quality tuning for RTC

Encoder (general)

- Refactored entropy coding: direct tile-buffer writes, arithmetic coder simplifications, coefficient shaving
- CDEF optimizations: 8-bit boundary-aware filter, persistent scratch buffers, luma/chroma specialization
- MD and ME optimizations (LPD1 early-skip, VLPD0 fast path, static-block ME bypass)
- Optimized still-image screen content detection
- Optimized TPL dispatch when TPL is disabled
- Added `ENABLE_STACK_PROTECTOR` CMake option to prevent the stack protector flag from being added

Arm

- Added lowbd (8-bit) int16 forward transform NEON kernels (4x4 through 32x32)
- Added Neon SAD, quantize-matrix, SSIM, VMAF, variance, and pixel projection error kernels
- Added SVE2 VMAF kernels and hardware CRC-32C for hash-based ME
- Optimized convolution, full distortion, and SAD calculation functions

Bug fixes and documentation

- Fixed superres recode crash, RESIZE_DYNAMIC under `--rtc`, RTC candidate-count overflow, recon output, and memory leak
- Fixed signed left-shift UB, OOB reads, and race conditions in rate control
- Added NVTX/Nsight Systems profiling hooks, PPC toolchain, and macOS universal binary support
- Addressed cppcheck warnings and rewrote affected unit tests
- Addressed USAN and MSAN warnings
- Reduced OBMC stack usage to fix a crash with PGO
- General code cleanup, documentation updates, and test improvements


===== v4.1.0 (03/23/2026 20:12:04) =====
## [4.1.0] - 2026-03-23

**Encoder**

- Refactor MD, EncDec, and Entropy Coding kernels (!2604)
- Improve Still Image coding efficiency (!2612, !2614)
- Change Wiener Filter level for chroma for presets M3 and below (!2620)
- Optimize Screen Content coding for Still Image (!2630)

**Arm**

- Refactor Subpixel Variance kernels (!2608)
- Optimize 16b SAD kernel (!2610)
- Fixed Neoverse V2 unit test detection (!2622)
- Update Arm build guide (!2625)

**Bug fixes and documentation**

- Fixed a hang caused by improper variable looping (#2338, !2600)
- Add missing option 2 for `--enable-dlf`'s help output (!2601)
- Depth Refinement algorithmic bug fix (!2602)
- Add mutexes to fix hangs when running multiple instances of the encoder in one process (!2603, !2605, !2619)
- Fix motion calculation for cyclic QP refresh (!2613)
- Fixed a Debug vs Release mismatch (!2618)
- Fixed some new warnings with newer GCC versions (!2621, !2636)
- Changed Temporal Filtering distortion calculation to not include padding (!2623)
- Cleanup some dead unit tests (!2626)
- Benchmark framework improvements (!2627)
- CI/CD improvements (!2628)
- Fixed some niche crashes (!2629)
- Readd missing PredStructure enum without SVT_AV1 prefix (!2635)
- Rename svt_log to prevent conflict with SVT-JPEG-XS (!2634)
- General code and doc cleanup (!2606, !2607, !2609, !2611, !2616, !2617, !2624, !2631, !2633, !2637)


===== v4.0.1 (01/28/2026 23:12:32) =====
## [4.0.1] - 2026-01-28

Bug fixes and documentation

- Fixed a missing version bump for shared library and pkg-config (!2593)
  - This is now tied to the CMake project version and should not happen again.
  - Added a CI check to verify this going forward (!2594)
- Fixed tf-strength's default value in the help output (!2595)
- Cleaned up some old debug prints and fixed some Windows build warnings (!2596)
- Fixed bug in incorrect plane selection in quantize_inv_quantize (!2597)
- Fixed hang caused by incorrect update of looping variable in pic_manager_process (!2600)


===== v4.0.0 (01/24/2026 08:08:33) =====
## [4.0.0] - 2026-1-23

**API updates**

- Major release with new API updates that are not backwards compatible.
- Extended the crf range to 70 reducing the impact or QP scaling allowing the encoder to reach lower bitrates
- Added quarter steps between crf increments to allow for further granularity in qp selection
- Added support for setting a custom global logger for library consumers (!2570, !2579)
- Cleaned up public API headers including removal of deprecated macros, structs, and fields (!2565, !2568)
  - Additionally cleaned up anything marked using SVT_AV1_CHECK_VERSION().
- Added ability to calculate per-frame PSNR and SSIM metrics (!2521)
- Allow sending more than 1 but less than 4 frames with avif mode (This is not for AVIF image sequence, but for encoding an alpha layer) (!2551, !2560)
- Added tune IQ and MS-SSIM for Still Image coding mode

**Encoder**

- Significant improvements in AVIF and still image modes (!2552,!2567):
- ~5-8x speedup M11-M0 at the same quality levels with tune MS-SSIM
- ~5-8% BD-Rate improvements at the same complexity with tune MS-SSIM
- Tradeoff improvements for the RTC modes (!2558):
- ~5-15% speedup at similar quality levels in --rtc mode across presets 7 - 11
- Tradeoff improvements for the Random Access mode (VOD use case) showing a 10-25% speedup across presets M7 down to M0 for --fast-decode 1 and 2 (!2558) 
- Major feature updates for the visual quality mode with the completion porting all SVT-AV1-PSY applicable features for --tune vq for video and --tune iq for avif (!2484, !2489, !2491, !2494, !2496, !2503, !2504, !2507, !2514, !2522 , !2561, !2562, !2576):
- Added AC Bias, a psychovisual feature that improves detail preservation and film grain retention
- Update S-Frame support to allow setting it in a specific decode order option and with more qp options (!2477 !2523 !2534)
- Further Arm Neon and SVE2 optimizations that improve high bitdepth encoding by an average of ~5% in low resolutions

**Cleanup, Build and bug fixes, testing and documentation**

- General code cleanup, bugfixes, documentation and console output changes
- Bugfixes: Fixed an issue with the encoder hanging when given an input with a height of 24 pixels or less (!2518)
- Bugfixes: Fixed a bug that results in encoding an invalid bitstream when using rtc with a high QP value (!2502)
- Bugfixes: Fixed a hang with VBR encoding (#2300, !2535)
- Bugfixes: Fixed a hang when using recon output with low delay mode (#2315, !2544)
- Bugfixes: Fixed an encoder crash when using RTC with resolutions not divisible by 16 and presets >= 11 (#2301, !2547)
- Bugfixes: Fixed bitstream level tier compliance with AV1 specification (#2332, !2577, !2581, !2587)
- Cleanup: Removed in-tree gstreamer plugin (!2586)
- Cleanup: Code specific cleanup for slimmer binary sizes (!2476)
- Testing: Added CI coverage for compiling FFmpeg on macOS Arm (!2536)
- Testing: Added a python based testing framework for comparing codec performance and quality (!2532, !2550, !2556, !2563, !2564, !2566)

