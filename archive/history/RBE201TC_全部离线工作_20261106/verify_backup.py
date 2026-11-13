from pathlib import Path
import hashlib,json,sys
root=Path(__file__).resolve().parent
manifest=json.loads((root/'MANIFEST_SHA256.json').read_text())
failed=[]
for name,expected in manifest.items():
 p=(root/name).resolve()
 if root not in p.parents or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:failed.append(name)
print('核对文件数：',len(manifest),'不一致：',len(failed))
for name in failed:print(name)
sys.exit(bool(failed))
