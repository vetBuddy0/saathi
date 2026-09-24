"""identity/embed.py — the local embedder, without the model. The
tokenizer runs over a hand-written vocab, the ONNX session is a fake
injected through `session_factory`, and one test cuts the network to
prove that an absent model degrades to `None` rather than a download or
a crash. Nothing here downloads anything; CI never sees the real files.
"""

import socket
from pathlib import Path

import numpy as np
import pytest

from saathi.identity import embed as embed_module
from saathi.identity.embed import (
    MODEL_FILE,
    VOCAB_FILE,
    OnnxEmbedder,
    WordPieceTokenizer,
    backfill_embeddings,
    from_blob,
    pool,
    to_blob,
)
from saathi.identity.store import IdentityStore

VOCAB = [
    "[PAD]", "[UNK]", "[CLS]", "[SEP]",
    "the", "scan", "##s", "is", "on", "thursday", "hospital", "appointment",
    "cafe", "she", "like", "garden", ",", ".", "milk", "##y", "tea",
]


@pytest.fixture
def tokenizer():
    return WordPieceTokenizer(VOCAB)


# -- tokenizer ---------------------------------------------------------------


def test_special_token_ids_come_from_the_vocab(tokenizer):
    assert (tokenizer.pad_id, tokenizer.unk_id, tokenizer.cls_id, tokenizer.sep_id) == (
        0, 1, 2, 3,
    )


def test_encode_wraps_in_cls_and_sep_and_lowercases(tokenizer):
    ids = tokenizer.encode("The scan is on Thursday")
    assert ids == [2, 4, 5, 7, 8, 9, 3]


def test_wordpiece_greedy_longest_match_with_continuations(tokenizer):
    # "scans" -> "scan" + "##s"; "milky" -> "milk" + "##y"
    ids = tokenizer.encode("scans milky")
    assert ids == [2, 5, 6, 18, 19, 3]


def test_punctuation_is_its_own_token(tokenizer):
    ids = tokenizer.encode("scan, tea.")
    assert ids == [2, 5, 16, 20, 17, 3]


def test_accents_are_stripped_like_bert_uncased(tokenizer):
    assert tokenizer.encode("Café") == [2, 12, 3]


def test_unknown_words_become_unk(tokenizer):
    assert tokenizer.encode("zebra") == [2, 1, 3]


def test_encode_truncates_and_keeps_sep_last(tokenizer):
    ids = tokenizer.encode("scan " * 100, max_tokens=8)
    assert len(ids) == 8
    assert ids[0] == tokenizer.cls_id and ids[-1] == tokenizer.sep_id


def test_vocab_missing_a_special_token_is_rejected():
    with pytest.raises(ValueError):
        WordPieceTokenizer(["[PAD]", "[UNK]", "[CLS]"])


# -- pooling and storage -----------------------------------------------------


def test_pool_means_over_the_mask_and_normalises():
    hidden = np.array(
        [[[3.0, 0.0], [1.0, 0.0], [100.0, 100.0]]], dtype=np.float32
    )  # third token is padding
    mask = np.array([[1, 1, 0]], dtype=np.int64)
    out = pool(hidden, mask)
    assert out.dtype == np.float32
    np.testing.assert_allclose(out, [[1.0, 0.0]], atol=1e-6)


def test_blob_round_trip_is_little_endian_float32():
    vector = np.array([0.25, -1.0, 3.5], dtype=np.float64)
    blob = to_blob(vector)
    assert len(blob) == 12
    assert np.frombuffer(blob, dtype=np.float32).tolist() == [0.25, -1.0, 3.5]
    np.testing.assert_array_equal(from_blob(blob), np.float32([0.25, -1.0, 3.5]))


# -- the embedder with a fake session ----------------------------------------


class FakeSession:
    """Returns a hidden state where every token's vector is one-hot on
    its own id (mod 4), so pooling gives a bag-of-token-ids vector --
    enough to check plumbing, masks and batching end to end."""

    def __init__(self):
        self.calls = []

    def run(self, outputs, feeds):
        self.calls.append(feeds)
        ids = feeds["input_ids"]
        hidden = np.zeros((*ids.shape, 4), dtype=np.float32)
        for b in range(ids.shape[0]):
            for t in range(ids.shape[1]):
                hidden[b, t, ids[b, t] % 4] = 1.0
        return [hidden]


