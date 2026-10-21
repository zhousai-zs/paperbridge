"""Offline Markdown/MathML preview. All document HTML is sanitized."""
from html import escape, unescape
import re
from urllib.parse import quote, urlsplit

from markdown_it import MarkdownIt
import nh3


def math_html(expression, display=False):
    try:
        from latex2mathml.converter import convert
        return convert(expression.strip(), display='block' if display else 'inline')
    except Exception:
        return '<code class="math-fallback">' + escape(expression) + '</code>'


def inline_math(state, silent):
    start = state.pos
    if state.src[start] != '$' or state.src.startswith('$$', start):
        return False
    if start + 1 >= state.posMax or state.src[start + 1].isspace():
        return False
    end = start + 1
    while True:
        end = state.src.find('$', end)
        if end < 0 or end >= state.posMax:
            return False
        if state.src[end - 1] != '\\':
            break
        end += 1
    if end == start + 1 or '\n' in state.src[start:end]:
        return False
    if not silent:
        token = state.push('math_inline', '', 0)
        token.content = state.src[start + 1:end]
    state.pos = end + 1
    return True


def block_math(state, start, end, silent):
    line = state.src[state.bMarks[start] + state.tShift[start]:state.eMarks[start]]
    if not line.startswith('$$'):
        return False
    if silent:
        return True
    content = line[2:]
    next_line = start + 1
    if content.rstrip().endswith('$$'):
        content = content.rstrip()[:-2]
    else:
        parts = [content]
        while next_line < end:
            line = state.src[state.bMarks[next_line]:state.eMarks[next_line]]
            next_line += 1
            if line.rstrip().endswith('$$'):
                parts.append(line.rstrip()[:-2])
                break
            parts.append(line)
        else:
            return False
        content = '\n'.join(parts)
    token = state.push('math_block', '', 0)
    token.content = content
    token.map = [start, next_line]
    state.line = next_line
    return True


MATH_TAGS = set('math mrow mi mn mo ms mtext mspace mfrac msqrt mroot mstyle msub msup msubsup '
                'munder mover munderover mtable mtr mtd menclose mpadded mphantom mfenced '
                'mlabeledtr semantics annotation mmultiscripts mprescripts none'.split())
MATH_ATTRS = set('xmlns display mathvariant mathsize mathcolor mathbackground stretchy fence '
                 'separator accent accentunder largeop movablelimits lspace rspace width height '
                 'depth voffset columnalign rowalign columnspacing rowspacing columnlines rowlines '
                 'columnspan rowspan linethickness notation bevelled scriptlevel displaystyle '
                 'minsize maxsize encoding'.split())


def render_markdown(text, asset_prefix=''):
    parser = MarkdownIt('commonmark', {'html': True}).enable('table')
    parser.inline.ruler.before('escape', 'math_inline', inline_math)
    parser.block.ruler.before('fence', 'math_block', block_math)
    parser.add_render_rule('math_inline', lambda self, tokens, idx, options, env: math_html(tokens[idx].content))
    parser.add_render_rule('math_block', lambda self, tokens, idx, options, env: math_html(tokens[idx].content, True))
    attrs = {k: set(v) for k, v in nh3.ALLOWED_ATTRIBUTES.items()}
    attrs.update({tag: MATH_ATTRS for tag in MATH_TAGS})
    attrs.update({'img': {'src', 'alt', 'title', 'width', 'height'},
                  'td': {'colspan', 'rowspan', 'align'}, 'th': {'colspan', 'rowspan', 'align'},
                  'code': {'class'}})
    html = nh3.clean(parser.render(text), tags=nh3.ALLOWED_TAGS | MATH_TAGS | {'img', 'small', 'sup', 'sub'},
                     attributes=attrs, url_schemes={'http', 'https', 'mailto'},
                     strip_comments=True)

    def local_url(match):
        attr, value = match[1], unescape(match[2])
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            return '' if attr == 'src' else match[0]
        if value.startswith('#'):
            return match[0]
        if value.startswith('/') or '\\' in value:
            return ''
        return f'{attr}="{escape(asset_prefix + quote(parsed.path, safe="/-%"), quote=True)}"'

    return re.sub(r'\b(src|href)="([^"]*)"', local_url, html)
