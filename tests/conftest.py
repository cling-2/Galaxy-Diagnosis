"""conftest.py — pytest 共享 fixture"""

import sys
from pathlib import Path

# 把 src 目录加入 sys.path，使 galaxy_diag 包可被导入（无需 pip install）
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
