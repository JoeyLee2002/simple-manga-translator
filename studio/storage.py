"""Portable projects, atomic checkpoints and a thread-safe translation memory."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path

from PIL import Image, ImageOps

APP_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
USER_ROOT = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'MangaTranslatorStudio'
USER_ROOT.mkdir(parents=True, exist_ok=True)
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}


def natural(value):
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r'(\d+)', str(value))]


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.write-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path, default=None):
    if not Path(path).exists():
        return default
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


class TranslationMemory:
    def __init__(self, path=None):
        self.path = Path(path or USER_ROOT / 'translations.sqlite3')
        with self.connection() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('CREATE TABLE IF NOT EXISTS memory (key TEXT PRIMARY KEY, value TEXT NOT NULL)')

    def connection(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def key(text, config, glossary):
        # Endpoint/model/language/glossary changes invalidate cached translations; keys never include secrets.
        obj = [text, config.get('engine'), config.get('endpoint'), config.get('model'),
               config.get('source_lang'), config.get('target_lang'), config.get('context', ''),
               config.get('dialogue_context', ''), glossary, 'prompt-v2']
        return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def get(self, key):
        with self.connection() as c:
            row = c.execute('SELECT value FROM memory WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def put(self, key, value):
        with self.connection() as c:
            c.execute('INSERT OR REPLACE INTO memory VALUES (?,?)', (key, value))


class Project:
    def __init__(self, root, create=False, name='新翻译项目'):
        self.root = Path(root).resolve()
        self.lock = threading.RLock()
        if create:
            if (self.root / 'project.json').exists():
                raise ValueError('这个目录已经有项目，请使用“打开项目”。')
            self.root.mkdir(parents=True, exist_ok=True)
            self.manifest = {'version': 1, 'name': name, 'pages': [], 'glossary': {}, 'config': {}}
            self.save()
        else:
            self.manifest = read_json(self.root / 'project.json')
            if not self.manifest or self.manifest.get('version') != 1:
                raise ValueError('不是受支持的项目目录。')

    def save(self):
        with self.lock:
            atomic_json(self.root / 'project.json', self.manifest)

    def load_page(self, page_id):
        d = read_json(self.root / 'pages' / f'{page_id}.json')
        if d is None:
            raise ValueError('页面检查点不存在。')
        d['source'] = str(self.root / d['image'])
        return d

    def save_page(self, d):
        with self.lock:
            clean = {k: v for k, v in d.items() if k != 'source'}
            atomic_json(self.root / 'pages' / f'{d["id"]}.json', clean)
            for p in self.manifest['pages']:
                if p['id'] == d['id']:
                    p.update(status=d.get('status', 'imported'), issues=len(d.get('issues', [])))
                    break
            self.save()

    def ordered(self):
        return list(self.manifest['pages'])

    def add_image(self, image, chapter, label, deduplicate=True):
        image = ImageOps.exif_transpose(image).convert('RGB')
        if image.width * image.height > 80_000_000:
            raise ValueError('单页超过8000万像素，请先缩小。')
        # Hash decoded pixels, so identical pages with different file encodings also deduplicate.
        digest = hashlib.sha256(str(image.size).encode() + image.tobytes()).hexdigest()
        with self.lock:
            if deduplicate and any(p['hash'] == digest for p in self.manifest['pages']):
                return None
            page_id = uuid.uuid4().hex[:16]
            relative = f'images/{page_id}.png'
            dst = self.root / relative
            dst.parent.mkdir(exist_ok=True)
            image.save(dst, 'PNG')
            meta = {'id': page_id, 'chapter': str(chapter), 'label': label, 'image': relative,
                    'size': list(image.size), 'hash': digest, 'groups': [], 'manual_bubbles': [],
                    'status': 'imported', 'issues': [], 'revision': 0}
            self.manifest['pages'].append({k: meta[k] for k in ('id', 'chapter', 'label', 'hash', 'status')})
            self.save_page(meta)
        return meta

    def import_paths(self, paths, emit=lambda *a: None, stop=None):
        count = skipped = 0
        candidates = []
        for value in paths:
            p = Path(value)
            if p.is_dir():
                candidates.extend(sorted((f for f in p.rglob('*') if f.is_file() and
                                  not f.resolve().is_relative_to(self.root) and
                                  f.suffix.lower() in IMAGE_EXTS | {'.pdf', '.cbz', '.zip'}), key=natural))
            else:
                candidates.append(p)
        for path in sorted(candidates, key=natural):
            if stop and stop.is_set():
                break
            ext = path.suffix.lower()
            if ext in IMAGE_EXTS:
                with Image.open(path) as image:
                    ok = self.add_image(image, path.parent.name, path.name)
                count += bool(ok)
                skipped += not bool(ok)
            elif ext in {'.cbz', '.zip'}:
                with zipfile.ZipFile(path) as z:
                    names = sorted((n for n in z.namelist() if Path(n).suffix.lower() in IMAGE_EXTS), key=natural)
                    for name in names:
                        if stop and stop.is_set():
                            break
                        info = z.getinfo(name)
                        if info.file_size > 100_000_000:
                            raise ValueError(f'压缩包中的单页过大：{name}')
                        # Decode in memory; never extract archive-controlled paths.
                        with Image.open(io.BytesIO(z.read(name))) as image:
                            ok = self.add_image(image, path.stem, Path(name).name)
                        count += bool(ok)
                        skipped += not bool(ok)
                        emit('import', count, f'{path.name} / {Path(name).name}')
            elif ext == '.pdf':
                import fitz
                with fitz.open(path) as doc:
                    if doc.needs_pass:
                        raise ValueError(f'PDF需要密码：{path.name}')
                    for i, page in enumerate(doc):
                        if stop and stop.is_set():
                            break
                        scale = min(2.0, 2200 / max(page.rect.width, page.rect.height))
                        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                        image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
                        ok = self.add_image(image, path.stem, f'{i+1:04d}.png')
                        count += bool(ok)
                        skipped += not bool(ok)
                        emit('import', count, f'{path.name} 第{i+1}页')
            emit('import', count, path.name)
        return count, skipped

    def import_legacy(self, folder, emit=lambda *a: None, stop=None):
        count = 0
        for path in sorted(Path(folder).rglob('*.json'), key=natural):
            if stop and stop.is_set():
                break
            old = read_json(path)
            if not isinstance(old, dict) or not old.get('source') or 'groups' not in old:
                continue
            src = Path(old['source'])
            if not src.is_file():
                continue
            chapter = f'第{int(old["chapter"]):02d}话' if str(old.get('chapter', '')).isdigit() else path.parent.name
            with Image.open(src) as image:
                d = self.add_image(image, chapter, f'{int(old.get("page", count+1)):03d}')
            if not d:
                continue
            d['groups'] = old['groups']
            d['manual_bubbles'] = old.get('manual_bubbles', [])
            for g in d['groups']:
                g.setdefault('role', 'dialogue')
                g.setdefault('zh', '')
            d['status'] = 'translated'
            d['reviewed'] = True
            d['ocr_complete'] = d['secondary_complete'] = True
            self.save_page(d)
            count += 1
            emit('import', count, path.name)
        return count
