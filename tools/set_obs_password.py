"""Save the password already configured in OBS without echoing it into shell history."""
import getpass
import os
from pathlib import Path

root=Path(__file__).resolve().parents[1]
value=getpass.getpass('输入 OBS → 工具 → WebSocket 服务器设置中的密码（不回显）：').strip()
if not value:raise SystemExit('未保存空密码。')
folder=root/'.secrets';folder.mkdir(mode=0o700,exist_ok=True);folder.chmod(0o700)
tmp=folder/'.obs.password.tmp'
fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:f.write(value)
tmp.chmod(0o600);tmp.replace(folder/'obs.password')
print('OBS 密码已保存在本机私密目录。')
