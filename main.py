import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from traderos.application.orchestrator import main

if __name__ == "__main__":
    main()
