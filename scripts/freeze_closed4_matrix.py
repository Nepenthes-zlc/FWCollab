"""Create Closed-4 matrix only from a passing non-benchmark preflight."""

from __future__ import annotations

import argparse, hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ["gpt-5.5", "gpt-5-mini", "gemini-3.7-flash", "gemini-3.5-flash"]

def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--preflight",default="eval_private/leaderboard_v1/preflight/closed4_preflight.json"); p.add_argument("--output",default="eval_private/leaderboard_v1/closed4_model_matrix.json"); p.add_argument("--concurrency",type=int,default=2); a=p.parse_args()
    source=ROOT/a.preflight; data=json.loads(source.read_text(encoding="utf-8-sig"))
    if data.get("benchmark_content_used") is not False or not data.get("all_pass"):
        raise SystemExit("preflight is not a passing non-benchmark preflight")
    records=data.get("models",[])
    if [x.get("model_id") for x in records] != EXPECTED or any(x.get("preflight_status") != "PASS" for x in records):
        raise SystemExit("preflight does not contain the exact passing Closed-4 order")
    models=[]
    for row in records:
        reasoning=row["reasoning"]["selected"]
        models.append({
            "model_id":row["model_id"],"resolved_model_id":row["resolved_model_id"],
            "provider":row["provider"],"endpoint_identity":row["endpoint_identity"],
            "api_style":row["api_style"],"reasoning_setting":reasoning,
            "temperature":1.0 if row["temperature"]["supported"] else None,
            "seed_behavior":"accepted_not_guaranteed" if row["seed"]["accepted"] else "unsupported_omitted",
            "max_output_tokens":800,"concurrency":a.concurrency,
            "retry_policy":"one model-behavior retry; one infrastructure retry",
            "error_policy":"docs/LEADERBOARD_RUN_PLAN.md section 5",
            "prompt_contract_hash":data["prompt_contract_hash"],
            "standard52_manifest_hash":data["standard52_manifest_hash"],
            "preflight_status":"PASS","preflight_sha256":sha(source),
        })
    payload={"format":"fwcollab.leaderboard.closed4_model_matrix.v1","status":"FROZEN","models":models}
    out=ROOT/a.output; out.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    digest=sha(out); (out.with_suffix(out.suffix+".sha256")).write_text(digest+"  "+out.name+"\n",encoding="utf-8")
    print(json.dumps({"path":out.relative_to(ROOT).as_posix(),"sha256":digest,"models":len(models)})); return 0
if __name__=="__main__": raise SystemExit(main())
