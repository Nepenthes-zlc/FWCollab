"""Replay-authenticated paired analysis of the frozen Held-out Layout-12 run."""
from __future__ import annotations
import hashlib,json,random,statistics,sys,csv
from collections import defaultdict
from pathlib import Path
from typing import Any,Mapping,Sequence
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from fwcollab.analysis.phase2 import BOOTSTRAP_SAMPLES,_quantile  # noqa:E402
from fwcollab.symbolic.collaboration_eval import evaluate_collaboration_trace  # noqa:E402
from fwcollab.symbolic.map import load_symbol_map  # noqa:E402
from fwcollab.symbolic.runner import replay_trace  # noqa:E402
OUT=ROOT/"artifacts/evaluations/heldout_layout_v1"
METRICS=("success_rate","dag_completion","dag_progress_auc","clean_handoff_rate","coordination_violation_rate")

def load(path):path=Path(path);path=path if path.is_absolute() else ROOT/path;return json.loads(path.read_text(encoding="utf-8-sig"))
def verify_freeze():
    freeze=load("eval_private/heldout_layout_12/freeze_manifest.json");bad=[]
    for item in freeze["files"]:
        p=ROOT/item["path"];data=p.read_bytes()
        if len(data)!=item["bytes"] or hashlib.sha256(data).hexdigest()!=item["sha256"]:bad.append(item["path"])
    if bad:raise RuntimeError(f"heldout freeze mismatch {bad[:5]}")
    return hashlib.sha256((ROOT/"eval_private/heldout_layout_12/freeze_manifest.json").read_bytes()).hexdigest()
def boot(values:Sequence[float],key:str):
    rng=random.Random(20260920^int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],"big"));draws=[statistics.fmean(values[rng.randrange(len(values))] for _ in values) for _ in range(BOOTSTRAP_SAMPLES)]
    return {"estimate":statistics.fmean(values),"ci_low":_quantile(draws,.025),"ci_high":_quantile(draws,.975),"task_count":len(values)}
