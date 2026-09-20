"""Create integrity/accounting tables for completed leaderboard traces."""
from __future__ import annotations
import argparse,csv,json,statistics,sys
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from fwcollab.symbolic.dag import load_json,save_json  # noqa:E402
from fwcollab.unified_eval import evaluate_fwcollab_task,validate_unified_evaluation  # noqa:E402
def write_csv(path,rows):
 with path.open("w",encoding="utf-8",newline="") as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ["empty"]);w.writeheader();w.writerows(rows)
def main():
 p=argparse.ArgumentParser();p.add_argument("--run-root",default="artifacts/runs/leaderboard_v1");p.add_argument("--output-dir",default="artifacts/evaluations/leaderboard_v1");a=p.parse_args();run=ROOT/a.run_root;out=ROOT/a.output_dir;out.mkdir(parents=True,exist_ok=True);episodes=[]
 bench=load_json(ROOT/"eval_private/benchmark_v2/manifest.json");by={x["task_id"]:x for x in bench["tasks"]}
 for path in sorted(run.rglob("*.json")):
  if path.name=="run_state.json":continue
  try:tr=load_json(path);meta=tr["leaderboard_v1"]
  except (KeyError,ValueError,json.JSONDecodeError):continue
  ev=evaluate_fwcollab_task(by[tr["map_id"]],tr,root=ROOT);validate_unified_evaluation(ev)
  acc=meta.get("provider_accounting",{})
  episodes.append({"model_id":meta["model_id"],"resolved_model_ids":";".join(meta.get("observed_resolved_model_ids",[])),"replicate":meta["replicate"],"task_id":tr["map_id"],"track":meta["suite"],"outcome":tr["outcome"],"success":int(ev["success"]),"dag_completion":ev["dag_completion"],"progress_auc":ev["progress_auc"],"rounds":ev["rounds"],**{k:int(acc.get(k,0)) for k in ("provider_requests","retries","model_behavior_errors","infrastructure_errors","interface_errors","wait_substitutions")},"trace":path.relative_to(ROOT).as_posix()})
 groups=defaultdict(list)
 for r in episodes:groups[(r["model_id"],r["task_id"],r["track"])].append(r)
 tasks=[]
 for (model,task,track),rs in sorted(groups.items()):tasks.append({"model_id":model,"task_id":task,"track":track,"replicates":len(rs),"success_rate":statistics.fmean(r["success"] for r in rs),"mean_dag_completion":statistics.fmean(r["dag_completion"] for r in rs),"mean_progress_auc":statistics.fmean(r["progress_auc"] for r in rs)})
 model_groups=defaultdict(list)
 for r in episodes:model_groups[r["model_id"]].append(r)
 models=[]
 for model,rs in sorted(model_groups.items()):models.append({"model_id":model,"episodes":len(rs),"replicates":len({r["replicate"] for r in rs}),"provider_requests":sum(r["provider_requests"] for r in rs),"provider_errors":sum(r["model_behavior_errors"]+r["infrastructure_errors"]+r["interface_errors"] for r in rs)})
 for name,rows in (("per_episode",episodes),("per_task",tasks),("per_model",models)):save_json(out/(name+".json"),rows);write_csv(out/(name+".csv"),rows)
 errors={"status_counts":dict(Counter(r["outcome"] for r in episodes)),"provider_requests":sum(r["provider_requests"] for r in episodes),"model_behavior_errors":sum(r["model_behavior_errors"] for r in episodes),"infrastructure_errors":sum(r["infrastructure_errors"] for r in episodes),"interface_errors":sum(r["interface_errors"] for r in episodes)};cost={"currency_cost":None,"note":"No provider price table is frozen; token and request accounting only.","models":models};summary={"format":"fwcollab.leaderboard.summary.v1","episodes":len(episodes),"models":len(models),"performance_interpretation":"deferred"};save_json(out/"provider_error_report.json",errors);save_json(out/"cost_accounting.json",cost);save_json(out/"summary.json",summary);print(json.dumps(summary));return 0
if __name__=="__main__":raise SystemExit(main())
