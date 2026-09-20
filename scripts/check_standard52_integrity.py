"""Replay, evaluate, and integrity-check one Standard-52 run directory."""
from __future__ import annotations
import argparse,csv,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from fwcollab.symbolic.dag import load_json,save_json  # noqa:E402
from fwcollab.symbolic.map import load_symbol_map  # noqa:E402
from fwcollab.symbolic.runner import replay_trace  # noqa:E402
from fwcollab.unified_eval import evaluate_fwcollab_task,validate_unified_evaluation  # noqa:E402
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def main()->int:
 p=argparse.ArgumentParser();p.add_argument("--run-dir",required=True);p.add_argument("--output-dir");a=p.parse_args();run=ROOT/a.run_dir;state=load_json(run/"run_state.json");manifest=load_json(ROOT/"eval_private/leaderboard_v1/standard_52_run_manifest.json");bench=load_json(ROOT/"eval_private/benchmark_v2/manifest.json");by={x["task_id"]:x for x in bench["tasks"]};rows=[];errors=[]
 for task_id in manifest["ordered_task_ids"]:
  found=list(run.rglob(task_id+".json"))
  if len(found)!=1:errors.append({"task_id":task_id,"error":f"trace_count={len(found)}"});continue
  path=found[0];trace=load_json(path);task=by[task_id]
  try:replay=replay_trace(load_symbol_map(ROOT/task["map_path"]),trace);ev=evaluate_fwcollab_task(task,trace,root=ROOT);validate_unified_evaluation(ev);assert replay["ok"]
  except Exception as exc:errors.append({"task_id":task_id,"error":f"{type(exc).__name__}: {exc}"});continue
  rows.append({"task_id":task_id,"suite":task["suite_name"],"outcome":trace["outcome"],"success":ev["success"],"dag_completion":ev["dag_completion"],"progress_auc":ev["progress_auc"],"rounds":ev["rounds"],"replay_valid":True,"evaluator_valid":True,"trace":path.relative_to(ROOT).as_posix(),"trace_sha256":sha(path)})
 ok=len(rows)==52 and not errors and state.get("expected_episodes")==52 and state.get("recorded_keys")==52 and state.get("status_counts")=={"completed":52};out=ROOT/(a.output_dir or str(run).replace("artifacts\\runs","artifacts\\evaluations"));out.mkdir(parents=True,exist_ok=True);report={"format":"fwcollab.leaderboard.integrity.v1","pass":ok,"expected":52,"valid":len(rows),"errors":errors,"run_state_sha256":sha(run/"run_state.json")};save_json(out/"integrity_report.json",report);save_json(out/"per_episode.json",rows)
 with (out/"per_episode.csv").open("w",encoding="utf-8",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ["task_id"]);w.writeheader();w.writerows(rows)
 print(json.dumps(report));return 0 if ok else 2
if __name__=="__main__":raise SystemExit(main())
