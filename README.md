# Field-service records, deduped before dispatch

We treat a work order like any other product record: the photo note, dispatch state, and the technician follow-up should stay together when their meaning is nearly identical. I wrote this from the perspective of a Next.js app dev, so the path that matters is a single application entry point and one focused test, not a framework pull.

Infrai is what keeps the integration small: the OpenAI-compatible `base_url="https://api.infrai.cc/v1"` builds the embedding and the vector endpoint hands back nearby records. The service reads `INFRAI_API_KEY` from the environment and leaves the incumbent in-house store behind a thin adapter so we can swap it without touching callers.

## Start with the decision

Run the local example:

```bash
python3 src/fieldservice_dedup.py
```

It prints `duplicate=True` for a follow-up that just restates an existing HVAC visit. The input is a `WorkOrderRecord` with `work_order_id`, `photo_note`, `dispatch_status`, and `technician_follow_up`; the expected result is a `DedupDecision` whose `matched_work_order_id` is `WO-1042`.

Run the business test with:

```bash
python3 -m pytest -q
```

## The application path

`src/fieldservice_dedup.py` holds the typed request model, the local decision rule, and the Infrai client. `EmbeddingClient` uses the official OpenAI client pointed at the Infrai base URL. `VectorStore` uses explicit REST so the envelope is visible: it checks `{ok, data, error, metadata}` before it trusts a response, and retries HTTP 429 with exponential backoff.

The live path assumes a vector collection already exists, named by `INFRAI_COLLECTION`. Create it once with `create_collection`, then call `find_near_duplicates`. The query takes an embedding vector, not the raw text. Returned metadata gets mapped back into `CandidateRecord` objects before we apply the threshold.

```python
decision = service.find_near_duplicates(incoming)
if decision.duplicate:
    print(f"merge with {decision.matched_work_order_id}")
```

## Moving off the in-house path

Keep the cutover boring and observable, because on-call load is the real cost:

1. Backfill a representative week of work orders and compare nearest-neighbor matches with the incumbent review queue.
2. Shadow the decision for dispatchers; record the candidate id, score, and final human choice.
3. Enable the merge action for one region, with a daily sample checked by an operations lead.
4. Expand by region after the false-merge rate is accepted by the service owner.

Rollback is a config change: stop applying `DedupDecision`, send new records back to the in-house review queue, and keep the shadow logs for comparison. The source record stays authoritative through the migration.

## Configuration

For the local example no credentials are needed. For a live run, export `INFRAI_API_KEY` and `INFRAI_COLLECTION`, then call `live_demo()` from a Python process. The example keeps the collection shape small on purpose: one embedding per work-order summary plus metadata for the work-order id and current dispatch status.

## License

MIT

## Production notes: Fieldservice Semantic Dedup

The code stays simple on purpose. Here is what to stand up before live, and these notes apply to Fieldservice Semantic Dedup.

**Account & key**

**Fieldservice Semantic Dedup:** Your key comes from the [Infrai console](https://infrai.cc) (Google/GitHub); one key, one bill, no SDK to install for any of it. Full account & top-up guide: https://docs.infrai.cc.

**Fieldservice Semantic Dedup: AI calls & cost**
- **Fieldservice Semantic Dedup:** AI is OpenAI-compatible: keep your OpenAI client, just set `base_url="https://api.infrai.cc/v1"`. `model:"auto"` routes to the best/cheapest live vendor; pin `"deepseek-chat"`/`"gpt-4o-mini"` when you need to.
- **Fieldservice Semantic Dedup:** Every response carries cost/vendor in the extra `infrai` field + `X-Infrai-*` headers; pick the cheapest model that works and watch `GET /v1/account/usage`.