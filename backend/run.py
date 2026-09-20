"""
MiroFish Backend Entry Point
"""

import os
import sys

# Solve Windows console Chinese character encoding issue: set UTF-8 encoding before all imports
if sys.platform == 'win32':
    # Set environment variable to ensure Python uses UTF-8
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    # Reconfigure standard output stream to UTF-8
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Add project root directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.config import Config
from app.runtime import RuntimeConfigurationError


def main():
    """Main function"""
    try:
        app = create_app()
    except RuntimeConfigurationError as error:
        print(f"Configuration error: {error}")
        print("Please check configuration in .env file")
        sys.exit(1)

    # Get runtime configuration
    host = Config.MIROFISH_BIND_HOST
    port = int(os.environ.get('FLASK_PORT', 5001))
    debug = Config.DEBUG

    # Start service
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    main()
