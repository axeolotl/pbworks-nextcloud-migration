<?php
declare(strict_types=1);
require __DIR__ . '/bootstrap.php';
$o = getopt('', ['nextcloud:', 'user:', 'collective-id:']);
try {
    bootNextcloud(realpath($o['nextcloud']), $o['user']);
    $service = \OCP\Server::get(\OCA\Collectives\Service\PageService::class);
    $pages = $service->findAll((int)$o['collective-id'], $o['user']);
    // A fresh Collective has only its landing page.
    if (count($pages) > 1) {
        throw new RuntimeException('Collective is not empty; create a NEW Collective instead');
    }
    echo "Fresh Collective preflight OK; page count: " . count($pages) . "\n";
} catch (\Throwable $e) {
    fwrite(STDERR, 'ERROR: ' . $e->getMessage() . "\n");
    exit(1);
}
