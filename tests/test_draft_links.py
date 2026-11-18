"""Credential-free handoff: Gmail compose links, mailto links, unsent .eml files, and the HTML page. Nothing is sent."""
import re
import urllib.parse
from email import message_from_bytes

import pytest

from icp import draft_links as dl
from scripts.draft_links import main
from tests.test_gmail import rec

BODY = "Hi Ria,\n\nI noticed Acme's Enterprise plan & SOC 2 work.\n\nBest,\n[Your name]"


def test_compose_url_round_trips_accents_newlines_and_ampersands():
    """[high] The Gmail compose link carries To, Subject and Body exactly, including newlines, & and accents."""
    url = dl.compose_url("ria@acme.com", "Sécurité & vente", BODY)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith("https://mail.google.com/mail/?") and q["view"] == ["cm"]
    assert q["to"] == ["ria@acme.com"] and q["su"] == ["Sécurité & vente"] and q["body"] == [BODY]


def test_header_injection_and_bad_addresses_are_refused_everywhere():
    """[high] A newline in the recipient or subject, or a malformed address, never becomes a link, mailto or file."""
    for fn in (dl.compose_url, dl.mailto_url, dl.eml_bytes):
        with pytest.raises(ValueError, match="line break"):
            fn("ria@acme.com\nBcc: x@evil.com", "s", "b")
        with pytest.raises(ValueError, match="line break"):
            fn("ria@acme.com", "s\r\nBcc: x@evil.com", "b")
        with pytest.raises(ValueError, match="valid email"):
            fn("not-an-email", "s", "b")


def test_eml_is_marked_unsent_and_keeps_the_text():
    """[high] The .eml carries X-Unsent: 1 (mail apps open it as an editable draft) with To, Subject and body intact."""
    m = message_from_bytes(dl.eml_bytes("ria@acme.com", "Hello", BODY))
    assert m["X-Unsent"] == "1" and m["To"] == "ria@acme.com" and "SOC 2" in m.get_payload(decode=True).decode()
    assert "Date" not in m and "Message-ID" not in m, "nothing that makes it look already sent"


def test_only_eligible_drafts_are_listed_with_reasons_for_the_rest():
    """[high] drafted go/go_flagged records with an email are listed; everything else is reported with a reason."""
    items, skipped = dl.items_from([rec("a.com"), rec("b.com", decision="no_go", status="blocked"), rec("c.com", email=None), rec("d.com", status="failed_provenance")])
    assert [i["domain"] for i in items] == ["a.com"] and {d for d, _ in skipped} == {"b.com", "c.com", "d.com"}


def test_sender_name_replaces_the_placeholder():
    """[medium] --sender-name fills the sign-off in the page, link and file."""
    items, _ = dl.items_from([rec()], "Mira Shah")
    assert "Mira Shah" in items[0]["body"] and "[Your name]" not in items[0]["body"]


def test_page_escapes_everything_and_has_no_scripts_from_data():
    """[high] Hostile text in names, subjects or bodies is escaped; the only script is ours."""
    r = rec(extra={"contact": {"first_name": "<img src=x onerror=alert(1)>", "last_name": "", "email": "ria@acme.com", "title": "<b>CEO</b>"}})
    r["draft"]["subject"] = '"><script>alert(1)</script>'
    r["draft"]["email"] = "Hi,\n</textarea><script>alert(2)</script>\nBest,\n[Your name]"
    items, sk = dl.items_from([r])
    page = dl.render_html(items, sk)
    assert "<img src=x" not in page and "alert(1)</script>" not in page and "</textarea><script>alert(2)" not in page
    assert page.count("<script>") == 1 and "&lt;script&gt;" in page


def test_page_says_local_nothing_sent_and_links_are_safe():
    """[high] The page warns it holds real emails, says nothing was sent, and links open in a new tab with noopener."""
    page = dl.render_html(*dl.items_from([rec()]))
    assert "Do not publish" in page and "Nothing has been sent" in page and 'rel="noopener noreferrer"' in page
    assert "noindex" in page and "https://mail.google.com/mail/?" in page
    assert not re.search(r"https?://(?!mail\.google\.com)[a-z0-9.-]+\.(com|io)/[^\"]*\bsend\b", page)


def test_cli_writes_the_page_and_eml_files(tmp_path, capsys):
    """[high] The script reads the named DB and writes draft_links.html plus one .eml per draft, and needs --db."""
    from icp import store
    from icp.fetch import open_db
    db = tmp_path / "d.db"
    c = open_db(str(db))
    store.save_record(c, rec("a.com"))
    store.save_record(c, rec("b.com", decision="no_go", status="blocked"))
    assert main(["--db", str(db), "--out", str(tmp_path / "o"), "--sender-name", "Mira Shah"]) == 0
    assert (tmp_path / "o" / "draft_links.html").exists() and (tmp_path / "o" / "eml" / "a.com.eml").exists()
    assert not (tmp_path / "o" / "eml" / "b.com.eml").exists()
    assert "1 drafts, 1 skipped" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main([])
