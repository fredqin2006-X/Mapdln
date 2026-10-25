# Compatibility entry point. Keep all packaging rules in tools/mapdln.spec.
from pathlib import Path

_task_spec = Path(SPECPATH) / 'tools' / 'mapdln.spec'
SPECPATH = str(_task_spec.parent)
exec(compile(_task_spec.read_text(encoding='utf-8'), str(_task_spec), 'exec'))
