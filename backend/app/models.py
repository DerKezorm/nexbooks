"""The data model.

Everything lives in SQLite. The schema carries a version number (``db.SCHEMA_VERSION``); a change to a table here
comes with a migration step in ``db.MIGRATIONS`` and a new recorded schema in ``tests/schema/``.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, column_property, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """SQLite forgets the time zone. Stored as UTC, read back as UTC with the zone attached."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)


OPERATOR = "operator"
MEMBER = "member"
ROLES = (OPERATOR, MEMBER)

SIGN_IN_PASSWORD = "password"
SIGN_IN_OIDC = "oidc"


class Account(Base):
    """A person. The first account is the operator; the others come by invitation or through OIDC.

    The operator manages accounts and does not read the books of anybody."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Lower case, the name people sign in with. What people see is the display name, when there is one.
    name: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(80), default="")
    #: The version whose "What's new" the account has read or put away.
    whats_new_seen: Mapped[str] = mapped_column(String(32), default="")
    role: Mapped[str] = mapped_column(String(16), default=MEMBER)
    sign_in: Mapped[str] = mapped_column(String(16), default=SIGN_IN_PASSWORD)
    #: Argon2id. Empty for accounts that sign in through OIDC only.
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    #: When the address was confirmed by a link sent to it (or came from the provider); empty: one the operator typed.
    email_confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: The address came from a sign-in provider (``services/oidc_store.py``): it goes with the last link, and it is
    #: never confirmed by that alone.
    email_from_provider: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    #: The interface language; empty: the browser's.
    language: Mapped[str] = mapped_column(String(16), default="")
    #: What the person chose for themselves (``routers/auth.py``, ``PROFILE``): only what it chose is kept.
    profile: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Set when the operator blocked the account: no sign-in, sessions and tokens end at once.
    blocked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: The second factor (``services/totp.py``): the seed encrypted with the server secret, empty while off; the
    #: recovery codes as a JSON list of keyed hashes; the time step of the last code taken (no replay).
    totp_secret_enc: Mapped[str] = mapped_column(Text, default="")
    totp_recovery: Mapped[str] = mapped_column(Text, default="")
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)
    #: The profile picture (``services/avatars.py``): a square WebP drawn anew, loaded only when asked for.
    avatar: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    avatar_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Whether the account ever signed in: its very first sign-in is no "new device" (``services/notices.py``).
    signed_in_before: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    #: The user handle its passkeys carry (``services/passkeys.py``): random, the same for all of its passkeys, so a
    #: passkey names its account without its name or id. Hex; empty until the first passkey.
    passkey_handle: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    #: Where "Send to Kindle" mails an e-book to (``services/kindle.py``): an address at kindle.com, set by the person
    #: in a browser; empty: none.
    kindle_address: Mapped[str] = mapped_column(String(255), default="", server_default=text("''"))
    #: Not a column: whether the session that asks came in through a provider that checks the second factor itself
    #: (``AuthSession.second_factor_by_provider``), set by ``deps.require_account`` for ``totp.setup_required``.
    second_factor_by_provider: ClassVar[bool] = False


#: What a session may do (``deps.require_account``): everything; only set up the second factor right after signing in;
#: only confirm that the recovery codes are kept. The last two never become ``full`` themselves: a new session does.
STAGE_FULL = "full"
STAGE_SETUP = "setup"
STAGE_CODES = "codes"


def _session_uid() -> str:
    return secrets.token_hex(16)


class AuthSession(Base):
    """A browser session. Only the hash of the token is stored; the token itself lives in the cookie.

    ``uid`` names the session in the list of signed-in devices (never the token or its hash). ``remember``: the
    person ticked "stay signed in on this device", the session lasts until it was not used for ``session_days``;
    without, it ends with the browser and after twelve hours at the latest."""

    __tablename__ = "auth_sessions"
    __table_args__ = (Index("uq_auth_sessions_uid", "uid", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    uid: Mapped[str] = mapped_column(String(32), default=_session_uid, server_default=text("''"))
    remember: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    stage: Mapped[str] = mapped_column(String(8), default=STAGE_FULL, server_default=text("'full'"))
    #: The session came in through a provider whose entry checks the second factor itself: "require a second factor"
    #: does not send it to set one up; the same account signing in with its password still is.
    second_factor_by_provider: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))


class OidcProvider(Base):
    """A sign-in provider (OpenID Connect), one row each, the columns as the shared sign-in module names them.
    Read and written only through ``services/oidc_store.py`` for the shared module ``vendor/nexoidc``.

    Not part of a numbered schema step: an addition (``db.ADDITIONS``) that 1.0.1 opens without knowing it, so that
    the way back to 1.0.1 stays open for one version."""

    __tablename__ = "oidc_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Part of the callback address ``/api/v1/oidc/<slug>/callback``; fixed once made.
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    #: The name on the sign-in button.
    label: Mapped[str] = mapped_column(String(64))
    #: Stored without blanks and without a slash at the end.
    issuer: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(255))
    #: Encrypted with the server secret under the context ``oidc-provider:<id>``; empty for a public client.
    client_secret_enc: Mapped[str] = mapped_column(Text, default="")
    scopes: Mapped[str] = mapped_column(String(500), default="openid profile email")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: "New people get an account": off from the start.
    auto_create: Mapped[bool] = mapped_column(Boolean, default=False)
    #: "The provider checks the second factor itself": on from the start.
    trusts_second_factor: Mapped[bool] = mapped_column(Boolean, default=True)
    #: "" by hand, "authentik" made by the authentik button.
    managed: Mapped[str] = mapped_column(String(20), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    #: A callback path the provider knew before (only a migration sets it); "" is the standard one. nexbooks has no
    #: older version, so it stays empty.
    redirect_path: Mapped[str] = mapped_column(String(300), default="")
    #: The redirect address the authentik button last registered at authentik for this entry, "" for none.
    registered_redirect: Mapped[str] = mapped_column(String(500), default="", server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class OidcLink(Base):
    """One identity at one provider, bound to one account. Found only by ``(provider_id, subject)``, never by an
    address: ``email`` is for display."""

    __tablename__ = "oidc_links"
    __table_args__ = (
        UniqueConstraint("provider_id", "subject"),
        UniqueConstraint("provider_id", "account_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("oidc_providers.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    account_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: The ``iss`` of the token that made the link (with Entra ``common`` the real tenant).
    issuer: Mapped[str] = mapped_column(String(500))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class Passkey(Base):
    """A passkey of an account (WebAuthn, ``services/passkeys.py``): signs in without password and code, and counts
    as a second factor. Only the public key is kept; ``sign_count`` is the authenticator's counter as last seen."""

    __tablename__ = "passkeys"
    __table_args__ = (
        Index("uq_passkeys_uid", "uid", unique=True),
        Index("uq_passkeys_credential", "credential_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[bytes] = mapped_column(LargeBinary)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class Invite(Base):
    """A link that brings somebody in with a new account. Only the hash of the token is stored; used once, then gone."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)


class PasswordReset(Base):
    """A link with which a person sets a new password (``services/resets.py``). Only the hash of the token is stored;
    used once, then gone. At most one per account: a new one replaces the one before."""

    __tablename__ = "password_resets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    #: The operator who sent it; empty when the person asked for it themselves ("Forgot your password?").
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)


class EmailConfirmation(Base):
    """A new mail address waiting for the link sent to it (``services/emails.py``). Only the hash of the token is
    stored; used once, then gone. At most one per account: a new one replaces the one before."""

    __tablename__ = "email_confirmations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    address: Mapped[str] = mapped_column(String(255))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)


class ApiKey(Base):
    """A key an account made for programs (``/api/v1``). Only the SHA-256 is stored.

    ``scope`` is what the key may do: ``read``."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    scope: Mapped[str] = mapped_column(String(8))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    blocked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


#: What a connection for an e-reader is for (``services/ereaders.py``): the catalogue for reading apps (OPDS), the
#: progress sync of KOReader, the sync of a Kobo.
EREADER_OPDS = "opds"
EREADER_KOSYNC = "kosync"
EREADER_KOBO = "kobo"
EREADER_KINDS = (EREADER_OPDS, EREADER_KOSYNC, EREADER_KOBO)


class EreaderLink(Base):
    """A connection an account made for one e-reader or reading app (``services/ereaders.py``). It acts as its account
    and sees no more than the account. Only the SHA-256 of its secret is stored, and the first characters to tell
    connections apart; the secret is shown once. ``blocked_at``: the operator blocked it for good."""

    __tablename__ = "ereader_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(100), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    blocked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Of a connection of the kind ``kobo``: the number of the last answer of the sync (``services/kobo.py``). Counted
    #: up by every answer, never back, so that a device that comes with the sync token of an older answer is known.
    sync_batch: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class KoboListed(Base):
    """An edition on the list of a person for the Kobo (``services/kobo.py``): the books the person chose to have on
    their Kobo, one list for all their Kobos. ``uuid`` is the name the Kobo knows the book by; it is made from the
    person and the edition, so the same book is the same name on every Kobo of the person and nobody else's."""

    __tablename__ = "kobo_listed"
    __table_args__ = (Index("uq_kobo_listed_uuid", "user_id", "uuid", unique=True),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36))
    added_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class KoboHeld(Base):
    """What the Kobo behind a connection holds of the list (``services/kobo.py``). ``stamp`` and ``state_stamp`` are
    what the device was told and acknowledged (the book as it was, the place as it was); ``next_stamp``,
    ``next_state_stamp`` and ``removing`` are what the answer ``batch`` told and the device has not acknowledged yet.
    ``edition_id`` is no
    foreign key: a book that is gone from the library is still told away from the device. ``archived``: the person
    removed the book on the device, so it is not sent there again."""

    __tablename__ = "kobo_held"
    __table_args__ = (Index("uq_kobo_held_uuid", "link_id", "uuid", unique=True),)

    link_id: Mapped[int] = mapped_column(ForeignKey("ereader_links.id", ondelete="CASCADE"), primary_key=True)
    edition_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36))
    stamp: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    state_stamp: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    next_stamp: Mapped[str | None] = mapped_column(String(40), nullable=True)
    next_state_stamp: Mapped[str | None] = mapped_column(String(40), nullable=True)
    removing: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    batch: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))


class Device(Base):
    """A device or app of an account (``services/devices.py``): it speaks for the account with a token in
    ``Authorization: Bearer``, next to the session cookie of a browser. Only the SHA-256 of the token is stored.
    Revoking sets ``revoked_at``; the row stays for the list. ``via_device_id`` is the device of the same account that
    connected this one from the app (the iPhone confirmed the code of a television); revoking that device
    revokes this one too, and a device made that way connects no other."""

    __tablename__ = "devices"
    __table_args__ = (Index("uq_devices_uid", "uid", unique=True), Index("ix_devices_via", "via_device_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(32), default=_session_uid)
    account_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    via_device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)


class AppPairing(Base):
    """A code a signed-in browser made so that the iPhone app can fetch a device token (``services/pairing.py``): its
    id and a secret travel in a QR code, a short code can be typed instead. Only the SHA-256 of the secret and of the
    code are stored. It lives ten minutes and can be fetched once; the token is made at that moment and not before, so
    a code nobody used leaves no device behind. ``state`` is ``open`` or ``used``; one that ran out is told by the
    clock."""

    __tablename__ = "app_pairings"
    __table_args__ = (Index("uq_app_pairings_code", "code_hash", unique=True),
                      Index("ix_app_pairings_account", "account_id", "state"))

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    secret_hash: Mapped[str] = mapped_column(String(64))
    code_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(8), default="open")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)


class AppRequest(Base):
    """A device that asked for a code of its own (``services/device_request.py``): the screen of a television shows the
    code, a signed-in person types it in the browser and confirms, and only then the device fetches its token with the
    secret only it knows. Only the SHA-256 of the secret and of the code are stored, and never a token: it is made at
    the moment the device fetches it. ``state`` is ``waiting``, ``confirmed`` (``account_id`` says whose) or ``used``;
    one that ran out is told by the clock. ``sender`` and ``proxy`` are salted hashes of where the request came from,
    kept only to count how many are open (the salt changes with every start). ``confirmed_by`` is the device that
    confirmed from the app (empty when a browser did): the device fetched later is connected through it."""

    __tablename__ = "app_requests"
    __table_args__ = (Index("uq_app_requests_code", "code_hash", unique=True),
                      Index("ix_app_requests_sender", "sender", "state"),
                      Index("ix_app_requests_account", "account_id"))

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    code_hash: Mapped[str] = mapped_column(String(64))
    device_name: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(10), default="waiting")
    sender: Mapped[str] = mapped_column(String(64))
    proxy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)


