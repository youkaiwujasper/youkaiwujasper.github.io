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
        record = paper(authors, "A molecular genetics study")
        record["container-title"] = ["Journal of Cell Biology"]
        self.assertFalse(pubs.identity_verified(record))

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

    def test_transport_field_auto_verifies(self):
        authors = [{"given": "Youkai", "family": "Wu"}]
        self.assertTrue(pubs.identity_verified(
            paper(authors, "Dynamic vehicle routing and on-demand mobility services")))

    def test_logistics_and_optimization_auto_verifies(self):
        authors = [{"given": "Youkai", "family": "Wu"}]
        self.assertTrue(pubs.identity_verified(
            paper(authors, "Stochastic optimization for logistics planning")))

    def test_operations_research_journal_auto_verifies(self):
        authors = [{"given": "Youkai", "family": "Wu"}]
        record = paper(authors, "Study of dynamic resource allocation")
        record["container-title"] = ["Operations Research"]
        self.assertTrue(pubs.identity_verified(record))

    def test_algorithm_alone_does_not_verify(self):
        authors = [{"given": "Youkai", "family": "Wu"}]
        record = paper(authors, "Algorithm for tumor image segmentation")
        record["container-title"] = ["Medical Image Analysis"]
        self.assertFalse(pubs.identity_verified(record))

    def test_norwegian_academic_affiliation_auto_verifies(self):
        authors = [{"given": "Youkai", "family": "Wu",
                    "affiliation": [{"name": "University of Bergen, Norway"}]}]
        record = paper(authors, "Generic network study")
        record["container-title"] = ["Applied Mathematics"]
        self.assertTrue(pubs.identity_verified(record))

    def test_sichuan_university_affiliation_auto_verifies(self):
        authors = [{"given": "Youkai", "family": "Wu",
                    "affiliation": [{"name": "School of Business, Sichuan University"}]}]
        record = paper(authors, "Generic network study")
        record["container-title"] = ["Applied Mathematics"]
        self.assertTrue(pubs.identity_verified(record))

    def test_not_any_sichuan_affiliation(self):
        authors = [{"given": "Youkai", "family": "Wu",
                    "affiliation": [{"name": "Sichuan Agricultural University"}]}]
        record = paper(authors, "Medical image analysis")
        record["container-title"] = ["Health Informatics"]
        self.assertFalse(pubs.identity_verified(record))

    def test_coauthor_affiliation_does_not_verify_user(self):
        authors = [{"given": "Youkai", "family": "Wu"},
                   {"given": "Unknown", "family": "Person",
                    "affiliation": [{"name": "University of Bergen, Norway"}]}]
        record = paper(authors, "Medical image analysis")
        record["container-title"] = ["Health Informatics"]
        self.assertFalse(pubs.identity_verified(record))

    def test_without_exact_author_cannot_verify_topic(self):
        authors = [{"given": "Youka", "family": "Wu"}]
        self.assertFalse(pubs.identity_verified(
            paper(authors, "Freight transport logistics optimization")))

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
