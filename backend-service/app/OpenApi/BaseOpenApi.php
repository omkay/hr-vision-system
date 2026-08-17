<?php

namespace App\OpenApi;

use OpenApi\Attributes as OA;

/**
 * Root OpenAPI metadata — swagger-php has no natural single place to attach
 * document-level attributes (Info, Servers, SecurityScheme), so by convention
 * they live on one otherwise-empty class that just gets scanned like any
 * other file under app/ (see config/l5-swagger.php's `paths.annotations`).
 */
#[OA\Info(
    version: '1.0.0',
    title: 'Hr_SmartPay API',
    description: 'Employee HR management + camera-based activity tracking. '
        . 'Most endpoints require a Sanctum bearer token obtained from POST /login.',
)]
#[OA\Server(
    url: 'http://localhost:8080/api',
    description: 'Local Docker Compose stack',
)]
#[OA\SecurityScheme(
    securityScheme: 'bearerAuth',
    type: 'http',
    scheme: 'bearer',
    description: 'Sanctum personal access token — obtained from POST /login, sent as `Authorization: Bearer <token>`.',
)]
#[OA\Tag(name: 'Auth', description: 'Login/logout.')]
#[OA\Tag(name: 'Zones', description: 'Named regions cameras are assigned to (work_area / common_area).')]
#[OA\Tag(name: 'Cameras', description: 'Camera registration and uploaded footage.')]
#[OA\Tag(name: 'Processing', description: 'Triggers the vision service to scan camera footage and returns activity events.')]
#[OA\Tag(name: 'Employees', description: 'Employee records.')]
#[OA\Tag(name: 'Employee Photos', description: 'Face/body reference photos used for vision-service enrollment.')]
#[OA\Tag(name: 'Checkin', description: 'Kiosk-style photo checkin, matched against enrolled employees.')]
class BaseOpenApi
{
    // Intentionally empty — see class docblock.
}
