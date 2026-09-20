"""Descriptive structured-message behavior from frozen Phase II self-play traces."""

from __future__ import annotations
import csv,json,statistics
from collections import Counter,defaultdict
from pathlib import Path
from typing import Any,Mapping
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/"artifacts/evaluations/message_behavior_v1"
STATUSES=("INFO","READY","HOLDING","CROSSED","RELEASE","BLOCKED")

def load(path):return json.loads((ROOT/path).read_text(encoding="utf-8-sig"))
def decision(round_record,role):return round_record.get("agents",{}).get(role,{}).get("decision",{})
def message(round_record,role):
    value=decision(round_record,role).get("message")
    return value if isinstance(value,Mapping) and str(value.get("status")) in STATUSES else None
def move(round_record,role):return str(decision(round_record,role).get("action",{}).get("move","WAIT"))
def moved(round_record,role):return move(round_record,role)!="WAIT" and str(round_record.get("agents",{}).get(role,{}).get("feedback"))=="moved"
def mean(values):return statistics.fmean(values) if values else None
def latency_to(rounds,start,role,predicate):
    for index in range(start+1,len(rounds)):
        if predicate(rounds[index],role):return index-start
    return None

def episode_metrics(row):
    trace=load(Path(row["trace"]));rounds=trace["rounds"];events=[];last_by_role={};repeated=0;consecutive=0;after_wait=[];ready_latency=[];release_latency=[];blocked_recovery=[]
    round_has=[]
    for i,r in enumerate(rounds):
        has=False
        for role in ("F","W"):
            msg=message(r,role)
            if msg is None:continue
            has=True;status=str(msg["status"]);stage=str(msg.get("stage",""));events.append((i,role,status,stage))
            previous=last_by_role.get(role)
            repeated+=bool(previous and previous[2:]==(status,stage))
            consecutive+=bool(previous and previous[0]==i-1)
            last_by_role[role]=(i,role,status,stage)
            if i+1<len(rounds):after_wait.append(move(rounds[i+1],role)=="WAIT")
            partner="W" if role=="F" else "F"
            if status in {"READY","HOLDING"}:
                value=latency_to(rounds,i,partner,lambda rr,p:moved(rr,p))
                if value is not None:ready_latency.append(value)
            if status=="CROSSED":
                value=None
                for j in range(i+1,len(rounds)):
                    candidate=message(rounds[j],partner)
                    if candidate and candidate.get("status")=="RELEASE" and (not stage or not candidate.get("stage") or str(candidate.get("stage"))==stage):value=j-i;break
                if value is not None:release_latency.append(value)
            if status=="BLOCKED":
                blocked_recovery.append(any(moved(rounds[j],role) for j in range(i+1,min(len(rounds),i+4))))
        round_has.append(has)
    bursts=[];i=0
    while i<len(round_has):
        if not round_has[i]:i+=1;continue
        j=i+1
        while j<len(round_has) and round_has[j]:j+=1
        bursts.append(j-i);i=j
    counts=Counter(status for _,_,status,_ in events);n=len(events)
    return {"model":"GPT" if row["condition"]=="gpt_selfplay" else "Gemini","condition":row["condition"],"replicate":row["replicate"],"task_id":row["task_id"],"success_group":"success" if row["outcome"]=="team_success" else "failure","rounds":len(rounds),"messages":n,**{f"count_{s}":counts[s] for s in STATUSES},"repeated_confirmations":repeated,"consecutive_messages":consecutive,"next_round_wait_events":sum(after_wait),"next_round_wait_opportunities":len(after_wait),"ready_holding_latency_sum":sum(ready_latency),"ready_holding_latency_n":len(ready_latency),"crossed_release_latency_sum":sum(release_latency),"crossed_release_latency_n":len(release_latency),"blocked_recoveries":sum(blocked_recovery),"blocked_events":len(blocked_recovery),"burst_rounds_total":sum(bursts),"burst_count":len(bursts)}

def aggregate(rows):
    episodes=len(rows);messages=sum(r["messages"] for r in rows);rounds=sum(r["rounds"] for r in rows)
    result={"episodes":episodes,"messages":messages,"rounds":rounds,"messages_per_episode":messages/episodes,"messages_per_100_rounds":100*messages/rounds}
    for status in STATUSES:result[f"proportion_{status}"]=sum(r[f"count_{status}"] for r in rows)/messages if messages else None
    result["repeated_confirmation_rate"]=sum(r["repeated_confirmations"] for r in rows)/messages if messages else None
    result["consecutive_message_rate"]=sum(r["consecutive_messages"] for r in rows)/messages if messages else None
    waits=sum(r["next_round_wait_opportunities"] for r in rows);result["next_round_wait_after_message"]=sum(r["next_round_wait_events"] for r in rows)/waits if waits else None
    n=sum(r["ready_holding_latency_n"] for r in rows);result["ready_holding_to_partner_move_mean_latency"]=sum(r["ready_holding_latency_sum"] for r in rows)/n if n else None;result["ready_holding_latency_n"]=n
    n=sum(r["crossed_release_latency_n"] for r in rows);result["crossed_to_release_mean_latency"]=sum(r["crossed_release_latency_sum"] for r in rows)/n if n else None;result["crossed_release_latency_n"]=n
    n=sum(r["blocked_events"] for r in rows);result["blocked_recovery_within_3_rounds"]=sum(r["blocked_recoveries"] for r in rows)/n if n else None;result["blocked_events"]=n
    n=sum(r["burst_count"] for r in rows);result["mean_burst_length_rounds"]=sum(r["burst_rounds_total"] for r in rows)/n if n else None;result["burst_count"]=n
    return result

