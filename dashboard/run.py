"""Launch the Streamlit dashboard."""
import subprocess
import sys
from pathlib import Path

dashboard = Path(__file__).parent / "app.py"
subprocess.run([sys.executable, "-m", "streamlit", "run", str(dashboard), "--server.port", "8501"], check=True)
