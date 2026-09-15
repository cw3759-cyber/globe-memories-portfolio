"""Build a public source ZIP using an explicit allowlist; exclude runtime data."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT.parent/'globe-memories-portfolio.zip'
DIRS={'app','static','demo','assets','docs','scripts'}
FILES={'README.md','DEPLOY.md','requirements.txt','start.sh','Dockerfile','docker-compose.yml','render.yaml','.gitignore','.dockerignore'}
with ZipFile(DEST,'w',ZIP_DEFLATED) as z:
    for path in sorted(ROOT.rglob('*')):
        rel=path.relative_to(ROOT)
        if not path.is_file() or path.is_symlink():continue
        if any(x in {'__pycache__','.git','.venv','data','data-demo'} for x in rel.parts):continue
        if path.suffix in {'.pyc','.db','.log'} or path.name.startswith(('.env','.secret')):continue
        if (len(rel.parts)==1 and rel.name in FILES) or rel.parts[0] in DIRS:
            z.write(path,Path(ROOT.name)/rel)
print(DEST)
