<?php
// Run ONLY via CLI as the Nextcloud web-server user. No HTTP endpoint.
declare(strict_types=1);
if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit(1);
}
function bootNextcloud(string $root, string $uid): void {
    if (!is_file($root . '/lib/base.php')) {
        throw new RuntimeException('Invalid Nextcloud root');
    }
    define('OC_CONSOLE', 1);
    chdir($root);
    require_once $root . '/lib/base.php';
    if (\OC::$server->getConfig()->getSystemValueBool('maintenance', false)) {
        throw new RuntimeException('Nextcloud is in maintenance mode');
    }
    $user = \OC::$server->getUserManager()->get($uid);
    if (!$user) {
        throw new RuntimeException('Unknown Nextcloud user');
    }
    \OC::$server->getUserSession()->setUser($user);
    \OC_Util::setupFS($uid);
    \OC_App::loadApps();
}
function safeRelative(string $path): string {
    if ($path === '' || str_starts_with($path, '/') || str_contains($path, '\\')
        || str_contains($path, "\0")) {
        throw new RuntimeException('Unsafe relative path');
    }
    foreach (explode('/', $path) as $part) {
        if ($part === '' || $part === '.' || $part === '..') {
            throw new RuntimeException('Unsafe path segment: ' . $path);
        }
    }
    return $path;
}
