"""Sentence embeddings for `episodes.embedding`, computed on the device,
between turns. This is what makes retrieval's relevance axis real.

**Why this exists.** `compile.retrieve_episodes` scores recency +
importance + relevance (Park et al. 2023), but every `episodes.embedding`
was NULL: Groq offers no embedding model, and CLAUDE.md rules out a
vector DB and a second vendor. So "relevance" was a constant zero and
SPEC.md's "recency + importance + relevance" was two out of three
(TODO.md, "Retrieval's relevance axis is dead"). That was framed as a
vendor question, and it isn't: a small local model running between
turns is not a vendor, not a service and not a vector DB.

**What runs.** `sentence-transformers/all-MiniLM-L6-v2` (Apache 2.0)
through `onnxruntime`, which this project already ships transitively
via Piper — no new runtime enters the device. The model file
(`onnx/model.onnx`, ~90 MB) and its `vocab.txt` are fetched by plain
HTTPS into `~/.saathi/embeddings/`, the same shape as Piper's voices in
`~/.saathi/tts-voices/`. Mean-pool the last hidden state over the
attention mask, L2-normalise, store as little-endian float32 bytes —
exactly the layout `compile.retrieve_episodes` already reads with
`np.frombuffer`.

**The options that lost.**
- `sentence-transformers` (the reference implementation): pulls
  `torch`, which has been an aarch64 problem on this project twice
  (Kokoro, MeloTTS). ONNX runtime is already installed and has a
  `manylinux_2_28_aarch64` wheel.
- HuggingFace `tokenizers`/`huggingface-hub` for the WordPiece
  tokenizer and download: both would be new direct dependencies (they
  exist in `uv.lock` only through optional groups CI never installs
  by default). BERT's uncased WordPiece is ~60 lines over `vocab.txt`
  (`WordPieceTokenizer` below) and `urllib` downloads a file; neither
  earns a dependency.
- A multilingual model: `digest.py`'s prompt produces English
  third-person observations regardless of the language she spoke, and
  the correction tool writes English too, so the text being embedded
  is English. An English-only model is the right size for that. If a
  writer of non-English episode text ever appears, swap the model
  files — nothing else here assumes the language.
- Downloading the model implicitly on first use: rejected. `embed()`
  never touches the network; with the files absent it returns `None`
  and everything degrades exactly as before (relevance = 0), the same
  shape as a TTS backend's `available()`. The download is an explicit
  step (`python -m saathi.identity.embed --download`, or the
  `--selfcheck` that also proves the model works), so no background
  thread ever starts a 90 MB fetch mid-conversation.

**Never in the hot path.** `OnnxEmbedder` creates its ONNX session on
first `embed()` call, not on import and not in `__init__` —
`compile_context()` runs on `CascadeSession`'s constructor thread and
must not pay for a model load, and nothing in a turn calls this at all.
`digest.write_episode` calls it from `cascade.py`'s post-`say()`
background thread, which is "between turns" by construction.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from saathi.identity.store import IdentityStore

logger = logging.getLogger(__name__)

MODEL_DIR = Path.home() / ".saathi" / "embeddings"
MODEL_FILE = "model.onnx"
VOCAB_FILE = "vocab.txt"

# Pinned to a repository commit, not `main`, so a re-export upstream can
# never change what a device downloads; the sha256 of each file is what
# a download is checked against (model.onnx's matches HuggingFace's own
# LFS etag for that commit, verified 2026-09-25). A different hash is a
# failed download, not a trusted one.
_HF_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
_HF_BASE = (
    "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/"
    f"{_HF_REVISION}/"
)
_MODEL_URL = _HF_BASE + "onnx/model.onnx"
_VOCAB_URL = _HF_BASE + "vocab.txt"
_MODEL_SHA256 = "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452"
_VOCAB_SHA256 = "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3"
_DOWNLOAD_TIMEOUT_S = 60  # per socket operation, not the whole transfer
# One padded ONNX batch's hidden state is rows x width x 384 float32;
# 64 rows at the 256-token ceiling is ~25 MB, fine on a Pi.
BACKFILL_CHUNK = 64

# sentence_bert_config.json's max_seq_length for this model. Episode
# sentences are one line; this is a ceiling, not a target.
MAX_TOKENS = 256
EMBEDDING_DIM = 384

Embedder = Callable[[str], "np.ndarray | None"]


# -- tokenizer ---------------------------------------------------------------


def _is_punctuation(char: str) -> bool:
    code = ord(char)
    if 33 <= code <= 47 or 58 <= code <= 64 or 91 <= code <= 96 or 123 <= code <= 126:
        return True
    return unicodedata.category(char).startswith("P")


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2CEAF
        or 0xF900 <= code <= 0xFAFF
        or 0x2F800 <= code <= 0x2FA1F
    )


def _basic_tokens(text: str) -> list[str]:
    """BERT's uncased `BasicTokenizer`: lowercase, strip accents, split
    on whitespace, and make every punctuation mark and CJK character its
    own token. Control characters are dropped."""
    out: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            out.append("".join(current))
            current.clear()

    for char in unicodedata.normalize("NFD", text.lower()):
        category = unicodedata.category(char)
        if category == "Mn":  # combining accent -- strip_accents
            continue
        if char.isspace():
            flush()
            continue
        if category.startswith("C"):  # control characters
            continue
        if _is_punctuation(char) or _is_cjk(char):
            flush()
            out.append(char)
            continue
        current.append(char)
    flush()
    return out


class WordPieceTokenizer:
    """Greedy longest-match WordPiece over `vocab.txt`, with `##`
    continuation pieces, the way `BertTokenizer` does it. Words longer
    than 100 characters become `[UNK]` rather than being split
    quadratically."""

    def __init__(self, vocab: Iterable[str]) -> None:
        self._ids = {token: i for i, token in enumerate(vocab)}
        for special in ("[PAD]", "[UNK]", "[CLS]", "[SEP]"):
            if special not in self._ids:
                raise ValueError(f"vocab is missing {special}")
        self.pad_id = self._ids["[PAD]"]
        self.unk_id = self._ids["[UNK]"]
        self.cls_id = self._ids["[CLS]"]
        self.sep_id = self._ids["[SEP]"]

    @classmethod
    def from_file(cls, path: Path) -> "WordPieceTokenizer":
        with path.open(encoding="utf-8") as f:
            return cls(line.rstrip("\n") for line in f)

    def _pieces(self, word: str) -> list[int]:
        if len(word) > 100:
            return [self.unk_id]
        pieces: list[int] = []
        start = 0
        while start < len(word):
            end = len(word)
            found = None
            while start < end:
                candidate = word[start:end]
                if start > 0:
                    candidate = "##" + candidate
                if candidate in self._ids:
                    found = self._ids[candidate]
                    break
                end -= 1
            if found is None:
                return [self.unk_id]
            pieces.append(found)
            start = end
        return pieces

    def encode(self, text: str, max_tokens: int = MAX_TOKENS) -> list[int]:
        """`[CLS] pieces... [SEP]`, truncated so `[SEP]` is always the last
        token when the text is too long."""
        ids = [self.cls_id]
        budget = max_tokens - 2
        for word in _basic_tokens(text):
            for piece in self._pieces(word):
                if len(ids) - 1 >= budget:
                    break
                ids.append(piece)
        ids.append(self.sep_id)
        return ids


# -- pooling -----------------------------------------------------------------


def pool(last_hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Mean over the tokens the mask keeps, then L2-normalise, per
    sentence-transformers' own pooling config for this model. Returns
    float32 `[batch, dim]`."""
    mask = attention_mask[..., None].astype(np.float32)
    summed = (last_hidden_state.astype(np.float32) * mask).sum(axis=1)
    counts = np.clip(mask.sum(axis=1), 1e-9, None)
    mean = summed / counts
    norms = np.clip(np.linalg.norm(mean, axis=1, keepdims=True), 1e-12, None)
    return (mean / norms).astype(np.float32)


