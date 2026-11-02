"""The listing (stage 2b spec §11.2; area A3): `PlaudSource.list_all` against the fake Plaud
server's account (§10.6 step 1) and its listing-shaped faults (§21). Every ID and title is
synthetic."""

import pytest
from fake_plaud import html_page, json_response, server_error
from recordings.plaud.client import (
    LIST_PATH,
    PlaudAuthError,
    PlaudBadResponse,
    PlaudClient,
    PlaudUnavailable,
)
from recordings.sources.base import Candidate, Source
from recordings.sources.plaud import (
    CAP,
    TOO_MANY_MALFORMED,
    ListingIncomplete,
    PlaudSource,
    id_form,
    malformed_limit,
)


class Tokens:
    def access_token(self) -> str:
        return "synthetic-access-1"

    def refresh(self, rejected: str) -> str:
        raise PlaudAuthError()


def source(fake, **kw) -> PlaudSource:
    return PlaudSource(PlaudClient(Tokens(), api_base=fake.api_url), **kw)


def page_of(page: str):
    return lambda path, query: path == LIST_PATH and query.get("page") == [page]


def listing_calls(fake) -> int:
    return sum(1 for r in fake.requests if r.path == LIST_PATH)


def test_it_pages_until_an_empty_page_and_a_clamped_page_never_ends_it(plaud_server):
    # why: §11.2, audio-router's lesson. A server that clamps page sizes answers short pages; a
    # listing that stopped at the first short one would leave the rest unlisted, and a later
    # step would read them as gone.
    account = plaud_server.account()
    account.add_many(25)
    account.clamp = 10
    listing = source(plaud_server, page_size=100).list_all()
    assert listing.status == "complete"
    assert len(listing.rows) == 25 and listing.pages == 4  # 10, 10, 5, then the empty page
    assert listing_calls(plaud_server) == 4
    assert listing.census == {"of_": 25}
    assert (listing.repeats, listing.malformed, listing.why) == (0, 0, None)


def test_an_empty_account_is_a_complete_listing_of_nothing(plaud_server):
    plaud_server.account()
    listing = source(plaud_server).list_all()
    assert (listing.status, listing.rows, listing.pages, listing.census) == ("complete", (), 1, {})


def test_repeats_across_pages_are_kept_once_and_counted(plaud_server):
    # §11.2: a row served twice (in either form) counts once, by canonical ID.
    account = plaud_server.account()
    recs = account.add_many(12)
    account.twice = {recs[0].plaud_id, recs[5].plaud_id}
    listing = source(plaud_server, page_size=10).list_all()
    assert listing.status == "complete"
    assert len(listing.rows) == 12 and listing.repeats == 2
    assert listing.ids() == {r.plaud_id for r in recs}


def test_drift_while_paging_shows_as_a_repeat_never_a_loss(plaud_server):
    # §21's "drift while paging": a recording made between page 1 and page 2 shifts every row by
    # one, so page 2 begins with page 1's last row again.
    account = plaud_server.account()
    recs = account.add_many(15)
    plaud_server.after(1, lambda: account.add("Synthetic arrival mid-listing"))
    listing = source(plaud_server, page_size=10).list_all()
    assert listing.status == "complete" and listing.repeats == 1
    assert {r.plaud_id for r in recs} <= listing.ids()
    assert len(listing.rows) == 15  # the newcomer, on page 1's first place, was already served


def test_malformed_rows_up_to_one_percent_and_at_least_two_are_tolerated(plaud_server):
    # §11.2: a row with no ID, or no title field, is counted. A row with an ID but no title is
    # still listed (its title None); a row with no ID can't be.
    account = plaud_server.account()
    account.add_many(9)
    untitled = account.add(untitled=True)
    account.malformed = 1
    listing = source(plaud_server).list_all()
    assert listing.status == "complete"
    assert listing.malformed == 2 and malformed_limit(11) == 2
    (row,) = [r for r in listing.rows if r.canonical_id == untitled.plaud_id]
    assert row.title is None
    account.malformed = 2  # 3 of 12
    listing = source(plaud_server).list_all()
    assert (listing.status, listing.why) == ("bad", TOO_MANY_MALFORMED)


