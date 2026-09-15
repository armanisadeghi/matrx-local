#!/usr/bin/env python3
"""Build an offline minimal image for a future isolated app.config import."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "f19fbf5164452619f553c8dd7fc310b0e52bc7ec"
IMAGE_ID = "sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285"
PATCH_SHA = "135f45c6d39bdfe8f8238ae8ad4371394410aa5bdd96f4785c644b8562840b30"
WHEEL_SHA = "b81ee9561e9ca4004139c6cbba3a238c32b03e4894671e181b671e8cb8425d61"
WHEEL_URL = "https://files.pythonhosted.org/packages/14/1b/a298b06749107c305e1fe0f814c6c74aea7b2f1e10989cb30f544a1b3253/python_dotenv-1.2.1-py3-none-any.whl"
FILES = {"run.py": "98d7588f3aaa3d72037a7c6c796ce9331f46bc346a1aec19579afb3b9c447bd7", "app/config.py": "ba709e1fb0cd12b17aebf8de489e232b8d0d0f3dfa0e890f2757f790b6b33240", "docs/observability/source-isolation-bootstrap.md": "d8a3481a3841d86ff63d5bb35d25ac12413a65945f9c632b19b396cc67f3d102"}
PATCH_SOURCE = Path('/Users/armanisadeghi/code/common-docs/projects/unified-error-observability/matrx-local/AGENT3-ISOLATION-BOOTSTRAP.patch')
TIMEOUT = 120

def run(cmd, *, cwd=None, capture=True):
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=capture, timeout=TIMEOUT)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout)[-1000:])
    return p.stdout

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def tree_sha(root):
    h=hashlib.sha256()
    for p in sorted(root.rglob('*'), key=lambda x:x.as_posix()):
        r=p.relative_to(root).as_posix().encode()
        if p.is_symlink(): h.update(b'L\0'+r+b'\0'+os.readlink(p).encode())
        elif p.is_file(): h.update(b'F\0'+r+b'\0'+p.read_bytes())
    return h.hexdigest()

def safe_extract(archive, dest):
    with tarfile.open(archive) as t:
        root=dest.resolve()
        for m in t.getmembers():
            if not (m.isfile() or m.isdir() or m.issym()): raise RuntimeError(f"nonregular archive entry: {m.name}")
            target=(dest/m.name).resolve()
            if target != root and root not in target.parents: raise RuntimeError(f"archive escape: {m.name}")
            if m.issym():
                linked=(dest/m.name).parent.joinpath(m.linkname).resolve()
                if linked != root and root not in linked.parents: raise RuntimeError(f"symlink escape: {m.name}")
        t.extractall(dest)

def check_source(source):
    for p in source.rglob('*'):
        if p.is_symlink():
            target=p.resolve(strict=False)
            if source not in target.parents and target != source: raise RuntimeError(f"symlink escape: {p.relative_to(source)}")
        rel=p.relative_to(source).as_posix()
        if '.venv' in p.parts or (p.name.startswith('.env') and not p.name.endswith('.example')) or p.suffix.lower() in {'.pem','.key'}: raise RuntimeError(f"disallowed input: {rel}")
    for name, expected in FILES.items():
        actual=sha(source/name)
        if actual != expected: raise RuntimeError(f"source digest mismatch: {name}")

def main():
    if not shutil.which('docker'): raise SystemExit('docker unavailable')
    task=Path(tempfile.mkdtemp(prefix='matrx-config-image-')).resolve(); task.chmod(0o700)
    archive=task/'source.tar'; snapshot=task/'snapshot'; snapshot.mkdir()
    with archive.open('wb') as out:
        p=subprocess.run(['git','archive','--format=tar',BASE],cwd=ROOT,stdout=out,stderr=subprocess.PIPE,timeout=TIMEOUT)
    if p.returncode: raise SystemExit(p.stderr.decode()[-1000:])
    patch=task/'reviewed.patch'; shutil.copyfile(PATCH_SOURCE,patch)
    if sha(patch)!=PATCH_SHA: raise SystemExit('Agent3 reviewed patch digest mismatch')
    safe_extract(archive,snapshot); run(['git','apply','--check',str(patch)],cwd=snapshot); run(['git','apply',str(patch)],cwd=snapshot)
    # Agent3's bootstrap also names this uncommitted evidence file by hash. It
    # is copied only after that exact content check; no shared dirty tree is read.
    for name, expected in FILES.items():
        target=snapshot/name
        if not target.exists():
            source=ROOT/name
            if sha(source)!=expected: raise SystemExit(f'bootstrap input digest mismatch: {name}')
            target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,target)
    check_source(snapshot)
    wheel_dir=task/'wheelhouse'; wheel_dir.mkdir(); wheel=wheel_dir/'python_dotenv-1.2.1-py3-none-any.whl'
    with urllib.request.urlopen(WHEEL_URL, timeout=30) as response: wheel.write_bytes(response.read())
    if sha(wheel)!=WHEEL_SHA: raise SystemExit('locked dotenv wheel digest mismatch')
    image=json.loads(run(['docker','image','inspect',IMAGE_ID]))[0]
    if image.get('Id')!=IMAGE_ID or image.get('Os')!='linux' or image.get('Architecture')!='arm64': raise SystemExit('required local Linux Python image unavailable')
    tag='matrx-isolation-base:'+uuid.uuid4().hex; run(['docker','image','tag',IMAGE_ID,tag])
    dockerfile=task/'Dockerfile'; shutil.copyfile(ROOT/'scripts/reliability-config-import.Dockerfile',dockerfile); image_tag='matrx-config-import:'+tree_sha(snapshot)[:20]
    try:
        run(['docker','build','--pull=false','--network=none','--build-arg','BASE_IMAGE='+tag,'-f',str(dockerfile),'-t',image_tag,str(task)],capture=True)
        built=json.loads(run(['docker','image','inspect',image_tag]))[0]
    finally:
        subprocess.run(['docker','image','rm',tag],capture_output=True,text=True,timeout=TIMEOUT)
    receipt={'kind':'config-import-image-build','timestamp':int(time.time()),'base_commit':BASE,'archive_sha256':sha(archive),'patch_sha256':sha(patch),'snapshot_sha256':tree_sha(snapshot),'source_files':FILES,'wheel_url':WHEEL_URL,'wheel_sha256':sha(wheel),'base_image_id':image['Id'],'built_image_id':built['Id'],'built_image_tag':image_tag,'dockerfile_sha256':sha(dockerfile),'builder_sha256':sha(__file__),'build_network':'none','runtime_import_executed':False}
    path=task/'build-receipt.json'; path.write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n'); print(json.dumps({'receipt':str(path),'image':image_tag},sort_keys=True))

if __name__=='__main__': main()
