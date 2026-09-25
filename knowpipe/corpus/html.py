"""Extract readable HTML without flattening code or fabricating image contents."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


class TextExtractor(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
    BLOCK = {'p', 'li', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'dt', 'dd', 'tr', 'div', 'section', 'article', 'blockquote'}

    def __init__(self, base_url='', selector=None, title_tags=('h1',), table_cells=False):
        super().__init__(convert_charrefs=True)
        self.base_url, self.selector = base_url, selector
        self.stack, self.parts, self.images, self.title = [], [], [], []
        self.active_depth = 0 if selector is None else None
        self.found = selector is None
        self.skip_depth = None
        self.pre_depth = None
        self.pre_start = None
        self.code_spans = []
        self.title_depth = None
        self.title_tags = title_tags
        self.table_cells = table_cells

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag not in self.VOID:
            self.stack.append(tag)
        depth = len(self.stack)
        if self.active_depth is None:
            if self.selector(tag, attrs):
                self.active_depth, self.found = depth, True
            else:
                return
        if self.skip_depth is not None:
            return
        if tag in {'script', 'style', 'nav'} or 'headerlink' in attrs.get('class', '').split():
            self.skip_depth = depth
            return
        if tag in self.BLOCK:
            self.parts.append('\n\n')
        if self.table_cells and tag in {'td', 'th'}:
            self.parts.append(' | ')
        if tag == 'pre':
            self.pre_depth = depth
            self.pre_start = len(self.parts)
            self.parts.append('```\n')
        if tag in self.title_tags and not self.title:
            self.title_depth = depth
        if tag == 'br':
            self.parts.append('\n')
        if tag == 'img':
            image = urljoin(self.base_url, attrs.get('src', ''))
            if urlsplit(image).scheme in {'http', 'https'}:
                self.images.append(image)
                self.parts.append('\n[Image: ' + attrs.get('alt', '').strip() + '](' + image + ')\n')

    def handle_endtag(self, tag):
        if tag in self.VOID or tag not in self.stack:
            return
        depth = len(self.stack) - self.stack[::-1].index(tag)
        if self.active_depth is not None:
            if self.skip_depth is not None:
                if depth <= self.skip_depth:
                    self.skip_depth = None
            else:
                if self.pre_depth is not None and depth <= self.pre_depth:
                    self.parts.append('\n```\n\n')
                    self.code_spans.append((self.pre_start, len(self.parts)))
                    self.pre_start = None
                    self.pre_depth = None
                if tag in self.BLOCK:
                    self.parts.append('\n\n')
                if self.title_depth is not None and depth <= self.title_depth:
                    self.title_depth = None
            if self.selector is not None and depth <= self.active_depth:
                self.active_depth = None
        del self.stack[depth - 1:]

    def handle_data(self, data):
        if self.active_depth is None or self.skip_depth is not None:
            return
        if self.title_depth is not None:
            self.title.append(data)
        # HTML layout whitespace outside code is not source-code indentation.
        self.parts.append(data if self.pre_depth is not None else re.sub(r'\s+', ' ', data))

    def finish(self):
        self.close()
        if self.pre_start is not None:
            self.parts.append('\n```')
            self.code_spans.append((self.pre_start, len(self.parts)))
            self.pre_start = None
        def prose(parts):
            text = re.sub(r' *\n *', '\n', ''.join(parts))
            return re.sub(r'\n{3,}', '\n\n', text)
        # Normalize only prose. Code containing Markdown fences remains verbatim too.
        result, position = [], 0
        for start, end in self.code_spans:
            result.extend((prose(self.parts[position:start]), ''.join(self.parts[start:end])))
            position = end
        result.append(prose(self.parts[position:]))
        return ''.join(result).replace('\r\n', '\n').strip(), list(dict.fromkeys(self.images))


def extract_html(html, *, base_url='', selector=None):
    parser = TextExtractor(base_url, selector)
    parser.feed(html)
    text, images = parser.finish()
    if not parser.found:
        raise ValueError('main_content_missing')
    return text, images