@pytest.fixture
def model_dir(tmp_path):
    (tmp_path / VOCAB_FILE).write_text("\n".join(VOCAB) + "\n")
    (tmp_path / MODEL_FILE).write_bytes(b"not a real model; the session is faked")
    return tmp_path


def test_embedder_is_lazy_and_reports_availability(model_dir):
    created = []

    def factory(path):
        created.append(path)
        return FakeSession()

    embedder = OnnxEmbedder(model_dir, session_factory=factory)
    assert created == []  # constructing loads nothing
    assert embedder.available() == (True, "")
    vector = embedder.embed("the scan")
    assert created == [model_dir / MODEL_FILE]
    assert vector is not None and vector.dtype == np.float32
    assert vector.shape == (4,)
    np.testing.assert_allclose(np.linalg.norm(vector), 1.0, atol=1e-6)


def test_embed_many_pads_and_masks_a_batch(model_dir):
    session = FakeSession()
    embedder = OnnxEmbedder(model_dir, session_factory=lambda _p: session)
    vectors = embedder.embed_many(["scan", "the scan is on thursday"])
    assert vectors is not None and len(vectors) == 2
    feeds = session.calls[0]
    assert feeds["input_ids"].shape == feeds["attention_mask"].shape == (2, 7)
    assert feeds["input_ids"].dtype == np.int64
    assert feeds["attention_mask"][0].tolist() == [1, 1, 1, 0, 0, 0, 0]
    assert feeds["input_ids"][0].tolist()[3:] == [0, 0, 0, 0]  # [PAD]
    assert feeds["token_type_ids"].sum() == 0
    # The same sentence alone or in a padded batch must embed identically.
    alone = embedder.embed("scan")
    np.testing.assert_allclose(vectors[0], alone, atol=1e-6)


def test_missing_model_files_mean_none_not_an_error(tmp_path):
    embedder = OnnxEmbedder(tmp_path / "empty", session_factory=lambda _p: FakeSession())
    ok, reason = embedder.available()
    assert not ok and "--download" in reason
    assert embedder.embed("anything") is None
    assert embedder.embed_many(["a", "b"]) is None


def test_a_session_that_fails_to_load_is_reported_once_and_returns_none(model_dir):
    attempts = []

    def factory(_path):
        attempts.append(1)
        raise RuntimeError("corrupt protobuf")

    embedder = OnnxEmbedder(model_dir, session_factory=factory)
    assert embedder.embed("x") is None
    assert embedder.embed("y") is None
    assert len(attempts) == 1  # not retried on every call
    ok, reason = embedder.available()
    assert not ok and "corrupt protobuf" in reason


def test_an_inference_failure_yields_none_not_a_dead_background_thread(model_dir):
    class Explodes:
        def run(self, *_a, **_k):
            raise RuntimeError("bad allocation")

    embedder = OnnxEmbedder(model_dir, session_factory=lambda _p: Explodes())
    assert embedder.embed("x") is None


def test_backfill_embeds_in_bounded_chunks(model_dir, tmp_path, monkeypatch):
    monkeypatch.setattr(embed_module, "BACKFILL_CHUNK", 2)
    store = _store(tmp_path)
    for i in range(5):
        _episode(store, f"episode {i}")
    session = FakeSession()
    embedder = OnnxEmbedder(model_dir, session_factory=lambda _p: session)
    assert backfill_embeddings(store, embedder=embedder) == 5
    assert [f["input_ids"].shape[0] for f in session.calls] == [2, 2, 1]
    assert all(r["embedding"] is not None for r in store.read("episodes"))
    store.close()


def test_download_rejects_a_wrong_hash_and_leaves_no_file(monkeypatch, tmp_path):
    import io

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    calls = {}

    def fake_urlopen(url, timeout=None):
        calls["timeout"] = timeout
        return FakeResponse(b"not the model")

    monkeypatch.setattr(embed_module.urllib.request, "urlopen", fake_urlopen)
    destination = tmp_path / "model.onnx"
    with pytest.raises(OSError, match="sha256"):
        embed_module._download("https://example.invalid/model.onnx", destination, "00" * 32)
    assert calls["timeout"] is not None  # a stalled socket fails instead of hanging
    assert not destination.exists()
    assert not destination.with_suffix(".onnx.part").exists()


