from os import path
import requests
import time
from .HTMLparsers import getSchiHubPDF, SciHubUrls
import random
from .NetInfo import NetInfo
from .Utils import URLjoin


UNPAYWALL_API = "https://api.unpaywall.org/v2/"


def getUnpaywallPDF(doi, email):
    """Query Unpaywall for a free legal PDF URL. Returns URL string or None."""
    try:
        r = requests.get(
            UNPAYWALL_API + doi,
            params={"email": email},
            headers=NetInfo.HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        best = data.get("best_oa_location")
        if best:
            return best.get("url_for_pdf") or best.get("url")
    except Exception:
        pass
    return None


def setSciHubUrl():
    print("Searching for a sci-hub mirror")
    KNOWN_MIRRORS = ["https://sci-hub.ru", "https://sci-hub.st", "https://sci-hub.se"]
    try:
        r = requests.get(NetInfo.SciHub_URLs_repo, headers=NetInfo.HEADERS, timeout=10)
        links = SciHubUrls(r.text)
    except Exception:
        links = []

    # Prioritize known-good mirrors, then append discovered ones
    candidates = KNOWN_MIRRORS + [l for l in links if l not in KNOWN_MIRRORS]

    for l in candidates:
        try:
            print("Trying with {}...".format(l))
            r = requests.get(l, headers=NetInfo.HEADERS, timeout=10)
            if r.status_code == 200 and ("sci-hub" in r.text.lower() or "object" in r.text.lower()):
                NetInfo.SciHub_URL = l
                break
        except:
            pass
    else:
        print(
            "\nNo working Sci-Hub instance found!\nIf in your country Sci-Hub is not available consider using a VPN or a proxy\nYou can use a specific mirror mirror with the --scihub-mirror argument")
        NetInfo.SciHub_URL = "https://sci-hub.ru"


def getSaveDir(folder, fname):
    dir_ = path.join(folder, fname)
    n = 1
    while path.exists(dir_):
        n += 1
        dir_ = path.join(folder, f"({n}){fname}")

    return dir_


def saveFile(file_name, content, paper, dwn_source):
    f = open(file_name, 'wb')
    f.write(content)
    f.close()

    paper.downloaded = True
    paper.downloadedFrom = dwn_source


def downloadPapers(papers, dwnl_dir, num_limit, SciHub_URL=None, SciDB_URL=None, unpaywall_email=None):

    NetInfo.SciHub_URL = SciHub_URL
    if NetInfo.SciHub_URL is None:
        setSciHubUrl()
    if SciDB_URL is not None:
        NetInfo.SciDB_URL = SciDB_URL

    # Quick check: is SciDB reachable? If not, skip it entirely.
    scidb_available = False
    if NetInfo.SciDB_URL:
        try:
            requests.head(NetInfo.SciDB_URL, headers=NetInfo.HEADERS, timeout=5)
            scidb_available = True
        except Exception:
            print("SciDB mirror {} is unreachable, skipping SciDB downloads.".format(NetInfo.SciDB_URL))

    print("\nUsing Sci-Hub mirror {}".format(NetInfo.SciHub_URL))
    if scidb_available:
        print("Using Sci-DB mirror {}".format(NetInfo.SciDB_URL))
    if unpaywall_email:
        print("Unpaywall enabled (email: {})".format(unpaywall_email))
    print("")

    # Deduplicate papers by DOI (Scholar can return the same paper on multiple pages)
    seen_dois = set()
    unique_papers = []
    duplicates_removed = 0
    for p in papers:
        key = p.DOI if p.DOI else id(p)
        if key in seen_dois:
            duplicates_removed += 1
            continue
        seen_dois.add(key)
        unique_papers.append(p)
    if duplicates_removed:
        print("Removed {} duplicate papers (same DOI found on multiple pages)".format(duplicates_removed))

    downloadable = [p for p in unique_papers if p.canBeDownloaded()]
    print("Papers to download: {} ({} total, {} without DOI/link skipped)".format(
        len(downloadable), len(unique_papers), len(unique_papers) - len(downloadable)))

    num_downloaded = 0
    num_skipped = 0
    num_failed = 0
    paper_number = 1
    paper_files = []
    for p in downloadable:
        if num_limit is not None and num_downloaded >= num_limit:
            break

        print("Download {} of {} -> {}".format(paper_number, len(downloadable), p.title))
        paper_number += 1

        # Skip if file already exists in the download directory
        target_file = path.join(dwnl_dir, p.getFileName())
        if path.exists(target_file):
            print("  Already exists, skipping")
            p.downloaded = True
            num_skipped += 1
            continue

        pdf_dir = getSaveDir(dwnl_dir, p.getFileName())

        failed = 0
        while not p.downloaded and failed != 6:
            url = ""
            try:
                dwn_source = 1  # 1 scidb - 2 scihub - 3 scholar/direct - 4 unpaywall
                if failed == 0 and p.DOI is not None and scidb_available:
                    url = URLjoin(NetInfo.SciDB_URL, p.DOI)
                elif failed <= 1 and p.DOI is not None and unpaywall_email:
                    dwn_source = 4
                    pdf_url = getUnpaywallPDF(p.DOI, unpaywall_email)
                    if pdf_url:
                        url = pdf_url
                    else:
                        failed = max(failed, 2)
                        continue
                elif failed <= 2 and p.DOI is not None:
                    url = URLjoin(NetInfo.SciHub_URL, p.DOI)
                    dwn_source = 2
                elif failed == 3 and p.scholar_link is not None:
                    url = URLjoin(NetInfo.SciHub_URL, p.scholar_link)
                elif failed == 4 and p.scholar_link is not None and p.scholar_link[-3:] == "pdf":
                    url = p.scholar_link
                    dwn_source = 3
                elif failed == 5 and p.pdf_link is not None:
                    url = p.pdf_link
                    dwn_source = 3

                if url != "":
                    r = requests.get(url, headers=NetInfo.HEADERS, timeout=60)
                    content_type = r.headers.get('content-type')

                    if (dwn_source == 1 or dwn_source == 2) and 'application/pdf' not in content_type and "application/octet-stream" not in content_type:
                        time.sleep(random.randint(1, 4))

                        pdf_link = getSchiHubPDF(r.text, base_url=url)
                        if pdf_link is not None:
                            r = requests.get(pdf_link, headers=NetInfo.HEADERS, timeout=60)
                            content_type = r.headers.get('content-type')

                    if 'application/pdf' in content_type or "application/octet-stream" in content_type:
                        paper_files.append(saveFile(pdf_dir, r.content, p, dwn_source))
                        num_downloaded += 1
                        source_names = {1: "SciDB", 2: "SciHub", 3: "direct", 4: "Unpaywall"}
                        print("  Saved ({})".format(source_names.get(dwn_source, "unknown")))
            except Exception as e:
                print(f"  Source {dwn_source} failed: {e}")

            failed += 1

        if not p.downloaded:
            num_failed += 1
            print("  Could not download from any source")

    print("\nDownload summary: {} saved, {} already existed, {} failed".format(
        num_downloaded, num_skipped, num_failed))
