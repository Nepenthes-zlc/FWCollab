"""Offline standalone integrity check for the portable runner bundle."""
from __future__ import annotations
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from fwcollab.symbolic.map import load_symbol_map  # noqa:E402
from fwcollab.unified_eval import SUPPORTED_EVALUATORS  # noqa:E402
def load(r):return json.loads((ROOT/r).read_text(encoding="utf-8-sig"))
def sha(r):return hashlib.sha256((ROOT/r).read_bytes()).hexdigest()
def main():
 run=load("eval_private/leaderboard_v1/standard_52_run_manifest.json");bench=load("eval_private/benchmark_v2/manifest.json");by={x["task_id"]:x for x in bench["tasks"]};ids=run["ordered_task_ids"];missing=[];hash_bad=[]
 for task_id in ids:
  t=by[task_id]
  for key in ("map_path","dag_path","witness_path","source_manifest_path"):
   if not (ROOT/t[key]).is_file():missing.append(t[key])
  for key,hash_key in (("map_path","map_sha256"),("dag_path","dag_sha256"),("witness_path","witness_source_sha256")):
   if (ROOT/t[key]).is_file() and sha(t[key])!=t["hashes"][hash_key]:hash_bad.append(f"{task_id}:{key}")
  load_symbol_map(ROOT/t["map_path"]);assert t["evaluator_type"] in SUPPORTED_EVALUATORS
 expected=run["prompt_contract"]["sha256"];pairs=[{"path":x["path"],"sha256":sha(x["path"])} for x in run["prompt_contract"]["components"]];actual=hashlib.sha256(json.dumps(pairs,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
 expected_freezes={"eval_private/benchmark_v1/freeze_manifest.json":"f375d465e798e1bdc8161f9662565768299d488c34dc3783c774d22e2847799f","eval_private/benchmark_v2/freeze_manifest.json":"e042ad324212b317a0d6616d9f1b0389bba4dc311f6da6f070920ec58aea1481","eval_private/c5_information_12/freeze_manifest.json":"743353e67bc6ad63646199212d2874e3f6d65a7657a766841ac9f08f1475b3e3","eval_private/synchronize_8/freeze_manifest.json":"31ff5bb16bfa68039de19ef3ef0b281b24bf7a84df4f65f096914fd570d19f4c","eval_private/parallel_join_8/freeze_manifest.json":"f00aee35a7d37e8c5676d5db9ff50190dd430262d5fe27d5f4ef889bddc59d4c"};freeze_bad=[p for p,h in expected_freezes.items() if not (ROOT/p).is_file() or sha(p)!=h]
 result={"pass":len(ids)==len(set(ids))==52 and not missing and not hash_bad and actual==expected and not freeze_bad,"standard52_refs_valid":52-len({x for x in ids if any(not (ROOT/by[x][k]).is_file() for k in ("map_path","dag_path","witness_path","source_manifest_path"))}),"standard52_asset_hash_mismatches":hash_bad,"prompt_contract_hash":actual,"prompt_contract_match":actual==expected,"freeze_manifest_hash_mismatches":freeze_bad,"evaluator_dispatch_types":sorted(SUPPORTED_EVALUATORS),"model_api_calls":0}
 print(json.dumps(result,indent=2));return 0 if result["pass"] else 2
if __name__=="__main__":raise SystemExit(main())
