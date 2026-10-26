"""Publish compact synthetic measurements without keys or private configuration."""
import json
from statistics import mean

from home_review.fixtures import ROOT

runs = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "artifacts").glob("investigation-*.json"))]
labels = ("first-scored-run", "shared-guard-repair-rerun", "final-shared-guards",
          "final-recovery-demonstration", "provenance-repair-check")
selected = [run for run in runs if run.get("label") in labels]
public = []
for run in selected:
    public.append({
        "run_id": run["run_id"], "label": run["label"], "dataset_sha256": run["dataset_sha256"],
        "provider_code_sha256": run["provider_code_sha256"], "source_code_sha256": run.get("source_code_sha256"),
        "model": run["model"], "injected_failure": run["injected_failure"],
        "rows": [{"case": row["case"], "mode": row["mode"], "metrics": row["metrics"],
                  "elapsed_seconds": row["elapsed_seconds"], "model_calls": len(row["model_roles"]),
                  "model_roles": row["model_roles"], "failure": row.get("failure"),
                  "local_calls": row.get("report", {}).get("local_count"),
                  "read_attempts": row.get("report", {}).get("read_attempts"),
                  "errors": row.get("report", {}).get("errors"),
                  "issues": row.get("report", {}).get("issues"),
                  "brief": row.get("report", {}).get("brief"),
                  "contributions": row.get("report", {}).get("contributions")}
                 for row in run["rows"]],
    })
(ROOT / "docs/evaluation-results.json").write_text(json.dumps(public, indent=2), encoding="utf-8")
final = next(run for run in reversed(selected) if run["label"] == "final-shared-guards")
for mode in ("rules", "single", "agent"):
    rows = [row for row in final["rows"] if row["mode"] == mode]
    print(json.dumps({"mode": mode, "passes": sum(r["metrics"]["case_pass"] for r in rows),
                      "cases": len(rows), "model_calls": sum(len(r["model_roles"]) for r in rows),
                      "mean_seconds": round(mean(r["elapsed_seconds"] for r in rows), 3)}))
print("Public measurement record: docs/evaluation-results.json")
