import unittest

from custom_addons.corr_engine_v2.engine_v2.styles import STYLE_REGISTRY


class TestEngineV2StyleAutoload(unittest.TestCase):
    def test_style_registry_autoloads_modules(self):
        # Importing the styles package should cause all style_* modules
        # to be imported and their @register_style decorators executed.
        handler = STYLE_REGISTRY.get("0201")
        self.assertIsNotNone(handler)
        self.assertTrue(callable(handler))





