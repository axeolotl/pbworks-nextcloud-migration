<?php
/**
 * Copy ordinary export files through Nextcloud's filesystem API.
 * Defaults to preview. No SQL, direct data-directory access, overwrites or deletes.
 */
declare(strict_types=1);
require __DIR__ . '/bootstrap.php';
$o = getopt('', ['nextcloud:', 'user:', 'groupfolder-id:', 'team-mount:',
    'destination:', 'bundle:', 'apply']);
try {
    foreach (['nextcloud', 'user', 'groupfolder-id', 'team-mount', 'destination', 'bundle'] as $key) {
        if (!isset($o[$key])) {
            throw new RuntimeException("Missing --$key");
        }
    }
    $bundle = realpath($o['bundle']);
    if (!$bundle) {
        throw new RuntimeException('Bundle missing');
    }
    $manifest = json_decode(file_get_contents($bundle . '/manifest.json'), true, 512, JSON_THROW_ON_ERROR);
    if (($manifest['schema'] ?? null) !== 1 || ($manifest['ready'] ?? false) !== true) {
        throw new RuntimeException('Bundle not ready');
    }
    bootNextcloud(realpath($o['nextcloud']), $o['user']);
    $root = \OC::$server->getRootFolder()->getUserFolder($o['user']);
    $team = $root->get(safeRelative($o['team-mount']));
    if (!$team instanceof \OCP\Files\Folder) {
        throw new RuntimeException('Team mount is not a folder');
    }
    $mount = $team->getMountPoint();
    if (!$mount instanceof \OCA\GroupFolders\Mount\GroupMountPoint
        || $mount->getFolderId() !== (int)$o['groupfolder-id']) {
        throw new RuntimeException('The selected user-visible folder is NOT the requested Team folder');
    }
    $destination = safeRelative($o['destination']);
    $apply = isset($o['apply']);
    $plan = [];
    // Preflight the complete file set before creating anything.
    foreach ($manifest['team_files'] as $rel => $record) {
        safeRelative($rel);
        $local = $bundle . '/team-files/' . $rel;
        $resolved = realpath($local);
        if (!$resolved || !str_starts_with($resolved, $bundle . '/team-files/')
            || hash_file('sha256', $local) !== $record['sha256']) {
            throw new RuntimeException('Source missing/changed/escaping: ' . $rel);
        }
        $target = $destination . '/' . $rel;
        // Refuse files in place of parent folders, even before any writes.
        $parts = explode('/', $target);
        array_pop($parts);
        $prefix = '';
        foreach ($parts as $part) {
            $prefix = ltrim($prefix . '/' . $part, '/');
            if ($team->nodeExists($prefix) && !$team->get($prefix) instanceof \OCP\Files\Folder) {
                throw new RuntimeException('Parent is not a folder: ' . $prefix);
            }
        }
        $existing = $team->nodeExists($target);
        if ($existing) {
            $node = $team->get($target);
            if (!$node instanceof \OCP\Files\File) {
                throw new RuntimeException('Target is not a file: ' . $target);
            }
            $s = $node->fopen('r');
            if (!is_resource($s)) {
                throw new RuntimeException('Cannot read existing target: ' . $target);
            }
            $h = hash_init('sha256');
            hash_update_stream($h, $s);
            fclose($s);
            if (hash_final($h) !== $record['sha256']) {
                throw new RuntimeException('CONFLICT: existing different file; never overwritten: ' . $target);
            }
        }
        $plan[$rel] = [$target, $existing];
        fwrite(STDERR, ($existing ? 'KEEP ' : 'COPY ') . $target . "\n");
    }
    $index = ['complete' => $apply, 'user' => $o['user'],
        'groupfolder_id' => (int)$o['groupfolder-id'], 'mount' => $o['team-mount'],
        'destination' => $destination, 'files' => []];
    if ($apply) {
        foreach ($plan as $rel => [$target, $existing]) {
            $record = $manifest['team_files'][$rel];
            if (!$existing) {
                $parts = explode('/', $target);
                $name = array_pop($parts);
                $folder = $team;
                foreach ($parts as $part) {
                    $folder = $folder->nodeExists($part) ? $folder->get($part) : $folder->newFolder($part);
                }
                // Re-check at the point of creation; do not overwrite a concurrent upload.
                if ($folder->nodeExists($name)) {
                    throw new RuntimeException('Target appeared during copy: ' . $target);
                }
                $stream = fopen($bundle . '/team-files/' . $rel, 'rb');
                if (!$stream) {
                    throw new RuntimeException('Cannot open source: ' . $rel);
                }
                try {
                    $node = $folder->newFile($name, $stream);
                } finally {
                    fclose($stream);
                }
                $node->touch((int)$record['mtime']);
            } else {
                $node = $team->get($target);
            }
            $stream = $node->fopen('r');
            if (!is_resource($stream)) {
                throw new RuntimeException('Cannot verify destination: ' . $target);
            }
            $h = hash_init('sha256');
            hash_update_stream($h, $stream);
            fclose($stream);
            if (hash_final($h) !== $record['sha256']) {
                throw new RuntimeException('Destination checksum mismatch: ' . $target);
            }
            $index['files'][$rel] = ['id' => $node->getId(), 'sha256' => $record['sha256']];
        }
    }
    echo json_encode($index, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR), "\n";
} catch (\Throwable $e) {
    fwrite(STDERR, "ERROR: " . $e->getMessage() . "\n");
    exit(1);
}
