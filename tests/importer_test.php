<?php
declare(strict_types=1);
// Usage: php importer_test.php COMPOSER_AUTOLOAD COLLECTIVES_CHECKOUT PATCHED_IMPORTER
require $argv[1];
require $argv[2] . '/lib/Service/IProgressReporter.php';
require __DIR__ . '/stubs.php';
require $argv[2] . '/lib/Fs/MarkdownHelper.php';
require $argv[3];

use OCA\Collectives\Service\{ImportService, PageService, AttachmentService, Reporter};

$root = sys_get_temp_dir() . '/pbworks-test-' . bin2hex(random_bytes(8));
mkdir($root);
mkdir($root . '/pages');
mkdir($root . '/pages/sub');
mkdir($root . '/media');
file_put_contents($root . '/pages/Start.md', <<<'MD'
# Start
[Ziel](sub/Ziel%20%C3%A4.md "Titel")
[Ref][r]

[r]: sub/Ziel%20%C3%A4.md

`[Code](sub/Ziel%20%C3%A4.md)`

```md
[Codeblock](sub/Ziel%20%C3%A4.md)
```

[Anker](#hier)
![Bild](../media/Bild%20neu.png)
MD);
file_put_contents($root . '/pages/sub/Ziel ä.md',
    "[Start](../Start.md#hier)\n[Fehlt](Start.md)\n");
file_put_contents($root . '/pages/sub/Readme.md', "# Unterordner\n");
file_put_contents($root . '/media/Bild neu.png', 'image fixture');
$mime = new class implements \OCP\Files\IMimeTypeDetector {
    public function detectPath($p) { return 'text/markdown'; }
};
$user = new class implements \OCP\IUser {
    public function getUID() { return 'test'; }
};
$ps = new PageService();
$as = new AttachmentService();
$r = new Reporter();
$svc = new ImportService($ps, $as, $mime, $r, new \OCA\Collectives\Db\Collective(), $user);
$svc->importDirectory($root . '/pages', 0);
function check(bool $condition, string $message): void {
    if (!$condition) {
        throw new RuntimeException('FAIL: ' . $message);
    }
    echo 'PASS: ' . $message . "\n";
}
$texts = [];
foreach ($ps->pages as $id => $p) {
    $texts[$p->path] = $ps->files[$id]->content;
}
$start = $texts['Start'];
check(str_contains($start, 'Ziel%20%C3%A4?fileId='), 'percent-decoded target lookup');
check(str_contains($start, '"Titel"'), 'link title preserved');
check(str_contains($start, '[r]: /apps/collectives/'), 'reference definition rewritten');
check(str_contains($start, '`[Code](sub/Ziel%20%C3%A4.md)`'), 'inline code unchanged');
check(str_contains($start, '[Codeblock](sub/Ziel%20%C3%A4.md)'), 'code block unchanged');
check(str_contains($start, '[Anker](#hier)'), 'local anchor unchanged');
check(count($as->uploads) === 1 && $as->uploads[0] === 'Bild neu.png', 'sibling media uploaded');
check(str_contains($start, '.attachments.X/Bild%20neu.png'), 'attachment URL encoded');
check(str_contains($texts['sub/Ziel ä'], '?fileId=100#hier'), 'parent link and fragment');
check(str_contains($texts['sub/Ziel ä'], '[Fehlt](Start.md)'), 'no wrong root fallback');
check(count(array_filter($r->lines, fn($l) => str_contains($l, 'Unresolved link'))) === 1,
    'unresolved link reported without verbose flag');
check($svc->getCount() === 3, 'README imported once');
// Remove only our own temporary fixture tree.
$it = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root,
    FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::CHILD_FIRST);
foreach ($it as $p) {
    $p->isDir() ? rmdir($p->getPathname()) : unlink($p->getPathname());
}
rmdir($root);
