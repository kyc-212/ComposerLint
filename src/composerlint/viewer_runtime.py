"""Lazy, bounded loopback presentation. No source-file routes or musical analysis."""
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import atexit
import hashlib
import json
from pathlib import Path
import secrets
import threading
import time
from composerlint.review import canonical
from composerlint.viewer_models import Selection
from composerlint.viewer_source import fail, ViewerFailure

ASSETS = Path(__file__).with_name('viewer_assets')


def current_source(entry):
    """Keep no-proposal live context bound to the original source bytes."""
    try:
        same = hashlib.sha256(Path(entry['source_path']).read_bytes()).hexdigest() == entry['manifest']['scope']['source_sha256']
    except (OSError, KeyError):
        same = False
    if not same:
        fail('stale_source', 'Original changed or is unavailable. Reopen the score for current context.')


class Runtime:
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = threading.RLock()
        self.server = None
        self.thread = None
        self.max_views, self.max_bytes, self.ttl = 8, 64 * 1024 * 1024, 1800

    def prune(self):
        now = time.monotonic()
        for key in list(self.entries):
            if now - self.entries[key]['touched'] >= self.ttl:
                del self.entries[key]

    def find(self, key):
        with self.lock:
            self.prune()
            entry = self.entries.get(key)
            if entry:
                entry['touched'] = time.monotonic()
                self.entries.move_to_end(key)
            return entry

    @staticmethod
    def selection_value(entry):
        from composerlint.viewer import encode_selection
        if entry['manifest'].get('viewer_schema_version')==2:
            from composerlint.viewer_passage import encode_selection as encode_source
            current_source(entry)
            return {'selection':entry['selected'].model_dump(mode='json') if entry['selected'] else None,
                    'selection_ref':encode_source(entry['selected'],entry) if entry['selected'] else None,
                    'execution_policy':'source_bound_passage_v1','expected_source_snapshot':entry['manifest']['scope']['source_snapshot_id'],
                    'view_instance_id':entry['instance'],'revision':entry['revision'],
                    'notice':'Resolve this current selection with the original path using resolve_score_selection; use its returned passage_request.'}
        return {'selection':entry['selected'].model_dump(mode='json') if entry['selected'] else None,
                'selection_ref':encode_selection(entry['selected']) if entry['selected'] else None,
                'view_instance_id':entry['instance'], 'revision':entry['revision'],
                'notice':'Resolve this selection against the current original source with resolve_score_selection before musical claims.'}

    def comparison_value(self,entry,body=None):
        """Two explicit Original bookmarks, independent of the current live selection."""
        current_source(entry)
        state=entry.get('comparison',{'revision':0,'A':None,'B':None})
        if body is not None:
            if not isinstance(body,dict) or set(body)!={'slot','selection_revision','pair_revision'} or body['slot'] not in ('A','B','clear') or type(body['selection_revision']) is not int or type(body['pair_revision']) is not int:
                fail('invalid_comparison','Invalid comparison capture.')
            if body['pair_revision']!=state['revision'] or body['selection_revision']!=entry['revision']:
                fail('stale_selection','Selection or A/B bookmarks changed. Retrieve fresh context.')
            next_state={**state,'revision':state['revision']+1}
            if body['slot']=='clear':next_state.update(A=None,B=None)
            else:
                selection=entry['selected']
                if selection is None:fail('invalid_selection','Select 1–8 measures before setting a passage.')
                other=state['B' if body['slot']=='A' else 'A']
                if other:
                    s=other['selection']
                    if max(s['start_measure_index'],selection.start_measure_index)<=min(s['end_measure_index'],selection.end_measure_index):
                        fail('invalid_comparison','A and B must be distinct, nonoverlapping passages.')
                next_state[body['slot']]={'selection':selection.model_dump(mode='json'),'captured_selection_revision':entry['revision']}
            size=len(canonical(next_state).encode());delta=size-entry.get('comparison_bytes',0)
            if sum(e['size'] for e in self.entries.values())+delta>self.max_bytes:fail('asset_too_large','View memory budget reached.')
            entry['size']+=delta;entry['comparison_bytes']=size;entry['comparison']=next_state;state=next_state
        binding=({'viewer_schema_version':2,'source_snapshot_id':entry['manifest']['scope']['source_snapshot_id']} if entry['manifest'].get('viewer_schema_version')==2 else {'base_snapshot':entry['manifest']['scope']['snapshot_id']})
        return {'view_instance_id':entry['instance'],'source_sha256':entry['manifest']['scope']['source_sha256'],
                **binding,'options_sha256':entry['manifest']['scope']['options_sha256'],
                'version':'original',**state}

    def start(self):
        if self.server:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, content=b'', mime='text/plain; charset=utf-8'):
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                self.end_headers()
                try:
                    self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def handle_request(self, post=False):
                origin = 'http://' + owner.authority
                if self.headers.get('Host') != owner.authority or self.headers.get('Origin', origin) != origin:
                    return self.reply(403)
                if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                    return self.reply(403)
                pieces = self.path.split('/')
                if len(pieces) != 4 or pieces[1] != 'v':
                    return self.reply(404)
                token, resource = pieces[2:]
                if resource in ('workspace.json','analysis.json','discard','approve-export') or resource.startswith('p-'):
                    body=None
                    if post:
                        try:
                            size=int(self.headers.get('Content-Length','0'))
                            if self.headers.get('Content-Type')!='application/json' or not 0<size<=8192:return self.reply(400)
                            body=json.loads(self.rfile.read(size))
                        except (ValueError,TypeError):return self.reply(400)
                    from composerlint.editing import browser_route
                    code,data,mime=browser_route(token,resource,post,body)
                    return self.reply(code,data,mime)
                with owner.lock:
                    owner.prune()
                    entry = next((e for e in owner.entries.values() if secrets.compare_digest(e['token'], token)), None)
                    if entry is None:
                        return self.reply(410, b'View expired. Ask Codex to recreate it.')
                    entry['touched'] = time.monotonic()
                    owner.entries.move_to_end(entry['instance'])
                    if resource=='comparison':
                        try:
                            body=None
                            if post:
                                size=int(self.headers.get('Content-Length','0'))
                                if self.headers.get('Content-Type')!='application/json' or not 0<size<=1024:return self.reply(400)
                                body=json.loads(self.rfile.read(size))
                            result=owner.comparison_value(entry,body)
                            return self.reply(200,canonical(result).encode(),'application/json')
                        except ViewerFailure as exc:
                            return self.reply(409,canonical({'error':{'code':exc.code,'reason':exc.reason}}).encode(),'application/json')
                        except (ValueError,TypeError):return self.reply(400)
                    if post:
                        if resource != 'selection' or self.headers.get('Content-Type') != 'application/json':
                            return self.reply(400)
                        try:
                            size = int(self.headers.get('Content-Length', '0'))
                            if not 0 < size <= 8192:
                                return self.reply(413)
                            body = json.loads(self.rfile.read(size))
                            source_bound=entry['manifest'].get('viewer_schema_version')==2
                            allowed={'start','end','revision','reference_part_id'} if source_bound else {'start','end','revision'}
                            if set(body) != allowed or type(body['revision']) is not int:
                                return self.reply(400)
                            if body['revision'] != entry['revision'] + 1:
                                return self.reply(409)
                            span = entry['manifest']['scope']
                            if body['start'] is None and body['end'] is None:
                                selected = None
                            else:
                                if type(body['start']) is not int or type(body['end']) is not int:
                                    return self.reply(400)
                                if source_bound:
                                    from composerlint.viewer_models import SourceSelection
                                    current_source(entry)
                                    count=sum(m['part_id']==body['reference_part_id'] for m in entry['manifest']['measures'])
                                    if not 1<=body['start']<=body['end']<=count:return self.reply(400)
                                    selected=SourceSelection.model_validate({**span,'reference_part_id':body['reference_part_id'],
                                        'view_instance_id':entry['instance'],'revision':body['revision'],
                                        'start_measure_index':body['start'],'end_measure_index':body['end']})
                                else:
                                    if not span['start_measure_index'] <= body['start'] <= body['end'] <= span['end_measure_index']:
                                        return self.reply(400)
                                    selected = Selection.model_validate({**span, 'start_measure_index':body['start'], 'end_measure_index':body['end']})
                            entry['selected'] = selected
                            entry['revision'] = body['revision']
                        except (ValueError, TypeError):
                            return self.reply(400)
                    if resource == 'selection':
                        try:return self.reply(200, canonical(owner.selection_value(entry)).encode(), 'application/json')
                        except ViewerFailure as exc:return self.reply(409,canonical({'error':{'code':exc.code,'reason':exc.reason}}).encode(),'application/json')
                    if resource == 'manifest.json':
                        return self.reply(200, entry['manifest_bytes'], 'application/json')
                    if resource == 'score.svg':
                        try:return self.reply(200, owner.page_asset(entry,1), 'image/svg+xml')
                        except ViewerFailure as exc:return self.reply(409,canonical({'error':{'code':exc.code,'reason':exc.reason}}).encode(),'application/json')
                    if resource.startswith('page-') and resource.endswith('.svg'):
                        try:
                            number = int(resource[5:-4])
                            if resource != f'page-{number}.svg' or not 1 <= number <= len(entry['pages']):
                                return self.reply(404)
                        except ValueError:
                            return self.reply(404)
                        try:return self.reply(200, owner.page_asset(entry,number), 'image/svg+xml')
                        except ViewerFailure as exc:return self.reply(409,canonical({'error':{'code':exc.code,'reason':exc.reason}}).encode(),'application/json')
                    if resource.startswith('focus-') and resource.endswith('.json') and not post:
                        try:
                            start, end = map(int, resource[6:-5].split('-'))
                            if resource != f'focus-{start}-{end}.json':
                                return self.reply(400)
                            result = owner.focus(entry, start, end)
                            return self.reply(200, canonical(result).encode(), 'application/json')
                        except (ValueError, ViewerFailure) as exc:
                            return self.reply(400, str(exc).encode())
                    if resource in ('', 'viewer.js', 'viewer.css', 'workspace.js') and not post:
                        name = resource or 'viewer.html'
                        mime = {'viewer.html':'text/html; charset=utf-8', 'viewer.js':'text/javascript; charset=utf-8', 'viewer.css':'text/css; charset=utf-8','workspace.js':'text/javascript; charset=utf-8'}[name]
                        return self.reply(200, (ASSETS/name).read_bytes(), mime)
                    self.reply(404)

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request(True)
        try:
            self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        except OSError:
            fail('client_surface_unavailable', 'Loopback presentation could not start. Text selection remains available.')
        self.server.daemon_threads = True
        self.authority = '127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, name='ComposerLint-viewer', daemon=True)
        self.thread.start()

    def add(self, key, pages, manifest, render_context=None, *, lazy_renderer=None):
        with self.lock:
            self.prune()
            if isinstance(pages, str):
                pages = [pages]
            pages = [s.encode() if isinstance(s, str) else s for s in pages]
            manifest_bytes = canonical(manifest).encode()
            size = sum(len(s) for s in pages if s is not None) + len(manifest_bytes)
            if render_context:
                size += len(render_context[0].encode()) + len(canonical(render_context[1:]).encode())
            if size > min(self.max_bytes, 48 * 1024 * 1024):
                fail('asset_too_large', 'Complete score exceeds its presentation cache budget; use M1 text evidence.')
            self.start()
            while self.entries and (len(self.entries) >= self.max_views or sum(e['size'] for e in self.entries.values()) + size > self.max_bytes):
                self.entries.popitem(last=False)
            entry = dict(key=key, token=secrets.token_urlsafe(32), instance=secrets.token_hex(16),
                         svg=pages[0], pages=pages, manifest=manifest, manifest_bytes=manifest_bytes, size=size,
                         render_context=render_context, lazy_renderer=lazy_renderer,
                         touched=time.monotonic(), selected=None, revision=0)
            self.entries[entry['instance']] = entry
            return entry

    def page_asset(self, entry, number):
        """Recreate full layout when needed; cache only independently checked SVG bytes."""
        from composerlint.review import digest
        with self.lock:
            if not 1 <= number <= len(entry['pages']):
                fail('invalid_page', 'Notation page is outside this score.')
            lazy = entry.get('lazy_renderer')
            if lazy:
                current_source(entry)
            existing = entry['pages'][number - 1]
            if existing is not None:
                return existing
            from composerlint.renderers.verovio import render
            from composerlint.viewer_pages import layout_pages,verify_page
            xml, measures, notes = entry['render_context']
            result = render(xml,width=lazy['width'],notation_page=number)
            pages = layout_pages(result,measures,notes)
            if digest(pages) != lazy['layout_sha256'] or result['package_version'] != lazy['build']:
                fail('mapping_unavailable', 'Renderer layout or build changed since opening.')
            verify_page(result['svg'],pages[number - 1],measures,notes)
            current_source(entry)
            content = result['svg'].encode()
            if (entry['size'] + len(content) > min(self.max_bytes,48*1024*1024) or
                sum(e['size'] for e in self.entries.values()) + len(content) > self.max_bytes):
                fail('asset_too_large', 'Verified page exceeds the existing presentation cache budget.')
            entry['pages'][number - 1] = content
            entry['size'] += len(content)
            return content

    def focus(self, entry, start, end):
        span = entry['manifest']['scope']
        if not span['start_measure_index'] <= start <= end <= span['end_measure_index'] or end-start > 7:
            fail('invalid_selection', 'Focus one to eight source measures.')
        if not entry['render_context']:
            fail('render_failed', 'Reopen a full-score view to focus a passage.')
        from composerlint.renderers.verovio import render
        from composerlint.viewer_source import verify_svg
        xml, measures, notes = entry['render_context']
        selected = [m for m in measures if start <= m['ordinal'] <= end]
        required = {n:notes[n] for m in selected for n in m['note_ids']}
        result = render(xml, start, end, 1800)
        groups = verify_svg(result['svg'], selected, required)
        # Transient focused asset, never cached or allowed to evict musical selection.
        return dict(svg=result['svg'], groups=groups, start=start, end=end)

    def references(self, entry, number=1):
        base = 'http://' + self.authority + '/v/' + entry['token'] + '/'
        resource = 'composerlint://view/' + entry['instance'] + '/'
        name = f'page-{number}.svg'
        return dict(view_instance_id=entry['instance'], viewer_url=base+('' if number==1 else f'#page={number}'), svg_url=base+name,
                    manifest_uri=resource+'manifest.json', svg_uri=resource+name,
                    svg_sha256=hashlib.sha256(self.page_asset(entry,number)).hexdigest())

    def validate_live(self, instance, revision, selection):
        with self.lock:
            self.prune()
            e = self.entries.get(instance)
            if e is None:
                fail('view_expired', 'Live view expired; recreate it or resolve the copied source-bound reference.')
            if e['revision'] != revision or e['selected'] != selection:
                fail('stale_selection', 'Live selection changed; retrieve the current selection again.')
            e['touched'] = time.monotonic()

    def resource(self, uri):
        with self.lock:
            self.prune()
            for e in self.entries.values():
                base = 'composerlint://view/' + e['instance'] + '/'
                if uri == base+'manifest.json':
                    e['touched'] = time.monotonic()
                    return e['manifest_bytes'], 'application/json'
                for number, svg in enumerate(e['pages'], 1):
                    if uri == base+f'page-{number}.svg' or (number == 1 and uri == base+'score.svg'):
                        e['touched'] = time.monotonic()
                        return self.page_asset(e,number), 'image/svg+xml'
        fail('view_expired', 'Unknown or expired view resource.')

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.server = None
        with self.lock:
            self.entries.clear()


runtime = Runtime()
atexit.register(runtime.close)
