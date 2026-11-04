"""Build identity for the packaged backend (28B).

The packaging step rewrites ``BUILD_SHA`` before PyInstaller runs so the
installed backend can prove which source build it came from.  Source-tree
runs keep the empty default and the desktop shell treats an empty value as
"identity unavailable" rather than a mismatch.
"""

BUILD_SHA = ""
