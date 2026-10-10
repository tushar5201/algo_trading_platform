"""Tiny in-memory stand-ins for Mongo / Redis / broker used by the tests."""
from bson import ObjectId


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.queries = []

    async def insert_one(self, doc):
        doc = dict(doc)
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)

        class R:  # noqa
            inserted_id = doc["_id"]
        return R()

    async def find_one(self, query, projection=None):
        self.queries.append(query)
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return d
        return None

    async def update_one(self, query, update, **kw):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update.get("$set", {}))
                return

    def by_id(self, _id):
        return next(d for d in self.docs if d["_id"] == _id)


class FakeDB:
    def __init__(self, **collections):
        self._c = {k: v for k, v in collections.items()}
        self.accessed = []

    def __getitem__(self, name):
        self.accessed.append(name)
        return self._c.setdefault(name, FakeCollection())

    def __getattr__(self, name):
        if name.startswith("_") or name == "accessed":
            raise AttributeError(name)
        return self[name]


class ScriptedBroker:
    """Broker whose order-status answers are scripted per order id."""

    def __init__(self, statuses=None, fail_cancel=False):
        self.statuses = statuses or {}
        self.placed = []
        self.cancelled = []
        self.fail_cancel = fail_cancel
        self.closed = False
        self._n = 0

    async def place_order(self, **kw):
        self._n += 1
        oid = f"ORD{self._n}"
        self.placed.append({"order_id": oid, **kw})
        return {"order_id": oid}

    async def place_sl_order(self, **kw):
        self._n += 1
        oid = f"SL{self._n}"
        self.placed.append({"order_id": oid, "kind": "sl", **kw})
        return {"order_id": oid}

    async def get_order_status(self, order_id):
        s = self.statuses.get(order_id, {"status": "open", "avg_price": 0})
        return {"order_id": order_id, "raw": [], **s}

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        if self.fail_cancel:
            raise RuntimeError("cancel failed")
        return True

    async def close(self):
        self.closed = True
