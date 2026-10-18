"""In-memory Mongo/Redis/queue stand-ins for the AI tests (supports the few operators the AI code uses)."""
import json

from bson import ObjectId

from src.ai.groq_client import LLMResponse


def _get(doc, path):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _match(doc, query):
    for k, cond in query.items():
        v = _get(doc, k)
        if isinstance(cond, dict):
            for op, arg in cond.items():
                if op == "$ne" and v == arg: return False
                if op == "$in" and v not in arg: return False
                if op == "$nin" and v in arg: return False
                if op == "$exists" and (v is not None) != arg: return False
        elif v != cond:
            return False
    return True


class Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction):
        self.docs.sort(key=lambda d: (d.get(key) is None, d.get(key)), reverse=direction < 0)
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    async def to_list(self, n):
        return self.docs[:n]


class Coll:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.updates = []

    async def insert_one(self, doc):
        doc = dict(doc); doc.setdefault("_id", ObjectId()); self.docs.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()

    async def find_one(self, query, projection=None):
        return next((d for d in self.docs if _match(d, query)), None)

    def find(self, query, projection=None):
        return Cursor([d for d in self.docs if _match(d, query)])

    async def count_documents(self, query):
        return len([d for d in self.docs if _match(d, query)])

    async def update_one(self, query, update, **kw):
        self.updates.append((query, update))
        for d in self.docs:
            if _match(d, query):
                for k, v in update.get("$set", {}).items():
                    if "." in k:
                        a, b = k.split(".", 1); d.setdefault(a, {})[b] = v
                    else:
                        d[k] = v
                for k in update.get("$unset", {}):
                    d.pop(k, None)
                return


class DB:
    def __init__(self, **colls):
        self.colls = colls
        self.accessed = []

    def __getitem__(self, name):
        self.accessed.append(name)
        return self.colls.setdefault(name, Coll())

    def __getattr__(self, name):
        if name.startswith("_") or name in ("colls", "accessed"):
            raise AttributeError(name)
        return self[name]


class Redis:
    def __init__(self):
        self.kv, self.ttl = {}, {}

    async def incr(self, k):
        self.kv[k] = self.kv.get(k, 0) + 1
        return self.kv[k]

    async def decr(self, k):
        self.kv[k] = self.kv.get(k, 0) - 1
        return self.kv[k]

    async def expire(self, k, s):
        self.ttl[k] = s


class FakeQueue:
    def __init__(self, fail=False):
        self.jobs, self.fail = [], fail

    async def add(self, name, data, opts=None):
        if self.fail:
            raise ConnectionError("redis down")
        opts = opts or {}
        if any(j["opts"].get("jobId") == opts.get("jobId") for j in self.jobs):
            return None  # BullMQ ignores a duplicate custom job id
        self.jobs.append({"name": name, "data": data, "opts": opts})
        return object()


DEFAULT_REVIEW = {"summary": "Fake review.", "bullets": ["a"], "risk_notes": ["b"], "what_worked": "x",
                  "what_didnt": "y", "confidence": 0.5}


class FakeGroq:
    """Test double for GroqClient. Script items: str (reply content) | Exception (raised)."""
    model = "fake-model"

    def __init__(self, script=None):
        self.script, self.calls = list(script or []), []

    async def complete(self, messages, json_mode=True):
        self.calls.append({"messages": json.loads(json.dumps(messages)), "json_mode": json_mode})
        item = self.script.pop(0) if self.script else json.dumps(DEFAULT_REVIEW)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(content=item, tokens_in=50, tokens_out=80, model="fake-model", finish_reason="stop")

    async def aclose(self):
        pass
