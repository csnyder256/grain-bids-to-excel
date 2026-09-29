"""Run BidBoard without opening a browser or installing at startup."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "src"))
from bidboard import create_app, paths
from waitress import serve
if __name__ == "__main__":
    paths.ensure_runtime_dirs()
    serve(create_app(), host="0.0.0.0", port=8383)
