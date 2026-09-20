"""Run one formal Standard-52 homogeneous-self-play model replicate."""

from __future__ import annotations

import argparse, hashlib, json, os, sys, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"src"))
from fwcollab.c5 import run_c5_session  # noqa:E402
from fwcollab.leaderboard_runtime import InterfaceUnavailableAbort, LeaderboardPolicy, UnscoredInfrastructureAbort  # noqa:E402
from fwcollab.symbolic.dag import load_json,save_json  # noqa:E402
from fwcollab.symbolic.map import load_symbol_map  # noqa:E402
from fwcollab.symbolic.runner import DualAgentSession,replay_trace,save_trace  # noqa:E402
from fwcollab.unified_eval import evaluate_fwcollab_task,validate_unified_evaluation  # noqa:E402

BUDGETS={"Core-72":200,"Information-12":30,"Sync-8":80,"Join-8":80}

def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def slug(v:str)->str:return "".join(c if c.isalnum() or c in "-_." else "_" for c in v)
def source_record(task:dict[str,Any])->dict[str,Any]:
    m=load_json(ROOT/task["source_manifest_path"]); rows=m["tasks"] if task["suite_name"]=="Information-12" else m["records"]; return rows[int(task["source_record_index"])]
def counters(policies:dict[str,LeaderboardPolicy])->dict[str,Any]:
    values=[p.counters.as_dict() for p in policies.values()]; keys=("provider_requests","retries","model_behavior_errors","infrastructure_errors","interface_errors","wait_substitutions")
    return {**{k:sum(int(v[k]) for v in values) for k in keys},"resolved_model_ids":sorted({x for v in values for x in v["resolved_model_ids"]}),"error_types":dict(Counter(x for v in values for x,n in v["error_types"].items() for _ in range(n)))}
def validate_existing(path:Path,task:dict[str,Any],model:str,replicate:int)->dict[str,Any]|None:
    if not path.is_file():return None
    trace=load_json(path); meta=trace.get("leaderboard_v1",{})
    if meta.get("model_id")!=model or meta.get("replicate")!=replicate: raise RuntimeError(f"existing trace key mismatch: {path}")
    replay=replay_trace(load_symbol_map(ROOT/task["map_path"]),trace)
    ev=evaluate_fwcollab_task(task,trace,root=ROOT); validate_unified_evaluation(ev)
    if not replay["ok"]: raise RuntimeError(f"existing completed trace fails replay: {path}")
    return {"task_id":task["task_id"],"suite":task["suite_name"],"status":"completed","outcome":trace["outcome"],"rounds":len(trace["rounds"]),"trace":path.relative_to(ROOT).as_posix(),"trace_sha256":sha(path),"evaluation":ev,"provider":meta.get("provider_accounting",{}),"resumed":True}
def run_one(*,task:dict[str,Any],entry:dict[str,Any],replicate:int,seed:int,endpoint:str,api_key:str|None,run_root:Path)->dict[str,Any]:
    path=run_root/task["suite_name"]/f"{task['task_id']}.json"; existing=validate_existing(path,task,entry["model_id"],replicate)
    if existing:return existing
    started=time.perf_counter(); common={"endpoint":endpoint,"model":entry["model_id"],"api_style":entry["api_style"],"api_key":api_key,"reasoning_setting":entry["reasoning_setting"],"temperature":entry.get("temperature"),"seed":seed if entry.get("seed_behavior")!="unsupported_omitted" else None,"max_output_tokens":int(entry["max_output_tokens"]),"timeout_seconds":120.0}
    policies={r:LeaderboardPolicy(**common) for r in ("F","W")}
    try:
        mp=load_symbol_map(ROOT/task["map_path"]); budget=BUDGETS[task["suite_name"]]
        result=run_c5_session(mp,policies,source_record(task),max_rounds=budget) if task["suite_name"]=="Information-12" else DualAgentSession(mp,policies,max_rounds=budget,planning_rounds=0,coordination_mode="emergent").run()
        trace=result.trace; account=counters(policies); trace["leaderboard_v1"]={"format":"fwcollab.leaderboard.episode.v1","formal":True,"model_id":entry["model_id"],"matrix_resolved_model_id":entry["resolved_model_id"],"observed_resolved_model_ids":account["resolved_model_ids"],"replicate":replicate,"request_seed":seed,"suite":task["suite_name"],"round_budget":budget,"prompt_contract_hash":entry["prompt_contract_hash"],"standard52_manifest_hash":entry["standard52_manifest_hash"],"provider_accounting":account}
        replay=replay_trace(mp,trace); ev=evaluate_fwcollab_task(task,trace,root=ROOT);validate_unified_evaluation(ev)
        if not replay["ok"]:raise RuntimeError("new trace failed deterministic replay")
        path.parent.mkdir(parents=True,exist_ok=True);save_trace(path,trace)
        return {"task_id":task["task_id"],"suite":task["suite_name"],"status":"completed","outcome":trace["outcome"],"rounds":len(trace["rounds"]),"trace":path.relative_to(ROOT).as_posix(),"trace_sha256":sha(path),"evaluation":ev,"provider":account,"wall_time_ms":round((time.perf_counter()-started)*1000),"resumed":False}
    except UnscoredInfrastructureAbort as exc:
        return {"task_id":task["task_id"],"suite":task["suite_name"],"status":"infrastructure_error","error_type":exc.error_type,"provider":counters(policies),"wall_time_ms":round((time.perf_counter()-started)*1000),"rerunnable":True}
    except InterfaceUnavailableAbort as exc:
        return {"task_id":task["task_id"],"suite":task["suite_name"],"status":"interface_unavailable","error_type":exc.error_type,"provider":counters(policies),"wall_time_ms":round((time.perf_counter()-started)*1000),"stop_model_entry":True}
    except Exception as exc:
        return {"task_id":task["task_id"],"suite":task["suite_name"],"status":"runner_error","error":f"{type(exc).__name__}: {exc}","provider":counters(policies),"wall_time_ms":round((time.perf_counter()-started)*1000)}
