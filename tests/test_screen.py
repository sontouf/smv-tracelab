"""ScreenText rebuilds plain text from a terminal screen stream (what ConPTY sends on Windows)."""
import pytest

pyte = pytest.importorskip('pyte')
from smv_tracelab.proc import ScreenText  # noqa: E402


def test_plain_lines_and_partial_prompt():
    st = ScreenText()
    out = st.feed('*** banner\r\n\r\nNuSMV > ')
    assert out == '*** banner\n\n'
    assert st.partial() == 'NuSMV >'


def test_cursor_moves_padding_and_echo():
    st = ScreenText()
    st.feed('NuSMV > ')
    # ConPTY repaints the echoed input, pads with blanks and repositions the cursor
    out = st.feed('\x1b[1;9Hgo' + ' ' * 40 + '\x1b[1;11H\r\n\x1b[?25lNuSMV > \x1b[?25h')
    assert out == 'NuSMV > go\n'
    assert st.partial() == 'NuSMV >'
    out = st.feed('\r\n  \x1b[2Cbtn : boolean\r\nNuSMV > ')
    assert out == 'NuSMV >\n    btn : boolean\n'


def test_scrolling_keeps_every_line_once():
    st = ScreenText()
    lines = ['line %d' % i for i in range(ScreenText.ROWS * 3)]
    out = ''
    for i in range(0, len(lines), 7):
        out += st.feed(''.join(l + '\r\n' for l in lines[i:i + 7]))
    out += st.feed('NuSMV > ')
    assert out.split('\n')[:-1] == lines
    assert st.partial() == 'NuSMV >'
