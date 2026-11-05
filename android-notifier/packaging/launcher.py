"""Desktop entry: configure a new installation, otherwise open the familiar panel."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment_runtime import configured

if __name__ == '__main__':
    if configured():
        from desktop_control import main
    else:
        from first_run import main
    main()
