from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .ocr import detect, group_lines, missed_candidates, vision_ocr
from .translate import Budget, Cancelled, Translator, FatalTranslationError
from .typeset import layout_issues, preview


def text_issues(d, glossary):
    result = []
    if not d['groups']:
        result.append({'severity': 'warning', 'kind': 'empty_ocr', 'ids': [],
                       'message': '没有识别到正文；请确认这页确实只有画面。'})
    if d.get('ocr_candidates'):
        result.append({'severity': 'warning', 'kind': 'missed_text', 'ids': [],
                       'message': f'二次识别发现{len(d["ocr_candidates"])}处疑似漏字，请加入或忽略。'})
    for g in d['groups']:
        if g.get('role') != 'dialogue':
            continue
        ids, en, zh = [g['id']], g['text'], g.get('zh', '')
        if g.get('confidence', 1) < .78:
            result.append({'severity': 'warning', 'kind': 'ocr_confidence', 'ids': ids,
                           'message': 'OCR置信度较低，请对照原图核实。'})
        if not zh:
            continue
        source_numbers = set(re.findall(r'\d+(?:[.,]\d+)*', en))
        if source_numbers and not source_numbers.issubset(set(re.findall(r'\d+(?:[.,]\d+)*', zh))):
            result.append({'severity': 'warning', 'kind': 'numbers', 'ids': ids,
                           'message': '原文数字与译文数字写法不同，请核对数值及单位。'})
        if re.search(r"\b(?:not|never|no|don't|cannot|can't|isn't|won't)\b", en, re.I) and not re.search('[不未无别没非勿]', zh):
            result.append({'severity': 'warning', 'kind': 'negation', 'ids': ids,
                           'message': '原文含否定词，译文未检出常见否定表达，请确认没有反译。'})
        for word, numeral in [('hundred', '百'), ('thousand', '千'), ('million', '百万'), ('billion', '亿')]:
            if re.search(r'\b'+word+r'\b', en, re.I) and not re.search(r'\d', zh) and not any(c in zh for c in numeral):
                result.append({'severity': 'warning', 'kind': 'magnitude', 'ids': ids,
                               'message': f'请核对数量级：{word}，避免把“三百年”译成“三年”。'})
        for source, target in glossary.items():
            if re.search(re.escape(source), en, re.I) and target not in zh:
                result.append({'severity': 'warning', 'kind': 'term', 'ids': ids,
                               'message': f'请核对术语：{source} → {target}'})
    return result


def process_page(project, page_id, config, key, stop, budget):
    if stop.is_set():
        raise Cancelled()
    d = project.load_page(page_id)
    # Existing or manually imported translations are never silently overwritten.
    if d.get('status') == 'imported' or not d.get('ocr_complete') and not d['groups']:
        if config['ocr_mode'] == 'vision':
            first = vision_ocr(d['source'], config, key, stop, budget)
        else:
            first = detect(d['source'], config['ocr_mode'], 736)
        d['groups'] = group_lines(first)
        d['ocr_complete'] = True
        d['status'] = 'recognized'
        project.save_page(d)
    if config.get('secondary_ocr') and config['ocr_mode'] != 'vision' and not d.get('secondary_complete'):
        if stop.is_set():raise Cancelled()
        first=[l for g in d['groups'] for l in g.get('lines',[])]
        second=detect(d['source'],config['ocr_mode'],1280)
        d['ocr_candidates']=missed_candidates(first,second)
        d['secondary_complete']=True
        project.save_page(d)
    if stop.is_set():
        raise Cancelled()
    translator = Translator(config, key, stop, budget, project.manifest.get('glossary', {}))
    translator.translate(d['groups'])
    d['status'] = 'translated'
    d.pop('error', None)
    project.save_page(d)  # checkpoint before the more expensive preview/typesetting stage
    _, report = preview(d, max_edge=600)
    d['issues'] = text_issues(d, project.manifest.get('glossary', {})) + layout_issues(d, report)
    d['status'] = 'review' if d['issues'] else 'ready'
    d['reviewed'] = False
    project.save_page(d)
    return d


def run(project, config, key, stop, emit=lambda *a: None, page_ids=None):
    pages = [p for p in project.ordered() if page_ids is None or p['id'] in page_ids]
    pages=[p for p in pages if p.get('status') in {'imported','recognized','translated','error'} or
           any(g.get('role')=='dialogue' and not g.get('zh','').strip() for g in project.load_page(p['id'])['groups'])]
    needs_api=any(p.get('status') in {'imported','recognized','error'} or
                  any(g.get('role')=='dialogue' and not g.get('zh','').strip() for g in project.load_page(p['id'])['groups']) for p in pages)
    if config['engine']!='quick' and needs_api and not key:raise ValueError('请先填写API密钥；不会先空跑整本OCR。')
    if config['ocr_mode']=='vision' and any(p.get('status')=='imported' for p in pages) and not key:raise ValueError('视觉OCR需要API密钥。')
    budget = Budget(max(1, int(config.get('max_requests', 2000))))
    start = time.monotonic()
    done = errors = consecutive_errors = 0
    workers=max(1,min(8,int(config.get('workers',4))))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Bound the backlog: stop does not leave thousands of queued page jobs behind.
        iterator = iter(pages)
        pending = {}
        def fill():
            while len(pending) < workers and not stop.is_set():
                try:
                    p = next(iterator)
                except StopIteration:
                    break
                pending[pool.submit(process_page, project, p['id'], config, key, stop, budget)] = p
        fill()
        while pending:
            future = next(as_completed(pending))
            meta = pending.pop(future)
            try:
                d = future.result()
                done += 1
                consecutive_errors=0
                emit('page', d['id'], d['status'])
            except Cancelled:
                pass
            except Exception as exc:
                errors += 1
                consecutive_errors+=1
                d = project.load_page(meta['id'])
                d['status'] = 'error'
                d['error'] = str(exc)[:400]
                d['issues'] = [{'severity': 'error', 'kind': 'request', 'ids': [], 'message': d['error']}]
                project.save_page(d)
                emit('page', d['id'], d['status'])
                emit('log', f'{d["chapter"]}/{d["label"]}：{d["error"]}')
                if isinstance(exc,FatalTranslationError) or consecutive_errors>=3:
                    stop.set()
                    emit('log','共享接口错误或连续3页失败，已停止队列，避免空跑。修复后可继续。')
            elapsed = time.monotonic()-start
            emit('progress', done+errors, len(pages), elapsed, budget.snapshot())
            fill()
    return {'done': done, 'errors': errors, 'cancelled': stop.is_set(),
            'seconds': time.monotonic()-start, **budget.snapshot()}