def main():
    freeze_hash=verify_freeze();manifest=load("eval_private/heldout_layout_12/manifest.json");held_by={r["id"]:r for r in manifest["records"]};source_manifest=load("eval_private/spatial_curriculum_full_v4/manifest.json");source_by={r["id"]:r for r in source_manifest["records"]};run=load("artifacts/runs/heldout_layout_v1/run_state.json")
    if run["recorded_episodes"]!=72 or len(run["results"])!=72:raise RuntimeError("heldout run incomplete")
    held=[]
    for item in run["results"]:
        trace=load(item["trace"]);record=held_by[item["task_id"]];symbol_map=load_symbol_map(ROOT/record["map"]);replay=replay_trace(symbol_map,trace)
        if not replay["ok"]:raise RuntimeError(f"replay failed {item['trace']}")
        evaluation=evaluate_collaboration_trace(record,load(record["dag"]),trace);s=evaluation["summary"]
        held.append({"condition":item["condition"],"replicate":item["replicate"],"heldout_id":item["task_id"],"source_id":record["source_id"],"trace":item["trace"],"trace_sha256":hashlib.sha256((ROOT/item["trace"]).read_bytes()).hexdigest(),"replay_verified":True,"success_rate":float(trace["outcome"]=="team_success"),"dag_completion":float(s["dag_completion"]),"dag_progress_auc":float(s["dag_progress_auc"]),"clean_handoff_rate":s["clean_handoff_rate"],"coordination_violation_rate":float(s["coordination_violations"])/max(1,len(trace["rounds"]))})
    phase=load("artifacts/evaluations/phase2_v1/per_episode.json")["rows"];source_ids={r["source_id"] for r in held_by.values()};seen=[r for r in phase if r["condition"] in {"gpt_selfplay","gemini_selfplay"} and r["task_id"] in source_ids]
    if len(seen)!=72 or not all(r["replay_verified"] and r["observations_authenticated"] for r in seen):raise RuntimeError("seen reference incomplete")
    held_index={(r["condition"],int(r["replicate"]),r["source_id"]):r for r in held};seen_index={(r["condition"],int(r["replicate"]),r["task_id"]):r for r in seen}
    if set(held_index)!=set(seen_index):raise RuntimeError("seen/heldout keys not paired")
    task_values=[];condition_rows=[];effect_rows=[]
    for condition in ("gpt_selfplay","gemini_selfplay"):
        for source_id in sorted(source_ids):
            values={}
            for layout,index in (("seen",seen_index),("heldout",held_index)):
                for metric in METRICS:
                    vals=[]
                    for replicate in (1,2,3):
                        row=index[(condition,replicate,source_id)];value=row["metrics"][metric] if layout=="seen" else row[metric]
                        if value is not None:vals.append(float(value))
                    values[(layout,metric)]=statistics.fmean(vals) if vals else None
            for metric in METRICS:
                left,right=values[("seen",metric)],values[("heldout",metric)];task_values.append({"condition":condition,"source_id":source_id,"metric":metric,"seen":left,"heldout":right,"seen_minus_heldout":None if left is None or right is None else left-right})
        for layout in ("seen","heldout"):
            for metric in METRICS:
                vals=[r[layout] for r in task_values if r["condition"]==condition and r["metric"]==metric and r[layout] is not None];condition_rows.append({"condition":condition,"layout":layout,"metric":metric,**boot(vals,f"condition/{condition}/{layout}/{metric}")})
        for metric in METRICS:
            vals=[r["seen_minus_heldout"] for r in task_values if r["condition"]==condition and r["metric"]==metric and r["seen_minus_heldout"] is not None];effect_rows.append({"condition":condition,"metric":metric,"direction":"seen_minus_heldout",**boot(vals,f"effect/{condition}/{metric}")})
    result={"format":"fwcollab.heldout_layout_analysis.v1","status":"formal_offline_analysis","model_api_calls":0,"new_episodes":0,"freeze_manifest_sha256":freeze_hash,"heldout_episodes":72,"seen_reference_episodes":72,"replay_verified_heldout":sum(r["replay_verified"] for r in held),"paired_episode_keys":len(held_index),"condition_means":condition_rows,"paired_effects":effect_rows,"task_values":task_values}
    OUT.mkdir(parents=True,exist_ok=True);(OUT/"results.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    with (OUT/"heldout_per_episode.csv").open("w",encoding="utf-8",newline="") as h:w=csv.DictWriter(h,fieldnames=tuple(held[0]));w.writeheader();w.writerows(held)
    lines=["# Held-out Layout-12 formal analysis","","Status: **complete replay-authenticated paired result**. Model/API calls: **0**; new episodes: **0**. All 72 existing held-out traces replay successfully and pair exactly with 72 frozen Phase II seen-layout references by source task, condition, and replicate.","","Scope: same executable DAG, mechanisms, roles, model condition, and aligned replicate; unseen spatial layout only. This does not test new DAGs, mechanisms, partners, or information structures. Replicates are averaged within each of 12 task pairs before a 10,000-draw task bootstrap.","","| Model | Metric | Seen | Held-out | Seen - held-out [95% CI] | Tasks |","|---|---|---:|---:|---:|---:|"]
    for condition in ("gpt_selfplay","gemini_selfplay"):
        for metric in METRICS:
            idx={(r["layout"],r["metric"]):r for r in condition_rows if r["condition"]==condition};effect=next(r for r in effect_rows if r["condition"]==condition and r["metric"]==metric);lines.append(f"| {condition} | {metric} | {idx[('seen',metric)]['estimate']:.6f} | {idx[('heldout',metric)]['estimate']:.6f} | {effect['estimate']:.6f} [{effect['ci_low']:.6f},{effect['ci_high']:.6f}] | {effect['task_count']} |")
    lines += ["","Clean Handoff is averaged only over task pairs with defined opportunities in both layouts; N/A is never converted to zero. Violation effects use the frozen Phase II rate per executed environment round.",""]
    (OUT/"REPORT.md").write_text("\n".join(lines),encoding="utf-8")
    print(json.dumps({"heldout_replay":len(held),"paired_keys":len(held_index),"formal_result":True,"model_api_calls":0}))
if __name__=="__main__":main()
