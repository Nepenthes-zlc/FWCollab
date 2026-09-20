"""Portable one-model non-benchmark preflight for an OpenAI-compatible endpoint."""
from __future__ import annotations
import argparse, runpy, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main()->None:
 p=argparse.ArgumentParser();p.add_argument("--model-id",required=True);p.add_argument("--base-url",required=True);p.add_argument("--api-key-env",default="FWCOLLAB_API_KEY");p.add_argument("--api-style",choices=("responses","chat_completions"),default="chat_completions");p.add_argument("--timeout",type=float,default=120.0);p.add_argument("--output");a=p.parse_args();out=a.output or f"eval_private/leaderboard_v1/preflight/{a.model_id.replace('/','_')}.json";sys.argv=[str(ROOT/"scripts/preflight_leaderboard_models.py"),"--endpoint",a.base_url,"--api-key-env",a.api_key_env,"--model-id",a.model_id,"--api-style",a.api_style,"--provider","open_weight_vllm","--timeout",str(a.timeout),"--output",out];runpy.run_path(str(ROOT/"scripts/preflight_leaderboard_models.py"),run_name="__main__")
if __name__=="__main__":main()
