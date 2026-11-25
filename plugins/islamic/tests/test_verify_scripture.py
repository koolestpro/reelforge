import sys
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import verify_scripture


TABLE = verify_scripture.load_table()


class ScriptureTextTests(unittest.TestCase):
    def test_wrong_3_146_arabic_fails(self):
        text = (
            'He says, وَٱللَّهُ يُحِبُّ ٱلشَّـٰكِرِينَ, '
            '"And Allah loves those who are grateful." (Qur\'an 3:146)'
        )
        errors, _ = verify_scripture.check_text(text, TABLE)
        self.assertTrue(any("3:146" in e for e in errors))

    def test_correct_3_144_passes(self):
        text = (
            'He says, وَسَيَجْزِي ٱللَّهُ ٱلشَّاكِرِينَ, '
            '"And Allah will reward those who are grateful." (Qur\'an 3:144)'
        )
        errors, _ = verify_scripture.check_text(text, TABLE)
        self.assertEqual(errors, [])

    def test_27_18_partial_passes(self):
        text = (
            'قَالَتْ نَمْلَةٌ يَا أَيُّهَا النَّمْلُ ادْخُلُوا مَسَاكِنَكُمْ '
            '(Qur\'an 27:18)'
        )
        errors, _ = verify_scripture.check_text(text, TABLE)
        self.assertEqual(errors, [])

    def test_unknown_reference_fails(self):
        errors, _ = verify_scripture.check_text(
            'Allah says (Qur\'an 99:1)', TABLE
        )
        self.assertTrue(any('unverified' in e for e in errors))

    def test_3_146_grateful_english_fails(self):
        errors, _ = verify_scripture.check_text(
            'Allah loves those who are grateful. (Qur\'an 3:146)', TABLE
        )
        self.assertTrue(any('patience' in e for e in errors))

    def test_paraphrase_without_arabic_warns(self):
        errors, warnings = verify_scripture.check_text(
            'Only Allah knows the unseen (Qur\'an 7:188)', TABLE
        )
        self.assertEqual(errors, [])
        self.assertTrue(warnings)

    def test_no_reference_passes(self):
        errors, warnings = verify_scripture.check_text(
            'The Imam opened the khutbah on one word: gratitude.', TABLE
        )
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])


class ScriptureJobTests(unittest.TestCase):
    def job(self, card):
        return {'chapters': [{'id': 'x', 'card': card}]}

    def test_job_card_wrong_3_146_fails(self):
        card = {
            'heading': "AL-QUR'AN 3:146",
            'arabic': ['وَٱللَّهُ يُحِبُّ ٱلشَّـٰكِرِينَ'],
            'english': ['And Allah loves those who are grateful.'],
            'source': "Al-Qur'an 3:146",
        }
        errors, _ = verify_scripture.check_job(self.job(card), TABLE)
        self.assertTrue(errors)

    def test_job_card_correct_3_144_passes(self):
        card = {
            'heading': "AL-QUR'AN 3:144",
            'arabic': ['وَسَيَجْزِي ٱللَّهُ ٱلشَّاكِرِينَ'],
            'english': ['And Allah will reward those who are grateful.'],
            'source': "Al-Qur'an 3:144",
        }
        errors, _ = verify_scripture.check_job(self.job(card), TABLE)
        self.assertEqual(errors, [])

    def test_job_card_correct_but_no_arabic_warns(self):
        card = {
            'heading': "AL-QUR'AN 17:3",
            'english': ['Indeed he was a grateful servant.'],
            'source': "Al-Qur'an 17:3",
        }
        errors, warnings = verify_scripture.check_job(self.job(card), TABLE)
        self.assertEqual(errors, [])
        self.assertTrue(warnings)


if __name__ == '__main__':
    unittest.main()
