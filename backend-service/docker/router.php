<?php

// Router for PHP's built-in server (used instead of `php artisan serve` in Docker —
// see the comment in the Dockerfile for why). Serves real files as-is (assets, images)
// and falls back to the Laravel front controller for everything else, same as
// Apache/nginx rewrite rules would in a normal deployment.

$uri = urldecode(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH));
$publicPath = __DIR__ . '/../public' . $uri;

if ($uri !== '/' && file_exists($publicPath) && !is_dir($publicPath)) {
    return false;
}

require __DIR__ . '/../public/index.php';
