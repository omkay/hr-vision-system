<?php

// Config for darkaonline/l5-swagger — generates the OpenAPI docs UI for this
// API at /api/documentation (see routes.docs below for the raw JSON path).
// Written by hand instead of via `artisan vendor:publish`, since backend-service
// has no bind-mount into the running container — anything vendor:publish writes
// inside the container is lost on the next rebuild, but a file here on the host
// gets baked into the image by the Dockerfile's `COPY . .` step.

return [
    'default' => 'default',

    'documentations' => [
        'default' => [
            'api' => [
                'title' => 'Hr_SmartPay API',
            ],

            'routes' => [
                // Swagger UI page.
                'api' => 'api/documentation',
                // Route used to serve the generated JSON/YAML spec that the UI reads.
                'docs' => 'docs',
                // Route used for the Oauth2 authentication callback.
                'oauth2_callback' => 'api/oauth2-callback',
            ],

            'routes_middleware' => [
                'api' => [],
                'asset' => [],
                'docs' => [],
                'oauth2_callback' => [],
            ],

            'paths' => [
                // Absolute path to the directory(ies) containing the OA attribute
                // annotations swagger-php scans. App\ covers every controller.
                'annotations' => [
                    base_path('app'),
                ],

                'docs' => storage_path('api-docs'),
                'docs_json' => 'api-docs.json',
                'docs_yaml' => 'api-docs.yaml',

                'format_to_use_for_docs' => env('L5_FORMAT_TO_USE_FOR_DOCS', 'json'),

                'excludes' => [],

                'base' => env('L5_SWAGGER_BASE_PATH', null),
            ],
        ],
    ],

    'defaults' => [
        'routes' => [
            'docs' => 'docs',
            'oauth2_callback' => 'api/oauth2-callback',
        ],

        'routes_middleware' => [
            'api' => [],
            'asset' => [],
            'docs' => [],
            'oauth2_callback' => [],
        ],

        'paths' => [
            'docs' => storage_path('api-docs'),
            'views' => base_path('resources/views/vendor/l5-swagger'),
            'base' => env('L5_SWAGGER_BASE_PATH', null),
            'excludes' => [],
        ],

        'scanOptions' => [
            'default_processors_configuration' => [],
            'analyser' => null,
            'analysis' => null,
            'processors' => [],
            'pattern' => null,
            'exclude' => [],
            // Literal instead of \L5Swagger\Generator::OPEN_API_DEFAULT_SPEC_VERSION —
            // config files get loaded (via `artisan package:discover`, which composer's
            // post-autoload-dump hook runs) before the package is necessarily installed
            // yet, e.g. mid Docker build if composer.json/lock were updated but the
            // vendor dir wasn't rebuilt in the same step. A class constant reference
            // here throws "Class not found" and fails the whole build; '3.0.0' is
            // l5-swagger's own default value for this key, just spelled out directly.
            'open_api_spec_version' => '3.0.0',
        ],

        'securityDefinitions' => [
            'securitySchemes' => [
                // This app authenticates with Sanctum bearer tokens returned by
                // POST /api/login — matches how test-ui.html and every other
                // client hits the API (`Authorization: Bearer <token>`).
                'bearerAuth' => [
                    'type' => 'http',
                    'description' => 'Sanctum personal access token — obtained from POST /api/login.',
                    'scheme' => 'bearer',
                ],
            ],
            'security' => [
                [
                    'bearerAuth' => [],
                ],
            ],
        ],

        // Re-generate the spec on every request in this dev/demo environment —
        // there's no CI step here to run `artisan l5-swagger:generate` after
        // each deploy, and the annotations change often enough during this
        // project's active development that a stale cached spec would be
        // more confusing than the (tiny, local-only) generation cost.
        'generate_always' => env('L5_SWAGGER_GENERATE_ALWAYS', true),

        'generate_yaml_copy' => env('L5_SWAGGER_GENERATE_YAML_COPY', false),

        'proxy' => false,

        'additional_config_url' => null,

        'operations_sort' => env('L5_SWAGGER_OPERATIONS_SORT', null),

        'validator_url' => null,

        'ui' => [
            'display' => [
                'dark_mode' => false,
                'doc_expansion' => 'none',
                'filter' => true,
            ],
            'authorization' => [
                'persist_authorization' => true,
            ],
        ],
    ],
];
