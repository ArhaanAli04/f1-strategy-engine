"""Unit tests for scripts/retrain_incremental.py's base-corpus download.

The S3 client is a stub that writes small parquet files where download_file is
told to put them; no network.
"""

from pathlib import Path

import pandas as pd
import pytest

from backend.scripts import retrain_incremental


class _StubS3:
    def __init__(self, laps: pd.DataFrame) -> None:
        self._laps = laps

    def download_file(self, bucket: str, key: str, path: str) -> None:
        if key.endswith("laps.parquet"):
            self._laps.to_parquet(path, index=False)
        else:
            pd.DataFrame({"session_id": ["s1"], "driver_id": ["d1"]}).to_parquet(path, index=False)


@pytest.fixture
def _cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retrain_incremental, "CACHE_DIR", tmp_path)


@pytest.mark.unit
@pytest.mark.usefixtures("_cache_dir")
def test_base_corpus_with_driver_codes_is_returned() -> None:
    laps = pd.DataFrame({"driver_id": ["uuid-ver"], "driver_code": ["VER"]})

    got_laps, got_stints = retrain_incremental._download_base_corpus(_StubS3(laps), "bucket")

    assert list(got_laps["driver_code"]) == ["VER"]
    assert len(got_stints) == 1


@pytest.mark.unit
@pytest.mark.usefixtures("_cache_dir")
def test_base_corpus_exported_before_driver_codes_is_refused() -> None:
    laps = pd.DataFrame({"driver_id": ["uuid-ver"]})

    with pytest.raises(ValueError, match="export_training_data"):
        retrain_incremental._download_base_corpus(_StubS3(laps), "bucket")


@pytest.mark.unit
def test_current_season_laps_carry_a_driver_code_column() -> None:
    assert "driver_code" in retrain_incremental.LAP_COLUMNS
