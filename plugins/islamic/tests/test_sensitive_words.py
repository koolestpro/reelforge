import sys
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import sensitive_words


class FindTests(unittest.TestCase):
    def test_finds_kill_forms(self):
        self.assertEqual(sensitive_words.find('Khidir killed the boy.'), ['killed'])
        self.assertEqual(sensitive_words.find('Whoever kills a believer'), ['kills'])
        self.assertEqual(sensitive_words.find('All the killing you see'), ['killing'])
        self.assertEqual(sensitive_words.find('Do not kill a soul'), ['kill'])

    def test_finds_murder_forms(self):
        self.assertEqual(sensitive_words.find('Murders and murdering'), ['Murders', 'murdering'])

    def test_ignores_masked_and_clean_text(self):
        self.assertEqual(sensitive_words.find('k!lled and m!rder'), [])
        self.assertEqual(sensitive_words.find('Allah is the judge.'), [])


class ParaphraseTests(unittest.TestCase):
    CASES = [
        ('Khidir killed the boy.', 'Khidir took the life of the boy.'),
        ('Why did you kill him?', 'Why did you take his life?'),
        ('Whoever kills a believer deliberately', 'Whoever takes the life of a believer deliberately'),
        ('The second is killing a believing soul.',
         'The second is taking the life of a believing soul.'),
        ('Anyone who is under protection must not be killed.',
         'Anyone who is under protection must not have his life taken.'),
        ('All the killing you see', 'All the taking of life you see'),
        ('That means a person who deserves death may be killed.',
         'That means a person who has earned that penalty may have his life taken.'),
        ('Ignorant people say Sharia kills every person who is not a Muslim.',
         'Ignorant people say Sharia takes the life of every person who is not a Muslim.'),
        ('He murdered the man.', 'He wrongfully took the life of the man.'),
    ]

    def test_cases(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(sensitive_words.paraphrase(text), expected)
                self.assertEqual(sensitive_words.find(sensitive_words.paraphrase(text)), [])

    def test_keeps_sentence_case(self):
        self.assertTrue(sensitive_words.paraphrase('Killed the boy.').startswith('Took'))


class MaskTests(unittest.TestCase):
    def test_masks_each_form(self):
        self.assertEqual(sensitive_words.mask('kill kills killed killing'),
                         'k!ll k!lls k!lled k!lling')
        self.assertEqual(sensitive_words.mask('murder murders murdered murdering'),
                         'm!rder m!rders m!rdered m!rdering')
        self.assertEqual(sensitive_words.find(sensitive_words.mask('Killed him')), [])


class RewriteTests(unittest.TestCase):
    def test_paraphrase_when_it_fits(self):
        text = 'Khidir killed the boy.'
        self.assertEqual(sensitive_words.rewrite(text, lines=lambda s: 1),
                         'Khidir took the life of the boy.')

    def test_masks_when_paraphrase_is_too_long(self):
        text = 'Khidir killed the boy.'
        self.assertEqual(sensitive_words.rewrite(text, max_lines=1, lines=lambda s: 2),
                         'Khidir k!lled the boy.')


class SpecTests(unittest.TestCase):
    def test_check_spec_reports_caption_and_words(self):
        spec = {'captions': [{'id': 2, 'text': 'clean'}, {'id': 4, 'text': 'He was killed.'}]}
        self.assertEqual(sensitive_words.check_spec(spec), [(4, 'He was killed.', ['killed'])])

    def test_rewrite_spec_marks_the_method(self):
        spec = {'captions': [{'id': 1, 'text': 'He killed a man.'}]}
        result = sensitive_words.rewrite_spec(spec, lines=lambda s: 1)
        self.assertEqual(result['captions'][0]['sensitive'], 'paraphrase')
        self.assertEqual(result['captions'][0]['text'], 'He took the life of a man.')


if __name__ == '__main__':
    unittest.main()
