# Hr_SmartPay — Laravel 12 multi-tenant HR/transportation backend
# Two stages: build front-end assets with Node, then a lean PHP runtime image.

# ---- Stage 1: front-end assets (Vite + Tailwind v4 — no separate tailwind/postcss config) ----
FROM node:20-alpine AS assets
WORKDIR /app
# No package-lock.json committed on this branch — `npm install` instead of `npm ci`.
COPY package.json ./
RUN npm install
COPY vite.config.js ./
COPY resources ./resources
RUN npm run build

# ---- Stage 2: PHP runtime ----
FROM php:8.2-cli-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    git unzip libzip-dev libpng-dev libonig-dev libxml2-dev libicu-dev default-mysql-client \
    && docker-php-ext-install pdo_mysql mbstring exif pcntl bcmath gd zip intl \
    && rm -rf /var/lib/apt/lists/*

# CameraController already validates videos up to 500MB — PHP's own defaults
# (2M/8M) silently rejected anything over that before this existed.
# max_execution_time raised 300 -> 900: /checkin now runs vision-service's
# /checkin/video-multi synchronously, which has no early exit (has to scan
# the whole clip to find every person) and runs full YOLO detection +
# tracking + face-matching per frame on CPU-only inference — a multi-minute
# 4K clip can genuinely take several minutes, and was hitting this ceiling.
RUN { \
      echo 'upload_max_filesize = 512M'; \
      echo 'post_max_size = 512M'; \
      echo 'max_execution_time = 900'; \
    } > /usr/local/etc/php/conf.d/uploads.ini

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

WORKDIR /var/www/html

# Install PHP deps first so this layer is cached across code-only changes.
COPY composer.json composer.lock ./
RUN composer install --no-dev --no-scripts --no-interaction --prefer-dist --no-progress

COPY . .
COPY --from=assets /app/public/build ./public/build

RUN composer dump-autoload --optimize \
    && cp -n .env.example .env \
    && mkdir -p storage/framework/cache storage/framework/sessions storage/framework/testing storage/framework/views storage/logs storage/api-docs bootstrap/cache \
    && chmod -R 775 storage bootstrap/cache

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["entrypoint.sh"]
# Plain `php -S`, not `php artisan serve` — Laravel's serve command re-reads DB_HOST etc.
# from the .env FILE when spawning its worker process, which silently ignores the
# container's injected environment variables and reconnects to 127.0.0.1 instead of `mysql`.
CMD ["php", "-S", "0.0.0.0:8080", "-t", "public", "docker/router.php"]