def test_one_percent_of_a_large_listing_is_tolerated(plaud_server):
    account = plaud_server.account()
    account.add_many(497)
    account.malformed = 5  # 5 of 502 is under 1%
    assert source(plaud_server).list_all().status == "complete"
    account.malformed = 6  # 6 of 503 is over
    assert source(plaud_server).list_all().status == "bad"


def test_an_html_page_or_the_wrong_shape_is_a_bad_listing_never_an_empty_account(plaud_server):
    # §11.2: "An HTML page, or JSON of the wrong shape, is a bad listing, never an empty account."
    account = plaud_server.account()
    account.add_many(25)
    plaud_server.fault(page_of("2"), html_page(), times=1)
    listing = source(plaud_server, page_size=10).list_all()
    assert (listing.status, listing.rows, listing.why) == ("bad", (), "PlaudBadResponse")
    assert isinstance(listing.error, PlaudBadResponse)
    plaud_server.fault(page_of("1"), json_response({"detail": "no list here"}), times=1)
    listing = source(plaud_server, page_size=10).list_all()
    assert (listing.status, listing.rows) == ("bad", ())


def test_a_run_ending_error_part_way_is_a_partial_listing_with_its_rows(plaud_server):
    # §11.2: "partial: it ended on an error part-way. Its rows feed §13.1's privacy checks
    # only." The error is kept, so the run can record what ended it.
    account = plaud_server.account()
    account.add_many(25)
    plaud_server.fault(page_of("3"), server_error())
    listing = source(plaud_server, page_size=10).list_all()
    assert listing.status == "partial" and len(listing.rows) == 20
    assert listing.why == "PlaudUnavailable" and isinstance(listing.error, PlaudUnavailable)


def test_a_run_ending_error_on_the_first_page_is_a_bad_listing(plaud_server):
    account = plaud_server.account()
    account.add_many(5)
    plaud_server.fault(page_of("1"), server_error())
    listing = source(plaud_server).list_all()
    assert (listing.status, listing.rows, listing.why) == ("bad", (), "PlaudUnavailable")


def test_a_refused_sign_in_part_way_is_partial_and_carries_the_refusal(plaud_server):
    account = plaud_server.account()
    account.add_many(15)
    plaud_server.fault(page_of("2"), json_response({"detail": "unauthorized"}, 401))
    listing = source(plaud_server, page_size=10).list_all()
    assert listing.status == "partial" and isinstance(listing.error, PlaudAuthError)


def test_the_page_cap_makes_the_listing_bad(plaud_server):
    account = plaud_server.account()
    account.add_many(30)
    listing = source(plaud_server, page_size=10, max_pages=3).list_all()
    assert (listing.status, listing.why, len(listing.rows)) == ("bad", CAP, 30)


def test_the_census_counts_each_form_and_reads_any_case():
    hexid = f"{0x3FA9:032x}"
    assert id_form("of_" + hexid) == id_form("OF_" + hexid.upper()) == "of_"
    assert id_form(hexid) == id_form(hexid.upper()) == "hex"
    assert id_form("rec-" + hexid) == id_form(hexid[:31]) == id_form("of_" + hexid + "0") == "other"


def test_a_listing_in_either_form_and_one_the_consent_list_cant_name(plaud_server):
    account = plaud_server.account(form="hex")
    account.add_many(3)
    account.add(other_id="synthetic-other-form-1")
    listing = source(plaud_server).list_all()
    assert listing.census == {"hex": 3, "other": 1}
    account.flip()
    assert source(plaud_server).list_all().census == {"of_": 3, "other": 1}


def test_candidates_are_the_complete_listing_and_refuse_anything_less(plaud_server):
    # Main spec §9.0's Source: the shared compare can list any source that lists.
    account = plaud_server.account()
    rec = account.add("Synthetic standup")
    plaud = source(plaud_server)
    assert isinstance(plaud, Source) and plaud.name == "plaud"
    assert plaud.candidates() == [
        Candidate(
            ref="of_" + rec.plaud_id,
            title="Synthetic standup",
            recorded_at=rec.start_at,
            duration_ms=rec.duration_ms,
        )
    ]
    plaud_server.fault(page_of("1"), server_error())
    with pytest.raises(ListingIncomplete):
        plaud.candidates()


def test_the_fake_refuses_a_page_size_under_ten(plaud_server):
    plaud_server.account()
    with pytest.raises(PlaudBadResponse):
        PlaudClient(Tokens(), api_base=plaud_server.api_url).list_page(1, 5)
