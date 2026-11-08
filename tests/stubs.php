<?php
declare(strict_types=1);
namespace OCP\Files { interface File { public function getContent(); } interface IMimeTypeDetector { public function detectPath($p); } }
namespace OCP { interface IUser { public function getUID(); } }
namespace OCA\Collectives\Db { class Collective { public function getId(): int { return 1; } public function getUrlPath(): string { return 'Wiki'; } } }
namespace OCA\Collectives\Model {
  class PageInfo { public function __construct(public int $id, public string $title, public int $parentId, public string $path) {}
    public function getId(): int { return $this->id; } public function getTitle(): string { return $this->title; } public function getParentId(): int { return $this->parentId; } }
}
namespace OCA\Collectives\Fs { class NodeHelper { public static function putContent($file, string $c): void { $file->content = $c; } } }
namespace OCA\Collectives\Service {
  use OCA\Collectives\Model\PageInfo;
  class ServiceException extends \Exception {} class NotFoundException extends ServiceException {} class NotPermittedException extends ServiceException {}
  class FakeFile implements \OCP\Files\File { public function __construct(public string $content) {} public function getContent() { return $this->content; } }
  class PageService {
    public array $pages = []; public array $files = []; private int $next = 100;
    public function __construct() { $this->pages[1] = new PageInfo(1, 'Readme', 0, ''); $this->files[1] = new FakeFile(''); }
    public function createBase($cid, $parentId, $title, $t, $uid, $d, $content): PageInfo {
      $pid = $parentId ?: 1; $id = $this->next++;
      $path = trim(($this->pages[$pid]->path ?? '') . '/' . $title, '/');
      $this->pages[$id] = new PageInfo($id, $title, $pid, $path); $this->files[$id] = new FakeFile($content); return $this->pages[$id]; }
    public function getOrCreate($cid, $parentId, $title, $uid): PageInfo { return $this->createBase($cid, $parentId, $title, null, $uid, null, ''); }
    public function findByFileId($cid, $id, $uid, $p = null): PageInfo { if (!isset($this->pages[$id])) throw new NotFoundException('nf'); return $this->pages[$id]; }
    public function findAll($cid, $uid): array { return array_values($this->pages); }
    public function findByPath($cid, $path, $uid, $parentId = null): PageInfo {
      if ($path === '' || $path === '/') return $this->pages[1];
      $parent = 1; $m = null;
      foreach (explode('/', $path) as $t) { $m = null; foreach ($this->pages as $p) { if ($p->title === $t && $p->parentId === $parent) { $m = $p; break; } } if (!$m) throw new NotFoundException('nf'); $parent = $m->id; }
      return $m; }
    public function getPageFile($cid, $id, $uid) { return $this->files[$id]; }
    public function getPageLink(string $c, PageInfo $p): string { return '/apps/collectives/' . $c . '/' . implode('/', array_map('rawurlencode', explode('/', $p->path))) . '?fileId=' . $p->id; }
  }
  class AttachmentService { public array $uploads = []; public function putAttachment($pageFile, string $name, string $data): string { $this->uploads[] = $name; return '.attachments.X/' . $name; } }
  class Reporter implements IProgressReporter { public array $lines = [];
    public function writeInfo(string $m): void { $this->lines[] = "I $m"; } public function writeInfoVerbose(string $m): void {}
    public function writeError(string $m): void { $this->lines[] = "E $m"; } public function writeErrorVerbose(string $m): void { $this->lines[] = "V $m"; } }
}
