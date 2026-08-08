"""Numba decorator compatibility for source and PyInstaller-frozen builds."""

import sys

from numba import njit as _numba_njit


def njit(*args, **kwargs):
    # PyInstaller one-file modules do not always expose a stable source path for
    # Numba's on-disk cache. Preserve the existing non-cached behavior there.
    kwargs["cache"] = not getattr(sys, "frozen", False)
    return _numba_njit(*args, **kwargs)