def main():
    episodes=[r for r in load(Path("artifacts/evaluations/phase2_v1/per_episode.json"))["rows"] if r["condition"] in {"gpt_selfplay","gemini_selfplay"}]
    if len(episodes)!=144 or not all(r["replay_verified"] and r["observations_authenticated"] for r in episodes):raise RuntimeError("input gate failed")
    per=[episode_metrics(r) for r in episodes];groups={}
    for model in ("GPT","Gemini"):
        groups[model]=aggregate([r for r in per if r["model"]==model])
        for outcome in ("success","failure"):groups[f"{model}_{outcome}"]=aggregate([r for r in per if r["model"]==model and r["success_group"]==outcome])
    result={"format":"fwcollab.message_behavior.v1","model_api_calls":0,"input_episodes":144,"input_mutation":False,"definitions":{"repeated_confirmation":"same sender's immediately preceding non-null message has the same status and stage","consecutive_message":"same sender also messaged in the immediately preceding environment round","next_round_wait":"sender chooses WAIT in the next environment round","ready_holding_latency":"environment rounds from READY/HOLDING send to partner's first subsequently successful non-WAIT move","crossed_release_latency":"environment rounds from CROSSED to partner's next matching-stage RELEASE","blocked_recovery":"sender makes a successful non-WAIT move within the next three rounds","burst":"maximal consecutive run of environment rounds containing at least one structured message"},"groups":groups,"per_episode":per}
    OUT.mkdir(parents=True,exist_ok=True);(OUT/"results.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    with (OUT/"per_episode.csv").open("w",encoding="utf-8",newline="") as h:w=csv.DictWriter(h,fieldnames=tuple(per[0]));w.writeheader();w.writerows(per)
    keys=["messages_per_episode","messages_per_100_rounds",*[f"proportion_{s}" for s in STATUSES],"repeated_confirmation_rate","consecutive_message_rate","next_round_wait_after_message","ready_holding_to_partner_move_mean_latency","crossed_to_release_mean_latency","blocked_recovery_within_3_rounds","mean_burst_length_rounds"]
    lines=["# Structured message behavior","","Status: **complete descriptive analysis**. Model/API calls: **0**. Inputs: 144 frozen, replay-authenticated GPT/Gemini Self-play episodes. No LLM judge is used.","","Definitions are frozen in `results.json`; missing opportunities remain N/A and are not treated as zero. Latencies use send-round time and the official one-round delayed channel.","","| Metric | GPT | Gemini | GPT success | GPT failure | Gemini success | Gemini failure |","|---|---:|---:|---:|---:|---:|---:|"]
    def fmt(v):return "N/A" if v is None else f"{v:.6f}"
    for key in keys:lines.append(f"| {key} | {fmt(groups['GPT'][key])} | {fmt(groups['Gemini'][key])} | {fmt(groups['GPT_success'][key])} | {fmt(groups['GPT_failure'][key])} | {fmt(groups['Gemini_success'][key])} | {fmt(groups['Gemini_failure'][key])} |")
    confirmation=lambda g:g["proportion_READY"]+g["proportion_HOLDING"]
    lines += ["","## Descriptive mechanism check","",f"- READY+HOLDING share: GPT={confirmation(groups['GPT']):.3%}, Gemini={confirmation(groups['Gemini']):.3%}.",f"- Repeated-confirmation rate: GPT={groups['GPT']['repeated_confirmation_rate']:.3%}, Gemini={groups['Gemini']['repeated_confirmation_rate']:.3%}.",f"- Sender next-round WAIT: GPT={groups['GPT']['next_round_wait_after_message']:.3%}, Gemini={groups['Gemini']['next_round_wait_after_message']:.3%}.",f"- READY/HOLDING to partner movement latency: GPT={fmt(groups['GPT']['ready_holding_to_partner_move_mean_latency'])}, Gemini={fmt(groups['Gemini']['ready_holding_to_partner_move_mean_latency'])} rounds.","","These are descriptive behavioral associations. They can support or contradict the proposed confirmation/waiting mechanism, but cannot establish that messages caused either violations or success.",""]
    (OUT/"REPORT.md").write_text("\n".join(lines),encoding="utf-8")
    print(json.dumps({"episodes":144,"gpt_messages":groups['GPT']['messages'],"gemini_messages":groups['Gemini']['messages'],"model_api_calls":0}))
if __name__=="__main__":main()
