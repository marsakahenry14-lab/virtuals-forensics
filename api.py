from fastapi import FastAPI, Query
import metrics

app = FastAPI(title="ACP Trust & Flow Analyzer", version="0.1.0")


@app.get("/api/v1/metrics")
def get_all_metrics(db_path: str = Query(default="indexer_cache.db")):
    return {
        "funnel": metrics.get_event_funnel(db_path),
        "top_pairs": metrics.get_top_client_provider_pairs(db_path),
        "empty_deliverables": metrics.get_empty_deliverables(db_path),
        "evaluator_behavior": metrics.get_evaluator_behavior(db_path),
        "structural_observations": metrics.get_structural_observations(db_path),
    }


@app.get("/")
def root():
    return {
        "status": "ok",
        "endpoints": {
            "metrics": "/api/v1/metrics",
            "docs": "/docs"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
