"""Public request validation. Secret inputs are never returned in validation errors."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    role: Literal["admin", "applicant"]
    username: str = Field(min_length=1, max_length=80)
    password: SecretStr = SecretStr("")


class Person(Input):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    quota: int | None = Field(default=None, ge=0, le=1000000)


class Quota(Input):
    value: int = Field(ge=0, le=1000000)


class Contact(Input):
    text: str = Field(max_length=2000)


class Assignment(Input):
    person_id: str


class Adoption(Input):
    machine_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    username: str = Field(min_length=1, max_length=64)
    person_id: str
    password: SecretStr | None = None


class Target(Input):
    host: str = Field(min_length=1, max_length=64)
    port: int = Field(default=22, ge=1, le=65535)


class Machine(Target):
    name: str = Field(min_length=1, max_length=80)
    username: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$")
    ssh_password: SecretStr
    sudo_password: SecretStr
    # Accepted for older clients; enrollment obtains the key on the server.
    fingerprint: str | None = Field(default=None, pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")
    archive_root: str = "/var/lib/compute-control/archives"
    available: bool = False


class Available(Input):
    available: bool


class AccountRequest(Input):
    machine_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    username: str = Field(min_length=1, max_length=32)
    password: SecretStr
    request_key: str = Field(min_length=16, max_length=100)
    person_id: str | None = None


class Password(Input):
    password: SecretStr


class ManagementPassword(Input):
    current_password: SecretStr = Field(max_length=256)
    new_password: SecretStr = Field(max_length=256)
    confirm_password: SecretStr = Field(max_length=256)


class Confirm(Input):
    confirmed: bool


class RebootRequest(Confirm):
    force: bool = False
    force_confirmed: bool = False


class Announcement(Input):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=12000)
    pinned: bool = False


class Pin(Input):
    pinned: bool


class AdminContact(Input):
    name: str = Field(min_length=1, max_length=80)
    contact: str = Field(min_length=1, max_length=2000)


class PermissionRequest(Input):
    account_id: str
    reason: str = Field(min_length=1, max_length=2000)


class PermissionDecision(Input):
    action: Literal["help_install", "grant", "reject"]
    response: str = Field(default="", max_length=2000)


class Privilege(Input):
    enabled: bool


class MachineNote(Input):
    content: str = Field(min_length=1, max_length=4000)
