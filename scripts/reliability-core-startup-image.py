#!/usr/bin/env python3
"""Build the immutable core-lock image only; never import or start the app."""
from __future__ import annotations
import hashlib, importlib.util, json, shutil, subprocess, tempfile, time, urllib.request, uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'scripts/reliability-config-import-image.py'
UV_URL='https://files.pythonhosted.org/packages/19/c5/6e5923d6c9e3b50dc8542647bea692b7c227a9489f59ddff4fdfb20d8459/uv-0.10.8-py3-none-manylinux_2_28_aarch64.whl'
UV_SHA='e26f8c35684face38db814d452dd1a2181152dbf7f7b2de1f547e6ba0c378d67'
T=600
spec=importlib.util.spec_from_file_location('config_image',CONFIG); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run(c,**kw):
 p=subprocess.run(c,text=True,capture_output=True,timeout=T,**kw)
 if p.returncode: raise RuntimeError((p.stderr or p.stdout)[-4000:])
 return p.stdout
def main():
 task=Path(tempfile.mkdtemp(prefix='matrx-core-image-')).resolve(); task.chmod(0o700); archive=task/'source.tar'; snap=task/'snapshot'; snap.mkdir()
 with archive.open('wb') as out:
  p=subprocess.run(['git','archive','--format=tar',m.BASE],cwd=ROOT,stdout=out,stderr=subprocess.PIPE,timeout=T)
 if p.returncode: raise SystemExit(p.stderr.decode()[-1000:])
 patch=task/'reviewed.patch'; shutil.copyfile(m.PATCH_SOURCE,patch)
 if sha(patch)!=m.PATCH_SHA: raise SystemExit('reviewed patch digest mismatch')
 m.safe_extract(archive,snap); run(['git','apply','--check',str(patch)],cwd=snap); run(['git','apply',str(patch)],cwd=snap)
 for n,e in m.FILES.items():
  dest=snap/n
  if not dest.exists():
   src=ROOT/n
   if sha(src)!=e: raise SystemExit('bootstrap evidence digest mismatch: '+n)
   dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(src,dest)
 m.check_source(snap)
 wheel_dir=task/'wheelhouse'; wheel_dir.mkdir(); wheel=wheel_dir/'uv-0.10.8-py3-none-manylinux_2_28_aarch64.whl'
 with urllib.request.urlopen(UV_URL,timeout=30) as r: wheel.write_bytes(r.read())
 if sha(wheel)!=UV_SHA: raise SystemExit('uv wheel digest mismatch')
 base=json.loads(run(['docker','image','inspect',m.IMAGE_ID]))[0]
 if base.get('Id')!=m.IMAGE_ID or base.get('Os')!='linux' or base.get('Architecture')!='arm64': raise SystemExit('pinned base unavailable')
 tag='matrx-isolation-base:'+uuid.uuid4().hex; run(['docker','image','tag',m.IMAGE_ID,tag]); df=task/'Dockerfile'; shutil.copyfile(ROOT/'scripts/reliability-core-startup.Dockerfile',df); outtag='matrx-core-startup:'+m.tree_sha(snap)[:20]
 built=None; error=None
 try:
  run(['docker','build','--pull=false','--build-arg','BASE_IMAGE='+tag,'-f',str(df),'-t',outtag,str(task)]); built=json.loads(run(['docker','image','inspect',outtag]))[0]
 except Exception as exc: error=str(exc)
 finally:
  clean=subprocess.run(['docker','image','rm',tag],capture_output=True,text=True,timeout=T)
  remaining=subprocess.run(['docker','image','inspect',tag],capture_output=True,text=True,timeout=T)
  cleanup={'returncode':clean.returncode,'tag_absent':remaining.returncode != 0,'stderr':clean.stderr[-1000:]}
 rec={'kind':'core-startup-image-build','timestamp':int(time.time()),'base_commit':m.BASE,'archive_sha256':sha(archive),'patch_sha256':sha(patch),'snapshot_sha256':m.tree_sha(snap),'base_image_id':base['Id'],'uv_url':UV_URL,'uv_sha256':sha(wheel),'uv_lock_sha256':sha(snap/'uv.lock'),'dockerfile_sha256':sha(df),'config_helper_sha256':sha(CONFIG),'builder_sha256':sha(__file__),'built_image_id':built['Id'] if built else None,'built_image_tag':outtag,'build_network_mode':'default','build_artifact_source_intent':['https://pypi.org/simple','https://files.pythonhosted.org'],'runtime_app_import_executed':False,'failure':error,'temporary_tag_cleanup':cleanup}
 rp=task/'build-receipt.json'; rp.write_text(json.dumps(rec,indent=2,sort_keys=True)+'\n'); print(json.dumps({'receipt':str(rp),'image':outtag,'success':built is not None},sort_keys=True))
 if error: raise SystemExit(error)
if __name__=='__main__': main()