def to_blob(vector: np.ndarray) -> bytes:
    """The one storage layout: little-endian float32, no header — what
    `compile.retrieve_episodes` reads back with `np.frombuffer`."""
    return np.asarray(vector, dtype="<f4").tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")


# -- model files -------------------------------------------------------------


def model_files_present(model_dir: Path = MODEL_DIR) -> bool:
    return (model_dir / MODEL_FILE).is_file() and (model_dir / VOCAB_FILE).is_file()


def _download(url: str, destination: Path, expected_sha256: str) -> None:
    """Fetch to a sibling temp file and rename into place, so a power
    cut mid-download leaves nothing that looks like a model. A stalled
    socket times out rather than hanging an install script forever."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    try:
        with (
            urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_S) as response,
            partial.open("wb") as out,
        ):
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != expected_sha256:
            raise OSError(f"{url}: sha256 {digest.hexdigest()} != expected {expected_sha256}")
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(destination)


def ensure_model(model_dir: Path = MODEL_DIR) -> Path:
    """Download the model and vocab if absent. The only function in this
    module that touches the network; nothing calls it implicitly."""
    if not (model_dir / VOCAB_FILE).is_file():
        _download(_VOCAB_URL, model_dir / VOCAB_FILE, _VOCAB_SHA256)
    if not (model_dir / MODEL_FILE).is_file():
        _download(_MODEL_URL, model_dir / MODEL_FILE, _MODEL_SHA256)
    return model_dir


# -- the embedder ------------------------------------------------------------

SessionFactory = Callable[[Path], Any]


def _onnx_session(model_path: Path) -> Any:
    # Imported here, not at module top: onnxruntime is ~100 ms to import
    # and nothing on the startup path needs it.
    import onnxruntime as ort

    options = ort.SessionOptions()
    # One sentence at a time, between turns, on a Pi: a single intra-op
    # thread keeps this from contending with audio for cores.
    options.intra_op_num_threads = 1
    options.log_severity_level = 3
    return ort.InferenceSession(
        str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
    )


class OnnxEmbedder:
    """Lazy: nothing is loaded until the first `embed()`. Safe to call
    from any thread (`InferenceSession.run` is thread-safe; the one-time
    load is behind a lock). `session_factory` exists for tests, which
    inject a fake session and never touch onnxruntime or the network."""

    def __init__(
        self, model_dir: Path = MODEL_DIR, *, session_factory: SessionFactory = _onnx_session
    ) -> None:
        self._model_dir = Path(model_dir)
        self._session_factory = session_factory
        self._lock = threading.Lock()
        self._session: Any = None
        self._tokenizer: WordPieceTokenizer | None = None
        self._load_error: str | None = None

    @property
    def model_dir(self) -> Path:
        return self._model_dir

    def available(self) -> tuple[bool, str]:
        if not model_files_present(self._model_dir):
            return False, (
                f"no embedding model in {self._model_dir} -- run "
                "`python -m saathi.identity.embed --download`"
            )
        if self._load_error is not None:
            return False, self._load_error
        return True, ""

    def _ensure_loaded(self) -> bool:
        if self._session is not None:
            return True
        with self._lock:
            if self._session is not None:
                return True
            if self._load_error is not None or not model_files_present(self._model_dir):
                return False
            try:
                self._tokenizer = WordPieceTokenizer.from_file(self._model_dir / VOCAB_FILE)
                self._session = self._session_factory(self._model_dir / MODEL_FILE)
            except Exception as exc:  # a corrupt file must not take the turn loop down
                self._load_error = f"embedding model failed to load: {exc}"
                logger.warning(self._load_error)
                return False
        return True

    def embed_many(self, texts: list[str]) -> list[np.ndarray] | None:
        """One float32, L2-normalised vector per text, or `None` when the
        model isn't available. Never raises for a missing model; a
        missing embedding is a degraded ranking, not a broken turn."""
        if not texts or not self._ensure_loaded():
            return None
        assert self._tokenizer is not None
        encoded = [self._tokenizer.encode(t) for t in texts]
        width = max(len(ids) for ids in encoded)
        input_ids = np.full((len(encoded), width), self._tokenizer.pad_id, dtype=np.int64)
        attention_mask = np.zeros((len(encoded), width), dtype=np.int64)
        for row, ids in enumerate(encoded):
            input_ids[row, : len(ids)] = ids
            attention_mask[row, : len(ids)] = 1
        feeds = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": np.zeros_like(input_ids),
        }
        try:
            (last_hidden_state,) = self._session.run(["last_hidden_state"], feeds)
        except Exception:
            # A model that deserialised but fails at inference (swapped
            # files, out of memory on a Pi) must not take the cascade's
            # post-say() thread down with it -- that thread also
            # recompiles context. Logged with the traceback, once per
            # call; a NULL embedding is the documented degraded state.
            logger.exception("embedding inference failed; episode stored without a vector")
            return None
        pooled = pool(np.asarray(last_hidden_state), attention_mask)
        return [pooled[i] for i in range(len(texts))]

    def embed(self, text: str) -> np.ndarray | None:
        vectors = self.embed_many([text])
        return None if vectors is None else vectors[0]


_default: OnnxEmbedder | None = None
_default_lock = threading.Lock()


def default_embedder() -> OnnxEmbedder:
    """The process-wide embedder over `MODEL_DIR`. Constructing it loads
    nothing; the first `embed()` does."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = OnnxEmbedder()
    return _default