def test_download_urls_are_pinned_to_a_revision_not_main():
    assert "/resolve/main/" not in embed_module._MODEL_URL
    assert embed_module._HF_REVISION in embed_module._MODEL_URL
    assert embed_module._HF_REVISION in embed_module._VOCAB_URL


def test_no_model_and_no_network_import_and_embed_stay_silent(monkeypatch, tmp_path):
    # Belt and suspenders in the style of tests/test_stt_offline.py:
    # with the model absent, embed() must return None without ever
    # opening a socket -- no implicit download, on any thread.
    def _no_network(*_args, **_kwargs):
        raise OSError("network access attempted by the embedder")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(embed_module, "_default", OnnxEmbedder(tmp_path / "absent"))
    assert embed_module.embed("her scan is on Thursday") is None
    assert embed_module.default_embedder().available()[0] is False


def test_the_default_model_dir_is_under_saathi_home():
    assert embed_module.MODEL_DIR == Path.home() / ".saathi" / "embeddings"


# -- backfill ----------------------------------------------------------------


def _store(tmp_path):
    store = IdentityStore(tmp_path / "identity.sqlite3")
    store.create()
    return store


def _episode(store, text, embedding=None, ts="2026-09-25T00:00:00+00:00"):
    return store.append(
        "episodes", ts=ts, entity_id=None, text=text, importance=5.0, embedding=embedding
    )


def test_backfill_fills_only_null_embeddings_and_never_overwrites(model_dir, tmp_path):
    store = _store(tmp_path)
    existing = to_blob(np.float32([9.0, 9.0, 9.0, 9.0]))
    kept = _episode(store, "already embedded", embedding=existing)
    a = _episode(store, "the scan")
    b = _episode(store, "hospital appointment")
    embedder = OnnxEmbedder(model_dir, session_factory=lambda _p: FakeSession())

    filled = backfill_embeddings(store, embedder=embedder)
    assert filled == 2
    rows = {r["id"]: r for r in store.read("episodes")}
    assert rows[kept]["embedding"] == existing
    for row_id in (a, b):
        vector = from_blob(rows[row_id]["embedding"])
        assert vector.dtype == np.float32 and vector.shape == (4,)
    # A second pass has nothing left to do.
    assert backfill_embeddings(store, embedder=embedder) == 0
    store.close()


def test_backfill_honours_limit_newest_first(model_dir, tmp_path):
    store = _store(tmp_path)
    old = _episode(store, "old")
    new = _episode(store, "new")
    embedder = OnnxEmbedder(model_dir, session_factory=lambda _p: FakeSession())
    assert backfill_embeddings(store, embedder=embedder, limit=1) == 1
    rows = {r["id"]: r for r in store.read("episodes")}
    assert rows[new]["embedding"] is not None
    assert rows[old]["embedding"] is None
    store.close()


def test_backfill_without_a_model_does_nothing(tmp_path):
    store = _store(tmp_path)
    _episode(store, "the scan")
    embedder = OnnxEmbedder(tmp_path / "absent", session_factory=lambda _p: FakeSession())
    assert backfill_embeddings(store, embedder=embedder) == 0
    assert store.read("episodes")[0]["embedding"] is None
    store.close()


def test_backfilled_vectors_are_what_retrieval_reads(model_dir, tmp_path):
    from datetime import datetime, timezone

    from saathi.identity.compile import retrieve_episodes

    store = _store(tmp_path)
    _episode(store, "the scan")
    _episode(store, "hospital appointment")
    embedder = OnnxEmbedder(model_dir, session_factory=lambda _p: FakeSession())
    backfill_embeddings(store, embedder=embedder)
    episodes = store.read("episodes")
    store.close()
    query = embedder.embed("the scan")
    top = retrieve_episodes(
        episodes, now=datetime(2026, 9, 25, tzinfo=timezone.utc), query_embedding=query, top_k=1
    )
    assert top[0]["text"] == "the scan"


def test_main_with_no_flags_prints_help_and_exits_2(capsys):
    assert embed_module.main([]) == 2
    assert "--selfcheck" in capsys.readouterr().out
