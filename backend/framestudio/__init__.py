"""FrameStudio backend: owns data, jobs, hardware detection, keys and engines,
and serves the UI over HTTP (plan decision D9)."""

from importlib import metadata

APP_NAME = "FrameStudio"
APP_SLUG = "framestudio"
APP_ID = "org.framestudio.FrameStudio"
#: Bumped when the HTTP API changes incompatibly; the desktop shell checks it.
API_VERSION = 1

try:
    __version__ = metadata.version("framestudio-backend")
except metadata.PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"
