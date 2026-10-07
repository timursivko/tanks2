"""Мир: фиксированный такт 1/60, порядок событий и снимок кадра.

Порядок тика выбран так, чтобы ни у кого не было преимущества первого хода:

1. снимок состояния и видимость (оба танка видят одно и то же);
2. оба скрипта принимают решение;
3. применяются команды, движение, тараны;
4. выстрелы;
5. полёт снарядов и разбор урона;
6. проверка конца боя, видимость для следующего тика, запись кадра.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from config import Balance
from engine import projectile
from engine.action import Action
from engine.events import EndReason, EventKind, Outcome
from engine.geometry import segment_obb
from engine.grid import Arena, push_out
from engine.tank import Tank
from engine.visibility import can_see, can_see_tuple


def _obb_span(tank: Tank, ux: float, uy: float) -> float:
    """Насколько корпус танка выступает вдоль направления ``(ux, uy)``.

    Половина ширины проекции повёрнутого прямоугольника: нужна, чтобы
    отличить реальное перекрытие корпусов от «танки стоят в одну линию».
    """
    hx, hy = tank.half_size
    ca, sa = math.cos(tank.hull), math.sin(tank.hull)
    return hx * abs(ca * ux + sa * uy) + hy * abs(-sa * ux + ca * uy)


@dataclass
class World:
    """Боевая сцена."""

    arena: Arena
    bal: Balance
    seed: int = 0
    tick_rate: int = 60
    max_ticks: int = 3600

    tanks: list[Tank] = field(default_factory=list)
    bullets: list = field(default_factory=list)
    tick: int = 0
    rng: random.Random = field(default_factory=random.Random)

    outcome: str = ""
    end_reason: str = ""
    over: bool = False

    def __post_init__(self) -> None:
        self.dt = 1.0 / self.tick_rate
        #: ``dt`` для наблюдения (тот же round, что был на каждый тик, но
        #: значение постоянно для боя — считается один раз).
        self.dt_rounded = round(self.dt, 5)
        self.rng = random.Random(self.seed)
        self.max_ticks = int(self.max_ticks)

    # --- создание -----------------------------------------------------------

    @classmethod
    def create(cls, arena: Arena, bal: Balance, seed: int = 0,
               names: tuple[str, str] = ("Танк A", "Танк B"),
               colors: tuple[str, str] = ("#ffb020", "#3ea6ff"),
               kinds: tuple[str, str] = ("script", "script"),
               tick_rate: int = 60, max_seconds: int = 60) -> "World":
        self = cls(arena=arena, bal=bal, seed=seed, tick_rate=tick_rate,
                   max_ticks=tick_rate * max_seconds)
        for i, sp in enumerate(arena.spawns[:2]):
            t = Tank(id=i, x=sp.x, y=sp.y, hull=sp.angle, turret=sp.angle,
                     name=names[i], color=colors[i], kind=kinds[i])
            t.configure(bal)
            self.free_spawn(t, arena)
            self.tanks.append(t)
        self.update_vision()
        return self

    @staticmethod
    def free_spawn(tank: Tank, arena: Arena) -> None:
        """Гарантирует, что со спавна можно уехать.

        На картах из репозитория спавны уже проверены генератором, но любую
        свою карту игрок может загрузить через API. Если нос танка смотрит в
        стену или он стоит в ней, управление выглядит сломанным: газ даёт
        несколько пикселей, а противник спокойно таранит. Поэтому выталкиваем
        корпус из стены и разворачиваем нос туда, где просторнее.
        """
        x, y, hit = push_out(arena, tank.x, tank.y, tank.half_size, tank.hull)
        if hit:
            tank.x, tank.y = x, y
        probe = 40.0
        fx = tank.x + math.cos(tank.hull) * probe
        fy = tank.y + math.sin(tank.hull) * probe
        if arena.clearance_px(fx, fy) >= 24.0:       # прямо по курсу ехать можно
            return
        best, best_clear = tank.hull, -1.0
        for k in range(16):                        # ищем самый просторный курс
            ang = math.pi * 2 * k / 16
            clear = arena.clearance_px(tank.x + math.cos(ang) * probe,
                                       tank.y + math.sin(ang) * probe)
            if clear > best_clear:
                best, best_clear = ang, clear
        tank.hull = tank.turret = best

    # --- наблюдение ---------------------------------------------------------

    def vision_of(self, i: int) -> dict:
        tanks = self.tanks
        me = tanks[i]
        foe = tanks[1 - i]
        return can_see(self.arena, me.x, me.y, me.turret, foe.x, foe.y,
                       foe.half_size, foe.hull, self.bal)

    def update_vision(self) -> None:
        # Тот же расчёт, что и vision_of, но для двух танков сразу:
        # вызовы и чтения полей мира на каждый тик дешевле не дублировать.
        tanks = self.tanks
        arena = self.arena
        bal = self.bal
        a = tanks[0]
        b = tanks[1]
        # Кортеж вместо словаря (см. can_see_tuple): первым полем идёт
        # «виден ли противник», ровно как в наблюдении скрипта. Если цель
        # уже мертва, поле сбрасывается: наблюдение читает его напрямую.
        d = can_see_tuple(arena, a.x, a.y, a.turret, b.x, b.y, b.half_size, b.hull, bal)
        alive_b = b.alive
        a.sees_enemy = d[0] and alive_b
        a.sight_detail = (False,) + d[1:] if (d[0] and not alive_b) else d
        d = can_see_tuple(arena, b.x, b.y, b.turret, a.x, a.y, a.half_size, a.hull, bal)
        alive_a = a.alive
        b.sees_enemy = d[0] and alive_a
        b.sight_detail = (False,) + d[1:] if (d[0] and not alive_a) else d
        a_sees = a.sees_enemy
        b_sees = b.sees_enemy
        a.seen_by = (b_sees, a_sees)
        b.seen_by = (a_sees, b_sees)

    # --- шаг ----------------------------------------------------------------

    def step(self, actions: list[Action]) -> list[dict]:
        """Один такт боя. ``actions`` — по команде на каждого из двух танков."""
        events: list[dict] = []
        self.tick += 1
        t_now = self.tick * self.dt

        if self.tick == 1:
            events.append(self._evt(EventKind.START, t=t_now))

        # 1. Движение по командам.
        for i, t in enumerate(self.tanks):
            if not t.alive:
                continue
            t.step(actions[i], self.dt, self.arena, self.bal)

        # 2. Тараны.
        ram = self._resolve_ram()
        if ram:
            events.append(ram)

        # 3. Выстрелы (только если ствол не упирается в стену).
        for i, t in enumerate(self.tanks):
            if not t.alive or not actions[i].fire:
                continue
            if not t.ammo_ready:
                continue
            if projectile.barrel_blocked(t, self.arena, self.bal):
                events.append(self._evt("barrel_blocked", t=t_now, tank=i))
                continue
            b = projectile.spawn(t, self.bal, self.rng)
            if b is not None:
                self.bullets.append(b)
                events.append(self._evt(EventKind.SHOT, t=t_now, tank=i,
                                        x=round(b.x, 1), y=round(b.y, 1),
                                        angle=round(math.atan2(b.dy, b.dx), 5)))

        # 4. Полёт снарядов.
        survivors = []
        for b in self.bullets:
            ev = projectile.step_bullet(b, self.dt, self.arena, self.tanks, self.bal, self.tick)
            if ev is not None:
                events.append(self._dict_event(ev, t_now))
            if b.alive:
                survivors.append(b)
        self.bullets = survivors

        # 5. Гибель.
        for i, t in enumerate(self.tanks):
            if t.alive and t.hp <= 0.0:
                t.alive = False
                events.append(self._evt(EventKind.DEATH, t=t_now, tank=i))

        # 6. Итог.
        self._finish(events, t_now)

        # 7. Видимость для следующего тика и для реплея.
        self.update_vision()
        return events

    # --- служебное ----------------------------------------------------------

    def _evt(self, kind: str, t: float, **kw) -> dict:
        ev = {"kind": kind, "t": round(t, 4)}
        ev.update(kw)
        return ev

    def _dict_event(self, ev: dict, t: float) -> dict:
        out = {"kind": EventKind.HIT if ev["kind"] == "hit" else "spark",
               "t": round(t, 4), "shooter": ev.get("owner"), "target": ev.get("target")}
        if ev["kind"] == "hit":
            out.update({"damage": ev["damage"], "face": ev["face"],
                        "theta": ev["theta"], "ricochet": ev["ricochet"],
                        "x": round(ev["x"], 1), "y": round(ev["y"], 1)})
            shooter = self.tanks[ev["owner"]]
            if ev["ricochet"]:
                # Отскок засчитывается отдельно от попаданий: урона он
                # не наносит, и в проценте точности считаться не должен.
                shooter.ricochets += 1
            else:
                shooter.hits += 1
                shooter.damage_dealt += ev["damage"]
        else:
            out.update({"x": round(ev["x"], 1), "y": round(ev["y"], 1),
                        # bounce=True — снаряд отскочил от стены и летит дальше,
                        # False — ударился и разбился. Фронту нужно для искр.
                        "bounce": ev["kind"] == "spark"})
        return out

    def _resolve_ram(self) -> dict | None:
        a, b = self.tanks
        if not a.alive or not b.alive:
            return None
        d = math.hypot(b.x - a.x, b.y - a.y)
        if d < 1e-6:
            return None
        # Быстрая отсечка: корпус не выходит за полудиагональ (с запасом
        # 1.4143 = чуть больше sqrt(2)), поэтому на расстоянии больше суммы
        # полудиагоналей перекрытия быть не может — _obb_span не нужен.
        hxa, hya = a.half_size
        hxb, hyb = b.half_size
        if d >= 1.4143 * ((hxa if hxa > hya else hya) + (hxb if hxb > hyb else hyb)):
            return None
        ux, uy = (b.x - a.x) / d, (b.y - a.y) / d
        # Настоящее перекрытие корпусов, а не попадание луча. segment_obb
        # срабатывает всегда, когда танки просто стоят в линию друг на
        # друга (центр A «видит» корпус B), поэтому для контакта считаем
        # сумму полуразмеров вдоль линии между центрами.
        pen = _obb_span(a, ux, uy) + _obb_span(b, ux, uy) - d
        if pen <= 0.0:
            return None
        closing = (a.vx - b.vx) * ux + (a.vy - b.vy) * uy
        ma, mb = max(a.mass, 1e-6), max(b.mass, 1e-6)
        # Расталкивание и импульс — каждый тик контакта, а не раз в паузу:
        # иначе танки продолжают вдавливаться друг в друга и «залипают».
        # Раньше здесь был обмен скоростей по мировым осям
        # (`a.vx, b.vx = b.vx*0.5, a.vx*0.5`): при косом ударе это давало
        # бессмысленный результат, а если жертва пятилась, атакующий
        # получал отрицательную скорость и уезжал назад — отсюда «едет
        # вперёд, потом откидывает».
        a.x -= ux * pen * 0.15
        a.y -= uy * pen * 0.15
        b.x += ux * pen * 0.15
        b.y += uy * pen * 0.15
        if closing > 0.0:
            # Сближающаяся скорость гасится и с коэффициентом ram_restitution
            # превращается в небольшое расхождение. Касательная не
            # трогается, поэтому скользящий таран не «прилипает».
            j = (1.0 + self.bal.ram_restitution) * closing / (1.0 / ma + 1.0 / mb)
            a.vx -= j * ux / ma
            a.vy -= j * uy / ma
            b.vx += j * ux / mb
            b.vy += j * uy / mb
        if closing <= self.bal.ram_speed:
            return None                         # просто расталкивание, не удар
        # Урон от тарана — раз в паузу, иначе контакт убивал бы за доли секунды.
        if a.ram_cd > 0.0 or b.ram_cd > 0.0:
            return None
        dmg = self.bal.ram_damage
        a.ram_cd = b.ram_cd = self.bal.ram_cooldown
        a.take_damage(dmg)
        b.take_damage(dmg)
        a.rams += 1
        b.rams += 1
        return self._evt(EventKind.RAM, t=self.tick * self.dt, damage=dmg,
                         x=round((a.x + b.x) / 2, 1), y=round((a.y + b.y) / 2, 1))

    def _finish(self, events: list[dict], t_now: float) -> None:
        if self.over:
            return
        a, b = self.tanks
        if a.alive and b.alive and self.tick >= self.max_ticks:
            self.over = True
            self.outcome = Outcome.DRAW
            self.end_reason = EndReason.TIMEOUT
            events.append(self._evt(EventKind.END, t=t_now, outcome=self.outcome,
                                    reason=self.end_reason))
        elif not a.alive and not b.alive:
            self.over = True
            self.outcome = Outcome.DRAW
            self.end_reason = EndReason.MUTUAL
            events.append(self._evt(EventKind.END, t=t_now, outcome=self.outcome,
                                    reason=self.end_reason))
        elif not b.alive:
            self.over = True
            self.outcome = Outcome.A_WIN
            self.end_reason = EndReason.DESTROYED
            events.append(self._evt(EventKind.END, t=t_now, outcome=self.outcome,
                                    reason=self.end_reason))
        elif not a.alive:
            self.over = True
            self.outcome = Outcome.B_WIN
            self.end_reason = EndReason.DESTROYED
            events.append(self._evt(EventKind.END, t=t_now, outcome=self.outcome,
                                    reason=self.end_reason))

    # --- снимок -------------------------------------------------------------

    def snapshot(self, think_ms: tuple[float, float] = (0.0, 0.0),
                 over_budget: tuple[bool, bool] = (False, False)) -> dict:
        """Кадр для реплея/клиента."""
        return {
            "t": round(self.tick * self.dt, 5),
            "a": self._tank_snap(0, think_ms[0], over_budget[0]),
            "b": self._tank_snap(1, think_ms[1], over_budget[1]),
            "sh": [[round(b.x, 1), round(b.y, 1), round(b.dx, 4), round(b.dy, 4), b.owner]
                   for b in self.bullets],
        }

    def _tank_snap(self, i: int, ms: float, bad: bool) -> dict:
        t = self.tanks[i]
        return {
            "x": round(t.x, 2), "y": round(t.y, 2),
            "h": round(t.hull, 5), "u": round(t.turret, 5),
            "hp": round(t.hp, 3), "cd": round(t.cooldown, 4),
            "vis": 1 if t.sees_enemy else 0,
            "ms": round(ms, 3), "bad": 1 if bad else 0,
            "alive": 1 if t.alive else 0,
            "spd": round(t.speed, 1),
        }