"""One hermeticity guard for the whole suite: no test may load the real
embedding model, whatever is in `~/.saathi/embeddings` on the machine
running it.

`identity/embed.py`'s process-wide default embedder is what
`digest.write_episode` uses when nothing is injected, and
`tests/test_cascade.py` reaches `write_episode` through the cascade's
real post-`say()` path. On CI the model files are absent and the
embedder returns `None`; on a developer machine that has run
`python -m saathi.identity.embed --selfcheck`, the same test would
silently load a 90 MB ONNX model and store real vectors — a test whose
behaviour depends on what a previous shell command left on disk.
Pointing the default at an empty directory for every test makes both
machines behave like CI. Tests that want embeddings inject a fake
embedder explicitly (`tests/test_identity_embed.py`).
"""

import pytest

from saathi.identity import embed


@pytest.fixture(autouse=True)
def _no_real_embedding_model(monkeypatch, tmp_path):
    monkeypatch.setattr(embed, "_default", embed.OnnxEmbedder(tmp_path / "no-model-here"))
