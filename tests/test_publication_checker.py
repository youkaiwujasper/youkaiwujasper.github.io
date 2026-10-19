"""Offline regression tests for safe automatic publication matching."""
import unittest

from scripts import update_publications as pubs


def paper(authors, title="A verified logistics optimization study"):
    return {
        "DOI": "10.1234/example.2026.1",
        "type": "journal-article",
        "title": [title],
        "container-title": ["Transportation Research Part E"],
        "volume": "170",
        "article-number": "123456",
        "published": {"date-parts": [[2026, 10, 1]]},
        "author": authors,
    }


class PublicationChecks(unittest.TestCase):
    def test_known_collaborator_can_verify(self):
        authors = [{"given": "Youkai", "family": "Wu"},
                   {"given": "Zhaoxia", "family": "Guo"}]
        self.assertTrue(pubs.identity_verified(paper(authors)))

    def test_same_name_alone_is_not_enough(self):
        authors = [{"given": "Youkai", "family": "Wu"},
                   {"given": "Unrelated", "family": "Researcher"}]
        self.assertFalse(pubs.identity_verified(paper(authors)))

    def test_same_name_with_nhh_affiliation(self):
        authors = [{"given": "Youkai", "family": "Wu",
                    "affiliation": [{"name": "NHH Norwegian School of Economics"}]}]
        self.assertTrue(pubs.identity_verified(paper(authors)))

    def test_missing_youkais_name_not_verified(self):
        authors = [{"given": "Other", "family": "Wu"},
                   {"given": "Zhaoxia", "family": "Guo"}]
        self.assertFalse(pubs.identity_verified(paper(authors)))

    def test_duplicate_detection(self):
        listed = {pubs.norm_title("An Example: Study of Logistics")}
        self.assertTrue(pubs.title_already_listed(
            "An Example Study of Logistics", listed))
        self.assertFalse(pubs.title_already_listed(
            "Completely Different Research", listed))

    def test_entry_category_and_doi(self):
        record = paper([{"given": "Youkai", "family": "Wu"},
                        {"given": "Haifan", "family": "Tang"}])
        category, entry = pubs.new_entry({
            "title": "A verified logistics optimization study",
            "venue": "Transportation Research Part E",
            "year": 2026,
            "authors": "", "url": "",
        }, record)
        self.assertEqual(category, "journal")
        self.assertIn("https://doi.org/10.1234/example.2026.1", entry)
        self.assertIn("<b>Youkai Wu</b>", entry)


if __name__ == "__main__":
    unittest.main()
