#!/bin/bash
# Exit immediately if any command fails
set -e

echo "1. Pulling latest progress from cloud..."
git pull origin main

# 2. Run the batcher if you want new sentences (uncomment if desired)
# python3 generate_batch.py

echo "2. Staging specific project files..."
# Explicitly add your scripts and templates—never use "git add ." blindly here!
git add generate_batch.py config.py.example sync.sh index.html style.js

# Check if there's anything to commit
if git diff-index --quiet HEAD --; then
    echo "No changes to sync."
else
    echo "3. Committing and pushing updates..."
    git commit -m "Auto-sync update: $(date +'%Y-%m-%d %H:%M')"
    git push origin main
    echo "Done! Refresh your PWA on your Pixel device."
fi
