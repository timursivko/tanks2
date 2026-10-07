#!TANKP 1
# name: Ghost Phantom
# author: CyberMind
# difficulty: 5
# color: #202020
# description: Умные засады, контроль границ карты (не выезжает за край), отступление ромбом.
# tags: засада, стелс, анти-стена, киберспорт

from tankp import TankProgram, Action
import math

class Brain(TankProgram):
    def on_start(self, ctx):
        self.enemy_last_pos = None
        self.my_fake_pos = None
        self.last_seen_time = 0

    def on_tick(self, o):
        turn = 0.0
        drive = 0.0
        turret = 0.0
        fire = False

        # Переводим текущий угол корпуса в радианы для расчетов
        hull_rad = math.radians(o.me.hull)

        # ==========================================
        # РЕЖИМ 1: БОЙ И АГРЕССИВНОЕ ОТСТУПЛЕНИЕ
        # ==========================================
        if o.enemy is not None:
            self.enemy_last_pos = (o.enemy.x, o.enemy.y)
            self.my_fake_pos = (o.me.x, o.me.y)
            self.last_seen_time = o.time

            # 1. Прицеливание (с упреждением)
            target_pt = o.lead(o.enemy)
            t_err = o.me.turret_error(target_pt)
            turret = max(-1.0, min(1.0, t_err * 0.5))
            fire = abs(t_err) < 2.0 

            # 2. Ромбование 32 градуса
            h_err_base = o.me.hull_error(o.enemy)
            ideal_angle = 32.0 if h_err_base > 0 else -32.0
            turn_diff = h_err_base - ideal_angle
            turn = max(-1.0, min(1.0, turn_diff * 0.2))

            # 3. Отступление с защитой от края карты
            drive = -0.8 
            back_rad = math.radians(o.me.hull + 180)
            
            # Если прямо позади нас (на расстоянии 60 px) граница карты или стена — рвем вперед!
            if o.map.blocked_between(o.me.x, o.me.y, o.me.x + math.cos(back_rad)*60, o.me.y + math.sin(back_rad)*60):
                drive = 1.0  # Уходим от зажатия в углу
                
                # Дополнительно сбиваем прицел противнику хаотичным газом, раз уж нельзя назад
                if math.sin(o.time * 5.0) > 0:
                    drive = 0.3

        # ==========================================
        # РЕЖИМ 2: СТЕЛС И ОБХОД С ТЫЛА
        # ==========================================
        else:
            target_x, target_y = o.map.spawns[1 - o.tank]

            # Умное предсказание (Без выезда за карту!)
            if self.enemy_last_pos and self.my_fake_pos:
                dx = self.my_fake_pos[0] - self.enemy_last_pos[0]
                dy = self.my_fake_pos[1] - self.enemy_last_pos[1]
                dist = math.hypot(dx, dy)

                if dist > 10:
                    nx, ny = dx / dist, dy / dist
                    dt = o.time - self.last_seen_time
                    travel = min(dist, 120 * dt)
                    
                    # Предполагаемая позиция врага
                    est_x = self.enemy_last_pos[0] + nx * travel
                    est_y = self.enemy_last_pos[1] + ny * travel

                    # Ищем точку для засады позади него, пошагово проверяя карту (шаг 20 px)
                    # Если наткнемся на край карты или стену - остановим цикл и возьмем последнюю легальную точку.
                    valid_x, valid_y = est_x, est_y
                    for step in range(20, 220, 20):
                        tx = est_x - nx * step
                        ty = est_y - ny * step
                        
                        # Если точка внутри стены или за картой - прекращаем строить вектор
                        if o.map.blocked_at(tx, ty):
                            break 
                        valid_x, valid_y = tx, ty
                    
                    target_x, target_y = valid_x, valid_y

            # --- САМОПИСНАЯ НАВИГАЦИЯ (Whiskers 2.0) ---
            target_deg = math.degrees(math.atan2(target_y - o.me.y, target_x - o.me.x))
            diff = (target_deg - o.me.hull + 180) % 360 - 180
            turn = max(-1.0, min(1.0, diff / 40.0))  # Базовый руль на цель
            drive = 1.0

            # АВАРИЙНЫЙ ТОРМОЗ (Защита от края карты)
            # Если прямо по курсу в 45 пикселях стена/край - сдаем назад!
            if o.map.blocked_between(o.me.x, o.me.y, o.me.x + math.cos(hull_rad)*45, o.me.y + math.sin(hull_rad)*45):
                drive = -0.8
                turn = 1.0 if turn >= 0 else -1.0  # Выкручиваем руль, чтобы развернуться
            else:
                # Мягкое огибание препятствий (5 лучей сканирования)
                rays = [(-45, 100), (-20, 130), (0, 150), (20, 130), (45, 100)]
                blocked = {}
                for angle, length in rays:
                    rad = math.radians(o.me.hull + angle)
                    bx = o.me.x + math.cos(rad) * length
                    by = o.me.y + math.sin(rad) * length
                    blocked[angle] = o.map.blocked_between(o.me.x, o.me.y, bx, by)

                if blocked[0]:
                    drive = 0.5  # Сбавляем скорость перед преградой
                    # Поворачиваем туда, где свободнее
                    if blocked[20] and not blocked[-20]: turn = -1.0
                    elif blocked[-20] and not blocked[20]: turn = 1.0
                    else: turn = 1.0
                
                # Отталкивание от стен по бокам
                if blocked[45] or blocked[20]: turn -= 0.7
                if blocked[-45] or blocked[-20]: turn += 0.7

            # Ограничиваем значение руля
            turn = max(-1.0, min(1.0, turn))

            # Башня заранее наводится в сторону цели
            t_err = o.me.turret_error((target_x, target_y))
            turret = max(-1.0, min(1.0, t_err * 0.3))

        return Action(drive=drive, turn=turn, turret=turret, fire=fire)

program = Brain()