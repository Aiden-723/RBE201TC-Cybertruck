"""Verify local staged bytes only. Never imports project modules or accesses a robot."""
import ast
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'PATCH_MANIFEST.json').read_text())
project = (Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else
           root.parents[1] / 'work/device-console-v2')
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
checked = 0
for relative, hashes in manifest['runtime_files'].items():
    assert sha(root / 'before' / relative) == hashes['before_sha256'], relative
    for path in (root / 'candidate' / relative, project / relative):
        assert sha(path) == hashes['candidate_sha256'], str(path)
        if path.suffix == '.py':
            ast.parse(path.read_bytes(), filename=str(path))
    checked += 1
for relative, expected in manifest['test_files'].items():
    assert sha(root / 'candidate' / relative) == expected, relative
    assert sha(project / relative) == expected, relative
    checked += 1
for relative, expected in manifest['protected_configuration'].items():
    assert sha(project / relative) == expected, 'protected file changed: ' + relative
print(json.dumps({'verified_patch_and_test_files': checked,
                  'protected_files': len(manifest['protected_configuration']),
                  'status': 'local_integrity_verified_not_deployed'}, indent=2))
