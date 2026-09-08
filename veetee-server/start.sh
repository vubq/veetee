#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
VENV="$DIR/../../venv"

if [ ! -d "$VENV" ]; then
    echo "Virtual environment not found at $VENV, creating..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip
    "$VENV/bin/pip" install vieneu websockets aiohttp numpy soundfile scipy opuslib-next pyyaml torch transformers accelerate soxr onnxruntime 'nemo_toolkit[asr]'
fi

export PYTHONPATH="$DIR:$PYTHONPATH"
echo "Starting VeeTee Voice Assistant Server (Local Non-Docker)..."
exec "$VENV/bin/python" "$DIR/server.py"
