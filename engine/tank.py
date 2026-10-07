"""Танк: движение гусениц, независимая башня, перезарядка и статистика."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from config import Balance
from engine.action import Action
from engine.geometry import TAU, clamp
from engine.grid import Arena, push_out


@dataclass
class Tank:
    """Состояние одного танка в бою."""

    id: int
    x: float
    y: float
    hull: float
    turret: float
    name: str = ""
    color: str = ""
    kind: str = "script"

    vx: float = 0.0
    vy: float = 0.0
    hp: float = 10.0
    hp_max: float = 10.0
    mass: float = 1.0              # тяжёлый упирается в таране, лёгкий отлетает
    cooldown: float = 0.0
    ram_cd: float = 0.0          # пауза между таранами (иначе урон идёт каждый тик)
    alive: bool = True

    # --- статистика ---
    shots: int = 0
    hits: int = 0
    ricochets: int = 0
    ricochets_taken: int = 0
    rams: int = 0
    damage_dealt: float = 0.0
    damage_taken: float = 0.0
    think_ms_max: float = 0.0
    think_ms_total: float = 0.0
    think_calls: int = 0
    over_budget: int = 0
    errors: int = 0
    timeouts: int = 0

    # --- наблюдение для противника (обновляется миром) ---
    seen_by: tuple[bool, bool] = (False, False)
    sees_enemy: bool = False
    sight_detail: dict = field(default_factory=dict)

    half_size: tuple[float, float] = (17.0, 11.0)

    def configure(self, bal: Balance) -> None:
        """Применяет размеры, здоровье и массу из баланса."""
        self.hp_max = bal.hp
        self.hp = bal.hp
        self.half_size = bal.half
        self.mass = bal.mass

    # --- геометрия ----------------------------------------------------------

    @property
    def half(self) -> tuple[float, float]:
        return self.half_size

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)

    @property
    def forward(self) -> tuple[float, float]:
        return (math.cos(self.hull), math.sin(self.hull))

    @property
    def barrel_dir(self) -> tuple[float, float]:
        return (math.cos(self.turret), math.sin(self.turret))

    def muzzle(self, bal: Balance) -> tuple[float, float]:
        dx, dy = self.barrel_dir
        return self.x + dx * bal.muzzle_offset, self.y + dy * bal.muzzle_offset

    @property
    def ammo_ready(self) -> bool:
        # 1e-6 спасает от остатка округления: 1.0 - 60 * (1/60) != 0.0,
        # и без допуска танк «залипал» бы с перезарядкой навсегда.
        return self.cooldown <= 1e-6

    def cooldown_ratio(self, bal: Balance) -> float:
        return clamp(self.cooldown / bal.reload, 0.0, 1.0)

    # --- шаг ----------------------------------------------------------------

    def step(self, cmd: Action, dt: float, arena: Arena, bal: Balance) -> bool:
        """Интегрирует тик. Возвращает True, если было столкновение со стеной."""
        # 1. Башня.
        angle = self.turret + cmd.turret * bal.turret_turn * dt
        angle = math.fmod(angle + math.pi, TAU)
        if angle < 0:
            angle += TAU
        self.turret = angle - math.pi

        # 2. Гусеницы. Тяга и руление задают скорости левого и правого трака,
        #    а из них уже следуют и поступательная скорость, и угловая:
        #    · газ + руление — танк идёт по дуге, нос уводит внутрь поворота;
        #    · газ + полный замок — скорость падает вдвое, гусеница буксует;
        #    · без газа + руление — разворот на месте, траки вразнобой;
        #    · задний ход — та же дуга, но зеркально: трак едет назад, нос
        #      уводит в ту же сторону, что и на переднем ходу (передача на
        #      разворот не влияет, как в аркадных танках).
        #    Раньше здесь крутили корпус напрямую и резали скорость плоским
        #    штрафом turn_speed_penalty, из-за чего поворот ощущался вялым.
        left = cmd.drive + cmd.turn
        right = cmd.drive - cmd.turn
        a_left = abs(left)
        a_right = abs(right)
        span = a_left if a_left > a_right else a_right
        if span > 1.0:                    # иначе газ с рулением уходит в дрейф
            left /= span
            right /= span
        # Знак берём из поступательной части, модуль — из speed_fwd/speed_rev.
        # Раньше стояло умножение на -speed_rev, и задний ход разворачивался
        # в передний: S вёс танк вперёд, а отступление ИИ упиралось в противника.
        drive = (left + right) * 0.5
        want = drive * (bal.speed_fwd if drive >= 0 else bal.speed_rev)
        want *= arena.speed_factor(self.x, self.y)
        angle = self.hull + (left - right) * 0.5 * bal.hull_turn * dt
        angle = math.fmod(angle + math.pi, TAU)
        if angle < 0:
            angle += TAU
        self.hull = angle - math.pi
        fx = math.cos(self.hull)
        fy = math.sin(self.hull)
        tvx = fx * want
        tvy = fy * want

        # 3. Разгон/торможение по вектору скорости.
        rate = bal.accel if (abs(want) > 1e-6) else bal.decel
        dvx = tvx - self.vx
        dvy = tvy - self.vy
        dlen = math.hypot(dvx, dvy)
        limit = rate * dt
        if dlen > limit:
            k = limit / dlen
            dvx *= k
            dvy *= k
        vx = self.vx + dvx
        vy = self.vy + dvy
        # Модуль скорости не может превысить максимум: иначе при резком
        # развороте корпуса инерция уводит танк дальше цели и он едет «назад».
        sp = math.hypot(vx, vy)
        fwd = bal.speed_fwd
        if sp > fwd:
            k = fwd / sp
            vx *= k
            vy *= k
        self.vx = vx
        self.vy = vy

        # 4. Перемещение и расталкивание стенами.
        x = self.x + vx * dt
        y = self.y + vy * dt
        # Границы карты жёсткие: за периметр не выезжаем ни при какой расталкировке.
        half = self.half_size
        hx, hy = half
        pad = (hx if hx > hy else hy) + 1.0
        lo_x = pad
        hi_x = arena.pixel_width - pad
        lo_y = pad
        hi_y = arena.pixel_height - pad
        if x < lo_x or x > hi_x:
            x = lo_x if x < lo_x else hi_x
            self.vx = 0.0
            vx = 0.0
        if y < lo_y or y > hi_y:
            y = lo_y if y < lo_y else hi_y
            self.vy = 0.0
            vy = 0.0

        # cos/sin курса уже посчитаны выше (fx, fy): push_out берёт их
        # готовыми, значения те же — угол тот же самый.
        nx, ny, hit = push_out(arena, x, y, half, self.hull, ca=fx, sa=fy)
        if hit:
            cx = nx - x
            cy = ny - y
            nlen = math.hypot(cx, cy)
            if nlen > 1e-9:
                ux = cx / nlen
                uy = cy / nlen
                dot = self.vx * ux + self.vy * uy
                if dot < 0:
                    # Гасим компоненту скорости, направленную в стену.
                    self.vx -= ux * dot
                    self.vy -= uy * dot
            x = nx
            y = ny
        self.x = x
        self.y = y

        # 5. Перезарядка и пауза после тарана.
        cooldown = self.cooldown
        self.cooldown = max(0.0, cooldown - dt) if cooldown > 1e-6 else 0.0
        ram_cd = self.ram_cd
        self.ram_cd = max(0.0, ram_cd - dt) if ram_cd > 1e-6 else 0.0
        return hit

    # --- урон ---------------------------------------------------------------

    def take_damage(self, amount: float) -> bool:
        """Применяет урон. Возвращает True, если танк уничтожен."""
        self.hp = max(0.0, round(self.hp - amount, 4))
        self.damage_taken += amount
        if self.hp <= 0.0:
            self.alive = False
            return True
        return False

    # --- наблюдение ---------------------------------------------------------

    def view(self, bal: Balance) -> dict:
        """Компактное представление для скрипта (сырые данные)."""
        return {
            "x": round(self.x, 2), "y": round(self.y, 2),
            "hull": round(self.hull, 5), "turret": round(self.turret, 5),
            "vx": round(self.vx, 2), "vy": round(self.vy, 2),
            "hp": round(self.hp, 3), "hp_max": self.hp_max,
            "ammo_ready": self.ammo_ready,
            "cooldown": round(self.cooldown, 4),
            "reload": bal.reload,
        }

    def stats(self) -> dict:
        avg = self.think_ms_total / self.think_calls if self.think_calls else 0.0
        acc = (self.hits / self.shots * 100.0) if self.shots else 0.0
        return {
            "name": self.name,
            "shots": self.shots,
            "hits": self.hits,
            "ricochets": self.ricochets,
            "ricochets_taken": self.ricochets_taken,
            "rams": self.rams,
            "accuracy": round(acc, 1),
            "damage_dealt": round(self.damage_dealt, 2),
            "damage_taken": round(self.damage_taken, 2),
            "hp_left": round(self.hp, 2),
            "think_ms_max": round(self.think_ms_max, 3),
            "think_ms_avg": round(avg, 3),
            "over_budget": self.over_budget,
            "errors": self.errors,
            "timeouts": self.timeouts,
        }