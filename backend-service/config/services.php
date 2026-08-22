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
        // Ask vision-service to emit its per-frame identity decision log
        // (outputs/<cam>_<video>_identity_debug.csv: crop-quality verdict,
        // face/ReID score + margin, which bank matched, assigned name, vote
        // score). Defaults ON: every trigger in this project goes through
        // /process-sequence — dashboard.html and all three test-ui.html
        // copies — so defaulting it here is what makes the log appear no
        // matter which button was pressed, without each UI having to know
        // about the flag. Cost is one CSV per camera per run; identity
        // tuning is impossible without it. Set VISION_DEBUG_IDENTITY=false
        // once thresholds are settled, or override per request with
        // debug_identity=0.
        'debug_identity' => env('VISION_DEBUG_IDENTITY', true),
    ],

];
