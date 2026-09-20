"""Measurement-only DAG decomposition sensitivity on frozen Phase II traces."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from fwcollab.analysis.phase2 import BOOTSTRAP_SAMPLES, _quantile  # noqa: E402
from fwcollab.symbolic.collaboration_eval import evaluate_collaboration_trace  # noqa: E402
from fwcollab.symbolic.dag import topological_order  # noqa: E402

OUT = ROOT / "artifacts/evaluations/dag_decomposition_sensitivity_v1"
CONDITIONS = ("gpt_selfplay","gemini_selfplay","gpt_no_comm","gemini_no_comm","crossplay_gptF_geminiW","crossplay_geminiF_gptW")
DECOMPOSITIONS = ("Original", "Coarse", "Refined")


def load(path: str | Path) -> Any:
    path = Path(path); path = path if path.is_absolute() else ROOT / path
    return json.loads(path.read_text(encoding="utf-8-sig"))


def auc(progress: Sequence[float]) -> float:
    if len(progress) <= 1: return progress[0] if progress else 0.0
    return sum((progress[i-1]+progress[i])/2 for i in range(1,len(progress)))/(len(progress)-1)


def rank(values: Sequence[float]) -> list[float]:
    indexed=sorted(enumerate(values),key=lambda x:x[1]); result=[0.0]*len(values); i=0
    while i<len(indexed):
        j=i+1
        while j<len(indexed) and indexed[j][1]==indexed[i][1]: j+=1
        value=(i+1+j)/2
        for k in range(i,j): result[indexed[k][0]]=value
        i=j
    return result


def spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left)<3: return None
    a,b=rank(left),rank(right); am,bm=statistics.fmean(a),statistics.fmean(b)
    den=math.sqrt(sum((x-am)**2 for x in a)*sum((y-bm)**2 for y in b))
    return None if not den else sum((x-am)*(y-bm) for x,y in zip(a,b,strict=True))/den


def stage_map(record: Mapping[str, Any], dag: Mapping[str, Any]) -> dict[str,str]:
    state_to_id={str(n["predicate"]["state"]):str(n["id"]) for n in dag["nodes"]}
    result={}
    for stage in record["stages"]:
        label=f"S{stage['stage']}"; aid=str(stage["actuator"]["id"])
        for controller in stage["controllers"]: result[state_to_id[f"{controller['id']}_on"]]=label
        result[state_to_id[f"{aid}_on"]]=label; result[state_to_id[f"{aid}_crossed"]]=label
    motif=str(record["primary_capability"]).lower()
    for suffix in ("engaged","crossed"):
        if f"{motif}_{suffix}" in state_to_id: result[state_to_id[f"{motif}_{suffix}"]]="CAP"
    for node in dag["nodes"]:
        if node["predicate"]["op"]=="team_success": result[str(node["id"])]="GOAL"
    return result


def metrics_for_decompositions(record: Mapping[str,Any], dag: Mapping[str,Any], trace: Mapping[str,Any]) -> dict[str,dict[str,Any]]:
    evaluation=evaluate_collaboration_trace(record,dag,trace)
    order=topological_order(dag); node_by={str(n["id"]):n for n in dag["nodes"]}; group_of=stage_map(record,dag)
    required=[r["node_id"] for r in evaluation["node_results"] if r["required_for_observed_path"] and node_by[r["node_id"]]["predicate"]["op"]!="start"]
    completed_at={r["node_id"]:r["first_completed_at"] for r in evaluation["node_results"]}
    rounds=len(evaluation["timeline"])-1
    original_progress=[float(x["progress"]) for x in evaluation["timeline"]]
    first_original=next((n for n in order if n in required and completed_at[n] is None),None)
    result={"Original":{"dag_completion":evaluation["summary"]["dag_completion"],"dag_progress_auc":evaluation["summary"]["dag_progress_auc"],"failure_stage":group_of.get(first_original) if first_original else None}}

    groups: dict[str,list[str]]=defaultdict(list)
    for node in required: groups[group_of[node]].append(node)
    group_order=sorted(groups,key=lambda g:min(order.index(n) for n in groups[g]))
    group_times={g:(max(completed_at[n] for n in nodes) if all(completed_at[n] is not None for n in nodes) else None) for g,nodes in groups.items()}
    coarse_progress=[sum(group_times[g] is not None and group_times[g] <= r for g in group_order)/len(group_order) for r in range(rounds+1)]
    first_coarse=next((g for g in group_order if group_times[g] is None),None)
    result["Coarse"]={"dag_completion":coarse_progress[-1],"dag_progress_auc":auc(coarse_progress),"failure_stage":first_coarse}

    incoming={n:set() for n in node_by}
    for edge in dag["edges"]: incoming[str(edge["to"])].add(str(edge["from"]))
    refined_units=[]
    for node in required:
        pred_times=[completed_at[p] for p in incoming[node]]
        if not incoming[node]: ready=0
        elif node_by[node].get("join","all")=="any": ready=min((x for x in pred_times if x is not None),default=None)
        else: ready=max(pred_times) if all(x is not None for x in pred_times) else None
        refined_units.extend([(node,"ready",ready),(node,"satisfied",completed_at[node])])
    refined_progress=[sum(time is not None and time<=r for _,_,time in refined_units)/len(refined_units) for r in range(rounds+1)]
    first_refined=next((node for node,_,time in refined_units if time is None),None)
    result["Refined"]={"dag_completion":refined_progress[-1],"dag_progress_auc":auc(refined_progress),"failure_stage":group_of.get(first_refined) if first_refined else None}
    return result


def bootstrap(values: Sequence[float], seed: int) -> dict[str,float|int]:
    rng=random.Random(seed); draws=[statistics.fmean(values[rng.randrange(len(values))] for _ in values) for _ in range(BOOTSTRAP_SAMPLES)]
    return {"estimate":statistics.fmean(values),"ci_low":_quantile(draws,.025),"ci_high":_quantile(draws,.975),"task_count":len(values)}


def main() -> None:
    episode_rows=load("artifacts/evaluations/phase2_v1/per_episode.json")["rows"]
    if len(episode_rows)!=432 or not all(row["replay_verified"] and row["observations_authenticated"] for row in episode_rows): raise RuntimeError("frozen trace gate failed")
    manifest=load("eval_private/spatial_curriculum_full_v4/manifest.json"); records={r["id"]:r for r in manifest["records"]}
    dag_cache={task:load(records[task]["dag"]) for task in {row["task_id"] for row in episode_rows}}
    per_episode=[]
    for i,row in enumerate(episode_rows,1):
        values=metrics_for_decompositions(records[row["task_id"]],dag_cache[row["task_id"]],load(row["trace"]))
        # Original must reproduce frozen values exactly to six decimals.
        for metric in ("dag_completion","dag_progress_auc"):
            if abs(float(values["Original"][metric])-float(row["metrics"][metric]))>1e-6: raise RuntimeError(f"original mismatch {row['trace']} {metric}")
        for decomposition,value in values.items(): per_episode.append({"condition":row["condition"],"replicate":row["replicate"],"task_id":row["task_id"],"outcome":row["outcome"],"decomposition":decomposition,**value})
        if i%72==0: print(f"evaluated {i}/432")
    grouped=defaultdict(list)
    for row in per_episode: grouped[(row["decomposition"],row["condition"],row["task_id"])].append(row)
    task_means=[]
    for (decomp,cond,task),vals in grouped.items():
        task_means.append({"decomposition":decomp,"condition":cond,"task_id":task,"dag_completion":statistics.fmean(v["dag_completion"] for v in vals),"dag_progress_auc":statistics.fmean(v["dag_progress_auc"] for v in vals)})
    condition_rows=[]
    for decomp in DECOMPOSITIONS:
        for cond in CONDITIONS:
            subset=[r for r in task_means if r["decomposition"]==decomp and r["condition"]==cond]
            for metric in ("dag_completion","dag_progress_auc"):
                vals=[r[metric] for r in subset]; condition_rows.append({"decomposition":decomp,"condition":cond,"metric":metric,**bootstrap(vals,20260920^int.from_bytes(hashlib.sha256(f"{decomp}/{cond}/{metric}".encode()).digest()[:8],"big"))})
    effect_rows=[]
    for decomp in DECOMPOSITIONS:
        idx={(r["condition"],r["task_id"]):r for r in task_means if r["decomposition"]==decomp}
        for model,left,right in (("GPT","gpt_selfplay","gpt_no_comm"),("Gemini","gemini_selfplay","gemini_no_comm")):
            for metric in ("dag_completion","dag_progress_auc"):
                vals=[idx[(left,t)][metric]-idx[(right,t)][metric] for t in sorted({r["task_id"] for r in task_means})]
                effect_rows.append({"decomposition":decomp,"model":model,"metric":metric,**bootstrap(vals,20260921^int.from_bytes(hashlib.sha256(f"{decomp}/{model}/{metric}".encode()).digest()[:8],"big")),"sign":0 if abs(statistics.fmean(vals))<1e-12 else (1 if statistics.fmean(vals)>0 else -1)})
    rankings=[]
    for decomp in DECOMPOSITIONS:
        for metric in ("dag_completion","dag_progress_auc"):
            means={r["condition"]:r["estimate"] for r in condition_rows if r["decomposition"]==decomp and r["metric"]==metric}
            rankings.append({"decomposition":decomp,"metric":metric,"condition_order":sorted(means,key=lambda c:(-means[c],c)),"gpt_vs_gemini_selfplay":"GPT>Gemini" if means["gpt_selfplay"]>means["gemini_selfplay"] else ("GPT<Gemini" if means["gpt_selfplay"]<means["gemini_selfplay"] else "tie")})
    correlations=[]
    for metric in ("dag_completion","dag_progress_auc"):
        for left,right in (("Original","Coarse"),("Original","Refined"),("Coarse","Refined")):
            li={(r["condition"],r["replicate"],r["task_id"]):r[metric] for r in per_episode if r["decomposition"]==left}; ri={(r["condition"],r["replicate"],r["task_id"]):r[metric] for r in per_episode if r["decomposition"]==right}; keys=sorted(li)
            correlations.append({"metric":metric,"left":left,"right":right,"spearman_rho":spearman([li[k] for k in keys],[ri[k] for k in keys]),"episodes":len(keys)})
    failures=[r for r in per_episode if r["outcome"]!="team_success"]
    failure_agreement=[]
    for left,right in (("Original","Coarse"),("Original","Refined"),("Coarse","Refined")):
        li={(r["condition"],r["replicate"],r["task_id"]):r["failure_stage"] for r in failures if r["decomposition"]==left};ri={(r["condition"],r["replicate"],r["task_id"]):r["failure_stage"] for r in failures if r["decomposition"]==right};keys=sorted(li);failure_agreement.append({"left":left,"right":right,"agree":sum(li[k]==ri[k] for k in keys),"eligible":len(keys),"agreement_rate":sum(li[k]==ri[k] for k in keys)/len(keys)})
    effect_sign_stable=all(len({r["sign"] for r in effect_rows if r["model"]==model and r["metric"]==metric})==1 for model in ("GPT","Gemini") for metric in ("dag_completion","dag_progress_auc"))
    model_rank_stable=all(len({r["gpt_vs_gemini_selfplay"] for r in rankings if r["metric"]==metric})==1 for metric in ("dag_completion","dag_progress_auc"))
    condition_rank_stable=all(len({tuple(r["condition_order"]) for r in rankings if r["metric"]==metric})==1 for metric in ("dag_completion","dag_progress_auc"))
    result={"format":"fwcollab.dag_decomposition_sensitivity.v1","model_api_calls":0,"input_episodes":432,"input_mutation":False,"environment_success_unchanged":True,"definitions":{"Original":"frozen native bound predicate nodes","Coarse":"all controller, actuator, and crossing nodes within one declared stage collapse to one stage milestone; capability and goal are separate stages","Refined":"each original required node is represented by a dependency-ready milestone and a predicate-satisfied milestone"},"condition_means":condition_rows,"paired_effects":effect_rows,"rankings":rankings,"condition_ranking_stable":condition_rank_stable,"model_ranking_stable":model_rank_stable,"paired_effect_sign_stable":effect_sign_stable,"correlations":correlations,"failure_stage_agreement":failure_agreement}
    OUT.mkdir(parents=True,exist_ok=True);(OUT/"results.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    with (OUT/"per_episode.csv").open("w",encoding="utf-8",newline="") as h: w=csv.DictWriter(h,fieldnames=tuple(per_episode[0]));w.writeheader();w.writerows(per_episode)
    lines=["# DAG decomposition sensitivity","","Status: **complete measurement-only robustness analysis**. Model/API calls: **0**. All 432 replay-authenticated Phase II traces are reused; environment trajectories and task success are unchanged. This is decomposition robustness, not human construct validation.","","## Decompositions","","- **Original:** frozen executable predicate nodes.","- **Coarse:** each declared controller→actuator→crossing stage is one completed milestone; capability and terminal goal remain separate.","- **Refined:** each required original node contributes a dependency-ready and predicate-satisfied milestone.","","## Condition means","","| Decomposition | Condition | Completion | AUC |","|---|---|---:|---:|"]
    for d in DECOMPOSITIONS:
        for c in CONDITIONS:
            x={r["metric"]:r for r in condition_rows if r["decomposition"]==d and r["condition"]==c};lines.append(f"| {d} | {c} | {x['dag_completion']['estimate']:.6f} | {x['dag_progress_auc']['estimate']:.6f} |")
    lines += ["","## Communication effects (Self - NoComm)","","| Decomposition | Model | Metric | Effect [95% CI] |","|---|---|---|---:|"]
    for r in effect_rows: lines.append(f"| {r['decomposition']} | {r['model']} | {r['metric']} | {r['estimate']:.6f} [{r['ci_low']:.6f},{r['ci_high']:.6f}] |")
    lines += ["",f"- Exact six-condition ranking stable: **{condition_rank_stable}**",f"- GPT-vs-Gemini self-play ordering stable: **{model_rank_stable}**",f"- Communication-effect signs stable: **{effect_sign_stable}**","","## Cross-decomposition correlations","","| Metric | Pair | Spearman rho | Episodes |","|---|---|---:|---:|"]
    for r in correlations:lines.append(f"| {r['metric']} | {r['left']} vs {r['right']} | {r['spearman_rho']:.6f} | {r['episodes']} |")
    lines += ["","## Failure localization at declared-stage level","","| Pair | Agreement |","|---|---:|"]
    for r in failure_agreement:lines.append(f"| {r['left']} vs {r['right']} | {r['agree']}/{r['eligible']} ({r['agreement_rate']:.3%}) |")
    lines += ["","Absolute Completion/AUC values vary with measurement granularity by construction; ranking, sign, correlation, and stage-level localization are the robustness targets.",""]
    (OUT/"REPORT.md").write_text("\n".join(lines),encoding="utf-8")
    print(json.dumps({"episodes":432,"condition_ranking_stable":condition_rank_stable,"model_ranking_stable":model_rank_stable,"effect_sign_stable":effect_sign_stable,"model_api_calls":0}))

if __name__=="__main__":main()
