#!/usr/bin/env bash
set -e

DB_HOST="${DB_HOST:-mysql}"
DB_PORT="${DB_PORT:-3306}"
DB_USERNAME="${DB_USERNAME:-root}"
DB_PASSWORD="${DB_PASSWORD:-root}"

echo "Waiting for MySQL at ${DB_HOST}:${DB_PORT}..."
until php -r "new PDO('mysql:host=${DB_HOST};port=${DB_PORT}', '${DB_USERNAME}', '${DB_PASSWORD}');" 2>/dev/null; do
  sleep 2
done
echo "MySQL is up."

if [ ! -f .env ]; then
  cp .env.example .env
fi

if ! grep -q '^APP_KEY=base64' .env 2>/dev/null; then
  php artisan key:generate --force
fi

if [ "${SKIP_MIGRATIONS:-false}" != "true" ]; then
  echo "Running central migrations..."
  php artisan migrate --force
else
  echo "SKIP_MIGRATIONS=true — leaving migrations to the hr-app container."
fi

# Camera videos and employee photos are served from Storage::disk('public'), which needs
# public/storage -> storage/app/public. Without this, every asset()/Storage::url() call
# 404s — including the URLs this app will need to hand to the vision service.
if [ ! -e public/storage ]; then
  echo "Linking public/storage..."
  php artisan storage:link
fi

echo "Caching config/routes for a clean boot..."
php artisan config:clear
php artisan route:clear

exec "$@"
