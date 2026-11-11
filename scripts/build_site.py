"""Build the static demo into docs/ (served by GitHub Pages): the UI plus the replayed demo cases."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DOCS.mkdir(exist_ok=True)
for f in (ROOT / "web").iterdir():
    if f.name == "cases" or f.name.startswith("."):
        continue
    dest = DOCS / f.name
    if f.is_dir():
        shutil.copytree(f, dest, dirs_exist_ok=True)
    else:
        shutil.copy2(f, dest)
(DOCS / ".nojekyll").write_text("")
code = subprocess.call([sys.executable, str(ROOT / "scripts" / "run_case.py"), "--demo", "--out", str(DOCS / "cases")])
if not code:
    code = subprocess.call([sys.executable, str(ROOT / "scripts" / "run_public_filing.py")])
sys.exit(code)