def write_state(path:Path,model:str,rep:int,expected:int,rows:list[dict[str,Any]])->None:
    ordered=sorted(rows,key=lambda r:r["order"]); counts=Counter(r["status"] for r in ordered); save_json(path,{"format":"fwcollab.leaderboard.run_state.v1","model_id":model,"replicate":rep,"expected_episodes":expected,"recorded_keys":len(ordered),"status_counts":dict(counts),"results":ordered})
def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--matrix",default="eval_private/leaderboard_v1/closed4_model_matrix.json");p.add_argument("--model-id",required=True);p.add_argument("--replicate",required=True,type=int,choices=(1,2,3));p.add_argument("--base-url");p.add_argument("--api-key-env",default="FWCOLLAB_API_KEY");p.add_argument("--output-dir",default="artifacts/runs/leaderboard_v1");p.add_argument("--concurrency",type=int);a=p.parse_args()
    matrix_path=ROOT/a.matrix;matrix=load_json(matrix_path);entries=[x for x in matrix["models"] if x["model_id"]==a.model_id]
    if len(entries)!=1 or matrix.get("status")!="FROZEN":raise SystemExit("model absent or matrix not frozen")
    entry=entries[0];endpoint=a.base_url or entry["endpoint_identity"];concurrency=a.concurrency or int(entry["concurrency"]);manifest=load_json(ROOT/"eval_private/leaderboard_v1/standard_52_run_manifest.json");benchmark=load_json(ROOT/"eval_private/benchmark_v2/manifest.json");by={x["task_id"]:x for x in benchmark["tasks"]};tasks=[by[x] for x in manifest["ordered_task_ids"]]
    if sha(ROOT/"eval_private/leaderboard_v1/standard_52_run_manifest.json")!=entry["standard52_manifest_hash"]:raise SystemExit("Standard-52 manifest hash mismatch")
    api_key=os.environ.get(a.api_key_env);run_root=ROOT/a.output_dir/slug(a.model_id)/f"rep{a.replicate}";run_root.mkdir(parents=True,exist_ok=True);state=run_root/"run_state.json";prior=load_json(state)["results"] if state.is_file() else [];keep={r["task_id"]:r for r in prior if r.get("status")=="completed"};rows=list(keep.values());seed=int(manifest["request_seeds"][str(a.replicate)]);pending=[(i,t) for i,t in enumerate(tasks,1) if t["task_id"] not in keep]
    with ThreadPoolExecutor(max_workers=concurrency,thread_name_prefix="standard52") as pool:
        futures={pool.submit(run_one,task=t,entry=entry,replicate=a.replicate,seed=seed,endpoint=endpoint,api_key=api_key,run_root=run_root):(i,t) for i,t in pending}
        for f in as_completed(futures):
            i,t=futures[f];row=f.result();row["order"]=i;rows=[x for x in rows if x["task_id"]!=row["task_id"]]+[row];write_state(state,a.model_id,a.replicate,52,rows);print(f"[{len(rows):02d}/52] {t['task_id']} {row['status']} {row.get('outcome','')}",flush=True)
            if row["status"] in {"interface_unavailable","runner_error"}:
                for other in futures:
                    if other is not f:other.cancel()
                break
    final=load_json(state);completed=sum(r["status"]=="completed" for r in final["results"]);ok=completed==52 and set(final["status_counts"])=={"completed"};print(json.dumps({"model":a.model_id,"replicate":a.replicate,"completed":completed,"integrity_pass":ok}));return 0 if ok else 2
if __name__=="__main__":raise SystemExit(main())
