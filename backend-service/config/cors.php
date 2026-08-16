<?php

return [

    /*
    |--------------------------------------------------------------------------
    | Cross-Origin Resource Sharing (CORS) Configuration
    |--------------------------------------------------------------------------
    |
    | Added to support the standalone test UI (public/test-ui.html or any
    | dev server serving it from a different origin/port than the API).
    | Auth here is a Bearer token in the Authorization header, not cookies,
    | so `supports_credentials` stays false and a wildcard origin is safe.
    |
    */

    'paths' => ['api/*', 'sanctum/csrf-cookie'],

    'allowed_methods' => ['*'],

    'allowed_origins' => ['*'],

    'allowed_origins_patterns' => [],

    'allowed_headers' => ['*'],

    'exposed_headers' => [],

    'max_age' => 0,

    'supports_credentials' => false,

];
