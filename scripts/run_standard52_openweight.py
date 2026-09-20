"""Portable Standard-52 entry for a preregistered open-weight model."""
from __future__ import annotations
import argparse, json, runpy, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main()->None:
 p=argparse.ArgumentParser();p.add_argument("--model-id",required=True);p.add_argument("--base-url",required=True);p.add_argument("--api-key-env",default="FWCOLLAB_API_KEY");p.add_argument("--replicate",required=True,type=int,choices=(1,2,3));p.add_argument("--output-dir",default="artifacts/runs/leaderboard_v1");p.add_argument("--concurrency",type=int,default=4);p.add_argument("--reasoning-setting",default="off");p.add_argument("--matrix",default="eval_private/leaderboard_v1/openweight_model_matrix.json");a=p.parse_args();matrix=ROOT/a.matrix
 if not matrix.is_file():raise SystemExit("formal openweight_model_matrix.json is required after non-benchmark preflight")
 data=json.loads(matrix.read_text(encoding="utf-8-sig"));entry=next((x for x in data.get("models",[]) if x.get("model_id")==a.model_id),None)
 if not entry or entry.get("reasoning_setting")!=a.reasoning_setting or entry.get("endpoint_identity")!=a.base_url:raise SystemExit("CLI model/endpoint/reasoning must exactly match the frozen matrix")
 sys.argv=[str(ROOT/"scripts/run_standard52_leaderboard.py"),"--matrix",a.matrix,"--model-id",a.model_id,"--replicate",str(a.replicate),"--base-url",a.base_url,"--api-key-env",a.api_key_env,"--output-dir",a.output_dir,"--concurrency",str(a.concurrency)];runpy.run_path(str(ROOT/"scripts/run_standard52_leaderboard.py"),run_name="__main__")
if __name__=="__main__":main()
