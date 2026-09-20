"""Freeze one or two preregistered open-weight entries after preflight."""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument("--preflight",action="append",required=True);p.add_argument("--slot",action="append",required=True,choices=("5","6"));p.add_argument("--concurrency",type=int,default=4);p.add_argument("--output",default="eval_private/leaderboard_v1/openweight_model_matrix.json");a=p.parse_args()
 if len(a.preflight)!=len(a.slot) or len(set(a.slot))!=len(a.slot):raise SystemExit("provide one unique --slot per --preflight")
 policy=ROOT/"docs/OPEN_WEIGHT_MODEL_SELECTION_POLICY.md";entries=[]
 for slot,rel in zip(a.slot,a.preflight):
  src=ROOT/rel;data=json.loads(src.read_text(encoding="utf-8-sig"));rows=data.get("models",[])
  if data.get("benchmark_content_used") is not False or not data.get("all_pass") or len(rows)!=1 or rows[0].get("preflight_status")!="PASS":raise SystemExit(f"invalid preflight {rel}")
  row=rows[0];entries.append({"slot":int(slot),"model_id":row["model_id"],"resolved_model_id":row["resolved_model_id"],"provider":row["provider"],"endpoint_identity":row["endpoint_identity"],"api_style":row["api_style"],"reasoning_setting":row["reasoning"]["selected"],"temperature":1.0 if row["temperature"]["supported"] else None,"seed_behavior":"accepted_not_guaranteed" if row["seed"]["accepted"] else "unsupported_omitted","max_output_tokens":800,"concurrency":a.concurrency,"retry_policy":"one model-behavior retry; one infrastructure retry","error_policy":"docs/LEADERBOARD_RUN_PLAN.md section 5","prompt_contract_hash":data["prompt_contract_hash"],"standard52_manifest_hash":data["standard52_manifest_hash"],"selection_policy_sha256":sha(policy),"preflight_status":"PASS","preflight_sha256":sha(src)})
 out=ROOT/a.output;out.write_text(json.dumps({"format":"fwcollab.leaderboard.openweight_model_matrix.v1","status":"FROZEN","models":sorted(entries,key=lambda x:x["slot"])},indent=2)+"\n",encoding="utf-8");digest=sha(out);out.with_suffix(out.suffix+".sha256").write_text(digest+"  "+out.name+"\n",encoding="utf-8");print(json.dumps({"path":out.relative_to(ROOT).as_posix(),"sha256":digest,"models":len(entries)}));return 0
if __name__=="__main__":raise SystemExit(main())
