"""Build and independently validate the minimal portable Standard-52 bundle."""
from __future__ import annotations
import hashlib,json,shutil,subprocess,sys,tarfile,tempfile
from pathlib import Path, PurePosixPath
ROOT=Path(__file__).resolve().parents[1];DIST=ROOT/"dist";BUNDLE=DIST/"fwcollab_openweight_runner_v1"
def load(p:Path):return json.loads(p.read_text(encoding="utf-8-sig"))
def sha(p:Path):return hashlib.sha256(p.read_bytes()).hexdigest()
def add(path:str,files:set[str])->None:
 p=ROOT/path
 if p.is_dir():files.update(x.relative_to(ROOT).as_posix() for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts)
 elif p.is_file():files.add(path)
 else:raise FileNotFoundError(path)
def main()->int:
 run=load(ROOT/"eval_private/leaderboard_v1/standard_52_run_manifest.json");bench=load(ROOT/"eval_private/benchmark_v2/manifest.json");by={x["task_id"]:x for x in bench["tasks"]};files:set[str]=set()
 for path in ("src/fwcollab","pyproject.toml","uv.lock","SYMBOL_SPEC.md","docs/PUBLIC_RULEBOOK.md","docs/COLLABORATION_EVALUATION_PROTOCOL.md","docs/LEADERBOARD_RUN_PLAN.md","docs/LEADERBOARD_PREFLIGHT_CHECKLIST.md","docs/OPEN_WEIGHT_MODEL_SELECTION_POLICY.md","schemas/state_dag.schema.json","schemas/collaboration_evaluation.schema.json","schemas/unified_evaluation.schema.json","scripts/preflight_leaderboard_models.py","scripts/preflight_openweight_model.py","scripts/freeze_openweight_matrix.py","scripts/run_standard52_leaderboard.py","scripts/run_standard52_openweight.py","scripts/check_standard52_integrity.py","scripts/package_openweight_results.py","scripts/selfcheck_openweight_bundle.py","eval_private/benchmark_v2/manifest.json","eval_private/benchmark_v2/freeze_manifest.json","eval_private/leaderboard_v1/standard_52_run_manifest.json","eval_private/leaderboard_v1/prompt_contract","eval_private/leaderboard_v1/final6_matrix_plan.json") : add(path,files)
 for task_id in run["ordered_task_ids"]:
  task=by[task_id]
  for key in ("map_path","dag_path","witness_path","source_manifest_path"):add(task[key],files)
 freeze_paths=("eval_private/benchmark_v1/freeze_manifest.json","eval_private/benchmark_v2/freeze_manifest.json","eval_private/c5_information_12/freeze_manifest.json","eval_private/synchronize_8/freeze_manifest.json","eval_private/parallel_join_8/freeze_manifest.json")
 for path in freeze_paths:add(path,files)
 if BUNDLE.exists():shutil.rmtree(BUNDLE)
 for rel in sorted(files):
  src=ROOT/rel;dst=BUNDLE/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
 (BUNDLE/"requirements.txt").write_text("pydantic>=2.10,<3\n",encoding="utf-8")
 readme=ROOT/"docs/SERVER_RUN_README.md"
 if not readme.is_file():raise FileNotFoundError(readme)
 shutil.copy2(readme,BUNDLE/"SERVER_RUN_README.md")
 records=[{"path":p.relative_to(BUNDLE).as_posix(),"bytes":p.stat().st_size,"sha256":sha(p)} for p in sorted(BUNDLE.rglob("*")) if p.is_file()]
 manifest={"format":"fwcollab.openweight_runner_bundle.v1","standard52_task_count":52,"prompt_contract_hash":run["prompt_contract"]["sha256"],"standard52_manifest_hash":sha(BUNDLE/"eval_private/leaderboard_v1/standard_52_run_manifest.json"),"files":records}
 internal=BUNDLE/"BUNDLE_MANIFEST.json";internal.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
 archive=DIST/"fwcollab_openweight_runner_v1.tar.gz"
 if archive.exists():archive.unlink()
 with tarfile.open(archive,"w:gz") as tar:tar.add(BUNDLE,arcname=BUNDLE.name)
 outer={**manifest,"bundle_path":archive.relative_to(ROOT).as_posix(),"bundle_bytes":archive.stat().st_size,"bundle_sha256":sha(archive)};(DIST/"fwcollab_openweight_runner_v1_MANIFEST.json").write_text(json.dumps(outer,indent=2)+"\n",encoding="utf-8")
 with tempfile.TemporaryDirectory(prefix="fwcollab_bundle_check_") as td:
  target=Path(td)
  with tarfile.open(archive,"r:gz") as tar:
   for member in tar.getmembers():
    parts=PurePosixPath(member.name).parts
    if member.name.startswith("/") or ".." in parts:raise RuntimeError(f"unsafe archive member: {member.name}")
   tar.extractall(target,filter="data")
  root=target/BUNDLE.name
  env={"PYTHONPATH":str(root/"src"),"PYTHONDONTWRITEBYTECODE":"1"}
  result=subprocess.run([sys.executable,"scripts/selfcheck_openweight_bundle.py"],cwd=root,env={**__import__('os').environ,**env},capture_output=True,text=True)
  if result.returncode:raise RuntimeError(result.stderr)
  boundary=subprocess.run([sys.executable,"scripts/preflight_openweight_model.py","--model-id","SYNTHETIC-ENDPOINT-REQUIRED","--base-url","http://127.0.0.1:9/v1","--timeout","0.1","--output","synthetic_preflight_boundary.json"],cwd=root,env={**__import__('os').environ,**env},capture_output=True,text=True)
  boundary_record=load(root/"synthetic_preflight_boundary.json")
  if boundary.returncode!=2 or boundary_record.get("benchmark_content_used") is not False or boundary_record.get("all_pass") is not False:raise RuntimeError("synthetic preflight did not stop cleanly at the unavailable-endpoint boundary")
 print(json.dumps({"bundle":archive.relative_to(ROOT).as_posix(),"bytes":archive.stat().st_size,"sha256":sha(archive),"files":len(records)+1,"refs":"52/52","self_check":"PASS"}));return 0
if __name__=="__main__":raise SystemExit(main())
