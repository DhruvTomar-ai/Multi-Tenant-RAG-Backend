"""User-management operations."""
import pytest

from api_tests.support import ANON, PASSWORD, new_user

USERS = "/users"
GOOD = {"email": "probe@example.com", "username": "probeuser", "password": PASSWORD}


def test_anonymous_cannot_list_users(api, fresh):
    api.check(
        ANON, "GET", USERS, expect=401, behavior="User directory requires a login", absent=[fresh.bob.email, fresh.alice.email],
        why="F-1 HIGH: anyone can read every user in every tenant.",
    )


def test_member_cannot_see_other_tenants_users(api, fresh):
    api.check(
        fresh.mona, "GET", USERS, expect=(200, 403), behavior="Denied, or limited to the user's own tenant", absent=[fresh.bob.email],
        why="F-1 HIGH: the list has no tenant scoping.",
    )


def test_anonymous_cannot_create_users(api, clean):
    api.check(ANON, "POST", USERS, expect=401, behavior="User creation requires authorization", json=GOOD, why="F-2 HIGH: anyone can create accounts here.")


def test_duplicate_email_is_handled(api, clean):
    new_user(api, "Dup", "dup@example.com")
    api.check(
        ANON, "POST", USERS, expect=(400, 401), behavior="Clean rejection, not a server error",
        json={**GOOD, "email": "dup@example.com"},
        why="BUG: the route catches ValueError, but a duplicate raises IntegrityError.",
    )


def test_duplicate_username_is_handled(api, clean):
    new_user(api, "Dup", "dup@example.com")
    api.check(
        ANON, "POST", USERS, expect=(400, 401), behavior="Clean rejection, not a server error",
        json={**GOOD, "username": "dup"},
        why="BUG: same unhandled IntegrityError.",
    )


@pytest.mark.parametrize(
    "body",
    [{}, {**GOOD, "email": "bad"}, {**GOOD, "username": "ab"}, {**GOOD, "password": "short"}],
    ids=["empty", "bad_email", "short_username", "short_password"],
)
def test_create_user_invalid_body_is_rejected(api, clean, body):
    api.check(ANON, "POST", USERS, expect=(401, 422), behavior="Rejected", json=body)


def test_account_from_users_route_can_log_in_or_fails_cleanly(api, clean):
    api.send(ANON, "POST", USERS, json=GOOD)
    api.check(
        ANON, "POST", "/auth/login", expect=(200, 401), behavior="Login works or is cleanly rejected",
        json={"email": GOOD["email"], "password": GOOD["password"]},
        why="F-2 BUG: the route stores 'placeholder_hash', which passlib cannot read, so login crashes.",
    )


def test_squatted_email_does_not_lock_out_the_real_owner(api, clean):
    api.send(ANON, "POST", USERS, json={**GOOD, "email": "victim@example.com"})
    api.check(
        ANON, "POST", "/auth/register", expect=201, behavior="An anonymous caller cannot block someone's email address",
        json={"name": "Victim", "email": "victim@example.com", "password": PASSWORD},
        why="F-2: an attacker can permanently block an address, and that account can never log in.",
    )


def test_user_list_returns_no_password_hashes(api, fresh):
    api.check(fresh.mona, "GET", USERS, expect=(200, 403), behavior="Password hashes are never returned", absent=["password", "$2b$"])
