"""Fetch pinned upstream code, apply audited local patches, and prepare a fresh Mac installation.

Does not copy credentials, start servers, create cloud sessions, or overwrite existing config.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def run(*args,cwd=ROOT):
    subprocess.run([str(x) for x in args],cwd=cwd,check=True)

def prepare_sources():
    manifest=json.loads((ROOT/'vendor-patches/manifest.json').read_text())
    for item in manifest:
        url=item['repository'];name=url.rsplit('/',1)[-1]
        if name not in ('cyber-xiaoya-live','cyber-xiaoya-danmaku'):
            raise RuntimeError('Unexpected upstream name')
        patch=ROOT/'vendor-patches'/item['patch']
        if hashlib.sha256(patch.read_bytes()).hexdigest()!=item['sha256']:
            raise RuntimeError(f'Patch checksum mismatch: {name}')
        target=ROOT/'upstream'/name
        if not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True)
            run('git','init',str(target))
            run('git','remote','add','origin',url,cwd=target)
            run('git','fetch','--depth','1','origin',item['commit'],cwd=target)
            run('git','checkout','--detach','FETCH_HEAD',cwd=target)
        current=subprocess.check_output(['git','rev-parse','HEAD'],cwd=target,text=True).strip()
        if current!=item['commit']:
            raise RuntimeError(f'{name} is on another revision; no files were reset')
        done=subprocess.run(['git','apply','--reverse','--check',str(patch)],cwd=target,capture_output=True)
        if done.returncode==0:
            print(f'{name}: patch already applied');continue
        run('git','apply','--check',patch,cwd=target)
        run('git','apply',patch,cwd=target)
        print(f'{name}: source and patch ready')

def environment(folder,requirements):
    python=folder/'bin/python'
    if not python.exists():run(sys.executable,'-m','venv',folder)
    run(python,'-m','pip','install','-r',requirements)
    return python

def collector_config():
    dest=ROOT/'upstream/cyber-xiaoya-danmaku/config.json'
    if dest.exists():return
    cfg={name:{'enabled':False} for name in ('douyin','bilibili','youtube','wechat')}
    cfg['douyin']['auto_start']=False
    cfg['tts']={'output':'dh','dh_url':'http://127.0.0.1:8795','dh_min_interval_ms':0}
    cfg['read']={'comments':True,'gifts':True,'templates':{'comment':'{content}','gift':'感谢 {nick} 送的 {item}'}}
    fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(cfg,stream,ensure_ascii=False,indent=2)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources-only',action='store_true',help='Fetch and patch sources without installing or configuring services')
    args=parser.parse_args()
    if not args.sources_only and (sys.platform!='darwin' or sys.version_info<(3,12)):
        raise SystemExit('This local deployment requires macOS and Python 3.12+. --sources-only can run elsewhere.')
    if not shutil.which('git'):raise SystemExit('Install Git first.')
    prepare_sources()
    if args.sources_only:return
    if not shutil.which('ffmpeg'):raise SystemExit('Install ffmpeg (for example: brew install ffmpeg), then rerun.')
    python=environment(ROOT/'.venv',ROOT/'requirements.lock.txt')
    source=ROOT/'upstream/cyber-xiaoya-live'
    environment(source/'.venv',source/'requirements.macos.lock.txt')
    run('sh',ROOT/'tools/fetch_vendor.sh')
    run(python,ROOT/'tools/setup_local.py')
    run(python,ROOT/'tools/upstream_local.py','setup')
    collector_config()
    print('Installed. No server or paid session was started. Run 启动平台.command, then enter your own keys locally.')

if __name__=='__main__':main()
