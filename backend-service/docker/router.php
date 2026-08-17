<?php

// Router for PHP's built-in server (used instead of `php artisan serve` in Docker —
// see the comment in the Dockerfile for why). Serves real files as-is (assets, images)
// and falls back to the Laravel front controller for everything else, same as
// Apache/nginx rewrite rules would in a normal deployment.

$uri = urldecode(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH));
$publicPath = __DIR__ . '/../public' . $uri;

if ($uri !== '/' && file_exists($publicPath) && !is_dir($publicPath)) {
    // Don't `return false` here to let PHP's built-in server serve it itself —
    // that built-in static-file handler refuses to follow the public/storage
    // symlink (created by storage:link -> storage/app/public) and answers
    // every camera-video / employee-photo URL with a bare 403 Forbidden, which
    // is exactly what broke vision-service's urlretrieve() of camera videos
    // (confirmed via a job error: "HTTPError: HTTP Error 403: Forbidden").
    // Reading and streaming the file ourselves sidesteps that symlink check.
    $mime = mime_content_type($publicPath) ?: 'application/octet-stream';
    header('Content-Type: ' . $mime);
    header('Content-Length: ' . filesize($publicPath));
    readfile($publicPath);
    return true;
}

require __DIR__ . '/../public/index.php';
