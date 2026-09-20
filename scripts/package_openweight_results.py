"""Package one validated open-weight run without credentials or weights."""
from __future__ import annotations
import argparse,hashlib,json,tarfile
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def main()->int:
 p=argparse.ArgumentParser();p.add_argument("--run-dir",required=True);p.add_argument("--evaluation-dir",required=True);p.add_argument("--model-id",required=True);p.add_argument("--output-dir",default="dist/results");a=p.parse_args();run=(ROOT/a.run_dir).resolve();ev=(ROOT/a.evaluation_dir).resolve();base=ROOT.resolve()
 for x in (run,ev):
  if base not in x.parents or not x.is_dir():raise SystemExit(f"invalid input directory: {x}")
 integrity=ev/"integrity_report.json"
 if not integrity.is_file() or not json.loads(integrity.read_text(encoding="utf-8-sig")).get("pass"):raise SystemExit("passing integrity_report.json required")
 files=sorted([x for d in (run,ev) for x in d.rglob("*") if x.is_file()])
 forbidden=(".env","token","credential","secret","model.safetensors","pytorch_model")
 bad=[x for x in files if any(term in x.name.lower() for term in forbidden)]
 if bad:raise SystemExit(f"forbidden files: {bad}")
 stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ");safe="".join(c if c.isalnum() or c in "-_." else "_" for c in a.model_id);outdir=ROOT/a.output_dir;outdir.mkdir(parents=True,exist_ok=True);archive=outdir/f"openweight_results_{safe}_{stamp}.tar.gz"
 manifest={"format":"fwcollab.openweight_result_package.v1","model_id":a.model_id,"created_at":datetime.now(timezone.utc).isoformat(),"files":[{"path":x.relative_to(ROOT).as_posix(),"bytes":x.stat().st_size,"sha256":sha(x)} for x in files]}
 manifest_path=outdir/f"openweight_results_{safe}_{stamp}_MANIFEST.json";manifest_path.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
 with tarfile.open(archive,"w:gz") as tar:
  for x in files:tar.add(x,arcname=x.relative_to(ROOT).as_posix(),recursive=False)
  tar.add(manifest_path,arcname=manifest_path.relative_to(ROOT).as_posix(),recursive=False)
 print(json.dumps({"archive":archive.relative_to(ROOT).as_posix(),"bytes":archive.stat().st_size,"sha256":sha(archive),"files":len(files)+1}));return 0
if __name__=="__main__":raise SystemExit(main())
