"""nexbooks's side of the shared sign-in module ``vendor/nexoidc``: the ``Store`` over its tables and the start-up
configuration.

The module runs the protocol, decides who an identity is and
keeps the provider list; it never sees a database. What is nexbooks's own lives here: accounts and their names, what an
invitation brings, the address an account takes from its provider (never confirmed by that alone) and the encryption
of the client secrets with the server secret.

nexbooks is new: it never kept a single provider in its settings, so there is nothing to migrate and no way back to an
older version (the adapter of the contract tests says ``legacy_model = "none"``).

A ``SqlStore`` works inside one SQLAlchemy session, one request. The module commits when an outcome is settled and
rolls back when it refuses after a write (a consumed invitation comes back that way).
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..db import SessionLocal
from ..models import MEMBER, SIGN_IN_OIDC, SIGN_IN_PASSWORD, Account, OidcLink, OidcProvider, utcnow
from ..models import Invite as InviteRow
from ..security import decrypt_secret, encrypt_secret
from ..vendor import nexoidc
from ..vendor.nexoidc import AccountState, Invite, Link, Provider, ProviderValues, secret_context
from . import accounts, settings_service

logger = logging.getLogger("nexbooks.oidc")

APP_NAME = "nexbooks"
#: Where the API lies: the paths are ``/api/v1/oidc/<slug>/start`` and so on.
API_PREFIX = "/api/v1"
#: Where nexbooks's sign-in and account pages are, as the module sends the browser there.
LOGIN_PAGE = "/login"
ACCOUNT_PAGE = "/account"
HOME = "/"
#: Longest account name (``accounts.NAME_PATTERN``).
NAME_MAX = 64
_NUMBERED = re.compile(r"^(.*?)(-\d+)$")


def _public_url() -> str:
    with SessionLocal() as db:
        return settings_service.public_url(db)


def _save_public_url(address: str) -> None:
    """The address the operator confirmed in the authentik card becomes the setting ``public_url``, at once and in a
    transaction of its own (module 1.0.10). nexbooks takes only ``scheme://host[:port]``: an address it would refuse in
    its own settings is not kept, and the module then answers ``public_url_needed``."""
    try:
        clean = settings_service.normalize_public_url(address)
    except ValueError:
        logger.warning("The address confirmed in the authentik card is not one nexbooks keeps")
        return
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": clean})


def _server_secret() -> str:
    return get_settings().resolved_secret_key()


def configure() -> None:
    """Tell the module who it runs in; called once at start-up (and by the tests)."""
    nexoidc.configure(
        nexoidc.AppConfig(
            app_name=APP_NAME,
            server_secret=_server_secret,
            api_prefix=API_PREFIX,
            public_url=_public_url,
            save_public_url=_save_public_url,
            login_page=LOGIN_PAGE,
            account_page=ACCOUNT_PAGE,
            home=HOME,
            # Two instances on one host under different ports keep their attempt cookies apart.
            cookie_suffix=get_settings().cookie_name_suffix(),
        )
    )


def clean_name(raw: str) -> str:
    """An account name from what the provider suggests: lower case, nexbooks's characters, at most 64; a number at the
    end (``max-2``) survives the cut. Empty when nothing usable is left."""
    cleaned = re.sub(r"[^a-z0-9._-]", "-", raw.strip().lower()).strip("-._")
    if len(cleaned) > NAME_MAX:
        numbered = _NUMBERED.match(cleaned)
        suffix = numbered.group(2) if numbered else ""
        stem = numbered.group(1) if numbered else cleaned
        cleaned = stem[: NAME_MAX - len(suffix)].rstrip("-._") + suffix
    return cleaned if accounts.NAME_PATTERN.match(cleaned) else ""


def has_links(db: Session, account_id: int) -> bool:
    return db.scalar(select(OidcLink.id).where(OidcLink.account_id == account_id).limit(1)) is not None


def follows_provider(account: Account) -> bool:
    """An account that signs in through a provider only: its address is the provider's and follows it. Every other
    account is only offered the provider's address (account page), never given it."""
    return account.sign_in == SIGN_IN_OIDC and not account.password_hash


def address_taken(db: Session, email: str, own_id: int) -> bool:
    """Whether another account has this address already: nexbooks never gives one address to two accounts."""
    return (
        db.scalar(
            select(Account.id).where(func.lower(Account.email) == email.strip().lower(), Account.id != own_id).limit(1)
        )
        is not None
    )


def take_address(db: Session, account: Account, email: str | None) -> bool:
    """The provider's address becomes the account's, marked as from the provider (it goes with the last link) and
    **not confirmed**: a provider may send an address the person never showed to be theirs (authentik lets everybody
    change their own, Entra sends no ``email_verified``). The person confirms it by the link nexbooks sends, as any
    other address. False when there is none, it is no address, or another account has it."""
    clean = (email or "").strip()[:255]
    if not clean or not accounts.EMAIL_PATTERN.match(clean) or address_taken(db, clean, account.id):
        return False
    if clean.lower() == (account.email or "").strip().lower():
        # The same address it has: a confirmation of it by the person stays.
        return True
    account.email = clean
    account.email_from_provider = True
    account.email_confirmed_at = None
    return True


