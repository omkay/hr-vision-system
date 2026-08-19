<?php

return [

    /*
    |--------------------------------------------------------------------------
    | Third Party Services
    |--------------------------------------------------------------------------
    |
    | This file is for storing the credentials for third party services such
    | as Mailgun, Postmark, AWS and more. This file provides the de facto
    | location for this type of information, allowing packages to have
    | a conventional file to locate the various service credentials.
    |
    */

    'postmark' => [
        'key' => env('POSTMARK_API_KEY'),
    ],

    'resend' => [
        'key' => env('RESEND_API_KEY'),
    ],

    'ses' => [
        'key' => env('AWS_ACCESS_KEY_ID'),
        'secret' => env('AWS_SECRET_ACCESS_KEY'),
        'region' => env('AWS_DEFAULT_REGION', 'us-east-1'),
    ],

    'slack' => [
        'notifications' => [
            'bot_user_oauth_token' => env('SLACK_BOT_USER_OAUTH_TOKEN'),
            'channel' => env('SLACK_BOT_USER_DEFAULT_CHANNEL'),
        ],
    ],

    'vision' => [
        // The employee_activity_tracker_2026 FastAPI service — see ADR-001.
        'url' => env('VISION_SERVICE_URL', 'http://localhost:8000'),
        // Browser-facing base URL for the same service — used only to build
        // links to annotated debug videos (served from vision-service's own
        // /outputs static route) that get returned to the frontend. Mirrors
        // the APP_URL vs INTERNAL_APP_URL split above `url`: `url` is what
        // THIS container uses to call vision-service server-to-server (may
        // be a Docker-internal hostname), `public_url` is what a browser on
        // the user's machine uses, which is usually just localhost since
        // vision-service's port is published to the host either way
        // (Dockerized or running natively).
        'public_url' => env('VISION_SERVICE_PUBLIC_URL', 'http://localhost:8000'),
    ],

];
