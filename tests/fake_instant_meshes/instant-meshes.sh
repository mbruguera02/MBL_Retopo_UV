#!/bin/sh
# Versión macOS/Linux del sustituto de pruebas (chmod +x antes de usarlo)
exec "$MBL_FAKE_PYTHON" "$(dirname "$0")/fake_instant_meshes.py" "$@"
