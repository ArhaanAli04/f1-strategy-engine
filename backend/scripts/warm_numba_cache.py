"""Compile the Numba code the app uses into NUMBA_CACHE_DIR, at image build time.

Numba compiles a decorated function the first time it runs and, with
cache=True, writes the result to a cache directory that later processes load
instead of recompiling. In a container that directory starts empty on every
boot, so each fresh machine paid the compile again: about 50 CPU-seconds for
umap's dependency pynndescent alone. Both Dockerfiles run this script once
during the build, so the compiled code ships inside the image.

What it compiles:
- umap and pynndescent (compiled when umap is imported, plus the fit path
  driver_style.fit_driver_style_clusters uses);
- race_simulator's two @numba.njit(cache=True) functions, called once with
  the argument types simulate_race passes.

The cache is only reused on a machine whose CPU matches the one that built
it, unless NUMBA_CPU_NAME=generic is set at both build and run time, which
both Dockerfiles do.

Usage (inside the image build, as the runtime user):
    python -m backend.scripts.warm_numba_cache
"""

import logging
import os
import sys
import time

import numpy as np

from backend.services.ml import race_simulator

logger = logging.getLogger(__name__)


def _warm_umap() -> None:
    import umap

    rng = np.random.default_rng(0)
    umap.UMAP(n_components=2, random_state=42).fit_transform(rng.normal(size=(40, 4)))


def _warm_race_simulator() -> None:
    n_sims, n_drivers = 4, 3
    race_simulator._seed_numba_rng(0)
    race_simulator._advance_lap(
        np.zeros((n_sims, n_drivers), dtype=np.float64),
        np.zeros((n_sims, n_drivers), dtype=np.int64),
        np.zeros((n_sims, n_drivers), dtype=np.float64),
        np.full(n_drivers, 90.0, dtype=np.float64),
        np.zeros(n_drivers, dtype=np.float64),
        0.3,
        np.zeros((n_sims, n_drivers), dtype=np.bool_),
        22.0,
        np.zeros(n_sims, dtype=np.bool_),
        120.0,
    )


def main() -> None:
    """Compile and cache every Numba function the app uses.

    Returns:
        None. Exits non-zero if NUMBA_CACHE_DIR is unset, since nothing would
        then be written anywhere the image keeps.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cache_dir = os.environ.get("NUMBA_CACHE_DIR")
    if not cache_dir:
        sys.exit("NUMBA_CACHE_DIR is not set; nothing would be cached in the image")
    for name, warm in (("umap", _warm_umap), ("race_simulator", _warm_race_simulator)):
        started = time.perf_counter()
        warm()
        logger.info("compiled %s in %.1fs", name, time.perf_counter() - started)
    logger.info(
        "Numba cache written to %s (NUMBA_CPU_NAME=%s)", cache_dir, os.environ.get("NUMBA_CPU_NAME")
    )


if __name__ == "__main__":
    main()
