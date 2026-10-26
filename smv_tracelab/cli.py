import argparse
import os
import sys
import threading
import webbrowser

from . import __version__
from .nusmv_bin import LIB_HINT, download_nusmv, find_nusmv
from .session import Session
from .web import serve


def main(argv=None):
    ap = argparse.ArgumentParser(prog='smv-tracelab',
                                 description='NuSMV interactive simulator, trace/FSM viewer and verifier in your browser.')
    ap.add_argument('model', nargs='?', help='.smv file to load on start (optional)')
    ap.add_argument('--root', default='.', help='directory searched for .smv files (default: current directory)')
    ap.add_argument('--nusmv', help='NuSMV executable (default: $NUSMV, PATH, then the auto-downloaded binary)')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8780)
    ap.add_argument('--no-browser', action='store_true', help='do not open a browser window')
    ap.add_argument('--no-download', action='store_true', help='never download NuSMV automatically')
    ap.add_argument('--install-nusmv', action='store_true', help='download the official NuSMV binary and exit')
    ap.add_argument('--version', action='version', version='%(prog)s ' + __version__)
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # e.g. cp1252 consoles on Windows
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass

    if a.install_nusmv:
        print(download_nusmv())
        return 0

    nusmv = find_nusmv(a.nusmv)
    if not nusmv:
        if a.nusmv or a.no_download:
            sys.exit('NuSMV executable not found. Pass --nusmv or set the NUSMV environment variable.')
        try:
            nusmv = download_nusmv()
        except Exception as e:
            sys.exit('Could not download NuSMV: %s\nInstall it yourself (https://nusmv.fbk.eu) and pass --nusmv.' % e)

    session = Session(nusmv, a.root)
    if session.missing_libs:
        print('WARNING: ' + LIB_HINT % ', '.join(session.missing_libs), file=sys.stderr)
    if a.model:
        st = session.load(path=os.path.abspath(a.model))
        print('model : %s (%s)' % (a.model, 'OK' if st['load_ok'] else 'ERROR - see console in the UI'))
    srv = serve(session, a.host, a.port)
    url = 'http://%s:%d/' % ('127.0.0.1' if a.host in ('0.0.0.0', '::') else a.host, srv.server_address[1])
    print('NuSMV : %s\nUI    : %s   (Ctrl+C to quit)' % (nusmv, url), flush=True)
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        session.close()
    return 0
