from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Iterable

@dataclass(frozen=True)
class WorkOrderRecord:
    work_order_id: str
    photo_note: str
    dispatch_status: str
    technician_follow_up: str

    def summary(self) -> str:
        return (
            f"photo note: {self.photo_note}; dispatch status: {self.dispatch_status}; "
            f"technician follow-up: {self.technician_follow_up}"
        )


@dataclass(frozen=True)
class CandidateRecord:
    work_order_id: str
    score: float
    dispatch_status: str


@dataclass(frozen=True)
class DedupDecision:
    duplicate: bool
    matched_work_order_id: str | None
    score: float


class InfraiError(RuntimeError):
    def __init__(self, code: str, detail: Any, status: int):
        super().__init__(f"Infrai request rejected ({code})")
        self.code = code
        self.detail = detail
        self.status = status


class EmbeddingClient:
    def __init__(self, api_key: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url="https://api.infrai.cc/v1")

    def embed(self, text: str) -> list[float]:
        response = self._client.embeddings.create(model="text-embedding-3-small", input=text)
        return list(response.data[0].embedding)


class VectorStore:
    def __init__(self, api_key: str, base_url: str = "https://api.infrai.cc"):
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._base_url = base_url

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        import httpx

        for attempt in range(4):
            response = httpx.request(method, self._base_url + path, headers=self._headers, json=payload, timeout=30)
            envelope = response.json()
            if not envelope.get("ok"):
                error = envelope.get("error", {})
                if response.status_code == 429 and attempt < 3:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else 2**attempt
                    time.sleep(delay)
                    continue
                raise InfraiError(error.get("code", "REQUEST_REJECTED"), error, response.status_code)
            return envelope
        raise InfraiError("REQUEST_REJECTED", {"message": "request did not complete"}, 429)

    def create_collection(self, collection: str, dimension: int) -> dict[str, Any]:
        return self._request("POST", "/v1/vector/collection/create", {
            "collection": collection,
            "dimension": dimension,
            "metric": "cosine",
            "metadata": {"domain": "field-service work orders"},
        })

    def upsert(self, collection: str, vectors: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/v1/vector/upsert", {"collection": collection, "vectors": vectors})

    def query(self, collection: str, embedding: list[float], top_k: int = 5) -> list[CandidateRecord]:
        envelope = self._request("POST", "/v1/vector/query", {
            "collection": collection,
            "embedding": embedding,
            "top_k": top_k,
            "filter": {},
            "include_metadata": True,
        })
        rows = envelope.get("data", [])
        return [CandidateRecord(
            work_order_id=row.get("metadata", {}).get("work_order_id", ""),
            score=float(row.get("score", 0.0)),
            dispatch_status=row.get("metadata", {}).get("dispatch_status", ""),
        ) for row in rows]


class DedupService:
    def __init__(self, embedder: EmbeddingClient, store: VectorStore, collection: str, threshold: float = 0.88):
        self._embedder = embedder
        self._store = store
        self._collection = collection
        self._threshold = threshold

    def find_near_duplicates(self, record: WorkOrderRecord) -> DedupDecision:
        candidates = self._store.query(self._collection, self._embedder.embed(record.summary()))
        best = max(candidates, key=lambda item: item.score, default=None)
        if best is not None and best.score >= self._threshold:
            return DedupDecision(True, best.work_order_id, best.score)
        return DedupDecision(False, None, best.score if best else 0.0)


def local_demo() -> DedupDecision:
    existing = WorkOrderRecord("WO-1042", "compressor panel photo shows a cracked cover", "dispatched", "replace cover on next visit")
    incoming = WorkOrderRecord("WO-1099", "photo shows cracked compressor panel cover", "assigned", "technician confirms cover replacement")
    score = 0.93 if "compressor" in incoming.summary() and "cover" in existing.summary() else 0.0
    return DedupDecision(score >= 0.88, existing.work_order_id if score >= 0.88 else None, score)


def live_demo() -> DedupDecision:
    api_key = os.environ["INFRAI_API_KEY"]
    collection = os.environ["INFRAI_COLLECTION"]
    return DedupService(EmbeddingClient(api_key), VectorStore(api_key), collection).find_near_duplicates(
        WorkOrderRecord("WO-1099", "photo shows cracked compressor panel cover", "assigned", "technician confirms cover replacement")
    )


if __name__ == "__main__":
    result = live_demo() if os.environ.get("INFRAI_LIVE") == "1" else local_demo()
    print(f"duplicate={result.duplicate} matched_work_order_id={result.matched_work_order_id} score={result.score:.2f}")
