<!-- Source URL: https://github.com/rust-av/Av1an/releases -->
<!-- Fetched at: 2026-08-29 17:10:46 -->


===== TAG: v0.5.2  PUBLISHED: 01/04/2026 03:56:04  NAME: v0.5.2 =====
## What's Changed
* Parse `nb_frames` from ffprobe as string by @Wallunen in https://github.com/rust-av/Av1an/pull/1184
* implement cache mode toogle by @Khaoklong51 in https://github.com/rust-av/Av1an/pull/1183
* Version 0.5.2 by @shssoichiro in https://github.com/rust-av/Av1an/pull/1185

## New Contributors
* @Wallunen made their first contribution in https://github.com/rust-av/Av1an/pull/1184
* @Khaoklong51 made their first contribution in https://github.com/rust-av/Av1an/pull/1183

**Full Changelog**: https://github.com/rust-av/Av1an/compare/v0.5.1...v0.5.2

===== TAG: v0.5.1  PUBLISHED: 01/03/2026 04:57:16  NAME: 0.5.1 =====
## What's Changed
* chore(deps): bump regex from 1.11.3 to 1.12.1 in the rust-dependencies group by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1153
* chore(deps): bump the rust-dependencies group with 4 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1155
* chore(deps): bump the rust-dependencies group with 3 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1156
* Fix extra splits rounding by @shssoichiro in https://github.com/rust-av/Av1an/pull/1157
* chore(deps): bump the rust-dependencies group with 5 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1158
* chore(deps): bump the rust-dependencies group across 1 directory with 3 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1163
* Support new svt-av1 progress format by @shssoichiro in https://github.com/rust-av/Av1an/pull/1165
* Fix new svt parsing, attempt 2 by @shssoichiro in https://github.com/rust-av/Av1an/pull/1166
* Handle ANSI color codes in progress parsing by @shssoichiro in https://github.com/rust-av/Av1an/pull/1167
* Bump av-scenechange library for up to 35% scenechange speedup by @shssoichiro in https://github.com/rust-av/Av1an/pull/1171
* Improve error message if output chunk cannot be created by @shssoichiro in https://github.com/rust-av/Av1an/pull/1172
* Fix build warnings by @shssoichiro in https://github.com/rust-av/Av1an/pull/1174
* Update Windows workflow by @Uranite in https://github.com/rust-av/Av1an/pull/1170
* Fix weird warnings that are only showing up in some CI builds by @shssoichiro in https://github.com/rust-av/Av1an/pull/1175
* chore(deps): bump actions/checkout from 5 to 6 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1164
* Update dependencies and fix clippy lints by @shssoichiro in https://github.com/rust-av/Av1an/pull/1178
* Fix 8-bit encode when probing rate is 1 by @Uranite in https://github.com/rust-av/Av1an/pull/1181
* chore(deps): bump actions/cache from 4 to 5 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1177
* Tag version 0.5.1 by @shssoichiro in https://github.com/rust-av/Av1an/pull/1182


**Full Changelog**: https://github.com/rust-av/Av1an/compare/0.5...v0.5.1

===== TAG: latest  PUBLISHED: 12/08/2025 16:59:15  NAME: latest =====
Latest build from 805dad69143fa0a81cfe2fb89c0b9e90a828ea72

Commit:
805dad6: chore(deps): bump the rust-dependencies group across 1 directory with 15 updates (dependabot[bot])


