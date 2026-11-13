"""Private worker for the native macOS app; the SwiftBar plugin keeps its own entry."""
import json
from pathlib import Path
import sys
import time

from swiftbar_claude_local import publish
import swiftbar_plugin as backend


def snapshot():
    backend.RENDER_MEMO={}
    try:
        data=backend.cached_data()
        backend.write_json_cache(backend.cache_path('panel-view'),backend.panel_snapshot(data))
    finally:backend.RENDER_MEMO=None


def main(args):
    if args==['paths']:
        print(json.dumps({'snapshot':backend.cache_path('panel-view')}))
        return 0
    if args==['smoke-test']:
        root=Path(backend.STATE_DIR)/'smoke-fixture';root.mkdir(parents=True,exist_ok=True)
        (root/'usage.jsonl').write_text(json.dumps({'t':time.time()-10,'agent':'codex','provider':'test','model':'gpt-5.4','in':1230,'out':4})+'\n',encoding='utf-8')
        settings=backend.preferences.read(backend.STATE_DIR)
        settings.update(visible=['summary','heatmap','magpie'],paths={'magpie':str(root)})
        backend.preferences.write(backend.STATE_DIR,settings)
        backend.range_usage_cached('all')
        backend.write_json_cache(backend.cache_path('view-range'),{'schema':1,'range':'all'})
        snapshot()
        view=backend.read_cache('panel-view')
        assert view['dashboard']['providers'][0]['tokens']==1234
        assert sum(view['dashboard']['heatmap']['tokens'])==1234
        assert backend.sources.pricing('model-pricing.json').get('source',{}).get('commit')
        import swiftbar_runtime
        swiftbar_runtime.activate_vendor()
        from curl_cffi import requests
        requests.Session(impersonate='chrome').close()
        import swiftbar_sources
        zstd=swiftbar_sources.zstd_codec()
        assert zstd.decompress(zstd.compress(b'archive'))==b'archive'
        publish(backend.STATE_DIR,{},refresh=False)
        return 0
    if len(args)!=1 or args[0] not in ('prepare','refresh','action'):return 2
    backend.sync_connection_cache()
    if args==['prepare']:
        if not (Path(backend.STATE_DIR)/'preferences.json').exists():
            backend.discovery.initialize(backend.STATE_DIR,backend.detected_modules())
        snapshot()
    elif args==['action']:
        backend.consume_panel_action()
        # Publish before doing long reads; clicks are acknowledged independently.
        snapshot()
    else:
        backend.background_refresh()
        snapshot()
    return 0


if __name__=='__main__':sys.exit(main(sys.argv[1:]))
