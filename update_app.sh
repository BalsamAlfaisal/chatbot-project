#!/bin/bash
# Pull the latest code and images, then restart the app.
set -e

cd /home/azureuser/chatbot-project

GIT_SSH_COMMAND='ssh -i /home/azureuser/.ssh/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new' git pull

docker compose pull backend chatbot
docker compose up -d --remove-orphans
docker image prune -f