def embed(text: str) -> np.ndarray | None:
    """Module-level convenience over `default_embedder()` — the
    `Embedder` that `digest.write_episode` uses when none is injected."""
    return default_embedder().embed(text)


# -- backfill ----------------------------------------------------------------


def backfill_embeddings(
    store: IdentityStore, *, embedder: OnnxEmbedder | None = None, limit: int | None = None
) -> int:
    """Fill `episodes.embedding` where it is NULL — rows written before
    the model was on the device, and correction episodes (written in the
    hot path, where a model must not load). Returns how many rows were
    filled; 0 when the model isn't available.

    This writes SQL against `store.path` directly rather than through
    `IdentityStore`, and that is a deliberate, recorded exception
    (DECISIONS.md 2026-09-25): filling a NULL cell exactly once is not a
    correction and not an update of anything that was ever believed, so
    it did not earn a second primitive on a protected interface next to
    `retire`. The statement's `AND embedding IS NULL` guard is what keeps
    it write-once — an existing value is never overwritten, and `store`'s
    own connection is not touched (this runs between turns from whatever
    thread has the store, so a fresh short-lived connection is the
    cross-thread-safe choice regardless).
    """
    embedder = embedder or default_embedder()
    if not embedder.available()[0]:
        return 0
    pending = [row for row in store.read("episodes") if row.get("embedding") is None]
    pending.sort(key=lambda row: row["id"], reverse=True)  # newest first: most retrievable
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        return 0
    filled = 0
    conn = sqlite3.connect(store.path)
    try:
        # Fixed-size chunks, one transaction each: the padded hidden
        # state scales with rows x width, and a device's first backfill
        # over a long history must not build one table-sized tensor.
        for start in range(0, len(pending), BACKFILL_CHUNK):
            chunk = pending[start : start + BACKFILL_CHUNK]
            vectors = embedder.embed_many([row["text"] for row in chunk])
            if vectors is None:
                break
            with conn:
                for row, vector in zip(chunk, vectors):
                    cursor = conn.execute(
                        "UPDATE episodes SET embedding = ? WHERE id = ? AND embedding IS NULL",
                        (to_blob(vector), row["id"]),
                    )
                    filled += cursor.rowcount
    finally:
        conn.close()
    return filled