===== TAG: 0.5  PUBLISHED: 10/02/2025 15:31:45  NAME: 0.5.0 =====
## What's Changed
* Update dependencies and fix lints by @shssoichiro in https://github.com/rust-av/Av1an/pull/912
* Fix x265 target quality by @shssoichiro in https://github.com/rust-av/Av1an/pull/923
* Update ffmpeg to 7.1 for the windows workflow by @Uranite in https://github.com/rust-av/Av1an/pull/926
* Fix encoding with multiple encoders by @SwareJonge in https://github.com/rust-av/Av1an/pull/910
* Fix Scene Detection frames mismatch causes invalid frame access crashes by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/927
* Fix clippy issues and stop it from breaking with every Rust update by @shssoichiro in https://github.com/rust-av/Av1an/pull/929
* Fix the CI by @shssoichiro in https://github.com/rust-av/Av1an/pull/935
* fix(av1an-core): fix path-handling in vapoursynth script by @baysonfox in https://github.com/rust-av/Av1an/pull/924
* Improve setting default encode parameters by @FlyingWombat in https://github.com/rust-av/Av1an/pull/798
* Add Richly-Typed loadscript VapourSynth Script to Source Control by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/886
* Bump crossbeam-channel from 0.5.14 to 0.5.15 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/933
* Enable automatic dependency updates by @shssoichiro in https://github.com/rust-av/Av1an/pull/937
* Bump tokio from 1.43.0 to 1.43.1 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/932
* Delete .mergify.yml by @shssoichiro in https://github.com/rust-av/Av1an/pull/939
* Validate MSRV as part of CI by @shssoichiro in https://github.com/rust-av/Av1an/pull/947
* chore(deps): bump serde from 1.0.217 to 1.0.219 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/945
* Update dependencies and cleanup unused ones by @shssoichiro in https://github.com/rust-av/Av1an/pull/948
* Also auto-update Github Actions by @shssoichiro in https://github.com/rust-av/Av1an/pull/949
* chore(deps): bump actions/configure-pages from 4 to 5 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/950
* chore(deps): bump docker/build-push-action from 4 to 6 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/951
* chore(deps): bump docker/metadata-action from 4 to 5 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/952
* chore(deps): bump actions/cache from 3 to 4 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/953
* chore(deps): bump actions/checkout from 3 to 4 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/954
* Print available encoder versions in `--version` by @shssoichiro in https://github.com/rust-av/Av1an/pull/955
* Fix scenechange script when downscale not specified by @shssoichiro in https://github.com/rust-av/Av1an/pull/957
* Enable code coverage checking by @shssoichiro in https://github.com/rust-av/Av1an/pull/958
* Add additional tests for chunk.rs by @shssoichiro in https://github.com/rust-av/Av1an/pull/959
* See if lcov gives more intuitive results by @shssoichiro in https://github.com/rust-av/Av1an/pull/960
* Fix logging parameters are ignored by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/956
* Improve CLI documentation and formatting by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/962
* Split CI into separate jobs by @shssoichiro in https://github.com/rust-av/Av1an/pull/963
* Add CTRL+C SIGINT handling by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/966
* Improve CLI Documentation formatting by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/964
* Fix logging by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/969
* Add Target Quality feedback to mini progress bar per chunk by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/970
* chore(deps): bump docker/login-action from 2 to 3 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/972
* chore(deps): bump docker/setup-qemu-action from 2 to 3 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/973
* chore(deps): bump docker/setup-buildx-action from 2 to 3 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/974
* Add kf-max-dist to aom and vpx defaults by @shssoichiro in https://github.com/rust-av/Av1an/pull/976
* Further changes to console logging by @shssoichiro in https://github.com/rust-av/Av1an/pull/975
* Move tracing instrumentation to debug by @shssoichiro in https://github.com/rust-av/Av1an/pull/977
* Fix issue where chunk progress is inaccurate on resume by @shssoichiro in https://github.com/rust-av/Av1an/pull/978
* chore(deps): bump sysinfo from 0.34.2 to 0.35.0 in the rust-dependencies group by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/983
* Set the output FPS when concatenating by @shssoichiro in https://github.com/rust-av/Av1an/pull/979
* Refactor tests into separate files by @shssoichiro in https://github.com/rust-av/Av1an/pull/985
* chore(deps): bump the rust-dependencies group with 5 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/987
* Improvement on rustfmt. by @master-of-zen in https://github.com/rust-av/Av1an/pull/989
* Cleanup and update dependencies by @shssoichiro in https://github.com/rust-av/Av1an/pull/997
* Cleanup and inline public exports by @shssoichiro in https://github.com/rust-av/Av1an/pull/998
* chore(deps): bump the rust-dependencies group with 2 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1000
* Fix resuming crashes from attempt to subtract with overflow by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1001
* Add `--probing-speed` Option for Target Quality by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1002
* chore(deps): bump the rust-dependencies group with 2 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1004
* chore(deps): bump which from 7.0.3 to 8.0.0 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1011
* chore(deps): bump the rust-dependencies group with 3 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1010
* New Probing Options & Better Search & Cleanups by @emrakyz in https://github.com/rust-av/Av1an/pull/1014
* Small fix for "copy" mechanism by @emrakyz in https://github.com/rust-av/Av1an/pull/1015
* Mention silent truncation of probing_rate>4 by @t-nil in https://github.com/rust-av/Av1an/pull/803
* Make codecov not fail CI if coverage decreases by @shssoichiro in https://github.com/rust-av/Av1an/pull/1018
* fix fbba019e: forcing output FPS breaks using FPS changing filter by @t-nil in https://github.com/rust-av/Av1an/pull/1017
* Refactor TQ & Consolidate Stats (Cross-PR with Boats) by @emrakyz in https://github.com/rust-av/Av1an/pull/1016
* chore(deps): bump the rust-dependencies group with 3 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1020
* Add new Target Quality Metrics by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/988
* Fix CLI argument validation warnings do not print by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1022
* Fix target-metric documentation inconsistencies by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1024
* Remove clap from av1an-core by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1025
* Fix final probe copy shortcut ignores probing rate, probing speed, and ffmpeg filters by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1023
* Add missing zoning options by @KosakaIsMe in https://github.com/rust-av/Av1an/pull/1032
* Add Target Quality Retries by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1026
* feat: Set Number of Threads for VScore to 1 When No VPY File Is Specified by @shssoichiro in https://github.com/rust-av/Av1an/pull/1037
* Undo restricting decoder threads by @shssoichiro in https://github.com/rust-av/Av1an/pull/1040
* Refactor scene handling code by @shssoichiro in https://github.com/rust-av/Av1an/pull/1039
* Fix incorrect comment by @shssoichiro in https://github.com/rust-av/Av1an/pull/1041
* Fix loading of the scenes.json to load pre- and post- splits by @shssoichiro in https://github.com/rust-av/Av1an/pull/1042
* Fix logging consistency by @shssoichiro in https://github.com/rust-av/Av1an/pull/1044
* Update to latest av-decoders and av-scenechange by @shssoichiro in https://github.com/rust-av/Av1an/pull/1045
* Further improvements for CRF search by @emrakyz in https://github.com/rust-av/Av1an/pull/1043
* chore(deps): bump indicatif from 0.17.11 to 0.17.12 in the rust-dependencies group across 1 directory by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1046
* Clean up dependencies by @shssoichiro in https://github.com/rust-av/Av1an/pull/1047
* replace TQ tolerance with configurable ranges by @emrakyz in https://github.com/rust-av/Av1an/pull/1048
* Fix extra-splits 0 and min-scene-len 0 erroring by @shssoichiro in https://github.com/rust-av/Av1an/pull/1051
* add configurable interpolation methods for TQ by @emrakyz in https://github.com/rust-av/Av1an/pull/1049
* ಠ_ಠ by @shssoichiro in https://github.com/rust-av/Av1an/pull/1052
* Cargo refused to update the lockfile by @shssoichiro in https://github.com/rust-av/Av1an/pull/1053
* Improve error tracking when vapoursynth calls fail by @shssoichiro in https://github.com/rust-av/Av1an/pull/1054
* Refactor to reduce number of spawned Environments by @shssoichiro in https://github.com/rust-av/Av1an/pull/1055
* chore(deps): bump the rust-dependencies group with 2 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1058
* Fix MKVMerge Fails to Concatenate on Linux When Total Chunks Exceed ulimit by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1065
* Fix Failing Test on Windows (#1066) by @NandeMD in https://github.com/rust-av/Av1an/pull/1067
* Fix zones replaces last frame with 0 and fails to parse by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1061
* Fix Split Method None Zoning Logic by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1068
* Reduce Scene Detection loadscript Usage by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1030
* chore(deps): bump the rust-dependencies group with 2 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1069
* Export Scenes as Pretty Json by @NandeMD in https://github.com/rust-av/Av1an/pull/1063
* Add --completions flag to generate shell completions at runtime by @aa-ko in https://github.com/rust-av/Av1an/pull/1059
* Add developer guide to documentation by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1064
* Add Input Proxy Option by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1028
* Avoid an ffmpeg pipe when probing rate is 1 by @shssoichiro in https://github.com/rust-av/Av1an/pull/1070
* Remove dependency on the ffmpeg library by @shssoichiro in https://github.com/rust-av/Av1an/pull/1072
* improve VMAF & small refactor by @emrakyz in https://github.com/rust-av/Av1an/pull/1074
* Add score-based enhanced extra splits method by @shssoichiro in https://github.com/rust-av/Av1an/pull/1060
* Revert "Add score-based enhanced extra splits method" by @shssoichiro in https://github.com/rust-av/Av1an/pull/1076
* Add score-based enhanced extra splits method by @shssoichiro in https://github.com/rust-av/Av1an/pull/1079
* Update nom to 8.0 by @shssoichiro in https://github.com/rust-av/Av1an/pull/1080
* Handle importing old scenes files format by @shssoichiro in https://github.com/rust-av/Av1an/pull/1083
* Migrate encode tests to integration tests by @shssoichiro in https://github.com/rust-av/Av1an/pull/1085
* Remove tokio dependency by @shssoichiro in https://github.com/rust-av/Av1an/pull/1086
* Remove parking_lot dependency by @shssoichiro in https://github.com/rust-av/Av1an/pull/1087
* Fix: Absolut Path generation on Linux when using dgdecnv by @Tyr3al in https://github.com/rust-av/Av1an/pull/1089
* Changes to scenechange decoding by @shssoichiro in https://github.com/rust-av/Av1an/pull/1092
* Add more pedantic clippy lints by @shssoichiro in https://github.com/rust-av/Av1an/pull/1093
* chore(deps): bump the rust-dependencies group with 5 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1094
* Fix log file does not accept absolute paths by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1096
* Add Target Quality support to Zones by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1088
* chore(deps): bump av-decoders from 0.3.0 to 0.3.1 in the rust-dependencies group by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1100
* Improvements to decoding frame accuracy by @shssoichiro in https://github.com/rust-av/Av1an/pull/1099
* Fix USE_OLD_SVT_AV1 does not parse non-semver versions by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1101
* fix: prevent integer underflow and handle zero TQ by @emrakyz in https://github.com/rust-av/Av1an/pull/1106
* feat: add quarter-step CRF support & cleanups by @emrakyz in https://github.com/rust-av/Av1an/pull/1108
* fix: remove git lfs by @shssoichiro in https://github.com/rust-av/Av1an/pull/1110
* Fix Scene Detection Scores are overwriting previous Zones by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1111
* Fix VSPipe arguments are not passed to VapourSynth script by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1104
* Fix FFmpeg VMAF fails to validate even when Target Quality is not used by @BoatsMcGee in https://github.com/rust-av/Av1an/pull/1103
* chore(deps): bump the rust-dependencies group across 1 directory with 5 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1116
* Fix Logging with TQ and Remove Repetitions by @emrakyz in https://github.com/rust-av/Av1an/pull/1118
* chore(deps): bump slab from 0.4.10 to 0.4.11 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1119
* chore(deps): bump actions/checkout from 4 to 5 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1122
* chore(deps): bump the rust-dependencies group with 3 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1121
* chore(deps): bump the rust-dependencies group with 5 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1125
* chore(deps): bump actions/upload-pages-artifact from 3 to 4 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1127
* chore(deps): bump tracing-subscriber from 0.3.19 to 0.3.20 by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1130
* Clarify butteraugli-3 requirements by @SugaryHull in https://github.com/rust-av/Av1an/pull/1129
* Change default encoder from aomenc to svt-av1 by @shenef in https://github.com/rust-av/Av1an/pull/1120
* fix: restore colors to console output by @shssoichiro in https://github.com/rust-av/Av1an/pull/1132
* fix: correct logic for limiting decoder threads by @shssoichiro in https://github.com/rust-av/Av1an/pull/1135
* chore: add rust-toolchain file by @shssoichiro in https://github.com/rust-av/Av1an/pull/1139
* perf: improvements to scenecut detection speed by @shssoichiro in https://github.com/rust-av/Av1an/pull/1140
* chore(deps): bump the rust-dependencies group across 1 directory with 6 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1143
* docs: update default concat method by @shssoichiro in https://github.com/rust-av/Av1an/pull/1146
* chore(deps): bump the rust-dependencies group across 1 directory with 6 updates by @dependabot[bot] in https://github.com/rust-av/Av1an/pull/1149

## New Contributors
* @SwareJonge made their first contribution in https://github.com/rust-av/Av1an/pull/910
* @BoatsMcGee made their first contribution in https://github.com/rust-av/Av1an/pull/927
* @baysonfox made their first contribution in https://github.com/rust-av/Av1an/pull/924
* @FlyingWombat made their first contribution in https://github.com/rust-av/Av1an/pull/798
* @emrakyz made their first contribution in https://github.com/rust-av/Av1an/pull/1014
* @t-nil made their first contribution in https://github.com/rust-av/Av1an/pull/803
* @KosakaIsMe made their first contribution in https://github.com/rust-av/Av1an/pull/1032
* @NandeMD made their first contribution in https://github.com/rust-av/Av1an/pull/1067
* @aa-ko made their first contribution in https://github.com/rust-av/Av1an/pull/1059
* @Tyr3al made their first contribution in https://github.com/rust-av/Av1an/pull/1089
* @SugaryHull made their first contribution in https://github.com/rust-av/Av1an/pull/1129
* @shenef made their first contribution in https://github.com/rust-av/Av1an/pull/1120

**Full Changelog**: https://github.com/rust-av/Av1an/compare/0.4.4...0.5

===== TAG: 0.4.4  PUBLISHED: 12/28/2024 13:43:08  NAME: 0.4.4 =====
**Full Changelog**: https://github.com/master-of-zen/Av1an/compare/0.4.3...0.4.4

## What's Changed
* Replace atty crate with fn is_terminal from std by @FreezyLemon in https://github.com/master-of-zen/Av1an/pull/875
* Fix cargo lock file by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/877
* Update lsmash link in Readme to ffmpeg7-compatible fork by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/876
* lockfile: bump vapoursynth-sys to 0.4.1 by @FreezyLemon in https://github.com/master-of-zen/Av1an/pull/880
* libvmaf: Use model='path=...' instead of model_path=... by @FreezyLemon in https://github.com/master-of-zen/Av1an/pull/869
* Allow more than 1 worker per thread pool set by set-thread-affinity by @damster101 in https://github.com/master-of-zen/Av1an/pull/873
* Remove assertion that prevents resuming if all chunks are finished by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/882
* Make --vmaf parameter work independently again by @damster101 in https://github.com/master-of-zen/Av1an/pull/881
* Windows build and compiling.md changes by @Uranite in https://github.com/master-of-zen/Av1an/pull/887
* Fix doc link by @SnowSquire in https://github.com/master-of-zen/Av1an/pull/890
* Delete tag before making a release by @Uranite in https://github.com/master-of-zen/Av1an/pull/888
* Fix compatibility with x265 4.0 by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/893
* Better logging with tracing by @master-of-zen in https://github.com/master-of-zen/Av1an/pull/897



===== TAG: 0.4.3  PUBLISHED: 08/17/2024 22:21:20  NAME: 0.4.3 =====
## What's Changed

**Full Changelog**: https://github.com/master-of-zen/Av1an/compare/0.4.2...0.4.3

* Bump mio from 0.8.10 to 0.8.11 by @dependabot in https://github.com/master-of-zen/Av1an/pull/816
* Bump libgit2-sys from 0.16.1+1.7.1 to 0.16.2+1.7.2 by @dependabot in https://github.com/master-of-zen/Av1an/pull/809
* Fix new clippy lint in Rust 1.77 by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/819
* append to log file by @jcj83429 in https://github.com/master-of-zen/Av1an/pull/817
* Update Windows CI job by @FreezyLemon in https://github.com/master-of-zen/Av1an/pull/820
* Fix vmaf flag check with target-quality by @luigi311 in https://github.com/master-of-zen/Av1an/pull/823
* Bump MSRV to 1.70 by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/835
* Support ffmpeg 7.0 by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/834
* Modify windows-build.yml to use ffmpeg 7.0 by @Uranite in https://github.com/master-of-zen/Av1an/pull/839
* Fix "Received a packet for an attachment stream" error when encoding certain files. by @0xBA5E64 in https://github.com/master-of-zen/Av1an/pull/841
* Implement pipeless scene detection for Vapoursynth inputs by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/844
* Bump dependencies and remove an old file by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/845
* Implement pipeless scene detection for basic video inputs by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/847
* Remove vapoursynth-plugin-lsmashsource by @Extarys in https://github.com/master-of-zen/Av1an/pull/851
* Use updated av-scenechange with threaded ffmpeg decoder by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/853
* Update compile docs by @Uranite in https://github.com/master-of-zen/Av1an/pull/855
* Add support for passing variables to the vspipe python environment by @Vernoxvernax in https://github.com/master-of-zen/Av1an/pull/858
* Clippy fixes and other minor cleanup by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/859
* fix docker ref by @lyj0309 in https://github.com/master-of-zen/Av1an/pull/862
* Fix everything breaking the CI by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/864
* Use absolute instead of canonicalize for better Windows path handling by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/861
* Update NASM and VapourSynth in windows-build.yml by @Uranite in https://github.com/master-of-zen/Av1an/pull/866
* Update compiling.md by @Uranite in https://github.com/master-of-zen/Av1an/pull/867

## New Contributors
* @jcj83429 made their first contribution in https://github.com/master-of-zen/Av1an/pull/817
* @Uranite made their first contribution in https://github.com/master-of-zen/Av1an/pull/839
* @0xBA5E64 made their first contribution in https://github.com/master-of-zen/Av1an/pull/841
* @Extarys made their first contribution in https://github.com/master-of-zen/Av1an/pull/851
* @Vernoxvernax made their first contribution in https://github.com/master-of-zen/Av1an/pull/858
* @lyj0309 made their first contribution in https://github.com/master-of-zen/Av1an/pull/862



===== TAG: 0.4.2  PUBLISHED: 02/17/2024 18:56:41  NAME: 0.4.2 =====
# What's Changed
* ci(Mergify): configuration update by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/737
* Remove tpl by @master-of-zen in https://github.com/master-of-zen/Av1an/pull/739
* Move framerate to chunk by @master-of-zen in https://github.com/master-of-zen/Av1an/pull/742
* Action: Test target-quality is working correctly. by @luigi311 in https://github.com/master-of-zen/Av1an/pull/743
* Action: Tag latest, update versions, add docker-publish checker by @luigi311 in https://github.com/master-of-zen/Av1an/pull/741
* Fix estimated fps when using --resume flag by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/736
* Routine dependency updates by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/748
* Move audio size to progress bar OnceCell by @master-of-zen in https://github.com/master-of-zen/Av1an/pull/754
* simplify progress bar finish and consolidate dec_bar function by @master-of-zen in https://github.com/master-of-zen/Av1an/pull/755
* Fix target quality command syntax by @HaveAGitGat in https://github.com/master-of-zen/Av1an/pull/756
* Bump rav1e dependency by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/757
* Split user facing encode args from internals by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/761
* Photon noise arguments by @superyu1337 in https://github.com/master-of-zen/Av1an/pull/764
* Add additional flags, options by @woot000 in https://github.com/master-of-zen/Av1an/pull/671
* Only get frame rate from Vapoursynth once while creating queue by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/774
* Fix rustc 1.71 clippy lints by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/775
* CI: Update dependencies in Windows build by @FreezyLemon in https://github.com/master-of-zen/Av1an/pull/779
* Add DGDecNV and BestSource chunk method by @Simulping in https://github.com/master-of-zen/Av1an/pull/776
* Fix a variety of clippy lints by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/792
* Bump proc-macro2 crate to fix compatibility with latest nightly by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/791
* move ignore frame mismatch to chunk by @master-of-zen in https://github.com/master-of-zen/Av1an/pull/794
* unimportant improvements by @damian101 in https://github.com/master-of-zen/Av1an/pull/795
* Remove support for DgDecNv by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/796
* Clarify preferred methods of obtaining support by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/800
* Revert "Remove support for DgDecNv" by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/799
* Update dependencies by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/804
* Bump shlex from 1.2.0 to 1.3.0 by @dependabot in https://github.com/master-of-zen/Av1an/pull/807
* Rework docs into BookMD by @master-of-zen in https://github.com/master-of-zen/Av1an/pull/811
* Support grain tables for SVT-AV1 by @shssoichiro in https://github.com/master-of-zen/Av1an/pull/812
* Create mdbook.yml by @master-of-zen in https://github.com/master-of-zen/Av1an/pull/813

## New Contributors
* @HaveAGitGat made their first contribution in https://github.com/master-of-zen/Av1an/pull/756
* @superyu1337 made their first contribution in https://github.com/master-of-zen/Av1an/pull/764
* @Simulping made their first contribution in https://github.com/master-of-zen/Av1an/pull/776
* @damian101 made their first contribution in https://github.com/master-of-zen/Av1an/pull/795

**Full Changelog**: https://github.com/master-of-zen/Av1an/compare/0.4.1...0.4.2

===== TAG: 0.4.1  PUBLISHED: 03/13/2023 11:31:24  NAME: 0.4.1 =====


