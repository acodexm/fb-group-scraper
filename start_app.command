#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
python app.py
read -p "Press [Enter] to close..."
