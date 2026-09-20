"""Strict, versioned runtime and action data models.

These models describe data only. Physics behavior starts in P01; keeping the
contract separate prevents tests, renderers, and model adapters from inventing
their own state shapes.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role: TypeAlias = Literal["fireboy", "watergirl"]
EntityKind: TypeAlias = Literal[
    "pressure_plate",
    "lever",
    "gate",
    "moving_platform",
    "box",
    "ball",
    "seesaw",
    "light_source",
    "mirror",
    "light_sensor",
    "phase_pool",
    "portal",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Vec2(StrictModel):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class ContactState(StrictModel):
    entity_id: str = Field(min_length=1, max_length=64)
    normal: Vec2


class CharacterState(StrictModel):
    role: Role
    position: Vec2
    velocity: Vec2
    alive: bool = True
    on_ground: bool = False
    contacts: tuple[ContactState, ...] = ()
    portal_cooldown_ticks: int = Field(default=0, ge=0)
    portal_rearm_required: bool = False


class BoxState(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal["box"] = "box"
    position: Vec2
    velocity: Vec2
    contacts: tuple[ContactState, ...] = ()


class BallState(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    kind: Literal["ball"] = "ball"
    position: Vec2
    velocity: Vec2
    angle_degrees: float = Field(default=0.0, allow_inf_nan=False)
    angular_velocity: float = Field(default=0.0, allow_inf_nan=False)
    contacts: tuple[ContactState, ...] = ()


ObjectState = Annotated[BoxState | BallState, Field(discriminator="kind")]


class MechanismState(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    kind: EntityKind
    active: bool = False
    position: Vec2 | None = None
    velocity: Vec2 | None = None
    phase: Literal["liquid", "frozen"] | None = None
    transition_counter_ticks: int = Field(default=0, ge=0)
    angle_degrees: float | None = Field(default=None, allow_inf_nan=False)
    blocked: bool = False
    cooldowns: dict[str, int] = Field(default_factory=dict)


class SkillRuntime(StrictModel):
    kind: Literal["move_to", "push"]
    target_id: str = Field(min_length=1, max_length=64)
    started_tick: int = Field(ge=0)
    deadline_tick: int = Field(ge=0)

    @model_validator(mode="after")
    def deadline_not_before_start(self) -> "SkillRuntime":
        if self.deadline_tick < self.started_tick:
            raise ValueError("deadline_tick must be greater than or equal to started_tick")
        return self


class QueuedMessage(StrictModel):
    sender: Role
    recipient: Role
    delivery_round: int = Field(ge=0)
    text: str = Field(max_length=256)


class WorldState(StrictModel):
    schema_version: Literal["fwcollab.state.v1"] = "fwcollab.state.v1"
    physics_version: str = Field(min_length=1, max_length=64)
    source_map_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tick: int = Field(ge=0)
    characters: tuple[CharacterState, CharacterState]
    objects: tuple[ObjectState, ...] = ()
    mechanisms: tuple[MechanismState, ...] = ()
    signal_latches: dict[str, bool] = Field(default_factory=dict)
    active_skills: dict[Role, SkillRuntime | None] = Field(default_factory=dict)
    pending_messages: tuple[QueuedMessage, ...] = ()
    rng_state: str = Field(min_length=1)

    @model_validator(mode="after")
    def roles_and_ids_are_unique(self) -> "WorldState":
        if {character.role for character in self.characters} != {"fireboy", "watergirl"}:
            raise ValueError("characters must contain exactly fireboy and watergirl")
        ids = [obj.id for obj in self.objects] + [item.id for item in self.mechanisms]
        if len(ids) != len(set(ids)):
            raise ValueError("runtime object and mechanism IDs must be unique")
        if set(self.active_skills) - {"fireboy", "watergirl"}:
            raise ValueError("active_skills contains an unknown role")
        return self


class MoveToAction(StrictModel):
    kind: Literal["move_to"]
    target_id: str = Field(min_length=1, max_length=64)
    deadline_ticks: int = Field(default=90, ge=1, le=90)


class InteractAction(StrictModel):
    kind: Literal["interact"]
    target_id: str = Field(min_length=1, max_length=64)


class PushAction(StrictModel):
    kind: Literal["push"]
    target_id: str = Field(min_length=1, max_length=64)
    direction: Literal["left", "right"]
    deadline_ticks: int = Field(default=90, ge=1, le=90)


class WaitAction(StrictModel):
    kind: Literal["wait"]


class ContinueAction(StrictModel):
    kind: Literal["continue"]


class KeysAction(StrictModel):
    kind: Literal["keys"]
    horizontal: Literal[-1, 0, 1] = 0
    jump: bool = False
    interact: bool = False


Action = Annotated[
    MoveToAction | InteractAction | PushAction | WaitAction | ContinueAction | KeysAction,
    Field(discriminator="kind"),
]


class Message(StrictModel):
    kind: Literal["request", "inform", "acknowledge"] | None = None
    target_id: str | None = Field(default=None, min_length=1, max_length=64)
    operation: Literal["activate", "maintain", "release", "move", "wait"] | None = None
    text: str = Field(default="", max_length=256)


class ActionPacket(StrictModel):
    schema_version: Literal["fwcollab.action.v1"] = "fwcollab.action.v1"
    action: Action
    message: Message | None = None

