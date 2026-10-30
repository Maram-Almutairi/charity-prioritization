"""
Shared chart style for the notebook, training figures and the report: the bundled Noto Sans
Arabic font and the same light palette as the web dashboard. Matplotlib >= 3.11 shapes and
orders Arabic text natively, so labels are written in Arabic directly.
"""

from pathlib import Path

import matplotlib
from matplotlib import font_manager

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
INK, INK2, GRID, SURFACE = "#0d1726", "#45526a", "#e3e8f0", "#ffffff"
PRIORITY_COLORS = {"high": "#eb6834", "medium": "#2a78d6", "low": "#1baf7a"}
PRIORITY_AR = {"high": "عالية", "medium": "متوسطة", "low": "منخفضة"}
NEED, RISK, PREVENT = "#2a78d6", "#eb6834", "#7c5ce6"
ORDER = ["high", "medium", "low"]


def setup() -> None:
    for ttf in FONT_DIR.glob("*.ttf"):
        font_manager.fontManager.addfont(str(ttf))
    matplotlib.rcParams.update({
        "font.family": ["Noto Sans Arabic", "DejaVu Sans"], "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
        "text.color": INK, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.7, "axes.axisbelow": True,
        "font.size": 10, "axes.titlesize": 11, "axes.titleweight": "bold", "figure.dpi": 150,
        "savefig.bbox": "tight", "axes.unicode_minus": False,
    })
