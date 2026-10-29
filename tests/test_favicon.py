"""Icons are available before sign-in in Safari-compatible formats."""
import struct
import unittest
from unittest.mock import Mock
import favicon_assets
import web_ui


class FaviconTests(unittest.TestCase):
    def test_ico_contains_classic_16_32_and_48_pixel_images(self):
        data=favicon_assets.FAVICON_ICO
        self.assertEqual(struct.unpack_from('<HHH',data),(0,1,3))
        self.assertEqual([tuple(data[6+i*16:8+i*16]) for i in range(3)],[(16,16),(32,32),(48,48)])

    def test_png_fallback_and_apple_icon_dimensions(self):
        for data,size in [(favicon_assets.FAVICON_PNG,32),(favicon_assets.APPLE_TOUCH_ICON_PNG,180)]:
            self.assertEqual(data[:8],b'\x89PNG\r\n\x1a\n')
            self.assertEqual(struct.unpack('>II',data[16:24]),(size,size))

    def test_all_icon_routes_are_public_and_accept_cache_version(self):
        routes=[('/favicon.ico',favicon_assets.FAVICON_ICO,'image/x-icon'),
                ('/favicon-32.png',favicon_assets.FAVICON_PNG,'image/png'),
                ('/apple-touch-icon.png',favicon_assets.APPLE_TOUCH_ICON_PNG,'image/png'),
                ('/favicon.svg',web_ui.FAVICON_SVG,'image/svg+xml; charset=utf-8')]
        for path,data,content_type in routes:
            with self.subTest(path=path):
                handler=object.__new__(web_ui.Handler);handler.path=path+'?v=2'
                handler._send=Mock();handler._require_authentication=Mock(side_effect=AssertionError('Icons must be public'))
                handler.do_GET();handler._send.assert_called_once_with(200,data,content_type)
                handler._require_authentication.assert_not_called()

    def test_login_and_dashboard_both_reference_raster_and_vector_icons(self):
        for page in [web_ui.HTML,web_ui.LOGIN_HTML]:
            for target in ['/favicon.ico?v=2','/favicon-32.png?v=2','/favicon.svg?v=2','/apple-touch-icon.png?v=2']:
                self.assertIn('href="'+target+'"',page)


if __name__=='__main__':unittest.main()
