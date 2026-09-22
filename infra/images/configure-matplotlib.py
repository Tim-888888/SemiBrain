"""Adjust the shipped template without replacing Matplotlib's other defaults."""

import re
import sysconfig
from pathlib import Path

path = Path(sysconfig.get_path("purelib")) / "matplotlib/mpl-data/matplotlibrc"
content = path.read_text(encoding="utf-8")
for key, value in {
    "font.family": "sans-serif",
    "font.sans-serif": "Noto Sans CJK JP, DejaVu Sans",
    "axes.unicode_minus": "False",
}.items():
    content, count = re.subn(
        r"(?m)^#?[ \t]*" + re.escape(key) + r"[ \t]*:.*$",
        key + ": " + value,
        content,
    )
    if count != 1:
        raise RuntimeError("Unexpected Matplotlib configuration template")
path.write_text(content, encoding="utf-8")