# --- Web Push ----------------------------------------------------------------------------------------


class PushDevice(Base):
    """A browser or an app on the home screen that gets notices (``services/push.py``). What the browser handed
    over (the address at its push service and the two keys) and the name of the device are sealed with the server
    secret: the address is a key in itself, whoever has it may send to the device. ``endpoint_key`` is a keyed
    hash of the address, so that the same browser signing up twice stays one device."""

    __tablename__ = "push_devices"
    __table_args__ = (
        UniqueConstraint("uid", name="uq_push_devices_uid"),
        UniqueConstraint("user_id", "endpoint_key", name="uq_push_devices_user_endpoint"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint_key: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: The last message the push service took for this device.
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class Job(Base):
    """Work for the background, kept in the database so that it survives a restart (``services/jobs.py``). At most one
    job of a kind is waiting for the same thing (``ref``)."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_state", "state", "id"),
        Index("uq_jobs_waiting", "kind", "ref", unique=True, sqlite_where=text("state = 'queued'")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    #: What the job is about (a number the kind of job gives), to find it and to keep two waiting for the same.
    ref: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(8), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    error: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))


# --- Libraries and the books in them ----------------------------------------------------------------


KIND_EBOOK = "ebook"
KIND_AUDIO = "audio"
KIND_BOTH = "both"
#: What a library may hold: only e-books, only audiobooks, or both.
LIBRARY_KINDS = (KIND_EBOOK, KIND_AUDIO, KIND_BOTH)
#: The rights in a library, each one including the one before it: ``read`` sees its books; ``acquire`` also asks for
#: books to be fetched;
#: ``manage`` also starts a scan, reads its list of problems and looks after what a book is. Making, changing and
#: removing a library, and who is in it, is the operator's alone.
READ = "read"
ACQUIRE = "acquire"
MANAGE = "manage"
LIBRARY_RIGHTS = (READ, ACQUIRE, MANAGE)

#: What an edition is: the text of a book, or a recording of it.
EDITION_EBOOK = "ebook"
EDITION_AUDIO = "audio"
UNABRIDGED = "unabridged"
ABRIDGED = "abridged"
DRAMATIZED = "dramatized"
VARIANTS = (UNABRIDGED, ABRIDGED, DRAMATIZED)

ROLE_BOOK = "book"
ROLE_SIDECAR = "sidecar"


class Library(Base):
    """A folder of books the operator made known (``services/libraries.py``). nexbooks only reads it. The folder lies
    at or below one of ``NEXBOOKS_LIBRARY_ROOTS``; ``path`` is the real path (links resolved) at the time it was
    made."""

    __tablename__ = "libraries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    path: Mapped[str] = mapped_column(String(1000), unique=True)
    kind: Mapped[str] = mapped_column(String(8), default=KIND_BOTH)
    #: Whether the folder is read again by itself (the interval is a setting of the instance); "Scan now" always works.
    scan_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    #: When the last run ended (or broke off), and when the next one is due early (files still being written).
    scanned_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: While a run is going: files done and files to do.
    progress_done: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    progress_total: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    #: What the last run found: ``seen``, ``added``, ``changed``, ``missing``, ``problems``, ``deferred``, ``seconds``
    #: and ``aborted`` (``offline``, ``empty`` or ``truncated``: the folder was gone, held no book, or was too big for
    #: one walk, so nothing was marked missing; ``error``: the run failed).
    report: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: The generation of reading the files of this library have all reached (``BookFile.rev``), and how many times the
    #: operator asked for "read everything again": a run reads every file whose generation is below what the code needs
    #: (``scanner.READ_REV``) plus that many, so a rule that changed is applied to the books that are there.
    rev: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    reread_rev: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class LibraryMember(Base):
    """Who may see a library, and with which right. A person sees the books of the libraries they are a member of and
    of no other, the operator included."""

    __tablename__ = "library_members"
    __table_args__ = (UniqueConstraint("library_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    right: Mapped[str] = mapped_column(String(8), default=READ)


class Person(Base):
    """An author, a narrator or a translator. ``key`` is the name folded (no case, no accents, first name first), so
    "Falkner, Rune" and "Rune Falkner" are one person; ``name`` is how it reads, ``sort_name`` is "Last, First"."""

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    sort_name: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200), unique=True)


class PersonAlias(Base):
    """Another name of a person who is one with the person it belongs to (a pen name): ``key`` is the name folded like
    ``Person.key``. A name that is an alias leads to its person wherever a name is turned into a person
    (``services/aliases.py``), so it is not made a person of its own. It exists only on proof (a source, the same ISBN
    or ASIN); a name without proof stays a person of its own."""

    __tablename__ = "person_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200), unique=True)
    #: What proved it: ``hardcover``, ``tags``, ``isbn``, ``asin``, ``spelling`` or ``hand``.
    source: Mapped[str] = mapped_column(String(16), default="", server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class AliasClaim(Base):
    """A piece of evidence about two names, kept as long as what it rests on is there (``services/aliases.py``).

    ``apart`` false: the evidence that the two names are one person, from an edition (``edition_id``): the tags of its
    files (``A schreibt als B``, ``source`` ``tags``), its ISBN or ASIN that settled a match (``isbn``, ``asin``), or
    Hardcover's other names of the author of the book it was matched to (``hardcover``). ``name_a`` is the name the
    edition's files give, ``name_b`` the other one. Evidence of an edition goes with the edition, and is made anew when
    the edition is read or matched again.

    ``apart`` false and no edition: evidence that belongs to no book. ``source`` ``spelling``: the two names are the
    same name written differently (``services/spelling.py``, made anew when the scan has settled). ``source``
    ``hand``: a person joined the two authors (``name_b`` is the name that stays); it stands until a person splits
    them.

    ``apart`` true: a person said the two names are two people (``source`` ``hand``, no edition). Nothing joins them
    again by itself."""

    __tablename__ = "alias_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_a: Mapped[str] = mapped_column(String(200))
    key_a: Mapped[str] = mapped_column(String(200))
    name_b: Mapped[str] = mapped_column(String(200))
    key_b: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(16), default="", server_default=text("''"))
    edition_id: Mapped[int | None] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), nullable=True,
                                                   index=True)
    apart: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class EditionMove(Base):
    """An edition that became part of another one when the scan put the parts of a recording together: a player that
    still reports a place for the old one is heard, the place moved by ``shift_ms`` (``services/progress.py``)."""

    __tablename__ = "edition_moves"

    old_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    new_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), index=True)
    shift_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class Series(Base):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200), unique=True)


class Work(Base):
    """A book, whatever forms it comes in: the editions (e-book, audiobook, other languages) hang on it.

    **What the columns here say is the summary of all the editions, for the server** (matching a new edition to its
    work, the operator's numbers). It is never what a person is shown: a person sees only the editions of the libraries
    they are a member of, and title, description, genre, series, pages, cover and the rest are worked out from those
    editions when they are read (``services/works.py``), by the same rule (``catalog.summarize``). Otherwise the
    description of a book in a library one may not see would be read off the book one may."""

    __tablename__ = "works"
    __table_args__ = (
        Index("ix_works_key", "key"),
        Index("ix_works_main_key", "main_key"),
        Index("ix_works_original_key", "original_key"),
        Index("ix_works_added_at", "added_at"),
        Index("ix_works_change_seq", "change_seq"),
        # A number is never given twice, not even after the newest work was deleted: an app that held the old work
        # would otherwise take a new book for a changed title (schema 20).
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    original_title: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    original_language: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: One of the eight genres of the shelf (``services/genres.py``), empty for none.
    genre: Mapped[str] = mapped_column(String(16), default="", server_default=text("''"))
    #: The main colour of the cover, ``#rrggbb``; without a cover one of the genre's palette.
    color: Mapped[str] = mapped_column(String(7), default="", server_default=text("''"))
    #: The cover as a file name in ``<data>/covers`` (the checksum is in the name, so a new cover is a new address).
    cover: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    series_id: Mapped[int | None] = mapped_column(ForeignKey("series.id", ondelete="SET NULL"), nullable=True,
                                                  index=True)
    #: The place in the series; 2.5 is allowed (a novella between two volumes).
    series_pos: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: The pages of the lead e-book (counted for a PDF, estimated for the rest), or what the recording's length comes
    #: to; the width of the spine.
    pages: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    #: How the spine looks, fixed for the work: a pattern 0 to 4, the height as 0..1, and the gilt lines.
    pattern: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    tall: Mapped[float] = mapped_column(Float, default=0.5, server_default=text("0.5"))
    gold: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    #: The title folded, whole and as far as its first subtitle: what other editions are matched by.
    key: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    main_key: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    #: The original title folded: a translation is found by it.
    original_key: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    added_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: Where the work stands in the order of changes the iPhone app follows (``services/app_changes.py``). **Never
    #: written by code**: the triggers of ``APP_CHANGE_STATEMENTS`` set it at every change of the work, of one of its
    #: editions, files, chapters or credits, of a name it shows, and of the members of a library it lies in, whichever
    #: way the change is written (the ORM, a plain statement, a migration).
    change_seq: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class Edition(Base):
    """One form of a work in one library: the e-book in a language (its files in several formats), or one recording
    (its files the parts). *The edition is the content, the format is the file.*"""

    __tablename__ = "editions"
    __table_args__ = (Index("ix_editions_isbn", "isbn"), {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), index=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    #: Of a recording: unabridged, abridged, dramatized. Empty for an e-book.
    variant: Mapped[str] = mapped_column(String(12), default="", server_default=text("''"))
    #: Nothing in the files or the names said which variant it is (unabridged was assumed): the matching checks it
    #: against the running time in the catalogue.
    variant_uncertain: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    language: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    title: Mapped[str] = mapped_column(String(300), default="")
    subtitle: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    isbn: Mapped[str] = mapped_column(String(20), default="", server_default=text("''"))
    asin: Mapped[str] = mapped_column(String(20), default="", server_default=text("''"))
    publisher: Mapped[str] = mapped_column(String(200), default="", server_default=text("''"))
    published: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    description: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    genre: Mapped[str] = mapped_column(String(16), default="", server_default=text("''"))
    series_name: Mapped[str] = mapped_column(String(200), default="", server_default=text("''"))
    series_id: Mapped[int | None] = mapped_column(ForeignKey("series.id", ondelete="SET NULL"), nullable=True,
                                                  index=True)
    series_pos: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_title: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    #: The title folded (no case, accents or noise): the order by title.
    title_key: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    #: Genre, author, series, place and title of this edition, folded and joined: its place on the shelf as one string.
    shelf_key: Mapped[str] = mapped_column(String(500), default="", server_default=text("''"))
    #: Of an e-book: pages (counted for a PDF, estimated from the amount of text for the rest). Of a recording: 0.
    pages: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    #: Of a recording: the running time in seconds, all parts together.
    duration: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    cover: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    color: Mapped[str] = mapped_column(String(7), default="", server_default=text("''"))
    #: Set while none of its files is on the disk any more; everything that hangs on the work stays.
    missing_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: The language the book was first written in (from a source). Per edition, like every field a source or a
    #: person fills in, so that what a person reads about a book comes from the editions they may see.
    original_language: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    #: What the sources (``by`` source) and a person (``by`` hand) said about this edition, ``{field: {"v": value, "by":
    #: "source" | "hand"}}``. The scan puts it over what the files say (``services/matching.overlay``): the rank is
    # hand,
    #: then the file, then a source. Empty: nothing was said.
    overlay: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: Who put the edition under its work: empty (the scan did), ``auto`` (a source said it is a translation of a book
    #: already there), ``wish`` (a download for a wish was filed there after the check said it is the book wished) or
    #: ``hand`` (a person joined or split it). Nothing automatic moves a ``wish`` or ``hand`` edition again.
    grouped_by: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    #: The genre is a guess: no voice said it, and it is what most of the other books of the same author in the same
    #: library are (``catalog.settle_guesses``). Worked out from the library's own books only, so that nobody is told
    #: anything about books of a library they are not in.
    genre_guess: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))


class BookFile(Base):
    """A file of a library: a book (``role`` book, belongs to an edition once the run has assigned it) or a file beside
    it that tells about it (``sidecar``: ``metadata.json``, ``metadata.opf``, ``cover.jpg``). The path is relative to
    the library and uses ``/``. A file that is gone is marked, not deleted (``missing_at``)."""

    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("library_id", "path"),
        Index("ix_files_unit", "library_id", "unit"),
        Index("ix_files_dirty", "library_id", "dirty"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    edition_id: Mapped[int | None] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), nullable=True,
                                                   index=True)
    role: Mapped[str] = mapped_column(String(8), default=ROLE_BOOK)
    path: Mapped[str] = mapped_column(String(1500))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, default=0)
    #: Of the whole file, made when it came or changed. Empty for a sidecar.
    sha256: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    #: epub, pdf, azw3, mobi, mp3, m4b, m4a, flac, ogg, opus; for a sidecar json, opf or cover.
    format: Mapped[str] = mapped_column(String(8), default="")
    #: The number of the part of a recording made of several files (1 for the rest).
    part: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    #: What belongs together: the files of one e-book (same folder, same name) or one recording (same folder, same
    #: album). Set by the run.
    unit: Mapped[str] = mapped_column(String(1500), default="", server_default=text("''"))
    #: What the run read out of the file (``services/readers``), kept so that a unit can be put together again
    #: without opening the files.
    probe: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: The file is new or changed (or a file beside it is): the run has to put its unit together again.
    dirty: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    missing_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: The generation of reading this file was read in (``Library.rev``): below what the code needs, it is read again.
    rev: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    #: The partial MD5 KOReader names an e-book by (``services/kosync.partial_md5``), for an EPUB: made when the file
    #: came or changed, and for files that were there before in small portions in the background. Empty until then.
    partial_md5: Mapped[str] = mapped_column(String(32), default="", server_default=text("''"), index=True)


class LibraryProblem(Base):
    """A file the run could not read, with the reason as a code. It also says "do not try again": a file with the
    same size and time is not opened a second time. It goes when the file does."""

    __tablename__ = "library_problems"
    __table_args__ = (UniqueConstraint("library_id", "path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(1500))
    reason: Mapped[str] = mapped_column(String(32))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, default=0)
    seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class Chapter(Base):
    """A chapter of an edition: of a recording with its start, of an e-book with the place in the book it leads to."""

    __tablename__ = "chapters"
    __table_args__ = (Index("ix_chapters_edition", "edition_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), default="")
    #: Of a recording: where the chapter starts, in milliseconds from the beginning of the whole.
    start_ms: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    #: Of an e-book: the address in the book (file and anchor) the entry of the table of contents points to.
    target: Mapped[str] = mapped_column(String(500), default="", server_default=text("''"))
    #: How deep in the table of contents the entry stands (0 at the top).
    depth: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


AUTHOR = "author"
NARRATOR = "narrator"
TRANSLATOR = "translator"
ROLES_OF_PEOPLE = (AUTHOR, NARRATOR, TRANSLATOR)


class Credit(Base):
    """Who made a work or an edition: the authors of a work, the narrators and translators of an edition."""

    __tablename__ = "credits"
    __table_args__ = (Index("ix_credits_work", "work_id"), Index("ix_credits_edition", "edition_id"),
                      Index("ix_credits_person", "person_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    work_id: Mapped[int | None] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=True)
    edition_id: Mapped[int | None] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), nullable=True)
    role: Mapped[str] = mapped_column(String(12))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


SOURCES = ("hardcover", "openlibrary", "audible", "dnb", "apple", "wikidata", "bnf", "libris", "kb", "nb")
#: What a match entry says: not looked at yet, sure (applied), maybe, open (a person is asked).
MATCH_STATES = ("pending", "sure", "maybe", "open")


class ExternalRef(Base):
    """What an edition is called in a source: the work or the edition there, so that two editions that are one work
    for a
    source can be joined, and a book is not looked up twice. ``kind`` is ``work`` or ``edition``; ``market`` is the
    market of a catalogue that has one (Audible), else empty."""

    __tablename__ = "external_refs"
    __table_args__ = (
        UniqueConstraint("edition_id", "source", "kind", "key", "market"),
        Index("ix_external_refs_key", "source", "kind", "key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[int | None] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=True, index=True)
    edition_id: Mapped[int | None] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), nullable=True,
                                                   index=True)
    source: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(8))
    key: Mapped[str] = mapped_column(String(120))
    market: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))


class SourceCache(Base):
    """An answer of a source, cut down to the fields nexbooks uses, with the moment it stops being good
    (``services/sources/cache.py``)."""

    __tablename__ = "source_cache"
    __table_args__ = (UniqueConstraint("source", "key"), Index("ix_source_cache_expires", "expires_at"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    key: Mapped[str] = mapped_column(String(300))
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)


class SourceState(Base):
    """One row per source: how many requests it may still make now (the bucket of the throttle, kept in the database so
    that a restart does not forget it), how many today, and how it fared last (``services/sources/throttle.py``)."""

    __tablename__ = "source_state"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    tokens: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"))
    refilled_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    day: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    day_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_ok_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    last_error_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Failures in a row, and until when the source rests after them (growing).
    fail_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    blocked_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: ``token_invalid``: the source said its token is no good; it is not asked again until the operator connects anew.
    state: Mapped[str] = mapped_column(String(16), default="", server_default=text("''"))


class MatchEntry(Base):
    """What the matching made of an edition (``services/matching.py``). One row per edition. ``proposals`` are what the
    sources offered (kept so that the list for a person shows them without asking again); ``decision`` is what a person
    decided, which no later run changes."""

    __tablename__ = "match_queue"
    __table_args__ = (UniqueConstraint("edition_id"), Index("ix_match_queue_library", "library_id", "state"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"))
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(8), default="pending")
    #: Why it is not sure, as a code; a list of the same for the proposals is inside them.
    reason: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    proposals: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: What the edition was when it was last looked at (title, authors, language ...): another one means a new look.
    fingerprint: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    #: ``""``, ``accepted``, ``kept``, ``unlinked`` or ``corrected``: after a person said so, the matching leaves it be.
    decision: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class SearchEntry(Base):
    """What the search index holds for an edition: a digest of the text that went in, so that an edition whose text did
    not change costs no new entry. The entries themselves are in ``book_fts`` (rowid = edition id), made by
    ``services/search_index.py``."""

    __tablename__ = "search_entries"

    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), primary_key=True)
    digest: Mapped[str] = mapped_column(String(40), default="")


class Progress(Base):
    """Where a person is in an edition: one row per person and edition (``services/progress.py``). It belongs to the
    person alone: nothing here is read for anybody else, the operator included. ``moved_at`` is when the person was at
    this place, as the server works it out (the moment the report arrived minus how old the device said it was), and
    it decides which of two reports from two devices stays."""

    __tablename__ = "progress"
    __table_args__ = (Index("ix_progress_edition", "edition_id"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), primary_key=True)
    #: The place in the book: a CFI for an EPUB, MOBI or AZW3, ``page:12`` for a PDF, ``time:8040.5`` (seconds from the
    #: beginning of all the parts) for a recording.
    locator: Mapped[str] = mapped_column(String(1000), default="")
    #: How far in the whole book, 0 to 1.
    fraction: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"))
    #: What ``chapter`` counts: ``chapter`` or ``page``; ``time`` for a recording (``chapter`` is still its chapter).
    unit: Mapped[str] = mapped_column(String(8), default="chapter", server_default=text("'chapter'"))
    chapter: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    chapter_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    chapter_title: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    #: The device that reported it: a key it made up for itself, and a name for people ("Firefox on Linux").
    device_key: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    device: Mapped[str] = mapped_column(String(80), default="", server_default=text("''"))
    moved_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    received_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: The pass the person is on has been entered in the history: by the automatic rule at the end of the
    #: book, or by hand. It is cleared when the place falls back
    #: below half of the book (a new pass begins), and it stays
    #: while the person only wanders about the end, so that one pass makes one entry.
    pass_done: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    #: When the pass the person is on began: the first report of it, or the report that fell back below
    #: half of
    #: the book. A book finished within a few days of this is one the person liked. Empty for a place from before.
    pass_started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: The place as KOReader worded it (an XPointer for a book it reflows) when the report came from KOReader's sync and
    #: nothing has moved the place since; else empty. KOReader goes to exactly this when it asks for the place back, and
    #: the browser, which cannot read it, goes by ``fraction`` (``services/kosync.py``).
    koreader: Mapped[str] = mapped_column(String(1000), default="", server_default=text("''"))
    #: The same for a Kobo: the place as the Kobo worded it (a small JSON text with its location and the share of the
    #: file it is in), kept as long as nothing else moves the place; else empty (``services/kobo.py``).
    kobo: Mapped[str] = mapped_column(String(1000), default="", server_default=text("''"))


class Bookmark(Base):
    """A mark a person set in an edition. Theirs alone, like the progress."""

    __tablename__ = "bookmarks"
    __table_args__ = (UniqueConstraint("user_id", "edition_id", "locator"),
                      Index("ix_bookmarks_person", "user_id", "edition_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"))
    locator: Mapped[str] = mapped_column(String(1000))
    fraction: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"))
    chapter_title: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: A few words of the person's own about the place (a mark in a recording says what was heard there).
    note: Mapped[str] = mapped_column(String(500), default="", server_default=text("''"))


# --- Discover: the lists of the sources, books the person does not own, the wish list ---------------------


class ForeignBook(Base):
    """A book nexbooks does not hold, as a source knows it: the stuff of the lists, the gaps in a series, the catalogue
    search and the wish list. One row per book of a source (``ref`` = ``<source>:<key>``). **It is public data**, the
    same for everybody; what is the person's own (whether the book stands on their shelf, whether they put it on the
    wish list) is worked out for the caller each time and never stored here. The page of a book is reached by
    ``token``, a random string nobody can count up to: so a book that only entered the table because of the gaps in
    somebody's series cannot be found by anybody else going through the numbers."""

    __tablename__ = "foreign_books"
    __table_args__ = (Index("ix_foreign_books_title", "title_key"), Index("ix_foreign_books_seen", "seen_at"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(24), unique=True)
    ref: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    #: The title folded (``readers.names.title_keys``): other forms of the same book are found by it.
    title_key: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    authors: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: ``YYYY-MM-DD`` (or ``YYYY``) when known: a date after today is a book that is yet to come.
    released: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    language: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    genre: Mapped[str] = mapped_column(String(16), default="", server_default=text("''"))
    series: Mapped[str] = mapped_column(String(200), default="", server_default=text("''"))
    series_pos: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: The address of the picture at the source (never given to a browser: the server fetches it, see ``cover``) and the
    #: stored picture (a name in ``<data>/foreign-covers``, its main colour).
    cover_url: Mapped[str] = mapped_column(String(500), default="", server_default=text("''"))
    cover: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    color: Mapped[str] = mapped_column(String(7), default="", server_default=text("''"))
    #: What else the sources said: description, ISBNs, ASIN, the forms the book is known in (``forms``), the markets.
    data: Mapped[Any] = mapped_column(JSON, nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ListEntry(Base):
    """One place in a list a source published (``lists_cache``): the list of a source in a market, the rank, the book.
    A list is replaced as a whole, in one transaction, when it was fetched again."""

    __tablename__ = "lists_cache"
    __table_args__ = (UniqueConstraint("source", "market", "list", "rank"),
                      Index("ix_lists_cache_list", "list", "market"), Index("ix_lists_cache_foreign", "foreign_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: ``apple``, ``audible`` or ``hardcover``; the market is empty for the trends of Hardcover (they have none).
    source: Mapped[str] = mapped_column(String(16))
    market: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    #: ``best``, ``new``, ``soon`` or ``trending``.
    list: Mapped[str] = mapped_column(String(10))
    rank: Mapped[int] = mapped_column(Integer)
    ref: Mapped[str] = mapped_column(String(80))
    foreign_id: Mapped[int] = mapped_column(ForeignKey("foreign_books.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    author: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    cover_url: Mapped[str] = mapped_column(String(500), default="", server_default=text("''"))
    released: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    #: ``ebook`` or ``audio``: what the entry is a list of.
    kind: Mapped[str] = mapped_column(String(8), default="ebook", server_default=text("'ebook'"))
    language: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    genre: Mapped[str] = mapped_column(String(16), default="", server_default=text("''"))
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ListFetch(Base):
    """When a list was last fetched and how it went, per source, market and unit (``best``, ``release``, ``trending``),
    and when a person last looked at it: a list nobody has looked at for a while is not fetched again."""

    __tablename__ = "list_fetches"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    market: Mapped[str] = mapped_column(String(8), primary_key=True)
    unit: Mapped[str] = mapped_column(String(10), primary_key=True)
    fetched_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: ``ok``, ``empty`` (answered, nothing in it) or ``failed``.
    state: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    wanted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class Wish(Base):
    """A book a person put on their wish list. The person's own: nobody else reads it, the operator included."""

    __tablename__ = "wishlist"
    __table_args__ = (UniqueConstraint("user_id", "foreign_id"), Index("ix_wishlist_person", "user_id", "added_at"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    foreign_id: Mapped[int] = mapped_column(ForeignKey("foreign_books.id", ondelete="CASCADE"))
    #: The book as it was when it was put on the list (the source may change its mind).
    title: Mapped[str] = mapped_column(String(300))
    author: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    cover: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    added_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


# --- The reading stack, the authors' photos, tidying -----------------------------------------------------


HISTORY_READ = "read"
HISTORY_LISTENED = "listened"
HISTORY_KINDS = (HISTORY_READ, HISTORY_LISTENED)


class HistoryEntry(Base):
    """A book a person finished (``services/history.py``): the e-book read or the recording heard to its end, once for
    each pass. **The person's own**: no query reads it for anybody else, the operator included. It hangs on the edition
    (and through it on the work), so it goes when the edition goes for good; an edition only marked missing keeps it.
    ``finished_on`` is a day (``YYYY-MM-DD`` in the person's time zone), so that the same edition finished twice on one
    day is one entry: a double click and two devices cannot make a second one."""

    __tablename__ = "history"
    __table_args__ = (UniqueConstraint("user_id", "edition_id", "finished_on"),
                      Index("ix_history_person", "user_id", "finished_on"), Index("ix_history_edition", "edition_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    edition_id: Mapped[int] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"))
    #: The work of the edition, read from the edition every time and not kept here: an edition the scan puts to
    #: another work (a recording read anew, two names that are one person, a book joined or split by hand) takes its
    #: entries with it, and a work that goes after its editions went elsewhere takes none of them along.
    work_id: Mapped[int] = column_property(
        select(Edition.work_id).where(Edition.id == edition_id).correlate_except(Edition).scalar_subquery())
    #: ``read`` (an e-book) or ``listened`` (a recording).
    kind: Mapped[str] = mapped_column(String(8))
    finished_on: Mapped[str] = mapped_column(String(10))
    #: ``auto`` (the rule at the end of the book) or ``hand`` (the person said so).
    source: Mapped[str] = mapped_column(String(4), default="hand", server_default=text("'hand'"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: How many days the pass took from its first report to its end, when the rule at the end of the book
    #: entered it and the pass began in nexbooks; empty for an entry made by hand and for one from before.
    days: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PersonInfo(Base):
    """What nexbooks learned about an author from the internet (``services/people_info.py``): the photo, who made it
    and under which licence, a short biography per language, and the Wikidata item the person was recognised as.
    **Public data**, the same for everybody; whether a person
    may see an author at all is decided by the books (a page of
    an author none of whose books one sees is a page that does not exist). ``wikidata_state`` is ``sure`` (the item was
    found and a book of ours is among its works), ``none`` (looked, found nothing sure: no photo rather than a wrong
    one) or empty (not looked at yet)."""

    __tablename__ = "person_info"

    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), primary_key=True)
    wikidata_id: Mapped[str] = mapped_column(String(16), default="", server_default=text("''"))
    wikidata_state: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    #: The photo as a file name in ``<data>/people`` and where it
    #: came from (``wikidata``, ``openlibrary``, ``hardcover``).
    photo: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    photo_source: Mapped[str] = mapped_column(String(16), default="", server_default=text("''"))
    #: Who made the photo, under which licence (a short name and its address) and the page of the photo.
    photo_author: Mapped[str] = mapped_column(String(200), default="", server_default=text("''"))
    photo_license: Mapped[str] = mapped_column(String(80), default="", server_default=text("''"))
    photo_license_url: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    photo_page: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    #: ``{"de": {"text": ..., "source": "wikidata", "url": ...}}``: a short biography in each language it was found in.
    bios: Mapped[Any] = mapped_column(JSON, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: The key of the name the photo and the text were found for (``Person.key``). Two names that turned out to be one
    #: person share them; when they are two people again, what was found for the other name goes (``services/aliases``).
    name_key: Mapped[str] = mapped_column(String(200), default="", server_default=text("''"))


class PersonPhoto(Base):
    """A photo that lies in the author's folder of a library (``author.jpg``, ``folder.jpg``): per library, so that a
    person who is not in that library is never shown it (``services/portraits.py``)."""

    __tablename__ = "person_photos"

    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), primary_key=True)
    photo: Mapped[str] = mapped_column(String(64))
    #: The file it was read from, and the size and time of that file (a file that did not change is not read again).
    path: Mapped[str] = mapped_column(String(1500))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, default=0)


# --- The catalogue of an author's works ------------------------------------------------------------------


class CatalogPerson(Base):
    """An author whose works nexbooks knows from the sources (``services/oeuvre``): one for each name (``key`` is the
    name folded like ``Person.key``), whether anybody has a book of theirs or not. **Public data**, the same for
    everybody: what a person has of these works is worked out from the libraries they may see when they ask, never
    stored here. The page of an author nobody has a book of is reached by ``token``, a random string nobody can count
    up to; ``person_id`` is the author of the libraries with the same name, when there is one."""

    __tablename__ = "catalog_people"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(24), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200), unique=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"), nullable=True,
                                                  index=True)
    #: The other names the author writes under, as the sources know them (pen names; Hardcover's other names).
    aliases: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: The language most of the works were written in, and the languages the catalogue was built for.
    orig_language: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    languages: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: What each source gave: ``{"dnb": {"entries": 41, "works": 38, "languages": ["de"]}}``, and the sources that were
    #: away (``{"away": ["audible"]}``).
    sources: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: The years of the life, as a source wrote them (``1951``, ``14 Mar 1951``).
    born: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    died: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    #: ``""`` (never built), ``ready``, ``failed`` (no source answered).
    state: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    built_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: When somebody last looked at the page: a catalogue nobody looks at is not built again.
    wanted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: What Wikidata says of an author nobody of the libraries is (``services/oeuvre/info.py``): ``state`` (``sure``,
    #: ``none``), ``qid``, ``photo`` (a file of ``<data>/people``) with ``credit`` (author, licence, page), ``bios`` and
    #: ``checked_at``. Only when two works of the catalogue are works of the item.
    info: Mapped[Any] = mapped_column(JSON, nullable=True)


class CatalogWork(Base):
    """One work of an author as the sources know it, its editions in every language put together: the titles and
    years in each language, what kind of book it is (``kind``; ``unsure`` with the reason in ``why``), the series,
    the pen name it came out under, the sources that know it. ``foreign_id`` is the book of the sources a wish, the wish
    list and the page of a book go through. Built again as a whole for the author (``key`` stays the same for the same
    work, so that what a person moved stays moved)."""

    __tablename__ = "catalog_works"
    __table_args__ = (UniqueConstraint("catalog_person_id", "key"), Index("ix_catalog_works_foreign", "foreign_id"),
                      {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catalog_person_id: Mapped[int] = mapped_column(ForeignKey("catalog_people.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))
    foreign_id: Mapped[int | None] = mapped_column(ForeignKey("foreign_books.id", ondelete="SET NULL"),
                                                   nullable=True)
    #: ``roman``, ``erz`` (stories), ``bio``, ``sach``, ``lyrik``, ``kinder``, ``box`` (a collection of books) or
    #: ``unsure``; ``why`` says why it is unsure (``comic``, ``anthology``, ``forms``, ``short``, ``sample``, ``about``:
    #: a book about the author or a book of theirs).
    kind: Mapped[str] = mapped_column(String(8))
    why: Mapped[str] = mapped_column(String(16), default="", server_default=text("''"))
    pen: Mapped[str] = mapped_column(String(200), default="", server_default=text("''"))
    series: Mapped[str] = mapped_column(String(200), default="", server_default=text("''"))
    series_pos: Mapped[float | None] = mapped_column(Float, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    orig_language: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    #: ``{"de": {"title", "year", "released", "ebook", "audio", "sources"}}``: the work in each language.
    titles: Mapped[Any] = mapped_column(JSON, nullable=True)
    sources: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: The ISBNs, ASINs and names at the sources of the editions: what a book on a shelf is recognised by first.
    isbns: Mapped[Any] = mapped_column(JSON, nullable=True)
    refs: Mapped[Any] = mapped_column(JSON, nullable=True)
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class ShelfEntry(Base):
    """One place on a themed shelf of Browse: the shelf of a source in a market, a section of it (``new``,
    ``popular``, ``award``, ``classic``) and the rank. **Public data**, the same for everybody, like the lists. A
    source's
    part of a shelf is replaced as a whole, in one transaction, when it was fetched again."""

    __tablename__ = "shelf_entries"
    __table_args__ = (UniqueConstraint("source", "market", "shelf", "section", "rank"),
                      Index("ix_shelf_entries_shelf", "market", "shelf", "section"),
                      Index("ix_shelf_entries_foreign", "foreign_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: ``apple`` or ``openlibrary`` (and, when they are on, ``audible`` or ``hardcover``).
    source: Mapped[str] = mapped_column(String(16))
    market: Mapped[str] = mapped_column(String(8))
    shelf: Mapped[str] = mapped_column(String(16))
    section: Mapped[str] = mapped_column(String(8))
    rank: Mapped[int] = mapped_column(Integer)
    foreign_id: Mapped[int] = mapped_column(ForeignKey("foreign_books.id", ondelete="CASCADE"))
    #: The award the source names for the book (a section ``award``), as the source writes it.
    award: Mapped[str] = mapped_column(String(80), default="", server_default=text("''"))
    #: The topics of the shelf the book is about (``services/browse/shelves.topics_of``), worked out from all the
    #: subjects
    #: the source gave, of which the book row keeps ten.
    topics: Mapped[Any] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: How strongly the source says the book belongs to the shelf: the share of its subjects that speak for it, more
    #: for a book of Apple's chart of a genre of the shelf. A book stands on the one shelf that has the most.
    evidence: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"))
    #: The series the book is a volume of (``services/browse/shelves.series_key``); empty for a book of its own.
    series_key: Mapped[str] = mapped_column(String(160), default="", server_default=text("''"))


class ShelfFetch(Base):
    """When the part of a shelf a source gives was last fetched, per source, market and shelf, and when a person last
    looked at the market: a market nobody looks at is not fetched again."""

    __tablename__ = "shelf_fetches"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    market: Mapped[str] = mapped_column(String(8), primary_key=True)
    shelf: Mapped[str] = mapped_column(String(16), primary_key=True)
    fetched_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: ``ok``, ``empty`` (answered, nothing in it) or ``failed``.
    state: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    wanted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class CatalogMove(Base):
    """What a person said about the kind of a work (``Verschieben nach …``): it holds for everybody and for every build
    of the catalogue after it, until somebody takes it back."""

    __tablename__ = "catalog_moves"
    __table_args__ = (UniqueConstraint("catalog_person_id", "work_key"), {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catalog_person_id: Mapped[int] = mapped_column(ForeignKey("catalog_people.id", ondelete="CASCADE"))
    work_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(8))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


WATCH_KINDS = ("ebook", "audio", "both")


class AuthorWatch(Base):
    """A person watching an author: new books of the author are wished (or, without the right to acquire, put on the
    wish list) as they are announced and as they come out. **The person's own**: nobody else reads it, the operator
    included. One per person and author. ``kind`` says what is wished; ``language`` is empty for the first of the
    languages books are fetched in (settings, "Sprachen", in their order), or one of those languages the person chose;
    ``more`` adds the kinds besides novels and stories (non-fiction, children's books, collections). What a beat of the
    watching has seen is kept here: ``seen`` (the keys of the works the catalogue held at the last look, and with a
    ``~`` those announced for a year without a day), ``since`` (that day), ``evaluated_for`` (the build of the
    catalogue it was looked at for) and ``retry_at`` (not before: a day's limit, no indexer)."""

    __tablename__ = "author_watches"
    __table_args__ = (UniqueConstraint("user_id", "catalog_person_id"),
                      Index("ix_author_watches_author", "catalog_person_id"), {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    catalog_person_id: Mapped[int] = mapped_column(ForeignKey("catalog_people.id", ondelete="CASCADE"))
    #: ``ebook``, ``audio`` or ``both``.
    kind: Mapped[str] = mapped_column(String(5), default="both", server_default=text("'both'"))
    #: Empty (the first language books are fetched in), or a language code.
    language: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    more: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: When the first look was made (the books announced then were wished; nothing that was out already). Empty
    #: while the catalogue of the author is not built.
    primed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    seen: Mapped[Any] = mapped_column(JSON, nullable=True)
    since: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    evaluated_for: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: When the beat last asked for a new build of the catalogue for this watch: the people take turns by it.
    queued_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class WatchItem(Base):
    """What watching an author did with one work in one language and kind: it was wished (``wish``: a wish for
    fetching it, ``wish_id``), put on the wish list (``list``; ``why`` says why it is not wished, when the person may
    ask for books: ``language:en`` announced only in another language, or a doubt such as ``reissue``), or held
    (``held``: the wish was refused for a reason that does not pass by itself, the code in ``why``). Once there is a
    row, the beat never does it again for the person, so a wish the person took away stays away, and a book they have
    or wished is never wished twice. Kept when the watching is switched off (the wish keeps saying where it came from)
    and gone with the person or the author."""

    __tablename__ = "author_watch_items"
    __table_args__ = (UniqueConstraint("user_id", "catalog_person_id", "work_key", "language", "kind"),
                      Index("ix_author_watch_items_wish", "wish_id"), {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    catalog_person_id: Mapped[int] = mapped_column(ForeignKey("catalog_people.id", ondelete="CASCADE"))
    work_key: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(8))
    #: ``ebook`` or ``audio`` for a wish; empty for the wish list (one entry for the book, whatever the kinds).
    kind: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    #: ``wish``, ``list`` or ``held``; empty while the beat is on it (a row without a result is taken back when the
    #: beat could not finish).
    via: Mapped[str] = mapped_column(String(5), default="", server_default=text("''"))
    why: Mapped[str] = mapped_column(String(32), default="", server_default=text("''"))
    wish_id: Mapped[int | None] = mapped_column(ForeignKey("procure_wishes.id", ondelete="SET NULL"), nullable=True)
    foreign_id: Mapped[int | None] = mapped_column(ForeignKey("foreign_books.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


ORGANIZE_STATES = ("preview", "queued", "running", "done", "failed", "undoing", "undone")


class OrganizeRun(Base):
    """One tidying of a library (``services/organize``): the scheme,
    the moves the preview listed, how far the run is and
    how it ended. The moves of a run are its journal: undoing reads them backwards."""

    __tablename__ = "organize_runs"
    __table_args__ = (Index("ix_organize_runs_library", "library_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    scheme: Mapped[str] = mapped_column(String(300))
    state: Mapped[str] = mapped_column(String(8), default="preview")
    #: What the preview counted (books that move, stay, cannot move; files), the digest of the plan, the folders the run
    #: made and the folders it removed.
    summary: Mapped[Any] = mapped_column(JSON, nullable=True)
    done: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    total: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    #: A code when the run stopped before its end (``failed``); empty else.
    error: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class OrganizeMove(Base):
    """One file a run moves: from where it lies to where the scheme puts it, relative to the library's folder and with
    ``/``. ``state``: ``planned``, ``moved``, ``undone``, ``skipped`` (the file changed since the preview), ``problem``
    (the goal is taken or the way is not allowed; ``reason`` says which), ``stays`` (already where it belongs)."""

    __tablename__ = "organize_moves"
    __table_args__ = (Index("ix_organize_moves_run", "run_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("organize_runs.id", ondelete="CASCADE"))
    #: The file row (it follows the move); gone when the file row went.
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    #: The edition the file belongs to, for the preview's lines (a line names the book).
    edition_id: Mapped[int | None] = mapped_column(ForeignKey("editions.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(1500))
    target: Mapped[str] = mapped_column(String(1500))
    state: Mapped[str] = mapped_column(String(8), default="planned")
    reason: Mapped[str] = mapped_column(String(32), default="", server_default=text("''"))
    #: The size and time the file had when the preview was made: a run moves nothing that is not as it was.
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, default=0)


class Relocation(Base):
    """An edition that goes into another library (``services/relocate.py``): all its files, from the folder of one
    library into the folder of another, by the naming scheme of filing. **The edition stays the same** (its id, and with
    it the progress, bookmarks, history and corrections). ``step`` is how far it is: ``place`` (the files are being put
    at their goals, the source untouched), ``clear`` (the edition belongs to the target; the files at the source are
    being taken away) and ``done``. ``state`` is ``queued``, ``running``, ``done`` or ``failed`` (``error`` says why); a
    failed one can be tried again and goes on from its step."""

    __tablename__ = "relocations"
    __table_args__ = (
        Index("ix_relocations_edition", "edition_id", "id"), Index("ix_relocations_state", "state"),
        # One move of an edition at a time.
        Index("uq_relocations_active", "edition_id", unique=True, sqlite_where=text("state IN ('queued', 'running')")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    edition_id: Mapped[int | None] = mapped_column(ForeignKey("editions.id", ondelete="SET NULL"), nullable=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("libraries.id", ondelete="SET NULL"), nullable=True)
    target_id: Mapped[int | None] = mapped_column(ForeignKey("libraries.id", ondelete="SET NULL"), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    state: Mapped[str] = mapped_column(String(8), default="queued")
    step: Mapped[str] = mapped_column(String(8), default="place")
    error: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    #: The folders the move made in the target (taken away again when it is given up), relative, with ``/``.
    made: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class RelocationFile(Base):
    """One file of a ``Relocation``: where it lies in the source and where it goes in the target (relative, with ``/``),
    as it was when it was planned (size and time), and how far it is: ``planned``, ``placed`` (it lies at its goal as
    well), ``done`` (the source is gone). ``how`` is ``link`` (a second name on the same disk) or ``copy``."""

    __tablename__ = "relocation_files"
    __table_args__ = (Index("ix_relocation_files_move", "relocation_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    relocation_id: Mapped[int] = mapped_column(ForeignKey("relocations.id", ondelete="CASCADE"))
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(1500))
    target: Mapped[str] = mapped_column(String(1500))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, default=0)
    state: Mapped[str] = mapped_column(String(8), default="planned")
    how: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))


#: The word index, rowid = edition id. Contentless: it holds no second copy of anything (the text is in the editions and
#: their credits), and an entry is dropped by its rowid (``contentless_delete``, SQLite 3.43). Case and accents do not
#: count. **Every word is written with the library of its edition in front** (``l<library>lx<word>``): a search names
#: the words of the libraries of the caller, so the index never reads a word, or the list of books that have it, in
#: another library, and the time a search takes does not depend on what other people have (``services/search.py``).
FTS_TABLE = "book_fts"
SEARCH_STATEMENTS = (
    (
        "CREATE VIRTUAL TABLE IF NOT EXISTS book_fts USING fts5(title, original, people, series, description, ids, "
        "tokenize = 'unicode61 remove_diacritics 2', content = '', contentless_delete = 1)"
    ),
    # An edition deleted for good (a library that went, a file that was never there) leaves no entry behind.
    (
        "CREATE TRIGGER IF NOT EXISTS editions_search_gone AFTER DELETE ON editions BEGIN "
        "DELETE FROM book_fts WHERE rowid = old.id; END"
    ),
)


@event.listens_for(Base.metadata, "after_create")
def _search_tables(_target: Any, connection: Any, **_kw: Any) -> None:
    """``create_all`` makes the tables SQLAlchemy has no type for, too: a new database and a recorded schema hold the
    same as a migrated one."""
    for statement in SEARCH_STATEMENTS:
        connection.exec_driver_sql(statement)


# --- Fetching books: indexers, Prowlarr, download programs ---------------------------------------------

INDEXER_KINDS = ("newznab", "torznab")
CLIENT_KINDS = ("sabnzbd", "nzbget", "qbittorrent", "transmission", "deluge")
#: The protocol each kind of program speaks: a release of an indexer goes to a program of the same protocol.
CLIENT_PROTOCOL = {"sabnzbd": "usenet", "nzbget": "usenet", "qbittorrent": "torrent", "transmission": "torrent",
                   "deluge": "torrent"}


class Indexer(Base):
    """A Newznab or Torznab endpoint the operator entered, or one that came from Prowlarr (``prowlarr_indexer_id``).

    ``url`` is the endpoint with its path (``https://indexer.example.com/api``). ``api_key_enc`` is the key sealed with
    the server's secret and never read back. ``categories`` are the category numbers that are asked (books in the 7000
    series, audiobooks 3030 and its neighbours), ``category_langs`` the language the operator gave each one (or
    ``mixed``), ``caps`` what the indexer said about itself (``supported_params`` of its book search among it)."""

    __tablename__ = "indexers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(8), default="newznab")
    url: Mapped[str] = mapped_column(String(1024))
    api_key_enc: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    #: 1 to 50: the lowest number is asked first.
    priority: Mapped[int] = mapped_column(Integer, default=25, server_default=text("25"))
    #: Requests a day the operator allows; 0 for no limit of their own.
    daily_limit: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    categories: Mapped[Any] = mapped_column(JSON, nullable=True)
    category_langs: Mapped[Any] = mapped_column(JSON, nullable=True)
    caps: Mapped[Any] = mapped_column(JSON, nullable=True)
    caps_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: After a request limit, an error or by the operator's switch: no request goes out before this moment, and why.
    paused_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    pause_cause: Mapped[str] = mapped_column(String(32), default="", server_default=text("''"))
    #: The code of the last failure (``indexer_key_rejected`` ...); empty after a request that went through.
    last_error: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    #: What ``newznab:apilimits`` of the last answer said (requests and grabs used and allowed in 24 hours).
    api_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grab_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grab_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limits_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Requests made on ``used_day`` (UTC, ``YYYY-MM-DD``).
    used_day: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    used_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    #: Releases fetched from it, all in all.
    grabs: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    #: The indexer's own number in the Prowlarr of the instance (the ``{id}`` of ``/{id}/api``); empty when entered
    #: by hand.
    prowlarr_indexer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: Releases fetched from it on ``grab_day`` (the day of the instance's time zone): counted apart from the requests.
    grab_day: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    grab_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    #: When the newest entries of the indexer were read the last time (RSS), and the code of its failure.
    rss_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    rss_error: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    #: No release is fetched from it before this moment: its own count of grabs is used up (``newznab:apilimits``).
    grab_paused_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class ProwlarrConnection(Base):
    """The Prowlarr of the instance, at most one. ``url`` is its address with a URL base if it has one; the key is
    sealed and never read back. Each of its indexers with book categories becomes an ``Indexer`` marked with the
    number Prowlarr gave it."""

    __tablename__ = "prowlarr_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(1024))
    api_key_enc: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    #: Whether an indexer that is new in Prowlarr is taken at the next sync (an indexer that is there is kept in step
    #: either way).
    auto_add: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    version: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    last_sync_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: The code of the last sync that failed; empty after one that went through.
    last_error: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    last_counts: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class DownloadClient(Base):
    """A download program (SABnzbd, NZBGet, qBittorrent, Transmission, Deluge). ``secret_enc`` is the API key or the
    password, sealed and never read back; ``category`` is nexbooks's own in the program (``nexbooks``), so that it sees
    only its own jobs."""

    __tablename__ = "download_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(1024))
    username: Mapped[str] = mapped_column(String(256), default="", server_default=text("''"))
    secret_enc: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    category: Mapped[str] = mapped_column(String(64), default="nexbooks", server_default=text("'nexbooks'"))
    #: 1 to 50: of several enabled programs of one protocol, the lowest number is asked first.
    priority: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))
    #: What the last test found: the program's version, and the code of the failure (empty when it went through).
    version: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    last_error: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class PathMapping(Base):
    """A path a program names that nexbooks sees under another one. Only a suggestion the operator confirmed becomes a
    row (the suggestions come with tracking the downloads)."""

    __tablename__ = "path_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("download_clients.id", ondelete="CASCADE"), index=True)
    remote: Mapped[str] = mapped_column(String(1000))
    local: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


# --- Searching and judging releases --------------------------------------------------------------------

#: The states of a search: waiting for its turn, asking the indexers, done (with or without releases), or failed.
SEARCH_STATES = ("queued", "running", "done", "failed")


class ReleaseSearch(Base):
    """A search of the indexers for one book a person asked for (the manual search): the book is a
    work of a library (``work_id``) or a book of a source (``foreign_id``). The person's own: nobody else reads it.

    ``subject`` is the book as it was when the search began (titles with their languages, authors, series and volume,
    running time, the formats there are already), so that the judgement does not change under the person's eyes.
    ``progress`` holds one entry per indexer (``{"id", "name", "kind", "state", "found", "error"}``)."""

    __tablename__ = "release_searches"
    __table_args__ = (Index("ix_release_searches_person", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    work_id: Mapped[int | None] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=True)
    foreign_id: Mapped[int | None] = mapped_column(ForeignKey("foreign_books.id", ondelete="CASCADE"), nullable=True)
    #: The language looked for (a code).
    language: Mapped[str] = mapped_column(String(8))
    subject: Mapped[Any] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(8), default="queued")
    progress: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: The code of a failure of the whole search (``no_indexers``); empty otherwise.
    error: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: ``search`` (a search of the indexers) or ``rss`` (the releases of the newest entries that fit a wish; made by the
    #: automatic work, never shown again as a recent search).
    origin: Mapped[str] = mapped_column(String(8), default="search", server_default=text("'search'"))


class SearchRelease(Base):
    """One release a search found, with the judgement: kind, format, language, the reasons against it as codes, the
    points and the rank among those that pass. ``link_enc`` is the link to the NZB or torrent, sealed (it carries the
    key of the indexer or a passkey) and only opened to load it."""

    __tablename__ = "search_releases"
    __table_args__ = (Index("ix_search_releases_search", "search_id", "kind", "rank"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_id: Mapped[int] = mapped_column(ForeignKey("release_searches.id", ondelete="CASCADE"))
    indexer_id: Mapped[int | None] = mapped_column(ForeignKey("indexers.id", ondelete="SET NULL"), nullable=True)
    indexer_name: Mapped[str] = mapped_column(String(100), default="", server_default=text("''"))
    #: ``usenet`` or ``torrent``.
    protocol: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(400))
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    seeders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grabs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    categories: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: A hash of the guid of the item: the same release found twice is kept once.
    guid: Mapped[str] = mapped_column(String(24), default="", server_default=text("''"))
    info_hash: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    link_enc: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    #: The seed goal the tracker asks for.
    min_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_seed_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: ``ebook`` or ``audio``.
    kind: Mapped[str] = mapped_column(String(8))
    #: The format in the words of the ranking (``epub``, ``mp3_192``), empty when the name does not tell.
    format: Mapped[str] = mapped_column(String(12), default="", server_default=text("''"))
    format_how: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    tier: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    language: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    #: ``mark``, ``words`` (sure), ``title``, ``category``, ``presumed`` (a guess).
    language_how: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    variant: Mapped[str] = mapped_column(String(12), default="", server_default=text("''"))
    bitrate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: ``[{"code": ..., "arg": ...}]``; empty when it passes.
    reasons: Mapped[Any] = mapped_column(JSON, nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    #: The place among the releases of its kind that pass, 1 first; empty for one that does not pass.
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BlocklistEntry(Base):
    """A release that is never taken for a book again: blocked by a person, or by nexbooks after a check.
    It counts for its book (a work or a book of a source) and is known by its name with the protocol, or by the info
    hash of a torrent."""

    __tablename__ = "blocklist"
    __table_args__ = (
        Index("ix_blocklist_name", "title_key"), Index("ix_blocklist_work", "work_id"),
        Index("ix_blocklist_foreign", "foreign_id"), Index("ix_blocklist_book", "book_key"),
        Index("ix_blocklist_library", "library_id"),
        # One entry per release and book, whatever races: for a work, for a book of the sources, and for a book that
        # lost both (its work was deleted), which is known by ``book_key``.
        Index("uq_blocklist_work", "work_id", "title_key", "protocol", unique=True,
              sqlite_where=text("work_id IS NOT NULL")),
        Index("uq_blocklist_foreign", "foreign_id", "title_key", "protocol", unique=True,
              sqlite_where=text("foreign_id IS NOT NULL")),
        Index("uq_blocklist_orphan", "book_key", "title_key", "protocol", unique=True,
              sqlite_where=text("work_id IS NULL AND foreign_id IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The work or the book of the sources the entry is for. A work that is deleted (the scanner drops one when its
    #: files are gone, and builds it again when they come back) does not take the entry with it: both ids go empty and
    #: the entry waits, known by ``book_key``, until a book with that key is looked for again.
    work_id: Mapped[int | None] = mapped_column(ForeignKey("works.id", ondelete="SET NULL"), nullable=True)
    foreign_id: Mapped[int | None] = mapped_column(ForeignKey("foreign_books.id", ondelete="SET NULL"), nullable=True)
    #: The author and the core of the title, folded: what the book is known by when its work is gone.
    book_key: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    #: ``ebook`` or ``audio``.
    kind: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(400))
    #: The name folded: a release is the same with dots or spaces, in any case.
    title_key: Mapped[str] = mapped_column(String(400))
    protocol: Mapped[str] = mapped_column(String(8))
    info_hash: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    indexer_name: Mapped[str] = mapped_column(String(100), default="", server_default=text("''"))
    #: Why, as a code of the judgement (``other_language``, ``bundle`` ...) or ``by_hand``.
    reason: Mapped[str] = mapped_column(String(32))
    #: Who blocked it; empty when nexbooks did after a check.
    by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: The library the release was blocked for (where the download was to go): who may ask for books in it sees the
    #: entry and who blocked it, and takes it off again. Empty for an entry of an older time or of a library that is
    #: gone.
    library_id: Mapped[int | None] = mapped_column(ForeignKey("libraries.id", ondelete="SET NULL"), nullable=True)


#: How a download goes. ``sending`` (on its way to the program: the file is fetched, the program asked) and ``unsure``
#: (the request went out but the answer did not come: the program may have it, and tracking looks for it by its name)
#: lead to ``queued``, ``downloading`` and ``paused`` (the program has it; ``CLIENT_STATES``), then ``completed`` (done
#: in the program, waiting to be filed), or ``problem`` (needs a person: the code
#: tells what), and end as ``failed`` (the program could not, the job is taken away), ``removed`` (a person took it
#: away) or ``imported`` (filed). The first group (``ACTIVE_DOWNLOADS``) holds the place of the book and the kind: a
#: book of one kind loads one release at a time.
#:
#: Two more states: ``rejected`` (the check after the download turned it down: the release is blocked, the files never
#: went into a library, the wish searches on) and ``uploading`` (a person sends the files of a book from the browser;
#: an upload is a download with the protocol ``upload`` and takes the same way into the library once it is
#: ``completed``). Neither holds the place of the book.
DOWNLOAD_STATES = ("sending", "unsure", "queued", "downloading", "paused", "completed", "problem", "failed", "removed",
                   "imported", "rejected", "uploading")
CLIENT_STATES = ("queued", "downloading", "paused")
ACTIVE_DOWNLOADS = ("sending", "unsure", "queued", "downloading", "paused", "completed", "problem")
#: The states of a wish: ``searching`` (asking the indexers, or about to), ``not_found`` (nothing passed; the next try
#: is ``next_try_at``), ``waiting`` (the book is not out yet), ``capped`` (an indexer's limit for the day), ``upgrade``
#: (the book is there, a better format is looked for), ``loading`` (a release is on its way), ``done``.
WISH_STATES = ("searching", "idle", "not_found", "waiting", "capped", "upgrade", "loading", "done")
OPEN_WISHES = ("searching", "idle", "not_found", "waiting", "capped", "upgrade", "loading")


class ProcureWish(Base):
    """A wish for one book in one kind and language: what nexbooks searches for, loads and files for the
    people who may ask for books in the library it goes into. The book is a work of a library or a book of the sources
    (it gets its work when the book is filed). There is one open wish per book, kind, language and library, whoever
    asked (``uq_procure_wishes_*``): two libraries may each ask for the same book, and neither sees the other's wish."""

    __tablename__ = "procure_wishes"
    __table_args__ = (
        Index("ix_procure_wishes_state", "state"), Index("ix_procure_wishes_library", "library_id"),
        Index("uq_procure_wishes_work", "work_id", "kind", "language", text("coalesce(library_id, 0)"), unique=True,
              sqlite_where=text("work_id IS NOT NULL AND state != 'done'")),
        Index("uq_procure_wishes_foreign", "foreign_id", "kind", "language", text("coalesce(library_id, 0)"),
              unique=True,
              sqlite_where=text("foreign_id IS NOT NULL AND state != 'done'")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Who asked: the searches run as this person, with their right.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    work_id: Mapped[int | None] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"), nullable=True)
    foreign_id: Mapped[int | None] = mapped_column(ForeignKey("foreign_books.id", ondelete="CASCADE"), nullable=True)
    #: ``ebook`` or ``audio``.
    kind: Mapped[str] = mapped_column(String(8))
    #: A language code, or empty for every language the operator accepts.
    language: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    #: The library the book is to go into (the person's choice, or the operator's for the kind).
    library_id: Mapped[int | None] = mapped_column(ForeignKey("libraries.id", ondelete="SET NULL"), nullable=True)
    state: Mapped[str] = mapped_column(String(12), default="searching")
    #: The search that is running or that was the last one (no foreign key: the table of searches is tidied).
    search_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    searched_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    next_try_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: What the last search found: ``{"total": 17, "reasons": [{"code", "count"}]}``; for ``capped``
    #: ``{"indexer", "limit", "until"}``; for ``upgrade`` ``{"have", "goal"}``.
    detail: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: ``YYYY-MM-DD`` when the book is not out yet.
    release_date: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: A release that was chosen but could not be handed over because the program did not answer:
    #: ``{"search": id, "release": id, "due": iso moment, "tries": n}``. The hand-over is tried again, never a search.
    #: ``none_as_null``: no hand-over is an SQL NULL, not the JSON text ``null`` (the planner and the newest entries
    #: ask for "no hand-over" with ``IS NULL``).
    handover: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    #: What was wished, as it read the moment the wish was made: ``{"title", "authors", "kind", "language"}``. The work
    #: may be renamed later, or the book of the sources go; the history shows what was wished from this.
    wished: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)


class RssSeen(Base):
    """An entry of an indexer's newest releases that nexbooks has looked at: the same entry is judged
    once. Known by a hash of its guid (or of its name), kept for two weeks."""

    __tablename__ = "rss_seen"
    __table_args__ = (Index("ix_rss_seen_at", "seen_at"),)

    indexer_id: Mapped[int] = mapped_column(ForeignKey("indexers.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class AutoRun(Base):
    """What one round of the automatic work did, for the operator: the kind of round (``rss``,
    ``search``, ``handover``), when, and the numbers: requests per indexer, entries read, wishes searched, releases
    loaded. Never a key, a title or a name. The newest ``KEEP_RUNS`` are kept."""

    __tablename__ = "auto_runs"
    __table_args__ = (Index("ix_auto_runs_kind", "kind", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: ``{"indexers": {"3": {"requests": 2, "entries": 40, "error": ""}}, "wishes": 1, "loaded": 0, ...}``.
    counts: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: A code when the round broke off.
    error: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))


class Download(Base):
    """A release handed to a download program, for a book (a work, or a book of a source) of one kind. Tracking
    follows it in the program, and the filing takes over once it is ``completed``."""

    __tablename__ = "downloads"
    __table_args__ = (
        Index("ix_downloads_state", "state"), Index("ix_downloads_work", "work_id"),
        Index("ix_downloads_foreign", "foreign_id"), Index("ix_downloads_wish", "wish_id"),
        # A book of one kind loads one release at a time for a library, whatever races (two people, a double click).
        Index("uq_downloads_work_kind", "work_id", "kind", text("coalesce(library_id, 0)"), unique=True,
              sqlite_where=text("work_id IS NOT NULL AND state IN ('sending', 'unsure', 'queued', 'downloading', "
                                "'paused', 'completed', 'problem')")),
        Index("uq_downloads_foreign_kind", "foreign_id", "kind", text("coalesce(library_id, 0)"), unique=True,
              sqlite_where=text("foreign_id IS NOT NULL AND state IN ('sending', 'unsure', 'queued', 'downloading', "
                                "'paused', 'completed', 'problem')")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Who asked for it.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    work_id: Mapped[int | None] = mapped_column(ForeignKey("works.id", ondelete="SET NULL"), nullable=True)
    foreign_id: Mapped[int | None] = mapped_column(ForeignKey("foreign_books.id", ondelete="SET NULL"), nullable=True)
    #: The library the book is to go into.
    library_id: Mapped[int | None] = mapped_column(ForeignKey("libraries.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(8))
    language: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    title: Mapped[str] = mapped_column(String(400))
    format: Mapped[str] = mapped_column(String(12), default="", server_default=text("''"))
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    protocol: Mapped[str] = mapped_column(String(8))
    info_hash: Mapped[str] = mapped_column(String(40), default="", server_default=text("''"))
    indexer_id: Mapped[int | None] = mapped_column(ForeignKey("indexers.id", ondelete="SET NULL"), nullable=True)
    indexer_name: Mapped[str] = mapped_column(String(100), default="", server_default=text("''"))
    client_id: Mapped[int | None] = mapped_column(ForeignKey("download_clients.id", ondelete="SET NULL"), nullable=True)
    client_name: Mapped[str] = mapped_column(String(100), default="", server_default=text("''"))
    #: The program's name for the job (SABnzbd's ``nzo_id``, the info hash of a torrent); empty while unsure.
    client_ref: Mapped[str] = mapped_column(String(128), default="", server_default=text("''"))
    category: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    state: Mapped[str] = mapped_column(String(16), default="queued")
    #: Loaded although the judgement said no ("Trotzdem laden"): the codes it said no with.
    forced: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    # Following it. ---------------------------------------------------------------------------------
    wish_id: Mapped[int | None] = mapped_column(ForeignKey("procure_wishes.id", ondelete="SET NULL"), nullable=True)
    #: 0 to 100, as the program says.
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    remaining_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The program's own word for the state, for the log and for "unpacking" and "checking" (SABnzbd).
    client_state: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    #: Why a download is a problem (``path_not_found``, ``no_write``, ``client_error``, ``no_space``,
    #: ``gone_from_client``, and the codes of the check after the download: ``language``, ``runtime``, ``bundle``,
    #: ``upgrade_position``), or a hint on a download that goes on (``stalled``). With the values for the words.
    problem_code: Mapped[str] = mapped_column(String(32), default="", server_default=text("''"))
    problem_values: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: The finished path as the program names it, and how often in a row the program did not know the job.
    reported_path: Mapped[str] = mapped_column(String(4096), default="", server_default=text("''"))
    missing_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: When it ended: failed, removed or filed.
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    failed_reason: Mapped[str] = mapped_column(String(32), default="", server_default=text("''"))
    failed_detail: Mapped[str] = mapped_column(String(32), default="", server_default=text("''"))
    #: A job to take out of the program that the program has not taken out yet (it did not answer): tried every round.
    removal_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    #: What a torrent is to seed (the tracker's wish, at least the operator's), and how it stands once it is filed.
    goal_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    goal_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: ``watching`` while the goal is looked at after filing, ``done`` when the torrent left the program, else empty.
    seed_state: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    seed_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    seed_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Filing it. ------------------------------------------------------------------------------------
    #: How far the filing is: empty (not begun), ``checked`` (the files are read and judged, ``procure_files`` says
    #: which go where), ``placed`` (they lie in the library), ``filed`` (the library knows them).
    place_step: Mapped[str] = mapped_column(String(10), default="", server_default=text("''"))
    #: The folder (or file) the files are read from, as this machine sees it (links resolved): the download's own, or
    #: the folder an upload was received into.
    source_path: Mapped[str] = mapped_column(String(4096), default="", server_default=text("''"))
    #: What a person decided about a question of the check (``{"variant": "abridged"}``, ``{"unit": "..."}``,
    #: ``{"take": true}``): the filing goes on with it.
    decision: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: The edition the files went into, once filed.
    edition_id: Mapped[int | None] = mapped_column(ForeignKey("editions.id", ondelete="SET NULL"), nullable=True)
    #: What an upgrade replaced: ``{"from": "mp3_128", "to": "flac", "trash_id": 4}``.
    replaced: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: What was wished (``ProcureWish.wished``, or the book the moment the download began when no wish names it).
    wished: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    #: The generation of the rules (``checking.CHECK_REV``) of the last judgement of the check after the download; 0 for
    #: a download judged before the number existed.
    check_rev: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


#: What becomes of a file of a finished download or an upload: ``found`` (read, not judged yet), ``take`` (it goes into
#: the library), ``drop`` (it does not: ``reason`` says why), ``placed`` (it lies in the library).
PLACE_FILE_STATES = ("found", "take", "drop", "placed")


class PlaceFile(Base):
    """A file of a finished download or an upload as the filing sees it: where it lies, what the readers
    of the scan made of it, whether it goes into the library and where to. The rows are the journal of the filing: a
    restart in the middle goes on from them (a file that lies at its goal already was placed)."""

    __tablename__ = "procure_files"
    __table_args__ = (Index("ix_procure_files_download", "download_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    download_id: Mapped[int] = mapped_column(ForeignKey("downloads.id", ondelete="CASCADE"))
    #: Relative to the folder it is read from, with ``/``.
    path: Mapped[str] = mapped_column(String(1500))
    #: ``source`` (the download's own folder) or ``unpacked`` (the folder nexbooks unpacked its archives into).
    origin: Mapped[str] = mapped_column(String(8), default="source", server_default=text("'source'"))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    #: epub, pdf, azw3, mobi, mp3, m4b, m4a, flac, ogg, opus; for a file beside a recording json, opf or cover.
    format: Mapped[str] = mapped_column(String(8), default="")
    #: ``book`` or ``sidecar``.
    role: Mapped[str] = mapped_column(String(8), default=ROLE_BOOK)
    #: The unit as the scan would form it (``catalog.unit_key``): the files of one book.
    unit: Mapped[str] = mapped_column(String(1500), default="", server_default=text("''"))
    probe: Mapped[Any] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(8), default="found")
    reason: Mapped[str] = mapped_column(String(32), default="", server_default=text("''"))
    #: Where it goes, relative to the library.
    target: Mapped[str] = mapped_column(String(1500), default="", server_default=text("''"))
    #: How it got there: ``link``, ``rename`` or ``copy``.
    how: Mapped[str] = mapped_column(String(8), default="", server_default=text("''"))
    #: Another wish this file fills (a volume of a bundle somebody wished too).
    wish_id: Mapped[int | None] = mapped_column(ForeignKey("procure_wishes.id", ondelete="SET NULL"), nullable=True)
    #: The time and checksum of the placed file, as the scan would read them.
    mtime_ns: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    sha256: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))


class TrashEntry(Base):
    """A book's files that an upgrade replaced: they lie in the folder ``.nexbooks-trash`` of their
    library (``services/procure/trash.py``) until ``expires_at``, a person takes them back, or the trash is emptied."""

    __tablename__ = "trash_entries"
    __table_args__ = (Index("ix_trash_entries_library", "library_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"))
    work_id: Mapped[int | None] = mapped_column(ForeignKey("works.id", ondelete="SET NULL"), nullable=True)
    #: The edition the files belonged to (an e-book keeps it; a recording that was replaced went into the new one).
    edition_id: Mapped[int | None] = mapped_column(ForeignKey("editions.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    author: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    #: The format in the words of the ranking (``pdf``, ``mp3_128``).
    format: Mapped[str] = mapped_column(String(12), default="", server_default=text("''"))
    #: ``[{"path": where it lay relative to the library, "size": bytes}]``.
    files: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: The folder below ``.nexbooks-trash`` that holds them.
    folder: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    download_id: Mapped[int | None] = mapped_column(ForeignKey("downloads.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class KeptState(Base):
    """The places, marks and finished passes of the people who read or heard an edition that was sent away as the wrong
    book (``services/procure/wrong.py``). They lie with the trash entry of its files and come back to the edition the
    scan makes of the files when they are taken out of the trash. ``trash_id`` is set while the files are in the trash
    (the row goes with the entry); when they are taken out it is cleared and the row waits for the scan until
    ``expires_at``. ``states`` holds ``progress``, ``bookmarks`` and ``history``, each a list of what one person had."""

    __tablename__ = "kept_states"
    __table_args__ = (Index("ix_kept_states_library", "library_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"))
    trash_id: Mapped[int | None] = mapped_column(ForeignKey("trash_entries.id", ondelete="CASCADE"), nullable=True)
    #: Where the files lay in the library (the edition they make again is found by them).
    paths: Mapped[Any] = mapped_column(JSON, nullable=True)
    states: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ProcureNotice(Base):
    """Something the filing tells one person, shown under "Braucht dich" until the person says they read
    it: ``upgrade_position`` (the place in an e-book was carried over to a new format by its share only). **The
    person's own**, like the progress it speaks of: no query reads it for anybody else."""

    __tablename__ = "procure_notices"
    __table_args__ = (Index("ix_procure_notices_person", "user_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    edition_id: Mapped[int | None] = mapped_column(ForeignKey("editions.id", ondelete="CASCADE"), nullable=True)
    download_id: Mapped[int | None] = mapped_column(ForeignKey("downloads.id", ondelete="SET NULL"), nullable=True)
    trash_id: Mapped[int | None] = mapped_column(ForeignKey("trash_entries.id", ondelete="SET NULL"), nullable=True)
    code: Mapped[str] = mapped_column(String(32))
    detail: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ForeignJob(Base):
    """A job in nexbooks's category of a program that nobody asked nexbooks for. nexbooks never takes one over by
    itself: it is shown under "Braucht dich" until a person says what to do with it."""

    __tablename__ = "foreign_jobs"
    __table_args__ = (UniqueConstraint("client_id", "ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("download_clients.id", ondelete="CASCADE"))
    ref: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(400))
    #: The state of the job in the program (``queued``, ``downloading``, ``completed`` ...).
    job_state: Mapped[str] = mapped_column(String(12), default="", server_default=text("''"))
    ignored: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


#: How a download ended, in the history: ``done`` (filed), ``rejected`` (the check after the download turned it down),
#: ``failed`` (the program could not), ``removed`` (a person took it away); and ``joined``: two works that were there
#: became one by the rules of the scan (no download; ``note.edition_id`` is the edition that moved).
HISTORY_RESULTS = ("done", "rejected", "failed", "removed", "joined")


class ProcureHistory(Base):
    """What became of a download, kept when the download row goes. The title and the author are kept as they were, so
    that a line stays readable when its work is gone."""

    __tablename__ = "procure_history"
    __table_args__ = (Index("ix_procure_history_library", "library_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    library_id: Mapped[int | None] = mapped_column(ForeignKey("libraries.id", ondelete="SET NULL"), nullable=True)
    work_id: Mapped[int | None] = mapped_column(ForeignKey("works.id", ondelete="SET NULL"), nullable=True)
    foreign_id: Mapped[int | None] = mapped_column(ForeignKey("foreign_books.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(8))
    book_title: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    book_author: Mapped[str] = mapped_column(String(300), default="", server_default=text("''"))
    #: The name of the release.
    release: Mapped[str] = mapped_column(String(400), default="", server_default=text("''"))
    indexer_name: Mapped[str] = mapped_column(String(100), default="", server_default=text("''"))
    result: Mapped[str] = mapped_column(String(10))
    #: Why, as a code with its values (``{"code": "client_failed", "detail": "password"}``); the interface words it.
    note: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: What was wished (``Download.wished``); None for a line from before there was one.
    wished: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)


# --- What the iPhone app follows: the order of changes ----------------------------------------------------


class AppChanges(Base):
    """The one row (``id`` 1) that counts the changes the app follows (``services/app_changes.py``). ``seq`` is the
    newest number given out; ``floor`` the newest number of what has been forgotten (a removal older than 90 days): a
    device that last looked before it has to start again. Written only by the triggers below and by the clean-up."""

    __tablename__ = "app_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    floor: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class AppGone(Base):
    """A work that is no more, with the number of the change that took it away and when (seconds since 1970, from the
    database's clock; the trigger writes it). Kept 90 days, so that a device that comes back within them hears of it."""

    __tablename__ = "app_gone"
    __table_args__ = (Index("ix_app_gone_seq", "seq"),)

    work_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    seq: Mapped[int] = mapped_column(Integer)
    gone_at: Mapped[int] = mapped_column(Integer)


class AppSeen(Base):
    """A work a device was given by ``/api/app/1/library``. A work that goes away, or that the person may no longer see,
    is named in ``removed`` only to a device that had it: nobody hears the numbers of books in libraries they were never
    in. Per device (schema 20; before, per person), so that the row can go once that device has heard of the removal:
    else everybody who left a library would hear again of every change in it. A device that is deleted takes its rows
    with it."""

    __tablename__ = "app_seen"

    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    work_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


def _differs(columns: str) -> str:
    return " OR ".join(f'OLD."{name}" IS NOT NEW."{name}"' for name in columns.split())


#: The next number, made if the row is not there (a database whose row somebody deleted starts again at 1, and every
#: device that knew a higher number starts again with it: ``marker_too_old``).
_TICK = "INSERT INTO app_changes (id, seq, floor) VALUES (1, 1, 0) ON CONFLICT (id) DO UPDATE SET seq = seq + 1;"
_SEQ = "(SELECT seq FROM app_changes WHERE id = 1)"


def _one(work: str) -> str:
    """One work gets the next number."""
    return f"{_TICK} UPDATE works SET change_seq = {_SEQ} WHERE id = {work};"  # noqa: S608 - names of this module


def _many(works: str) -> str:
    """Every work of the set ``works`` (a query of work ids) gets a number of its own, all newer than any before: the
    numbers from the next one on, by id, and the row moves past them. A number is never shared, so a page of the
    library that ends inside a set of changes loses none."""
    return (
        "INSERT INTO app_changes (id, seq, floor) VALUES (1, 0, 0) ON CONFLICT (id) DO NOTHING; "  # noqa: S608
        f"UPDATE works SET change_seq = {_SEQ} + 1 + id - (SELECT min(id) FROM works WHERE id IN ({works})) "
        f"WHERE id IN ({works}); "
        f"UPDATE app_changes SET seq = seq + coalesce((SELECT max(id) - min(id) + 1 FROM works WHERE id IN ({works})), "
        "0) WHERE id = 1;"
    )


_WORK_COLUMNS = ("title original_title original_language description year genre color cover series_id series_pos "
                 "pages pattern tall gold key main_key original_key added_at")
_EDITION_COLUMNS = ("work_id library_id kind variant variant_uncertain language title subtitle isbn asin publisher "
                    "published description genre series_name series_id series_pos original_title title_key shelf_key "
                    "pages duration cover color missing_at created_at original_language overlay grouped_by genre_guess")
#: What the app is told of a file. Not the path (the app never sees one), not when the scan last saw it.
_FILE_COLUMNS = "edition_id library_id role size mtime_ns sha256 format part duration_ms missing_at"
_CHAPTER_COLUMNS = "edition_id number title start_ms target depth"
_CREDIT_COLUMNS = "person_id work_id edition_id role position"
_MEMBER_COLUMNS = "library_id user_id right"

_WORK_OF_EDITION = "(SELECT work_id FROM editions WHERE id = {0}.edition_id)"
_WORKS_OF_CREDIT = "SELECT {0}.work_id UNION SELECT work_id FROM editions WHERE id = {0}.edition_id"
_WORKS_OF_LIBRARY = "SELECT work_id FROM editions WHERE library_id = {0}.library_id"


def _trigger(name: str, when: str, body: str) -> str:
    return f"CREATE TRIGGER IF NOT EXISTS {name} {when} BEGIN {body} END"


#: **Every way a change is written moves the number**: the triggers sit on the tables, so whatever writes there (the
#: scan, filing a fetched book, moving an edition into another library, joining and splitting works, a correction by
#: hand, a migration, a plain statement) is caught, and nobody has to remember to call anything. A write that changes
#: nothing the app is told (the scan noting it saw a file, the time of the last change) moves nothing.
APP_CHANGE_STATEMENTS = (
    _trigger("app_works_made", "AFTER INSERT ON works", _one("NEW.id")),
    _trigger("app_works_changed", f"AFTER UPDATE ON works WHEN {_differs(_WORK_COLUMNS)}", _one("NEW.id")),
    _trigger("app_works_gone", "AFTER DELETE ON works",
             f"{_TICK} INSERT INTO app_gone (work_id, seq, gone_at) VALUES (OLD.id, {_SEQ}, "  # noqa: S608
             "CAST(strftime('%s', 'now') AS INTEGER)) ON CONFLICT (work_id) DO UPDATE SET seq = excluded.seq, "
             "gone_at = excluded.gone_at;"),
    _trigger("app_editions_made", "AFTER INSERT ON editions", _one("NEW.work_id")),
    _trigger("app_editions_changed", f"AFTER UPDATE ON editions WHEN {_differs(_EDITION_COLUMNS)}",
             _one("NEW.work_id")),
    # An edition that went to another work: the work it left changes too.
    _trigger("app_editions_left", "AFTER UPDATE OF work_id ON editions WHEN OLD.work_id IS NOT NEW.work_id",
             _one("OLD.work_id")),
    _trigger("app_editions_gone", "AFTER DELETE ON editions", _one("OLD.work_id")),
    _trigger("app_files_made", "AFTER INSERT ON files WHEN NEW.role = 'book' AND NEW.edition_id IS NOT NULL",
             _one(_WORK_OF_EDITION.format("NEW"))),
    _trigger("app_files_changed",
             f"AFTER UPDATE ON files WHEN (OLD.role = 'book' OR NEW.role = 'book') AND ({_differs(_FILE_COLUMNS)})",
             _many("SELECT work_id FROM editions WHERE id IN (OLD.edition_id, NEW.edition_id)")),
    _trigger("app_files_gone", "AFTER DELETE ON files WHEN OLD.role = 'book' AND OLD.edition_id IS NOT NULL",
             _one(_WORK_OF_EDITION.format("OLD"))),
    _trigger("app_chapters_made", "AFTER INSERT ON chapters", _one(_WORK_OF_EDITION.format("NEW"))),
    _trigger("app_chapters_changed", f"AFTER UPDATE ON chapters WHEN {_differs(_CHAPTER_COLUMNS)}",
             _many("SELECT work_id FROM editions WHERE id IN (OLD.edition_id, NEW.edition_id)")),
    _trigger("app_chapters_gone", "AFTER DELETE ON chapters", _one(_WORK_OF_EDITION.format("OLD"))),
    _trigger("app_credits_made", "AFTER INSERT ON credits", _many(_WORKS_OF_CREDIT.format("NEW"))),
    _trigger("app_credits_changed", f"AFTER UPDATE ON credits WHEN {_differs(_CREDIT_COLUMNS)}",
             _many(f"{_WORKS_OF_CREDIT.format('OLD')} UNION {_WORKS_OF_CREDIT.format('NEW')}")),
    _trigger("app_credits_gone", "AFTER DELETE ON credits", _many(_WORKS_OF_CREDIT.format("OLD"))),
    # A name the app shows: an author, a narrator, a translator, a series.
    _trigger("app_people_renamed", "AFTER UPDATE OF name ON people WHEN OLD.name IS NOT NEW.name",
             _many("SELECT work_id FROM credits WHERE person_id = NEW.id AND work_id IS NOT NULL UNION SELECT "
                   "e.work_id FROM credits c JOIN editions e ON e.id = c.edition_id WHERE c.person_id = NEW.id")),
    _trigger("app_series_renamed", "AFTER UPDATE OF name ON series WHEN OLD.name IS NOT NEW.name",
             _many("SELECT id FROM works WHERE series_id = NEW.id UNION "
                   "SELECT work_id FROM editions WHERE series_id = NEW.id")),
    # Who sees a library: every work in it, for everybody (the one who no longer sees it hears of it in ``removed``).
    _trigger("app_members_made", "AFTER INSERT ON library_members", _many(_WORKS_OF_LIBRARY.format("NEW"))),
    _trigger("app_members_changed", f"AFTER UPDATE ON library_members WHEN {_differs(_MEMBER_COLUMNS)}",
             _many("SELECT work_id FROM editions WHERE library_id IN (OLD.library_id, NEW.library_id)")),
    _trigger("app_members_gone", "AFTER DELETE ON library_members", _many(_WORKS_OF_LIBRARY.format("OLD"))),
)


@event.listens_for(Base.metadata, "after_create")
def _app_change_triggers(_target: Any, connection: Any, **_kw: Any) -> None:
    """A new database gets the triggers with its tables, the same as a migrated one (``step_v19_schema``)."""
    for statement in APP_CHANGE_STATEMENTS:
        connection.exec_driver_sql(statement)


__all__ = [
    "ABRIDGED",
    "ACQUIRE",
    "ACTIVE_DOWNLOADS",
    "APP_CHANGE_STATEMENTS",
    "AUTHOR",
    "CLIENT_KINDS",
    "CLIENT_PROTOCOL",
    "CLIENT_STATES",
    "DOWNLOAD_STATES",
    "DRAMATIZED",
    "EDITION_AUDIO",
    "EDITION_EBOOK",
    "FTS_TABLE",
    "HISTORY_KINDS",
    "HISTORY_LISTENED",
    "HISTORY_READ",
    "HISTORY_RESULTS",
    "INDEXER_KINDS",
    "KIND_AUDIO",
    "KIND_BOTH",
    "KIND_EBOOK",
    "LIBRARY_KINDS",
    "LIBRARY_RIGHTS",
    "MANAGE",
    "MATCH_STATES",
    "MEMBER",
    "NARRATOR",
    "OPEN_WISHES",
    "OPERATOR",
    "ORGANIZE_STATES",
    "PLACE_FILE_STATES",
    "READ",
    "ROLES",
    "ROLES_OF_PEOPLE",
    "ROLE_BOOK",
    "ROLE_SIDECAR",
    "SEARCH_STATEMENTS",
    "SEARCH_STATES",
    "SIGN_IN_OIDC",
    "SIGN_IN_PASSWORD",
    "SOURCES",
    "STAGE_CODES",
    "STAGE_FULL",
    "STAGE_SETUP",
    "TRANSLATOR",
    "UNABRIDGED",
    "VARIANTS",
    "WISH_STATES",
    "Account",
    "ApiKey",
    "AuthSession",
    "Base",
    "BlocklistEntry",
    "BookFile",
    "Bookmark",
    "Chapter",
    "Credit",
    "Device",
    "Download",
    "DownloadClient",
    "Edition",
    "EmailConfirmation",
    "ExternalRef",
    "ForeignBook",
    "ForeignJob",
    "HistoryEntry",
    "Indexer",
    "Invite",
    "Job",
    "Library",
    "LibraryMember",
    "LibraryProblem",
    "ListEntry",
    "ListFetch",
    "MatchEntry",
    "OidcLink",
    "OidcProvider",
    "OrganizeMove",
    "OrganizeRun",
    "Passkey",
    "PasswordReset",
    "PathMapping",
    "Person",
    "PersonInfo",
    "PersonPhoto",
    "PlaceFile",
    "ProcureHistory",
    "ProcureNotice",
    "ProcureWish",
    "Progress",
    "ProwlarrConnection",
    "PushDevice",
    "ReleaseSearch",
    "Relocation",
    "RelocationFile",
    "SearchEntry",
    "SearchRelease",
    "Series",
    "Setting",
    "SourceCache",
    "SourceState",
    "TrashEntry",
    "UtcDateTime",
    "Wish",
    "Work",
    "utcnow",
]
