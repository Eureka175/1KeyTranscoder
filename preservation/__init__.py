"""Sony metadata-preservation POC package.

Separate from the x265 scaling architecture (core/, encoders/). This
package implements the backend-boundary prototypes:

    VideoBackend                 FFmpeg + libx265 ultrafast (poc_video.py)
    AudioBackend                 container-level copy (poc_video.py)
    MetadataPreservationBackend  Sony rtmd/nrtm/uuid (sony.py)
    ContainerBackend             GPAC/MP4Box (gpac.py) + ISO-BMFF patcher
                                 (isobmf.py) for uuid boxes GPAC cannot write

Nothing here is imported by x265_archive.py.
"""
