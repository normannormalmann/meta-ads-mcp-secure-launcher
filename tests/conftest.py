import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAKE_TOKEN = "EAA" + "x1Y2z3" * 10
FAKE_PROOF = "0123456789abcdef" * 4
