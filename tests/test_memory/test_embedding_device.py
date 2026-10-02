"""验证 embedding 根据硬件选择设备，不下载模型。"""

from unittest.mock import MagicMock

import pytest

from mediZJ.memory import embedding


@pytest.mark.parametrize(
    ("cuda_available", "mps_available", "expected"),
    [(True, True, "cuda"), (True, False, "cuda"),
     (False, True, "mps"), (False, False, "cpu")],
)
def test_device_priority(monkeypatch, cuda_available, mps_available, expected):
    monkeypatch.setattr(
        embedding.torch.cuda, "is_available", lambda: cuda_available
    )
    monkeypatch.setattr(
        embedding.torch.backends.mps, "is_available", lambda: mps_available
    )
    assert embedding.select_embedding_device() == expected


@pytest.mark.parametrize("device", ["cuda", "mps", "cpu"])
def test_model_uses_selected_device(monkeypatch, device):
    embedding.load_embedding_model.cache_clear()
    constructor = MagicMock()
    monkeypatch.setattr(embedding, "SentenceTransformer", constructor)
    monkeypatch.setattr(embedding, "select_embedding_device", lambda: device)
    monkeypatch.setattr(embedding, "_get_local_cache_path", lambda name: None)
    try:
        model = embedding.load_embedding_model("test-model")
        assert embedding.load_embedding_model("test-model") is model
        constructor.assert_called_once_with("test-model", device=device)
    finally:
        embedding.load_embedding_model.cache_clear()


def test_device_initialization_failure_is_reported(monkeypatch):
    embedding.load_embedding_model.cache_clear()
    constructor = MagicMock(side_effect=RuntimeError("device initialization"))
    monkeypatch.setattr(embedding, "SentenceTransformer", constructor)
    monkeypatch.setattr(embedding, "select_embedding_device", lambda: "cuda")
    monkeypatch.setattr(embedding, "_get_local_cache_path", lambda name: None)
    try:
        with pytest.raises(RuntimeError, match="device initialization"):
            embedding.load_embedding_model("test-model")
        constructor.assert_called_once_with("test-model", device="cuda")
    finally:
        embedding.load_embedding_model.cache_clear()