# -- command line ------------------------------------------------------------


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))  # both are unit vectors


def _selfcheck(model_dir: Path) -> int:
    started = time.monotonic()
    ensure_model(model_dir)
    print(f"model files ready in {model_dir} ({time.monotonic() - started:.2f}s)")
    embedder = OnnxEmbedder(model_dir)
    sentences = ["her scan is on Thursday", "the hospital appointment", "she likes milky tea"]
    started = time.monotonic()
    first = embedder.embed(sentences[0])
    load_and_first = time.monotonic() - started
    if first is None:
        print("embedder unavailable:", embedder.available()[1])
        return 1
    started = time.monotonic()
    vectors = embedder.embed_many(sentences)
    per_batch = time.monotonic() - started
    assert vectors is not None
    print(f"first embed (session load + one sentence): {load_and_first * 1000:.0f} ms")
    print(f"three sentences, warm: {per_batch * 1000:.0f} ms")
    for i in range(len(sentences)):
        for j in range(i + 1, len(sentences)):
            print(f"  {_cosine(vectors[i], vectors[j]):.3f}  {sentences[i]!r} ~ {sentences[j]!r}")
    scan_appt = _cosine(vectors[0], vectors[1])
    scan_tea = _cosine(vectors[0], vectors[2])
    ok = scan_appt > scan_tea
    print("scan~appointment beats scan~tea:", "yes" if ok else "NO")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m saathi.identity.embed")
    parser.add_argument("--download", action="store_true", help="fetch the model files if absent")
    parser.add_argument(
        "--selfcheck", action="store_true", help="download, embed three sentences, print cosines"
    )
    parser.add_argument(
        "--backfill", action="store_true", help="embed every episode whose embedding is NULL"
    )
    parser.add_argument("--db", type=Path, default=None, help="identity DB for --backfill")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    args = parser.parse_args(argv)

    if args.download:
        ensure_model(args.model_dir)
        print(f"model files ready in {args.model_dir}")
    if args.selfcheck:
        code = _selfcheck(args.model_dir)
        if code:
            return code
    if args.backfill:
        from saathi.config import Config

        db_path = args.db or Config.load().identity_db_path
        ensure_model(args.model_dir)
        started = time.monotonic()
        with IdentityStore(db_path) as store:
            store.create()
            filled = backfill_embeddings(store, embedder=OnnxEmbedder(args.model_dir))
        print(f"backfilled {filled} episode(s) in {db_path} ({time.monotonic() - started:.2f}s)")
    if not (args.download or args.selfcheck or args.backfill):
        parser.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
