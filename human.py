"""
human.py — Имитация человеческого поведения в браузере.

Принципы антибот-обнаружения:
  - Scroll: нелинейное ускорение (easing), случайное чтение (долгие паузы),
    лёгкое подёргивание назад, дрожание курсора
  - Mouse: Bezier-кривые, случайный дрейф, неточное попадание
  - Timings: нет постоянных интервалов, используется нормальное распределение
"""

import math
import random
import time


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _gauss(mu: float, sigma: float, lo: float, hi: float) -> float:
    """Нормальное распределение, зажатое в [lo, hi]."""
    return max(lo, min(hi, random.gauss(mu, sigma)))


def _ease_out_quad(t: float) -> float:
    """Квадратичное easing out: начинает быстро, замедляется."""
    return 1 - (1 - t) ** 2


def _ease_in_out(t: float) -> float:
    """Плавное ускорение и замедление."""
    return t * t * (3 - 2 * t)


def _jitter(px: int = 3) -> tuple[int, int]:
    """Мелкое случайное дрожание курсора."""
    return random.randint(-px, px), random.randint(-px, px)


def _bezier_move(page, x0: float, y0: float, x1: float, y1: float, steps: int = 20):
    """
    Перемещение мыши по кривой Безье с 1 случайной контрольной точкой.
    Имитирует неровное человеческое движение руки.
    """
    # Случайная контрольная точка смещена перпендикулярно вектору движения
    mx = (x0 + x1) / 2 + random.randint(-80, 80)
    my = (y0 + y1) / 2 + random.randint(-80, 80)

    for i in range(1, steps + 1):
        t = i / steps
        # Квадратичная кривая Безье
        bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * mx + t ** 2 * x1
        by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * my + t ** 2 * y1
        # Мелкое дрожание
        jx, jy = _jitter(2)
        page.mouse.move(bx + jx, by + jy)
        time.sleep(random.uniform(0.004, 0.012))


# ---------------------------------------------------------------------------
# Scrolling
# ---------------------------------------------------------------------------

def human_scroll(page, cfg: dict):
    """
    Человекоподобный скролл:
    - Серии из 2-5 быстрых scroll-шагов (читаем контент), затем пауза
    - Нелинейное ускорение внутри серии (easing)
    - Случайное подёргивание назад (backscroll)
    - Долгие паузы «на чтение» (≈15% вероятность)
    - Редкое движение мыши с Bezier-кривой
    """
    total_steps = random.randint(cfg["scroll_steps_min"], cfg["scroll_steps_max"])
    step = 0
    cursor_x = random.randint(300, 800)
    cursor_y = random.randint(300, 600)

    while step < total_steps:
        # Серия быстрых скроллов (как будто пролистываем)
        burst = random.randint(2, 5)
        burst = min(burst, total_steps - step)

        for b in range(burst):
            t = (b + 1) / burst
            ease = _ease_in_out(t)

            # Базовое расстояние скролла с нормальным распределением
            base_delta = _gauss(
                mu=(cfg["scroll_min_px"] + cfg["scroll_max_px"]) / 2,
                sigma=(cfg["scroll_max_px"] - cfg["scroll_min_px"]) / 4,
                lo=cfg["scroll_min_px"],
                hi=cfg["scroll_max_px"],
            )
            delta = int(base_delta * (0.7 + ease * 0.6))

            # Мелкое дрожание колеса
            delta += random.randint(-15, 15)
            delta = max(40, delta)

            page.mouse.wheel(0, delta)

            # Пауза между шагами в серии — короткая
            pause = _gauss(
                mu=(cfg["min_scroll_pause"] + cfg["max_scroll_pause"]) / 2,
                sigma=0.08,
                lo=cfg["min_scroll_pause"] * 0.6,
                hi=cfg["max_scroll_pause"] * 0.8,
            )
            time.sleep(pause)
            step += 1

        # После серии — пауза «читаем контент»
        if random.random() < 0.18:
            # Долгая пауза (остановились почитать)
            read_time = _gauss(mu=2.2, sigma=0.7, lo=1.0, hi=5.0)
            print(f"    👀 Reading pause ({read_time:.1f}s)")
            time.sleep(read_time)
        else:
            # Обычная пауза между сериями
            gap = _gauss(
                mu=(cfg["min_idle_pause"] + cfg["max_idle_pause"]) / 2,
                sigma=0.3,
                lo=cfg["min_idle_pause"] * 0.5,
                hi=cfg["max_idle_pause"],
            )
            time.sleep(gap)

        # Случайное движение мышью (30%)
        if random.random() < 0.30:
            new_x = _gauss(mu=500, sigma=180, lo=100, hi=900)
            new_y = _gauss(mu=450, sigma=150, lo=100, hi=700)
            steps_m = random.randint(10, 25)
            _bezier_move(page, cursor_x, cursor_y, new_x, new_y, steps=steps_m)
            cursor_x, cursor_y = new_x, new_y
            time.sleep(random.uniform(0.1, 0.4))

        # Лёгкий backscroll (подёргивание вверх, как будто вернулись)
        if random.random() < 0.12:
            back = random.randint(60, 200)
            page.mouse.wheel(0, -back)
            time.sleep(random.uniform(0.3, 0.8))
            # И снова вниз
            page.mouse.wheel(0, back + random.randint(10, 50))
            time.sleep(random.uniform(0.1, 0.3))

    # Финальное небольшое подёргивание назад (30%)
    if random.random() < 0.30:
        page.mouse.wheel(0, -random.randint(50, 180))
        time.sleep(random.uniform(0.2, 0.6))


