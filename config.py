"""Единая точка настройки баланса и путей проекта TANKSIM.

Все игровые числа собраны здесь, чтобы баланс можно было крутить, не трогая код.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --- пути -------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
AI_DIR = ROOT / "ai"
EXAMPLES_DIR = ROOT / "examples"
MAPS_DIR = ROOT / "maps"
POOL_DIR = ROOT / "pool"
SAVES_DIR = Path(os.environ.get("TANKSIM_SAVES", ROOT / "saves"))
REPLAYS_DIR = SAVES_DIR / "replays"
SCRIPTS_DIR = SAVES_DIR / "scripts"
WEB_DIR = ROOT / "web"

for _d in (SAVES_DIR, REPLAYS_DIR, SCRIPTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- символы карты ----------------------------------------------------------

TILE_FLOOR = "."
TILE_WALL = "#"        # блокирует движение и обзор
TILE_COLUMN = "o"      # колонна: блокирует движение и обзор
TILE_LOW = ":"          # низкий укрыт: блокирует движение, обзор проходит
TILE_MUD = ","          # грязь: проходима, замедляет
TILE_SPAWN_A = "A"
TILE_SPAWN_B = "B"

#: Символы, которые не пропускают взгляд и в которые нельзя въехать
SOLID_TILES = frozenset({TILE_WALL, TILE_COLUMN, TILE_LOW})
#: Символы, закрывающие обзор
OPAQUE_TILES = frozenset({TILE_WALL, TILE_COLUMN})
#: Символы, в которые нельзя въехать
BLOCK_TILES = frozenset({TILE_WALL, TILE_COLUMN, TILE_LOW})


# --- баланс -----------------------------------------------------------------


@dataclass(frozen=True)
class Balance:
    """Игровые константы. Время в секундах, расстояния в пикселях, углы в градусах."""

    # --- такт ---
    tick_rate: int = 60
    max_seconds: int = 60

    # --- танк ---
    hp: float = 10.0
    hull_len: float = 34.0
    hull_wid: float = 22.0
    speed_fwd: float = 190.0
    speed_rev: float = 120.0
    accel: float = 460.0
    decel: float = 560.0
    hull_turn: float = 2.6          # рад/с
    turret_turn: float = 3.6        # рад/с
    turn_speed_penalty: float = 0.4  # не используется: потеря скорости в
    # повороте теперь следует из самих гусениц (среднее по тракам), а не из
    # плоского штрафа. Поле оставлено, потому что bal уходит скриптам в
    # наблюдении и его набор ключей менять нельзя.

    # --- снаряд ---
    reload: float = 1.0             # КД между выстрелами
    bullet_speed: float = 620.0
    bullet_life: float = 10.0
    bullet_radius: float = 3.0
    spread: float = 0.4             # ± градусов
    muzzle_offset: float = 26.0     # от центра до конца ствола
    base_damage: float = 4.0
    self_damage: bool = False       # может ли снаряд попасть в стрелка

    # --- броня ---
    # Броня на урон не влияет: любое пробитие стоит ровно base_damage, независимо
    # от грани и угла. Единственное, что она делает, — рикошет: снаряд под
    # большим углом к нормали грани скользит и не пробивает.
    front_half_angle: float = 35.0  # ± от курса корпуса — лоб
    
    # Порог задан как угол к нормали (0 - прямой удар, 90 - по касательной).
    # Угол к броне = 90 - угол к нормали.
    ricochet_angles: dict[str, float | None] = field(default_factory=lambda: {
        "front": 30.0,
        "side": 50.0,
        "rear": 60.0,
    })
    ricochet_damage: float = 0.0    # рикошет не наносит урона вовсе
    wall_ricochet_angle: float = 60.0 # угол рикошета от препятствий

    # --- контакт ---
    ram_damage: float = 0.3
    ram_speed: float = 60.0
    ram_cooldown: float = 0.8      # пауза между таранами
    # Доля импульса, которую тяжёлый танк отдаёт при столкновении.
    # 0 = полностью неупругий удар (оба глохнут), 1 = отскок как у резины.
    ram_restitution: float = 0.25
    mass: float = 1.0              # масса танка: влияет на расталкивание

    # --- зрение ---
    view_range: float = 760.0
    # Половина угла обзора: 180 = круговой обзор во все стороны.
    # Раньше было 62 (конус 124°) — обзор был узким и упирался в нос танка.
    view_cone: float = 180.0
    bumper_range: float = 90.0      # круговая «ближняя зона» awareness

    # --- лимит времени на решение ---
    default_budget_ms: float = 10.0
    min_budget_ms: float = 1.0
    max_budget_ms: float = 500.0
    hard_kill_ms: float = 250.0     # минимальный жёсткий kill-таймаут
    kill_grace: float = 20.0        # множитель бюджета для жёсткого kill

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def half(self) -> tuple[float, float]:
        return (self.hull_len / 2.0, self.hull_wid / 2.0)

    @property
    def half_far(self) -> tuple[float, float]:
        """Полуразмеры с учётом радиуса снаряда."""
        r = self.bullet_radius
        return (self.hull_len / 2.0 + r, self.hull_wid / 2.0 + r)


BALANCE = Balance()

#: Быстрая копия баланса с переопределениями (для тестов).
BALANCE_OVERRIDES: dict = {}


@dataclass
class PlayerConfig:
    """Описание одного участника боя."""

    kind: str = "script"           # "script" | "manual"
    source: str = ""               # путь к .tankp.py или "" для ручного
    name: str = ""                 # если пусто — возьмём из заголовка скрипта
    color: str = ""
    seed: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BattleConfig:
    """Полное описание боя: два участника, карта и лимиты."""

    a: PlayerConfig = field(default_factory=PlayerConfig)
    b: PlayerConfig = field(default_factory=PlayerConfig)
    map_name: str = "arena"
    budget_ms: float = BALANCE.default_budget_ms
    seed: int = 12345
    max_seconds: int = BALANCE.max_seconds

    def to_dict(self) -> dict:
        return {
            "a": self.a.to_dict(),
            "b": self.b.to_dict(),
            "map_name": self.map_name,
            "budget_ms": self.budget_ms,
            "seed": self.seed,
            "max_seconds": self.max_seconds,
        }
