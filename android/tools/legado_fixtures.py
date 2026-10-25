"""Create original TXT and illustrated EPUB files for the native handoff check."""
from pathlib import Path
import json
import sys
import zipfile

directory = Path(sys.argv[1])
suffix = sys.argv[2]
directory.mkdir(parents=True, exist_ok=True)
txt_title = '山间来信' + suffix
epub_title = '海边来信' + suffix
paragraph = '邮差沿着山路走来，把信放在窗台。屋内的人收好书签，推开窗看见远处的云。信里记着一路遇见的河流、旧桥和点着灯的房子，也记着每一位愿意等他讲完故事的人。'
chapters = [(f'第{index + 1}章 ' + name, '\n\n'.join(f'第{row + 1}封来信。{paragraph}{paragraph}' for row in range(10)))
            for index, name in enumerate(('山风', '回声', '归途'))]
(directory / 'letter.txt').write_bytes(('\n\n'.join(title + '\n' + text for title, text in chapters)).encode('gb18030'))
with zipfile.ZipFile(directory / 'illustrated.epub', 'w') as book:
    book.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
    book.writestr('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    book.writestr('content.opf', f'''<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" unique-identifier="id" version="2.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">bookpack-legado-{suffix}</dc:identifier><dc:title>{epub_title}</dc:title><dc:creator>书包原创联调</dc:creator><dc:language>zh-CN</dc:language></metadata><manifest><item id="one" href="one.xhtml" media-type="application/xhtml+xml"/><item id="two" href="two.xhtml" media-type="application/xhtml+xml"/><item id="image" href="ocean.png" media-type="image/png"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx"><itemref idref="one"/><itemref idref="two"/></spine></package>''')
    book.writestr('toc.ncx', f'''<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="bookpack-legado-{suffix}"/></head><docTitle><text>{epub_title}</text></docTitle><navMap><navPoint id="one" playOrder="1"><navLabel><text>第一章 海风</text></navLabel><content src="one.xhtml"/></navPoint><navPoint id="two" playOrder="2"><navLabel><text>第二章 潮声</text></navLabel><content src="two.xhtml"/></navPoint></navMap></ncx>''')
    book.writestr('one.xhtml', '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>第一章 海风</title></head><body><h1>第一章 海风</h1><p>这是书包与 Legado 的原创 EPUB 联调样文。海面插图应当保留。</p><p><img src="ocean.png" alt="海面插图" style="width:100%;"/></p><p>灯塔守望着归来的船只，信纸上还留着海风的气息。</p></body></html>')
    book.writestr('two.xhtml', '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>第二章 潮声</title></head><body><h1>第二章 潮声</h1><p>潮声停下后，我们在这里继续阅读。这是用于检查目录与阅读进度恢复的原创文字。</p></body></html>')
    book.write(Path(__file__).resolve().parents[2] / 'mobile-plan/prototype/assets/ocean.png', 'ocean.png')
print(json.dumps({'txt': txt_title, 'epub': epub_title}, ensure_ascii=False))
