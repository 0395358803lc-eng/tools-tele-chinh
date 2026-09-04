from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator


def _normalize_phone(value: str) -> str:
    import re
    digits = re.sub(r"\D", "", value or "")
    if not digits or len(digits) > 15:
        raise ValueError("Invalid phone number")
    return f"+{digits}"


class AccountIdsIn(BaseModel):
    account_ids: list[int] = Field(min_length=1, max_length=100)

    @field_validator("account_ids")
    @classmethod
    def _dedupe_account_ids(cls, values):
        return list(dict.fromkeys(values))


class NonEmptyTargetIn(BaseModel):
    @field_validator("target", check_fields=False)
    @classmethod
    def _target_not_blank(cls, value):
        if not value or not value.strip():
            raise ValueError("Telegram target must not be empty")
        return value.strip()


class AccountOut(BaseModel):
    id: int
    phone: str
    tg_user_id: Optional[int] = None
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    bio: str = ""
    status: str
    has_2fa: bool
    is_online: bool
    last_seen: Optional[datetime] = None
    unread_security: int = 0

    class Config:
        from_attributes = True


class GoneAccountOut(BaseModel):
    id: int
    account_id: Optional[int] = None
    tg_user_id: Optional[int] = None
    phone: str
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    old_serial: Optional[int] = None
    reason: str
    gone_at: datetime

    class Config:
        from_attributes = True


class StatsOut(BaseModel):
    total: int
    connected: int
    banned: int
    with_2fa: int
    unread_security: int


class SendCodeIn(BaseModel):
    phone: str = Field(max_length=64)

    _phone_normalized = field_validator("phone")(_normalize_phone)


class SignInIn(BaseModel):
    phone: str = Field(max_length=64)
    code: str = Field(default="", max_length=16)
    password: Optional[str] = Field(default=None, max_length=256)

    _phone_normalized = field_validator("phone")(_normalize_phone)


class RemoveAllAccountsIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class QrStartOut(BaseModel):
    qr_id: str
    url: str
    expires_at: Optional[str] = None


class QrPollIn(BaseModel):
    qr_id: str


class QrSubmit2faIn(BaseModel):
    qr_id: str
    password: str = Field(max_length=256)


class ProfileUpdateIn(BaseModel):
    first_name: Optional[str] = Field(default=None, max_length=64)
    last_name: Optional[str] = Field(default=None, max_length=64)
    bio: Optional[str] = Field(default=None, max_length=70)


class UsernameUpdateIn(BaseModel):
    username: str = Field(max_length=32)


class UsernameCheckOut(BaseModel):
    available: bool
    reason: str = ""


class BulkProfileIn(AccountIdsIn):
    first_name: Optional[str] = Field(default=None, max_length=64)
    last_name: Optional[str] = Field(default=None, max_length=64)
    username: Optional[str] = Field(default=None, max_length=32)
    bio: Optional[str] = Field(default=None, max_length=70)
    append_number: bool = False  # if true: "Name 1", "Name 2", "username1", "username2"
    start_number: int = 1
    # Per-account overrides: {"<account_id>": {"first_name": "...", "last_name": "...", "username": "...", "bio": "..."}}
    per_account: Optional[dict[str, dict[str, Optional[str]]]] = None


class BulkPhotoIn(AccountIdsIn):
    # image is uploaded separately as multipart
    pass


class SecurityMessageOut(BaseModel):
    id: int
    account_id: int
    tg_msg_id: int
    message_text: str
    type: str
    is_read: bool
    received_at: datetime

    class Config:
        from_attributes = True


class TgSessionOut(BaseModel):
    hash: int
    device: str
    platform: str
    app_name: str
    ip: str
    country: str
    date_created: Optional[datetime] = None
    is_current: bool = False


class JoinIn(NonEmptyTargetIn):
    target: str = Field(max_length=512)  # username or invite link


class BulkJoinIn(AccountIdsIn, NonEmptyTargetIn):
    target: str = Field(max_length=512)


class GroupOut(BaseModel):
    id: int
    title: str
    username: Optional[str] = None
    type: str  # group/supergroup/channel
    members: Optional[int] = None
    invite_link: Optional[str] = None


class LeaveIn(BaseModel):
    chat_id: int


class BulkLeaveIn(AccountIdsIn):
    chat_id: int


class BulkLeaveTargetIn(AccountIdsIn, NonEmptyTargetIn):
    """Leave ONE specific group/channel (by @username or invite link) from every
    selected account that is currently a member of it."""
    target: str = Field(max_length=512)


class BulkLeaveAllIn(AccountIdsIn):
    """Leave EVERY group/channel each account is in."""


class BulkDeleteMyMessagesIn(AccountIdsIn):
    """Delete every message each account sent across ALL its groups/channels."""
    max_scan: int = Field(default=2000, ge=1, le=10000)


class SendMessageIn(NonEmptyTargetIn):
    target: str = Field(max_length=512)
    text: str = Field(min_length=1, max_length=4096)


class BulkMessageIn(AccountIdsIn, NonEmptyTargetIn):
    target: str = Field(max_length=512)
    text: str = Field(min_length=1, max_length=4096)


class BulkWipeChatIn(AccountIdsIn, NonEmptyTargetIn):
    """Delete the ENTIRE conversation with one user/chat (by @username or t.me
    link) from every selected account: clears history for both sides (revoke)
    and removes the dialog so the chat no longer exists."""
    target: str = Field(max_length=512)


class OpenChatIn(BaseModel):
    # A @username, bare username, t.me link, or tg://resolve deep link. Bot
    # referral links like t.me/Bot?start=PAYLOAD fire the bot /start so the
    # referral registers.
    input: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=40, ge=1, le=100)


class ChatSendIn(BaseModel):
    peer: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1, max_length=4096)


class TargetUsageCheckIn(NonEmptyTargetIn):
    target: str = Field(max_length=512)


class ReactionAssignment(AccountIdsIn):
    emoji: str  # the alt/standard glyph (display + standard reactions)
    custom_emoji_id: Optional[int] = None  # premium custom emoji document id


class ReactIn(BaseModel):
    post_link: str  # t.me/channel/123
    reactions: list[ReactionAssignment]


class ViewPostIn(AccountIdsIn):
    post_link: str


class AllowedReactionsIn(BaseModel):
    post_link: str
    account_id: Optional[int] = None  # which account to query through; else first connected


class AllowedCustomReaction(BaseModel):
    id: int          # custom emoji document id
    alt: str = ""    # fallback glyph to display


class AllowedReactionsOut(BaseModel):
    mode: str  # "all" | "some" | "none"
    allow_custom: bool = False
    standard: list[str] = Field(default_factory=list)
    custom: list[AllowedCustomReaction] = Field(default_factory=list)


class Bulk2faIn(AccountIdsIn):
    new_password: str = Field(min_length=1, max_length=256)
    hint: Optional[str] = Field(default="", max_length=64)
    # Current-password attempt bank (max 5). Tried in order, after each account's
    # own remembered password, until one is accepted (5 tries max per account).
    password_bank: list[str] = Field(default_factory=list)


class SettingsIn(BaseModel):
    rate_min: float = Field(ge=0, le=3600)
    rate_max: float = Field(ge=0, le=3600)
    concurrency: int = Field(default=5, ge=1, le=100)
    auto_reconnect: bool


class SettingsOut(SettingsIn):
    pass


class BulkProgressEvent(BaseModel):
    type: str  # "progress" | "done"
    current: int = 0
    total: int = 0
    account_name: str = ""
    success: int = 0
    failed: int = 0
    skipped: int = 0
    detail: str = ""
    errors: list[dict] = Field(default_factory=list)
