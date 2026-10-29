"""An in-memory stand-in for `cloud/firebase.py`'s client (another unit's
file, not on this branch): the five methods `cloud/pairing.py` and
`cloud/inbox.py` call, with Firestore's semantics where they matter --
`create_doc` with an id that exists fails, `patch_doc` creates or
updates (and with a mask touches only those fields), a query ordered by
a field leaves out documents without it."""

from __future__ import annotations

import itertools
import operator
from typing import Any

_OPS = {
    "==": operator.eq,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


class AlreadyExists(Exception):
    pass


class Offline(Exception):
    pass


class FakeFirestore:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple] = []
        self.offline = False
        self._ids = itertools.count(1)

    def _call(self, *call) -> None:
        self.calls.append(call)
        if self.offline:
            raise Offline("no network")

    def create_doc(self, path, fields, doc_id=None):
        self._call("create_doc", path, doc_id)
        doc_id = doc_id or f"auto{next(self._ids)}"
        full = f"{path}/{doc_id}"
        if full in self.docs:
            raise AlreadyExists(full)
        self.docs[full] = dict(fields)
        return {"id": doc_id, **fields}

    def patch_doc(self, path, fields, update_mask=None):
        self._call("patch_doc", path, update_mask)
        if update_mask is None:
            self.docs[path] = dict(fields)
        else:
            doc = self.docs.setdefault(path, {})
            for name in update_mask:
                if name in fields:
                    doc[name] = fields[name]
                else:
                    doc.pop(name, None)

    def get_doc(self, path):
        self._call("get_doc", path)
        doc = self.docs.get(path)
        return None if doc is None else {"id": path.rsplit("/", 1)[-1], **doc}

    def query(self, parent_path, collection_id, where=(), order_by=None, limit=None):
        self._call("query", parent_path, collection_id, list(where), order_by, limit)
        prefix = f"{parent_path}/{collection_id}/" if parent_path else f"{collection_id}/"
        found = []
        for path, doc in self.docs.items():
            rest = path[len(prefix):] if path.startswith(prefix) else None
            if not rest or "/" in rest:
                continue
            if all(field in doc and _OPS[op](doc[field], value) for field, op, value in where):
                found.append({"id": rest, **doc})
        if order_by:
            found = sorted((d for d in found if order_by in d), key=lambda d: d[order_by])
        return found[:limit] if limit else found

    def sign_in(self, email, password):
        self._call("sign_in", email)

    # -- for tests --------------------------------------------------------

    def put(self, path: str, **fields: Any) -> None:
        self.docs[path] = fields

    def children(self, path: str) -> dict[str, dict]:
        prefix = path + "/"
        return {
            p[len(prefix):]: d
            for p, d in self.docs.items()
            if p.startswith(prefix) and "/" not in p[len(prefix):]
        }
