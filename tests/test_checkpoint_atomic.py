import json
from pathlib import Path

from core.checkpoint import Checkpoint


def test_checkpoint_save_uses_main_file_without_tmp_leftover(monkeypatch, tmp_path):
    monkeypatch.setattr("core.checkpoint.CHECKPOINT_DIR", str(tmp_path))
    cp = Checkpoint()
    cp.load()
    cp.mark_done("matches", 123, auto_save_every=1)

    state_path = Path(tmp_path) / "state.json"
    tmp_state_path = Path(tmp_path) / "state.json.tmp"

    assert state_path.exists()
    assert not tmp_state_path.exists()

    with open(state_path, encoding="utf-8") as f:
        data = json.load(f)
    assert "123" in data["matches"]

