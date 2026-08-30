<!-- Source URL: https://github.com/staxrip/staxrip/releases/tag/v2.52.5 -->
<!-- Fetched at: 2026-08-29 17:12:11 -->

TAG: v2.52.5  PUBLISHED: 08/08/2026 14:42:03

BODY:
> [!TIP]
> #### **If you want to keep StaxRip alive, support its development, get early access to new releases or just say *thanks*, please consider visiting:**
> [![Static Badge](https://img.shields.io/badge/BuyMeACoffee-BuyMeACoffee?style=for-the-badge&logo=BuyMeACoffee&labelColor=hsl(156%2C%2080%25%2C%2020%25)&color=hsl(156%2C%2080%25%2C%2020%25))](https://www.buymeacoffee.com/Dendraspis)  [![Static Badge](https://img.shields.io/badge/Ko--fi-F16061?style=for-the-badge&logo=Ko-Fi&labelColor=hsl(156%2C%2080%25%2C%2020%25)&color=hsl(156%2C%2080%25%2C%2020%25))](https://ko-fi.com/Dendraspis)  [![Static Badge](https://img.shields.io/badge/Patreon-F16061?style=for-the-badge&logo=Patreon&labelColor=hsl(156%2C%2080%25%2C%2020%25)&color=hsl(156%2C%2080%25%2C%2020%25))](https://www.patreon.com/Dendraspis)  
> 
> *Special thanks to all supporters, who mode this version available for us:*  ❤️ 
> ***pat-e, Eric, chowderhead, L Freya, Eagledsm, kingpanther, Ray, lawleenaja, ttplayer, HAL9081, Digi and some more!***

-----------------------------

### [Changelog](https://github.com/staxrip/staxrip/blob/master/CHANGELOG.md):

- General: Improve Dolby Vision cropping
    - In case no cropping could be determined, a normal check via AutoCrop is performed
- UI: Add a filter bar to the Jobs window, which let's you filter the names of your jobs ([#1761](/../../issues/1761))
    - Similar to the one on the Apps Manager
    - Case-insensitive
    - Special words:
        - `<active>` -> Shows only active jobs
    - :information_source: Does not affect the job queue
- SvtAv1EncApp: Add "--enable-kf-tf" parameter
- SvtAv1EncApp: Extend "--hierarchical-levels" parameter
- x265: Add "--fovea-delta" parameter
- x265: Add "--fovea-gaze" parameter
- x265: Add "--fovea-sigma" parameter
- x265: Add "--fovea-gaze" parameter
- x265: Add "--mcstf" parameter
- x265: Add "--mcstf-ref-range" parameter
- x265: Add "--selective-mcstf" parameter
- AviSynth: Add indexing for BestSource
- AviSynth: Alter BestSource filter profile
- VapourSynth: Add indexing for BestSource
- VapourSynth: Alter BestSource filter profile
- VapourSynth: Extend QTGMC filter profiles with new macro functions regarding ScanOrder
- Update tools
    - eac3to v3.64
    - ffmpeg v8.2-dev-N-125670-x64-clang22.1.8
    - MediaInfo v26.05
    - MKVToolNix v100.0
    - NVEncC v9.30
    - QSVEncC v8.25
    - SvtAv1EncApp v4.2.0+71+88-17cd99550-[Mod-by-Patman]-x64-clang22.1.8 [SVT-AV1]
    - SvtAv1EncApp v4.1.0+77+85-e4b6c4ff5-[Mod by Patman]-x64-clang22.1.8 [SVT-AV1-HDR]
    - SvtAv1EncApp v4.1.0+54+28-12aa310be-[Mod-by-Patman]-x64-clang22.1.8 [SVT-AV1-Tritium]
    - TrueHDD v0.5.3
    - VCEEncC v9.11
    - vvencFFapp v1.14.0 r705-9c979c5
    - x264 v0.165.3223+40-25a99de-[Mod-by-Patman]-x64-clang22.1.8
    - x265 v4.3+6+70-44ebc4e46-[Mod-by-Patman]-x64-avx2-clang22.1.8
- Update VapourSynth plugins
    - DotKill R4
    - FillBorders v4
    - VSFilterMod r5.3.1
   
  

***Special thanks to @Patman86 for the continuous sheer number of binaries for various tools as well as the developers of all tools and libraries used by StaxRip.*** 💚 

-----------------------------

> [!NOTE]
> Supporters, follower and Discord users already know, that I made a **BIG Announcement** regarding the future of StaxRip. If you are interested in what is going on and what will change in the future, you may want to read that announcement - I highly recommend it!  
> You can find it on [Discord](https://discord.gg/uz8pVR79Bd) as well as [Patreon](https://www.patreon.com/posts/146531651) and [BuyMeACoffee](https://buymeacoffee.com/dendraspis/big-announcement-4327424).

-----------------------------

> [!CAUTION]
> `StaxRip-v2.52.5-x64.7z`
> Regular full archive.  Do **NOT** extract it into an existing StaxRip folder!  
Extract it into a new location and copy over your *Settings* folder or start with fresh/new settings.
In case encoder settings/parameters have changed, I highly recommend updating only when no affected jobs in the Jobs List are present - the encode may be different than expected. 