class SqlStore:
    """``nexoidc.Store`` over ``oidc_providers`` and ``oidc_links``, inside one session."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Providers -------------------------------------------------------------------------------------------------

    def _provider(self, row: OidcProvider | None) -> Provider | None:
        if row is None:
            return None
        return Provider(
            id=row.id,
            slug=row.slug,
            label=row.label,
            issuer=row.issuer,
            client_id=row.client_id,
            client_secret=decrypt_secret(row.client_secret_enc, secret_context(row.id)),
            scopes=row.scopes,
            enabled=row.enabled,
            auto_create=row.auto_create,
            trusts_second_factor=row.trusts_second_factor,
            managed=row.managed,
            position=row.position,
            redirect_path=row.redirect_path,
            created_at=row.created_at,
            registered_redirect=row.registered_redirect or "",
        )

    def list_providers(self) -> list[Provider]:
        rows = self.db.scalars(select(OidcProvider).order_by(OidcProvider.position, OidcProvider.id))
        return [provider for provider in (self._provider(row) for row in rows) if provider is not None]

    def get_provider(self, provider_id: int) -> Provider | None:
        return self._provider(self.db.get(OidcProvider, provider_id))

    def provider_by_slug(self, slug: str) -> Provider | None:
        return self._provider(self.db.scalar(select(OidcProvider).where(OidcProvider.slug == slug)))

    @staticmethod
    def _apply(row: OidcProvider, values: ProviderValues) -> None:
        row.slug, row.label, row.issuer, row.client_id = values.slug, values.label, values.issuer, values.client_id
        row.scopes, row.enabled, row.auto_create = values.scopes, values.enabled, values.auto_create
        row.trusts_second_factor, row.managed, row.position = (
            values.trusts_second_factor,
            values.managed,
            values.position,
        )
        row.redirect_path = values.redirect_path
        # None keeps what the authentik button registered last; only the button writes it.
        if values.registered_redirect is not None:
            row.registered_redirect = values.registered_redirect

    def insert_provider(self, values: ProviderValues) -> Provider:
        row = OidcProvider()
        self._apply(row, values)
        if values.registered_redirect is None:
            row.registered_redirect = ""
        self.db.add(row)
        self.db.flush()
        row.client_secret_enc = encrypt_secret(values.client_secret or "", secret_context(row.id))
        self.db.flush()
        provider = self._provider(row)
        assert provider is not None
        return provider

    def update_provider(self, provider_id: int, values: ProviderValues) -> Provider:
        row = self.db.get(OidcProvider, provider_id)
        assert row is not None
        self._apply(row, values)
        if values.client_secret is not None:
            row.client_secret_enc = encrypt_secret(values.client_secret, secret_context(row.id))
        self.db.flush()
        provider = self._provider(row)
        assert provider is not None
        return provider

    def delete_provider(self, provider_id: int) -> None:
        # Foreign keys are on (db.py), the links would go anyway; said here so that nothing depends on a pragma.
        self.drop_links(provider_id)
        self.db.execute(delete(OidcProvider).where(OidcProvider.id == provider_id))

    # --- Links -----------------------------------------------------------------------------------------------------

    @staticmethod
    def _link(row: OidcLink) -> Link:
        return Link(row.provider_id, row.subject, row.account_id, row.issuer, row.email, row.created_at,
                    row.last_used_at)

    def find_link(self, provider_id: int, subject: str) -> Link | None:
        row = self.db.scalar(select(OidcLink).where(OidcLink.provider_id == provider_id, OidcLink.subject == subject))
        return self._link(row) if row else None

    def links_of_provider(self, provider_id: int) -> list[Link]:
        rows = self.db.scalars(select(OidcLink).where(OidcLink.provider_id == provider_id).order_by(OidcLink.id))
        return [self._link(row) for row in rows]

    def links_of_account(self, account_id: int) -> list[Link]:
        rows = self.db.scalars(select(OidcLink).where(OidcLink.account_id == account_id).order_by(OidcLink.id))
        return [self._link(row) for row in rows]

    def add_link(self, link: Link) -> None:
        self.db.add(OidcLink(provider_id=link.provider_id, subject=link.subject, account_id=link.account_id,
                             issuer=link.issuer, email=link.email))
        self.db.flush()
        self._address(link.account_id, link.email)

    def remove_link(self, provider_id: int, account_id: int) -> bool:
        result = self.db.execute(
            delete(OidcLink).where(OidcLink.provider_id == provider_id, OidcLink.account_id == account_id)
        )
        removed = bool(result.rowcount)  # type: ignore[attr-defined]
        if removed:
                self._forget_provider_address(account_id)
        return removed

    def drop_links(self, provider_id: int) -> int:
        holders = set(self.db.scalars(select(OidcLink.account_id).where(OidcLink.provider_id == provider_id)))
        result = self.db.execute(delete(OidcLink).where(OidcLink.provider_id == provider_id))
        for account_id in holders:
            self._forget_provider_address(account_id)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def _forget_provider_address(self, account_id: int) -> None:
        """No link left for the account: an address that came from a provider goes too; one of its own (confirmed by
        a link, from the invitation, typed by the operator) stays, and so does every address while another link
        holds."""
        if has_links(self.db, account_id):
            return
        account = self.db.get(Account, account_id)
        if account is not None and account.email_from_provider:
            account.email = ""
            account.email_confirmed_at = None
            account.email_from_provider = False

    def touch_link(self, provider_id: int, subject: str, email: str | None) -> None:
        self.db.execute(
            update(OidcLink)
            .where(OidcLink.provider_id == provider_id, OidcLink.subject == subject)
            .values(last_used_at=utcnow(), email=email)
        )
        account_id = self.db.scalar(
            select(OidcLink.account_id).where(OidcLink.provider_id == provider_id, OidcLink.subject == subject)
        )
        if account_id is not None:
            self._address(account_id, email)

    def _address(self, account_id: int, email: str | None) -> None:
        """What the provider says about the address: an account through the provider only follows it (unconfirmed),
        as long as its address is the provider's or none; one it has from elsewhere (the invitation, confirmed by its
        owner) stays, and the provider's is offered on the account page, as to every other account."""
        if not email:
            return
        account = self.db.get(Account, account_id)
        if account is None or not follows_provider(account):
            return
        if account.email and not account.email_from_provider:
            return
        take_address(self.db, account, email)

    # --- Accounts --------------------------------------------------------------------------------------------------

    def account_state(self, account_id: int) -> AccountState | None:
        row = self.db.get(Account, account_id)
        if row is None:
            return None
        from . import totp

        return AccountState(
            id=row.id,
            name=row.name,
            blocked=row.blocked_at is not None,
            has_password=row.sign_in == SIGN_IN_PASSWORD and bool(row.password_hash),
            # A code from an app or a passkey: either is asked after a provider that is not trusted with it.
            has_second_factor=totp.has_second_factor(self.db, row),
        )

    def clean_name(self, raw: str) -> str:
        return clean_name(raw)

    def name_taken(self, name: str) -> bool:
        return accounts.by_name(self.db, name) is not None

    def create_account(self, name: str, *, invite: Invite | None, email: str | None) -> int:
        row = Account(name=name, role=MEMBER, sign_in=SIGN_IN_OIDC, whats_new_seen=__version__)
        self.db.add(row)
        # A name or a link another return wrote a moment ago fails here (unique), and the module takes it from there.
        self.db.flush()
        invited = invite.rights if invite is not None and isinstance(invite.rights, InviteRow) else None
        if invited is not None and invited.email and not address_taken(self.db, invited.email, row.id):
            # The address the operator made the invitation for, as with a password (unconfirmed, the person's own).
            row.email = invited.email.strip()[:255]
        else:
            take_address(self.db, row, email)
        logger.info("Account created name=%s role=%s sign_in=oidc", row.name, MEMBER)
        return row.id

    # --- Invitations -----------------------------------------------------------------------------------------------

    def find_invite(self, key: str) -> Invite | None:
        row = accounts.find_invite(self.db, key)
        # nexbooks's invitations bring membership and, when the operator named one, an address; the row travels as
        # its "rights".
        return Invite(key=key, id=row.id, rights=row) if row is not None else None

    def consume_invite(self, invite: Invite) -> bool:
        row = invite.rights
        return isinstance(row, InviteRow) and accounts.consume(self.db, row)

    def accept_invite(self, account_id: int, invite: Invite) -> None:
        """A known identity came through an invitation (module 1.0.8): what it brings comes on top, never instead.
        nexbooks's invitations bring membership, which the account has, and the address they were made for, which an
        account without one takes (unconfirmed, like any typed address)."""
        row = self.db.get(Account, account_id)
        invited = invite.rights if isinstance(invite.rights, InviteRow) else None
        if row is None or invited is None or not invited.email or row.email:
            return
        if not address_taken(self.db, invited.email, row.id):
            row.email = invited.email.strip()[:255]
            row.email_from_provider = False
            row.email_confirmed_at = None
    
    # --- Transaction -----------------------------------------------------------------------------------------------

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


__all__ = [
    "SqlStore",
    "clean_name",
    "configure",
    "follows_provider",
    "take_address",
]
