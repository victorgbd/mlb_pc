import ast
import sys
import unittest
from unittest.mock import patch

from paths import ROOT

sys.path.insert(0, str(ROOT / 'launcher'))
import i18n_base


class LauncherTranslationTests(unittest.TestCase):
    def test_each_language_translates_play(self):
        expected = {
            'es': 'Jugar', 'pt': 'Jogar', 'fr': 'Jouer', 'de': 'Spielen',
            'it': 'Gioca', 'ja': 'プレイ',
        }
        for language, translation in expected.items():
            with self.subTest(language=language):
                self.assertEqual(i18n_base.translate(language, 'Play'), translation)

    def test_missing_text_falls_back_to_english(self):
        self.assertEqual(i18n_base.translate('es', 'Untranslated text'), 'Untranslated text')
        self.assertEqual(i18n_base.translate('en', 'Play'), 'Play')

    def test_automatic_language_uses_windows_locale(self):
        for language in ('es', 'pt', 'fr', 'de', 'it', 'ja'):
            with self.subTest(language=language), patch.object(
                    i18n_base.locale, 'getlocale', return_value=(f'{language}_XX', 'UTF-8')):
                self.assertEqual(i18n_base.language_code(''), language)
        with patch.object(i18n_base.locale, 'getlocale', return_value=('ko_KR', 'UTF-8')):
            self.assertEqual(i18n_base.language_code(''), 'en')

    def test_launcher_offers_selected_languages(self):
        self.assertEqual(
            [code for code, _name in i18n_base.LANGUAGES],
            ['', 'en', 'es', 'pt', 'fr', 'de', 'it', 'ja'],
        )

    def test_interface_language_help_is_translated(self):
        text = 'This changes only the launcher. The game language is selected on the Game page.'
        self.assertNotEqual(i18n_base.translate('es', text), text)

    def test_spanish_covers_every_launcher_text(self):
        import mlb_i18n
        tree = ast.parse((ROOT / 'launcher' / 'mlb_launcher.py').read_text(encoding='utf-8'))
        texts = {node.args[0].value for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == '_' and node.args
                 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)}
        same_in_spanish = {'Audio', 'Zoom', 'Hero'}
        missing = {text for text in texts - same_in_spanish if mlb_i18n.translate('es', text) == text}
        self.assertEqual(missing, set())


if __name__ == '__main__':
    unittest.main()
