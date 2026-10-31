"""Safari icons: explicit PNG, pinned-tab mask and cacheable public responses."""
import io
import struct
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from unittest.mock import Mock
from app.webpage import favicon_assets
from app.webpage import web_ui


class FaviconTests(unittest.TestCase):
    def test_icon_assets_have_expected_formats_and_sizes(self):
        data=favicon_assets.FAVICON_ICO
        self.assertEqual(struct.unpack_from('<HHH',data),(0,1,3))
        self.assertEqual([tuple(data[6+i*16:8+i*16]) for i in range(3)],[(16,16),(32,32),(48,48)])

        for data,size in [(favicon_assets.FAVICON_PNG,32),(favicon_assets.APPLE_TOUCH_ICON_PNG,180)]:
            self.assertEqual(data[:8],b'\x89PNG\r\n\x1a\n')
            self.assertEqual(struct.unpack('>II',data[16:24]),(size,size))
        self.assertEqual(favicon_assets.APPLE_TOUCH_ICON_PNG[25],2,'RGB icon must have no alpha channel')

    def test_all_icon_routes_are_public_and_cacheable(self):
        routes=[('/favicon.ico',favicon_assets.FAVICON_ICO,'image/x-icon'),
                ('/favicon-32.png',favicon_assets.FAVICON_PNG,'image/png'),
                ('/apple-touch-icon.png',favicon_assets.APPLE_TOUCH_ICON_PNG,'image/png'),
                ('/apple-touch-icon-precomposed.png',favicon_assets.APPLE_TOUCH_ICON_PNG,'image/png'),
                ('/bitcoin-dca-icon-v3.png',favicon_assets.APPLE_TOUCH_ICON_PNG,'image/png'),
                ('/safari-pinned-tab-v3.svg',favicon_assets.FAVICON_MASK_SVG,'image/svg+xml; charset=utf-8'),
                ('/favicon.svg',web_ui.FAVICON_SVG,'image/svg+xml; charset=utf-8')]
        for path,data,content_type in routes:
            with self.subTest(path=path):
                handler=object.__new__(web_ui.Handler);handler.path=path+'?v=3'
                handler._send=Mock();handler._require_authentication=Mock(side_effect=AssertionError('Icons must be public'))
                handler.do_GET();handler._send.assert_called_once_with(200,data,content_type,cache_control='public, max-age=86400')
                handler._require_authentication.assert_not_called()

    def test_login_and_dashboard_have_one_unambiguous_normal_icon_plus_apple_and_mask_icons(self):
        class Links(HTMLParser):
            def __init__(self):super().__init__();self.links=[]
            def handle_starttag(self,tag,attrs):
                if tag=='link':self.links.append(dict(attrs))
        for page in [web_ui.HTML,web_ui.LOGIN_HTML]:
            parser=Links();parser.feed(page)
            regular=[link for link in parser.links if link.get('rel')=='icon']
            self.assertEqual(len(regular),1)
            self.assertEqual(regular[0]['href'],'/favicon-32.png')
            self.assertEqual(regular[0]['sizes'],'32x32')
            touch=next(link for link in parser.links if link.get('rel')=='apple-touch-icon')
            self.assertEqual(touch['sizes'],'180x180')
            self.assertNotEqual(regular[0]['href'],touch['href'])
            self.assertEqual(regular[0]['type'],'image/png')
            self.assertTrue(any(link.get('rel')=='apple-touch-icon' and link['href']=='/bitcoin-dca-icon-v3.png' for link in parser.links))
            self.assertTrue(any(link.get('rel')=='mask-icon' and link['href']=='/safari-pinned-tab-v3.svg' and link['color']=='#8bafff' for link in parser.links))

        svg=ET.fromstring(favicon_assets.FAVICON_MASK_SVG)
        self.assertEqual(svg.attrib['viewBox'],'0 0 16 16')
        self.assertEqual(len(svg),1);self.assertTrue(svg[0].tag.endswith('path'))
        self.assertEqual(svg[0].attrib['fill'],'#000000')

    def test_normal_responses_remain_no_store(self):
        handler=object.__new__(web_ui.Handler)
        handler.send_response=Mock();handler.send_header=Mock();handler.end_headers=Mock();handler.wfile=io.BytesIO()
        handler._send(200,'private account data')
        handler.send_header.assert_any_call('Cache-Control','no-store')
        handler.send_header.reset_mock()
        handler._send(200,b'public icon','image/png',cache_control='public, max-age=86400')
        handler.send_header.assert_any_call('Cache-Control','public, max-age=86400')


if __name__=='__main__':unittest.main()


class DesignAccessibilityTests(unittest.TestCase):
    def test_semantic_text_and_controls_have_accessible_contrast_in_both_appearances(self):
        from app.webpage.design_system import PALETTES
        def luminance(color):
            channels=[int(color[i:i+2],16)/255 for i in (1,3,5)]
            linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in channels]
            return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))
        def contrast(a,b):
            low,high=sorted((luminance(a),luminance(b)))
            return (high+.05)/(low+.05)
        for appearance,colors in PALETTES.items():
            for role in ('text','muted','accent','green','red','warning'):
                for background in ('bg','panel','surface','control'):
                    with self.subTest(appearance=appearance,role=role,background=background):
                        self.assertGreaterEqual(contrast(colors[role],colors[background]),4.5)
            self.assertGreaterEqual(contrast(colors['primary'],colors['on-primary']),4.5)
            self.assertGreaterEqual(contrast(colors['control-line'],colors['control']),3)

    def test_dashboard_login_setup_and_settings_share_the_accessible_theme(self):
        from app.webpage import settings_ui
        from app.webpage.design_system import DESIGN_CSS
        from types import SimpleNamespace
        pages=[web_ui.HTML,web_ui.LOGIN_HTML,settings_ui.PAGE,
               settings_ui.settings_page(SimpleNamespace(),create_account=True)]
        for page in pages:
            with self.subTest(page=page[:100]):
                self.assertIn(DESIGN_CSS,page)
                self.assertNotIn('__DESIGN_CSS__',page)
                self.assertIn('class="skip-link"',page)
                self.assertIn('id="main-content"',page)
                self.assertIn('viewport-fit=cover',page)
        for preference in ('prefers-color-scheme:dark','prefers-contrast:more','prefers-reduced-motion:reduce','prefers-reduced-transparency:reduce','forced-colors:active'):
            self.assertIn(preference,DESIGN_CSS)


    def test_explicit_appearance_sets_initial_palette_and_browser_chrome_safely(self):
        from app.webpage.design_system import apply_appearance
        for appearance,color in [('light','#f5f5f7'),('dark','#000000')]:
            page=apply_appearance(web_ui.LOGIN_HTML,appearance)
            self.assertIn('data-appearance="'+appearance+'"',page)
            self.assertEqual(page.count('name="theme-color"'),1)
            self.assertIn('<meta name="theme-color" content="'+color+'">',page)
        page=apply_appearance(web_ui.HTML,'system')
        self.assertEqual(page.count('name="theme-color"'),2)
        self.assertIn('data-appearance="system"',apply_appearance(web_ui.HTML,'"><script>bad</script>'))
