"""Sony metadata-preservation package.

Separate from the x265 scaling architecture (core/, encoders/). The
backend-boundary seams:

    VideoBackend                 FFmpeg + libx265 (poc_video.py POC;
                                 production: encoders/x265.py video-only
                                 intermediate injected by 1keytransc.py)
    AudioBackend                 container-level per-track copy
                                 (poc_video.py)
    MetadataPreservationBackend  Sony rtmd/nrtm/uuid (sony.py)
    ContainerBackend             GPAC/MP4Box (gpac.py) + ISO-BMFF patcher
                                 (isobmf.py) for uuid boxes GPAC cannot
                                 write

Timing: rtmd/audio tracks are container-copied from the source by
MP4Box (-add src#<trackID>) — native ISOBMFF copies preserve exact
source timing (stts 1001/60000, elst, tkhd). GPAC's NHML import path
is NOT used for reconstruction (it rounds track durations at 600-tick
precision on GPAC 26.02). isobmf.patch_track_durations() is kept as a
Level-3 fallback only; it is not part of the normal pipeline.

pipeline.py orchestrates the Sony production path for 1keytransc.py;
sony_poc.py is the original standalone POC (reference).
"""