# ---------------------------------------------------------------------------
# Idle behaviour
# ---------------------------------------------------------------------------

def human_idle(page, cfg: dict):
    """
    Случайное поведение в паузах:
    - Движение мыши по Bezier
    - Лёгкое hover над контентом
    - Иногда ничего не делаем (просто ждём)
    """
    roll = random.random()

    if roll < 0.35:
        # Двигаем мышь по Bezier-кривой
        x0 = random.randint(100, 900)
        y0 = random.randint(100, 700)
        x1 = random.randint(100, 900)
        y1 = random.randint(100, 700)
        _bezier_move(page, x0, y0, x1, y1, steps=random.randint(12, 22))
        time.sleep(_gauss(mu=0.5, sigma=0.2, lo=0.15, hi=1.2))

    elif roll < 0.55:
        # Hover и микропауза
        page.mouse.move(
            random.randint(200, 800),
            random.randint(200, 600),
            steps=random.randint(6, 14),
        )
        time.sleep(_gauss(
            mu=(cfg["min_hover_pause"] + cfg["max_hover_pause"]) / 2,
            sigma=0.15,
            lo=cfg["min_hover_pause"],
            hi=cfg["max_hover_pause"],
        ))

    else:
        # Ничего — просто ждём (человек отвлёкся)
        idle = _gauss(
            mu=(cfg["min_idle_pause"] + cfg["max_idle_pause"]) / 2,
            sigma=0.4,
            lo=cfg["min_idle_pause"],
            hi=cfg["max_idle_pause"] * 1.2,
        )
        time.sleep(idle)


# ---------------------------------------------------------------------------
# Liker
# ---------------------------------------------------------------------------

_LIKE_SELECTORS = [
    '[aria-label="Like"]',
    '[data-testid="UFI2ReactionLink/root"]',
    'div[role="button"] span:text-is("Like")',
]


def human_like_post(page, cfg: dict) -> bool:
    """Случайно лайкает видимый пост. Возвращает True при успехе."""
    if not cfg.get("like_enabled", True):
        return False

    try:
        candidates = []
        for selector in _LIKE_SELECTORS:
            try:
                elements = page.query_selector_all(selector)
                for el in elements:
                    try:
                        if el.is_visible():
                            box = el.bounding_box()
                            if box and box["width"] > 0 and box["height"] > 0:
                                candidates.append((el, box))
                    except Exception:
                        pass
            except Exception:
                pass

        if not candidates:
            return False

        el, box = random.choice(candidates)
        cx = box["x"] + box["width"] / 2 + random.randint(-3, 3)
        cy = box["y"] + box["height"] / 2 + random.randint(-3, 3)

        # Двигаем мышь по Bezier к кнопке
        cur_x = random.randint(100, 900)
        cur_y = random.randint(100, 700)
        _bezier_move(page, cur_x, cur_y, cx, cy, steps=random.randint(14, 28))

        delay = _gauss(
            mu=(cfg.get("like_min_delay", 1.2) + cfg.get("like_max_delay", 3.5)) / 2,
            sigma=0.5,
            lo=cfg.get("like_min_delay", 1.2),
            hi=cfg.get("like_max_delay", 3.5),
        )
        time.sleep(delay)

        el.click(timeout=3000)
        print(f"   👍 Liked a post (delay={delay:.1f}s)")

        time.sleep(_gauss(mu=0.9, sigma=0.3, lo=0.4, hi=2.0))
        return True

    except Exception as e:
        print(f"   ℹ️  Like attempt skipped: {e}")
        return False
