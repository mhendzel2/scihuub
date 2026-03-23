"""
BioSearch.py - PubMed and bioRxiv search integration for PyPaperBot.

Uses NCBI E-utilities for PubMed queries and PMID-to-DOI conversion.
Uses the Europe PMC REST API for bioRxiv preprint keyword search.
"""

import time
import xml.etree.ElementTree as ET

import requests

NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_EFETCH  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EUROPEPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def search_pubmed(query, max_results=50):
    """Search PubMed with a boolean query string.

    Supports standard PubMed boolean syntax:
        AND, OR, NOT, field tags such as [ti], [tiab], [au], [mh]

    Returns a list of record dicts with keys:
        pmid, doi, title, year, authors, journal
    """
    max_results = max(1, min(int(max_results), 100000))
    resp = requests.get(NCBI_ESEARCH, params={
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
    }, timeout=30)
    resp.raise_for_status()
    pmids = resp.json().get("esearchresult", {}).get("idlist", [])
    if not pmids:
        return []
    return pmids_to_records(pmids)


def pmids_to_records(pmids):
    """Convert a list of PMID strings to record dicts containing DOI and metadata.

    Makes batched EFetch calls (200 per request) to respect NCBI rate limits.
    """
    if not pmids:
        return []
    records = []
    batch_size = 200
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i: i + batch_size]
        for attempt in range(3):
            try:
                resp = requests.get(NCBI_EFETCH, params={
                    "db": "pubmed",
                    "id": ",".join(batch),
                    "rettype": "xml",
                    "retmode": "xml",
                }, timeout=60)
                resp.raise_for_status()
                records.extend(_parse_pubmed_xml(resp.text))
                break
            except requests.exceptions.RequestException as e:
                print(f"Warning: Failed to fetch batch {i//batch_size + 1} from NCBI. Attempt {attempt + 1}/3... Error: {e}")
                time.sleep(2 ** attempt)
                if attempt == 2:
                    print(f"Failed to fetch batch {i//batch_size + 1} after 3 attempts.")

        if i + batch_size < len(pmids):
            time.sleep(0.4)  # stay within NCBI's 3 req/s limit for unauthenticated use
    return records


def _parse_pubmed_xml(xml_text):
    """Parse PubMed XML response and extract per-article metadata."""
    records = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return records

    for article in root.findall(".//PubmedArticle"):
        rec = {
            "pmid": None, "doi": None, "title": None,
            "year": None, "authors": None, "journal": None,
        }

        pmid_el = article.find(".//PMID")
        if pmid_el is not None:
            rec["pmid"] = pmid_el.text

        for aid in article.findall(".//ArticleId"):
            if aid.get("IdType") == "doi":
                rec["doi"] = aid.text
                break

        title_el = article.find(".//ArticleTitle")
        if title_el is not None:
            rec["title"] = "".join(title_el.itertext())

        pub_date = article.find(".//PubDate")
        if pub_date is not None:
            year_el = pub_date.find("Year")
            if year_el is not None:
                rec["year"] = year_el.text

        authors = []
        for auth in article.findall(".//Author"):
            last = auth.findtext("LastName", "")
            first = auth.findtext("ForeName", "")
            if last:
                authors.append("{} {}".format(last, first).strip())
        if authors:
            rec["authors"] = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

        j_el = article.find(".//Journal/Title")
        if j_el is not None:
            rec["journal"] = j_el.text

        records.append(rec)
    return records


def search_biorxiv(query, max_results=50):
    """Search bioRxiv preprints via the Europe PMC REST API.

    Supports standard boolean queries (AND, OR, NOT) and field filtering.
    Returns a list of record dicts with keys:
        pmid, doi, title, year, authors, journal
    """
    max_results = max(1, min(int(max_results), 100000))
    full_query = "({}) AND SRC:PPR AND PUBLISHER:bioRxiv".format(query)
    resp = requests.get(EUROPEPMC_SEARCH, params={
        "query": full_query,
        "format": "json",
        "pageSize": max_results,
        "resultType": "core",
    }, timeout=30)
    resp.raise_for_status()

    records = []
    for item in resp.json().get("resultList", {}).get("result", []):
        doi = item.get("doi")
        if not doi:
            continue
        records.append({
            "pmid": None,
            "doi": doi,
            "title": item.get("title"),
            "year": item.get("pubYear"),
            "authors": item.get("authorString"),
            "journal": "bioRxiv",
        })
    return records
