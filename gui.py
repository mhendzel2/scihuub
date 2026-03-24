import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.filedialog as fd

CLI_CHILD_FLAG = "--_pypaperbot_cli"


def run_pypaperbot_cli(cli_args):
    import PyPaperBot.__main__ as pypaperbot_main

    original_argv = sys.argv[:]
    sys.argv = [original_argv[0]] + cli_args

    try:
        pypaperbot_main.checkVersion()
        pypaperbot_main.main()
        print(
            """\nWork completed!
        -Join the telegram channel to stay updated --> https://t.me/pypaperbotdatawizards <--
        -If you like this project, you can share a cup of coffee at --> https://www.paypal.com/paypalme/ferru97 <-- :)\n"""
        )
        return 0
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        print(exc.code)
        return 1
    finally:
        sys.argv = original_argv


def is_frozen_app():
    return bool(getattr(sys, "frozen", False))


if len(sys.argv) > 1 and sys.argv[1] == CLI_CHILD_FLAG:
    raise SystemExit(run_pypaperbot_cli(sys.argv[2:]))


import customtkinter as ctk


ctk.set_appearance_mode("System")
ctk.set_default_color_theme("dark-blue")


class PyPaperBotGUI(ctk.CTk):
    POLL_INTERVAL_MS = 120

    def __init__(self):
        super().__init__()
        self.title("PyPaperBot Downloader")
        self.geometry("980x780")
        self.minsize(900, 720)

        self.repo_root = os.path.dirname(os.path.abspath(__file__))
        self.execution_cwd = (
            os.path.dirname(sys.executable) if is_frozen_app() else self.repo_root
        )
        self.process = None
        self.is_starting = False
        self.stop_requested = False
        self.output_queue = queue.Queue()

        self.status_text = tk.StringVar(value="Idle")
        self.restrict_value = tk.StringVar(value="Download PDFs + BibTeX")
        self.use_doi_filename = tk.BooleanVar(value=False)

        self.phase_text = tk.StringVar(value="")
        self._progress_total = 0
        self._progress_current = 0
        self._progress_phase = ""  # "search", "crossref", "download", "done"

        self._re_download = re.compile(r"Download (\d+) of (\d+)")
        self._re_crossref = re.compile(r"Searching paper (\d+) of (\d+) on Crossref")
        self._re_doi_resolve = re.compile(r"Searching paper (\d+) of (\d+) with DOI")
        self._re_scholar_page = re.compile(r"Google Scholar page (\d+)\s*:.*?(\d+) papers found")
        self._re_papers_found = re.compile(r"Found (\d+) papers")
        self._re_work_completed = re.compile(r"Work completed!")

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.grid_columnconfigure(0, weight=1)
        # row 0 = scrollable top (header+tabs+settings), row 1 = actions,
        # row 2 = progress, row 3 = console
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=2)

        self._build_top_scroll()
        self._build_actions()
        self._build_progress()
        self._build_console()

        self.after(self.POLL_INTERVAL_MS, self._drain_output_queue)

    def _build_top_scroll(self):
        self.top_scroll = ctk.CTkScrollableFrame(self)
        self.top_scroll.grid(row=0, column=0, padx=10, pady=(10, 4), sticky="nsew")
        self.top_scroll.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_search_tabs()
        self._build_settings()

    def _build_header(self):
        header = ctk.CTkFrame(self.top_scroll, corner_radius=18, fg_color=("#e8eefc", "#13233d"))
        header.grid(row=0, column=0, padx=10, pady=(10, 6), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            header,
            text="PyPaperBot Local GUI",
            font=ctk.CTkFont(size=28, weight="bold"),
        )
        title.grid(row=0, column=0, padx=18, pady=(16, 4), sticky="w")

        subtitle = ctk.CTkLabel(
            header,
            text=(
                "Standalone subprocess wrapper for the existing CLI. "
                "Downloads run in the background and stream live output below."
            ),
            justify="left",
            wraplength=760,
        )
        subtitle.grid(row=1, column=0, padx=18, pady=(0, 16), sticky="w")

    def _build_search_tabs(self):
        self.tabs = ctk.CTkTabview(self.top_scroll, height=200)
        self.tabs.grid(row=1, column=0, padx=10, pady=6, sticky="ew")

        self.tab_query = self.tabs.add("Keyword Search")
        self.tab_doi = self.tabs.add("Single DOI")
        self.tab_file = self.tabs.add("Batch Download")
        self.tab_pubmed = self.tabs.add("PubMed Search")
        self.tab_biorxiv = self.tabs.add("bioRxiv Search")
        self.tab_pmid = self.tabs.add("PubMed IDs")
        self.tab_mixed = self.tabs.add("Mixed Batch")

        self._build_query_tab()
        self._build_doi_tab()
        self._build_file_tab()
        self._build_pubmed_tab()
        self._build_biorxiv_tab()
        self._build_pmid_tab()
        self._build_mixed_tab()

    def _build_query_tab(self):
        self.tab_query.grid_columnconfigure(0, weight=1)

        self.query_entry = ctk.CTkEntry(
            self.tab_query,
            placeholder_text="Search query (boolean: AND, OR, NOT supported) or Scholar page URL",
        )
        self.query_entry.grid(row=0, column=0, columnspan=2, padx=12, pady=(16, 10), sticky="ew")

        max_row = ctk.CTkFrame(self.tab_query, fg_color="transparent")
        max_row.grid(row=1, column=0, columnspan=2, padx=12, pady=8, sticky="w")
        ctk.CTkLabel(max_row, text="Max papers (1\u2013100000, default 50):").pack(side="left", padx=(0, 8))
        self.query_max_entry = ctk.CTkEntry(max_row, width=80, placeholder_text="50")
        self.query_max_entry.pack(side="left")

        self.skip_words_entry = ctk.CTkEntry(
            self.tab_query,
            placeholder_text="Optional skip words, comma separated",
        )
        self.skip_words_entry.grid(row=2, column=0, columnspan=2, padx=12, pady=(4, 16), sticky="ew")

    def _build_doi_tab(self):
        self.tab_doi.grid_columnconfigure(0, weight=1)

        self.doi_entry = ctk.CTkEntry(
            self.tab_doi,
            placeholder_text="10.1038/s41586-020-2649-2",
        )
        self.doi_entry.grid(row=0, column=0, padx=12, pady=(24, 12), sticky="ew")

        note = ctk.CTkLabel(
            self.tab_doi,
            text="Single DOI mode launches the existing --doi workflow without modifying the package.",
            justify="left",
        )
        note.grid(row=1, column=0, padx=12, pady=(0, 20), sticky="w")

    def _build_file_tab(self):
        self.tab_file.grid_columnconfigure(0, weight=1)

        self.file_entry = ctk.CTkEntry(
            self.tab_file,
            placeholder_text="Select a .txt or .csv file containing one DOI per line",
        )
        self.file_entry.grid(row=0, column=0, padx=(12, 8), pady=(24, 12), sticky="ew")

        self.file_button = ctk.CTkButton(
            self.tab_file,
            text="Browse",
            width=110,
            command=self.select_file,
        )
        self.file_button.grid(row=0, column=1, padx=(8, 12), pady=(24, 12), sticky="e")

        note = ctk.CTkLabel(
            self.tab_file,
            text="Accepts local .txt or .csv lists for the existing --doi-file entrypoint.",
            justify="left",
        )
        note.grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 20), sticky="w")

    def _build_mixed_tab(self):
        self.tab_mixed.grid_columnconfigure(0, weight=1)

        self.mixed_file_entry = ctk.CTkEntry(
            self.tab_mixed,
            placeholder_text="Select a .txt or .csv file containing DOIs, PMIDs, and Queries",
        )
        self.mixed_file_entry.grid(row=0, column=0, padx=(12, 8), pady=(24, 12), sticky="ew")

        self.mixed_file_button = ctk.CTkButton(
            self.tab_mixed,
            text="Browse",
            width=110,
            command=self.select_mixed_file,
        )
        self.mixed_file_button.grid(row=0, column=1, padx=(8, 12), pady=(24, 12), sticky="e")

        note = ctk.CTkLabel(
            self.tab_mixed,
            text="Accepts a mixed list of DOIs, PMIDs (numbers only), and PubMed Queries (one per line).",
            justify="left",
        )
        note.grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 20), sticky="w")

    def _build_pubmed_tab(self):
        self.tab_pubmed.grid_columnconfigure(0, weight=1)

        hint = ctk.CTkLabel(
            self.tab_pubmed,
            text="Boolean query — use AND, OR, NOT and field tags: [ti] title, [tiab] title+abstract, [au] author, [mh] MeSH term.\nExample: (COVID-19[ti] AND vaccine[tiab]) NOT commentary[pt]",
            justify="left",
            wraplength=720,
        )
        hint.grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w")

        self.pubmed_query_text = ctk.CTkTextbox(self.tab_pubmed, height=72)
        self.pubmed_query_text.grid(row=1, column=0, padx=12, pady=(0, 8), sticky="ew")

        results_row = ctk.CTkFrame(self.tab_pubmed, fg_color="transparent")
        results_row.grid(row=2, column=0, padx=12, pady=(0, 12), sticky="w")
        ctk.CTkLabel(results_row, text="Max results (1–100000, default 50):").pack(side="left", padx=(0, 8))
        self.pubmed_results_entry = ctk.CTkEntry(results_row, width=80, placeholder_text="50")
        self.pubmed_results_entry.pack(side="left")

    def _build_biorxiv_tab(self):
        self.tab_biorxiv.grid_columnconfigure(0, weight=1)

        hint = ctk.CTkLabel(
            self.tab_biorxiv,
            text="Boolean query searched against bioRxiv preprints via Europe PMC.\nExample: CRISPR AND gene editing AND cancer",
            justify="left",
            wraplength=720,
        )
        hint.grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w")

        self.biorxiv_query_entry = ctk.CTkEntry(
            self.tab_biorxiv,
            placeholder_text="Enter bioRxiv search query",
        )
        self.biorxiv_query_entry.grid(row=1, column=0, padx=12, pady=(0, 8), sticky="ew")

        results_row = ctk.CTkFrame(self.tab_biorxiv, fg_color="transparent")
        results_row.grid(row=2, column=0, padx=12, pady=(0, 12), sticky="w")
        ctk.CTkLabel(results_row, text="Max results (1–100000, default 50):").pack(side="left", padx=(0, 8))
        self.biorxiv_results_entry = ctk.CTkEntry(results_row, width=80, placeholder_text="50")
        self.biorxiv_results_entry.pack(side="left")

    def _build_pmid_tab(self):
        self.tab_pmid.grid_columnconfigure(0, weight=1)

        hint = ctk.CTkLabel(
            self.tab_pmid,
            text="Enter PubMed IDs (PMIDs) — one per line, or comma / space separated.\nThey will be converted to DOIs via NCBI and then downloaded.",
            justify="left",
            wraplength=720,
        )
        hint.grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w")

        self.pmid_textbox = ctk.CTkTextbox(self.tab_pmid, height=110)
        self.pmid_textbox.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="ew")

    def _build_settings(self):
        settings = ctk.CTkFrame(self.top_scroll)
        settings.grid(row=2, column=0, padx=10, pady=6, sticky="ew")

        for column in (1, 3, 5):
            settings.grid_columnconfigure(column, weight=1)

        ctk.CTkLabel(settings, text="Download Dir").grid(row=0, column=0, padx=8, pady=8, sticky="e")
        self.dir_entry = ctk.CTkEntry(settings, placeholder_text="Directory for PDFs, CSV, and BibTeX")
        self.dir_entry.grid(row=0, column=1, columnspan=4, padx=(0, 6), pady=8, sticky="ew")
        self.dir_button = ctk.CTkButton(settings, text="Browse", width=90, command=self.select_directory)
        self.dir_button.grid(row=0, column=5, padx=(6, 8), pady=8, sticky="e")

        ctk.CTkLabel(settings, text="Min Year").grid(row=1, column=0, padx=8, pady=6, sticky="e")
        self.min_year_entry = ctk.CTkEntry(settings, placeholder_text="2018")
        self.min_year_entry.grid(row=1, column=1, padx=(0, 8), pady=6, sticky="ew")

        ctk.CTkLabel(settings, text="Max by Year").grid(row=1, column=2, padx=8, pady=6, sticky="e")
        self.max_year_entry = ctk.CTkEntry(settings, placeholder_text="Optional")
        self.max_year_entry.grid(row=1, column=3, padx=(0, 8), pady=6, sticky="ew")

        ctk.CTkLabel(settings, text="Max by Cites").grid(row=1, column=4, padx=8, pady=6, sticky="e")
        self.max_cites_entry = ctk.CTkEntry(settings, placeholder_text="Optional")
        self.max_cites_entry.grid(row=1, column=5, padx=(0, 8), pady=6, sticky="ew")

        ctk.CTkLabel(settings, text="Sci-Hub Mirror").grid(row=2, column=0, padx=8, pady=6, sticky="e")
        self.scihub_entry = ctk.CTkEntry(settings, placeholder_text="https://sci-hub.se")
        self.scihub_entry.grid(row=2, column=1, padx=(0, 8), pady=6, sticky="ew")

        ctk.CTkLabel(settings, text="Annas Mirror").grid(row=2, column=2, padx=8, pady=6, sticky="e")
        self.annas_entry = ctk.CTkEntry(settings, placeholder_text="https://annas-archive.se")
        self.annas_entry.grid(row=2, column=3, columnspan=3, padx=(0, 8), pady=6, sticky="ew")

        ctk.CTkLabel(settings, text="Unpaywall Email").grid(row=3, column=0, padx=8, pady=6, sticky="e")
        self.unpaywall_entry = ctk.CTkEntry(settings, placeholder_text="your.name@university.edu (enables free OA lookup)")
        self.unpaywall_entry.grid(row=3, column=1, columnspan=3, padx=(0, 8), pady=6, sticky="ew")

        ctk.CTkLabel(settings, text="Single Proxy").grid(row=4, column=0, padx=8, pady=6, sticky="e")
        self.single_proxy_entry = ctk.CTkEntry(
            settings,
            placeholder_text="http://127.0.0.1:8080",
        )
        self.single_proxy_entry.grid(row=4, column=1, padx=(0, 8), pady=6, sticky="ew")

        ctk.CTkLabel(settings, text="Restrict").grid(row=4, column=2, padx=8, pady=6, sticky="e")
        self.restrict_menu = ctk.CTkOptionMenu(
            settings,
            variable=self.restrict_value,
            values=["Download PDFs + BibTeX", "BibTeX only", "PDF only"],
        )
        self.restrict_menu.grid(row=4, column=3, padx=(0, 8), pady=6, sticky="ew")

        self.use_doi_checkbox = ctk.CTkCheckBox(
            settings,
            text="Use DOI as filename when downloading from DOI-based modes",
            variable=self.use_doi_filename,
        )
        self.use_doi_checkbox.grid(row=4, column=4, columnspan=2, padx=8, pady=6, sticky="w")

        ctk.CTkLabel(settings, text="Proxy Chain").grid(row=5, column=0, padx=8, pady=(6, 8), sticky="ne")
        self.proxy_textbox = ctk.CTkTextbox(settings, height=50)
        self.proxy_textbox.grid(row=5, column=1, columnspan=5, padx=(0, 8), pady=(6, 4), sticky="ew")

        proxy_hint = ctk.CTkLabel(
            settings,
            text="Separate proxies with commas, spaces, or newlines. Leave blank when using Single Proxy.",
            justify="left",
        )
        proxy_hint.grid(row=6, column=1, columnspan=5, padx=(0, 8), pady=(0, 8), sticky="w")

    def _build_actions(self):
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=1, column=0, padx=20, pady=(6, 2), sticky="ew")
        actions.grid_columnconfigure(2, weight=1)

        self.start_button = ctk.CTkButton(
            actions,
            text="Search & Download",
            width=180,
            fg_color="#1f8f5a",
            hover_color="#176b43",
            command=self.start_process,
        )
        self.start_button.grid(row=0, column=0, padx=(0, 10), pady=4, sticky="w")

        self.stop_button = ctk.CTkButton(
            actions,
            text="Stop / Cancel",
            width=140,
            fg_color="#b93838",
            hover_color="#8d2a2a",
            state="disabled",
            command=self.stop_process,
        )
        self.stop_button.grid(row=0, column=1, padx=10, pady=4, sticky="w")

        self.status_label = ctk.CTkLabel(
            actions,
            textvariable=self.status_text,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=("gray15", "gray90"),
        )
        self.status_label.grid(row=0, column=2, padx=(12, 0), pady=4, sticky="e")

    def _build_progress(self):
        progress_frame = ctk.CTkFrame(self, corner_radius=8)
        progress_frame.grid(row=2, column=0, padx=20, pady=(4, 4), sticky="ew")
        progress_frame.grid_columnconfigure(1, weight=1)

        self.phase_label = ctk.CTkLabel(
            progress_frame,
            textvariable=self.phase_text,
            font=ctk.CTkFont(size=13),
            text_color=("gray30", "gray80"),
            anchor="w",
            width=220,
        )
        self.phase_label.grid(row=0, column=0, padx=(12, 8), pady=10, sticky="w")

        self.progress_bar = ctk.CTkProgressBar(
            progress_frame,
            height=18,
            mode="determinate",
            corner_radius=6,
            border_width=1,
            border_color=("gray60", "gray40"),
        )
        self.progress_bar.grid(row=0, column=1, padx=(0, 8), pady=10, sticky="ew")
        self.progress_bar.set(0)
        self._indeterminate_active = False

        self.progress_pct_label = ctk.CTkLabel(
            progress_frame,
            text="",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("gray30", "gray80"),
            width=52,
            anchor="e",
        )
        self.progress_pct_label.grid(row=0, column=2, padx=(4, 12), pady=10, sticky="e")

    def _build_console(self):
        self.console = ctk.CTkTextbox(self, font=("Consolas", 12), wrap="none", state="disabled")
        self.console.grid(row=3, column=0, padx=20, pady=(6, 12), sticky="nsew")

    def select_directory(self):
        folder = fd.askdirectory()
        if folder:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, folder)

    def select_file(self):
        file_path = fd.askopenfilename(filetypes=[("Text files", "*.txt"), ("CSV files", "*.csv")])
        if file_path:
            self.file_entry.delete(0, "end")
            self.file_entry.insert(0, file_path)

    def select_mixed_file(self):
        file_path = fd.askopenfilename(filetypes=[("Text files", "*.txt"), ("CSV files", "*.csv")])
        if file_path:
            self.mixed_file_entry.delete(0, "end")
            self.mixed_file_entry.insert(0, file_path)

    def append_log(self, message):
        try:
            self.console.configure(state="normal")
            self.console.insert("end", message)
            self.console.see("end")
            self.console.configure(state="disabled")
        except tk.TclError:
            return

    def clear_console(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def set_running_state(self, running):
        if running:
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.status_text.set("Running")
            self.status_label.configure(text_color="#6bd0a3")
        else:
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.status_text.set("Idle")
            self.status_label.configure(text_color=("gray15", "gray90"))

    def _drain_output_queue(self):
        try:
            while True:
                event, payload = self.output_queue.get_nowait()
                if event == "log":
                    self.append_log(payload)
                    self._parse_progress(payload)
                elif event == "state":
                    self.set_running_state(payload == "running")
        except queue.Empty:
            pass

        try:
            self.after(self.POLL_INTERVAL_MS, self._drain_output_queue)
        except tk.TclError:
            return

    def _reset_progress(self):
        self._progress_total = 0
        self._progress_current = 0
        self._progress_phase = ""
        self._stop_indeterminate()
        self.progress_bar.set(0)
        self.phase_text.set("Starting...")
        self.progress_pct_label.configure(text="0%")

    def _start_indeterminate(self):
        if not self._indeterminate_active:
            self._indeterminate_active = True
            self.progress_bar.configure(mode="indeterminate")
            self.progress_bar.start()
            self.progress_pct_label.configure(text="")

    def _stop_indeterminate(self):
        if self._indeterminate_active:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
            self._indeterminate_active = False

    def _set_progress(self, current, total, phase_label):
        self._stop_indeterminate()
        self._progress_current = current
        self._progress_total = total
        fraction = current / total if total > 0 else 0.0
        self.progress_bar.set(fraction)
        self.phase_text.set(phase_label)
        pct = int(fraction * 100)
        self.progress_pct_label.configure(text="{}%".format(pct))

    def _parse_progress(self, line):
        # Version/startup: "PyPaperBot v1.x.x"
        if "PyPaperBot v" in line:
            if self._progress_phase == "":
                self.phase_text.set("Initializing...")
                self._start_indeterminate()
            return

        # DOI-based download starting: "Downloading papers from DOIs"
        if "Downloading papers from DOIs" in line:
            if self._progress_phase not in ("download",):
                self.phase_text.set("Preparing DOI downloads...")
                self._start_indeterminate()
            return

        # Download phase: "Download 3 of 10 -> ..."
        m = self._re_download.search(line)
        if m:
            cur, total = int(m.group(1)), int(m.group(2))
            self._progress_phase = "download"
            self._set_progress(cur, total, "Downloading {} of {}".format(cur, total))
            return

        # Crossref resolution: "Searching paper 2 of 8 on Crossref..."
        m = self._re_crossref.search(line)
        if m:
            cur, total = int(m.group(1)), int(m.group(2))
            if self._progress_phase != "download":
                self._progress_phase = "crossref"
                self._set_progress(cur, total, "Resolving on Crossref {} of {}".format(cur, total))
            return

        # DOI resolution: "Searching paper 2 of 10 with DOI ..."
        m = self._re_doi_resolve.search(line)
        if m:
            cur, total = int(m.group(1)), int(m.group(2))
            if self._progress_phase != "download":
                self._progress_phase = "doi_resolve"
                self._set_progress(cur, total, "Resolving DOI {} of {}".format(cur, total))
            return

        # Scholar search: "Google Scholar page 1 : 10 papers found"
        m = self._re_scholar_page.search(line)
        if m:
            if self._progress_phase not in ("download", "crossref"):
                self._progress_phase = "search"
                self.phase_text.set("Searching Google Scholar (page {})...".format(m.group(1)))
                self._start_indeterminate()
            return

        # Papers found (PubMed/bioRxiv): "Found 12 papers with DOIs."
        m = self._re_papers_found.search(line)
        if m:
            if self._progress_phase not in ("download", "crossref"):
                self._stop_indeterminate()
                self.phase_text.set("Found {} papers".format(m.group(1)))
            return

        # PubMed/bioRxiv search
        if "Searching PubMed" in line or "Searching bioRxiv" in line:
            if self._progress_phase not in ("download", "crossref"):
                self._progress_phase = "search"
                self.phase_text.set("Searching...")
                self._start_indeterminate()
            return

        # Sci-Hub mirror search
        if "Searching for a sci-hub mirror" in line:
            if self._progress_phase not in ("download",):
                self.phase_text.set("Finding Sci-Hub mirror...")
                self._start_indeterminate()
            return

        # Mirror found
        if "Using Sci-Hub mirror" in line:
            if self._progress_phase not in ("download",):
                self._stop_indeterminate()
                self.phase_text.set("Mirrors ready, starting downloads...")
            return

        # Completion
        m = self._re_work_completed.search(line)
        if m:
            self._progress_phase = "done"
            self._set_progress(1, 1, "Complete")
            return

    def _format_command(self, command):
        try:
            return shlex.join(command)
        except AttributeError:
            return " ".join(shlex.quote(part) for part in command)

    def _parse_integer(self, raw_value, label):
        raw_value = raw_value.strip()
        if not raw_value:
            return None

        try:
            return int(raw_value)
        except ValueError as exc:
            raise ValueError("{} must be a whole number.".format(label)) from exc

    def _parse_proxy_chain(self):
        raw_value = self.proxy_textbox.get("1.0", "end").strip()
        if not raw_value:
            return []
        return [value for value in re.split(r"[\s,]+", raw_value) if value]

    def _build_command(self):
        cli_args = []

        download_dir = self.dir_entry.get().strip()
        if not download_dir:
            raise ValueError("Select a download directory before starting.")
        cli_args.append("--dwn-dir={}".format(download_dir))

        active_tab = self.tabs.get()
        if active_tab == "Keyword Search":
            query = self.query_entry.get().strip()
            if not query:
                raise ValueError("Enter a search query for Keyword Search mode.")

            max_raw = self.query_max_entry.get().strip()
            try:
                max_papers = int(max_raw) if max_raw else 50
                if not 1 <= max_papers <= 100000:
                    raise ValueError()
            except ValueError:
                raise ValueError("Max papers must be a whole number between 1 and 100000.")

            import math
            num_pages = math.ceil(max_papers / 10)
            cli_args.append("--query={}".format(query))
            cli_args.append("--scholar-pages=1-{}".format(num_pages))
            cli_args.append("--scholar-results=10")

            skip_words = self.skip_words_entry.get().strip()
            if skip_words:
                cli_args.append("--skip-words={}".format(skip_words))

        elif active_tab == "Single DOI":
            doi = self.doi_entry.get().strip()
            if not doi:
                raise ValueError("Enter a DOI for Single DOI mode.")
            cli_args.append("--doi={}".format(doi))

        elif active_tab == "Batch Download":
            doi_file = self.file_entry.get().strip()
            if not doi_file:
                raise ValueError("Select a DOI list file for Batch Download mode.")
            if not os.path.isfile(doi_file):
                raise ValueError("The selected DOI file does not exist: {}".format(doi_file))
            cli_args.append("--doi-file={}".format(doi_file))

        elif active_tab == "PubMed Search":
            query = self.pubmed_query_text.get("1.0", "end").strip()
            if not query:
                raise ValueError("Enter a PubMed search query.")
            cli_args.append("--pubmed-query={}".format(query))
            results_raw = self.pubmed_results_entry.get().strip()
            if results_raw:
                try:
                    n = int(results_raw)
                    if not 1 <= n <= 100000:
                        raise ValueError()
                except ValueError:
                    raise ValueError("PubMed max results must be a whole number between 1 and 100000.")
                cli_args.append("--pubmed-results={}".format(n))

        elif active_tab == "bioRxiv Search":
            query = self.biorxiv_query_entry.get().strip()
            if not query:
                raise ValueError("Enter a bioRxiv search query.")
            cli_args.append("--biorxiv-query={}".format(query))
            results_raw = self.biorxiv_results_entry.get().strip()
            if results_raw:
                try:
                    n = int(results_raw)
                    if not 1 <= n <= 100000:
                        raise ValueError()
                except ValueError:
                    raise ValueError("bioRxiv max results must be a whole number between 1 and 100000.")
                cli_args.append("--pubmed-results={}".format(n))

        elif active_tab == "PubMed IDs":
            pmids_raw = self.pmid_textbox.get("1.0", "end").strip()
            if not pmids_raw:
                raise ValueError("Enter at least one PubMed ID.")
            pmids = [p.strip() for p in re.split(r"[\s,]+", pmids_raw) if p.strip()]
            if not pmids:
                raise ValueError("No valid PubMed IDs found.")
            cli_args.append("--pubmed-ids={}".format(",".join(pmids)))

        elif active_tab == "Mixed Batch":
            mixed_file = self.mixed_file_entry.get().strip()
            if not mixed_file:
                raise ValueError("Select a mixed list file for Mixed Batch mode.")
            if not os.path.isfile(mixed_file):
                raise ValueError("The selected mixed file does not exist: {}".format(mixed_file))
            cli_args.append("--mixed-file={}".format(mixed_file))

        min_year = self._parse_integer(self.min_year_entry.get(), "Min Year")
        if min_year is not None:
            cli_args.append("--min-year={}".format(min_year))

        max_by_year = self._parse_integer(self.max_year_entry.get(), "Max by Year")
        max_by_cites = self._parse_integer(self.max_cites_entry.get(), "Max by Cites")
        if max_by_year is not None and max_by_cites is not None:
            raise ValueError("Use either Max by Year or Max by Cites, not both.")
        if max_by_year is not None:
            cli_args.append("--max-dwn-year={}".format(max_by_year))
        if max_by_cites is not None:
            cli_args.append("--max-dwn-cites={}".format(max_by_cites))

        scihub_mirror = self.scihub_entry.get().strip()
        if scihub_mirror:
            cli_args.append("--scihub-mirror={}".format(scihub_mirror))

        annas_mirror = self.annas_entry.get().strip()
        if annas_mirror:
            cli_args.append("--annas-archive-mirror={}".format(annas_mirror))

        unpaywall_email = self.unpaywall_entry.get().strip()
        if unpaywall_email:
            cli_args.append("--unpaywall-email={}".format(unpaywall_email))

        single_proxy = self.single_proxy_entry.get().strip()
        proxy_chain = self._parse_proxy_chain()
        if single_proxy and proxy_chain:
            raise ValueError("Use either Single Proxy or Proxy Chain, not both.")
        if single_proxy:
            cli_args.append("--single-proxy={}".format(single_proxy))

        restrict_mapping = {
            "Download PDFs + BibTeX": None,
            "BibTeX only": 0,
            "PDF only": 1,
        }
        restrict_value = restrict_mapping[self.restrict_value.get()]
        if restrict_value is not None:
            cli_args.append("--restrict={}".format(restrict_value))

        if self.use_doi_filename.get():
            cli_args.append("--use-doi-as-filename")

        if proxy_chain:
            cli_args.append("--proxy")
            cli_args.extend(proxy_chain)

        if is_frozen_app():
            return [sys.executable, CLI_CHILD_FLAG] + cli_args

        return [sys.executable, os.path.abspath(__file__), CLI_CHILD_FLAG] + cli_args

    def start_process(self):
        if self.process is not None:
            return

        try:
            command = self._build_command()
        except ValueError as exc:
            self.append_log("Error: {}\n".format(exc))
            return

        self.stop_requested = False
        self.is_starting = True
        self.clear_console()
        self._reset_progress()
        self.append_log("Repository root: {}\n".format(self.repo_root))
        self.append_log("Executing: {}\n\n".format(self._format_command(command)))
        self.set_running_state(True)

        worker = threading.Thread(target=self.run_bot, args=(command,), daemon=True)
        worker.start()

    def run_bot(self, command):
        try:
            creationflags = 0
            popen_kwargs = {}
            if os.name == "nt":
                creationflags = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
            else:
                popen_kwargs["start_new_session"] = True

            env = os.environ.copy()
            env["PYPAPERBOT_GUI"] = "1"

            process = subprocess.Popen(
                command,
                cwd=self.execution_cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                env=env,
                **popen_kwargs
            )
            self.process = process
            self.is_starting = False

            if self.stop_requested:
                self._terminate_process(process)

            if process.stdout is not None:
                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    self.output_queue.put(("log", line))

                remainder = process.stdout.read()
                if remainder:
                    self.output_queue.put(("log", remainder))

            return_code = process.wait()
            if self.stop_requested:
                self.output_queue.put(("log", "\n--- Process stopped by user ---\n"))
            elif return_code == 0:
                self.output_queue.put(("log", "\n--- Process finished successfully ---\n"))
            else:
                self.output_queue.put(
                    ("log", "\n--- Process exited with code {} ---\n".format(return_code))
                )
        except Exception as exc:
            self.output_queue.put(("log", "\nError: {}\n".format(exc)))
        finally:
            self.is_starting = False
            self.process = None
            self.output_queue.put(("state", "idle"))

    def _force_stop_after_grace_period(self, process):
        try:
            process.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            pass

        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            self.output_queue.put(("log", "\n--- Forced process kill sent after timeout ---\n"))
        except ProcessLookupError:
            return
        except Exception as exc:
            self.output_queue.put(("log", "\nError while forcing stop: {}\n".format(exc)))

    def stop_process(self):
        process = self.process
        if process is None:
            if self.is_starting:
                self.stop_requested = True
                self.append_log("\n--- Stop requested while the process is launching ---\n")
                return
            self.set_running_state(False)
            return

        self.stop_requested = True
        self.append_log("\n--- Stop requested ---\n")

        try:
            self._terminate_process(process)
        except ProcessLookupError:
            return
        except Exception as exc:
            self.append_log("\nError while stopping process: {}\n".format(exc))
            return

        watcher = threading.Thread(
            target=self._force_stop_after_grace_period,
            args=(process,),
            daemon=True,
        )
        watcher.start()

    def on_close(self):
        process = self.process
        if self.is_starting and process is None:
            self.stop_requested = True
        if process is not None:
            self.stop_requested = True
            try:
                self._terminate_process(process)
            except Exception:
                pass
        self.destroy()

    def _terminate_process(self, process):
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)


if __name__ == "__main__":
    app = PyPaperBotGUI()
    app.mainloop()
